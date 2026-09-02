"""
Aave V3 USDC yield provider on Base.

Knows only about Aave — which contracts to call and how to encode the calldata.
How those calls get signed and submitted is the Signer's job.
"""

from decimal import Decimal

from web3 import Web3

from ..signers.base import Call, GasEstimate, Signer
from .base import YieldProvider

USDC_ADDRESS      = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
AAVE_POOL_ADDRESS = "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
USDC_DECIMALS     = 6
RAY               = Decimal(10 ** 27)

POOL_ABI = [
    {"name": "supply",   "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "asset",        "type": "address"},
                {"name": "amount",       "type": "uint256"},
                {"name": "onBehalfOf",   "type": "address"},
                {"name": "referralCode", "type": "uint16"}],
     "outputs": []},
    {"name": "withdraw", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "asset",  "type": "address"},
                {"name": "amount", "type": "uint256"},
                {"name": "to",     "type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "getReserveData", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "asset", "type": "address"}],
     "outputs": [
         {"name": "configuration",             "type": "uint256"},
         {"name": "liquidityIndex",            "type": "uint128"},
         {"name": "currentLiquidityRate",      "type": "uint128"},
         {"name": "variableBorrowIndex",       "type": "uint128"},
         {"name": "currentVariableBorrowRate", "type": "uint128"},
         {"name": "currentStableBorrowRate",   "type": "uint128"},
         {"name": "lastUpdateTimestamp",       "type": "uint40"},
         {"name": "id",                        "type": "uint16"},
         {"name": "aTokenAddress",             "type": "address"},
         {"name": "stableDebtTokenAddress",    "type": "address"},
         {"name": "variableDebtTokenAddress",  "type": "address"},
         {"name": "interestRateStrategyAddress","type": "address"},
         {"name": "accruedToTreasury",         "type": "uint128"},
         {"name": "unbacked",                  "type": "uint128"},
         {"name": "isolationModeTotalDebt",    "type": "uint128"},
     ]},
]

ERC20_ABI = [
    {"name": "approve",   "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"type": "uint256"}]},
]

_MAX_UINT256 = 2 ** 256 - 1


class AaveProvider(YieldProvider):
    """
    Aave V3 USDC provider on Base.

    Args:
        signer: Any ``Signer`` implementation — EOA, Gnosis Safe, or custom.
                The signer's ``address`` is where the USDC lives and where
                aTokens accumulate.

    Example::

        from defi_savings import AaveProvider
        from defi_savings.signers import EOASigner, GnosisSafeSigner

        # Single private key
        provider = AaveProvider(EOASigner(private_key, rpc_url))

        # Gnosis Safe 2-of-2
        provider = AaveProvider(GnosisSafeSigner(safe_addr, key1, key2, rpc_url))

        # Your own wallet type
        provider = AaveProvider(MyCoinbaseWalletSigner(...))
    """

    name = "aave-v3-base"

    def __init__(self, signer: Signer) -> None:
        self._signer = signer
        # Reuse the signer's Web3 connection for read-only calls
        self.w3   = signer.w3
        self.pool = self.w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=POOL_ABI)
        self.usdc = self.w3.eth.contract(address=USDC_ADDRESS,      abi=ERC20_ABI)

    def current_apy(self) -> Decimal:
        reserve  = self.pool.functions.getReserveData(USDC_ADDRESS).call()
        apr_ray  = Decimal(reserve[2])   # currentLiquidityRate in RAY units
        return ((apr_ray / RAY) * 100).quantize(Decimal("0.01"))

    def position_balance(self) -> Decimal:
        """Return the signer's aToken balance (principal + accrued yield) in USDC."""
        reserve     = self.pool.functions.getReserveData(USDC_ADDRESS).call()
        atoken      = self.w3.eth.contract(address=reserve[8], abi=ERC20_ABI)
        raw         = atoken.functions.balanceOf(self._signer.address).call()
        return Decimal(raw) / Decimal(10 ** USDC_DECIMALS)

    def _deposit_calls(self, amount_raw: int) -> list[Call]:
        """Build the deposit call batch, skipping approve() when the Pool
        already holds sufficient allowance from a prior deposit (approves
        for ``_MAX_UINT256`` the first time so later deposits skip it)."""
        calls: list[Call] = []
        current_allowance = self.usdc.functions.allowance(
            self._signer.address, AAVE_POOL_ADDRESS
        ).call()
        if current_allowance < amount_raw:
            calls.append(Call(
                to   = USDC_ADDRESS,
                data = self.usdc.encode_abi("approve", [AAVE_POOL_ADDRESS, _MAX_UINT256]),
            ))
        calls.append(Call(
            to   = AAVE_POOL_ADDRESS,
            data = self.pool.encode_abi("supply", [USDC_ADDRESS, amount_raw, self._signer.address, 0]),
        ))
        return calls

    def _withdraw_calls(self, amount_raw: int) -> list[Call]:
        return [
            Call(
                to   = AAVE_POOL_ADDRESS,
                data = self.pool.encode_abi("withdraw", [USDC_ADDRESS, amount_raw, self._signer.address]),
            ),
        ]

    def _check_deposit_balance(self, amount_usdc: Decimal) -> int:
        amount_raw = int(amount_usdc * 10 ** USDC_DECIMALS)
        bal = self.usdc.functions.balanceOf(self._signer.address).call()
        if bal < amount_raw:
            have = Decimal(bal) / Decimal(10 ** USDC_DECIMALS)
            raise RuntimeError(
                f"USDC balance too low: have ${have:.2f}, need ${amount_usdc:.2f}."
            )
        return amount_raw

    def deposit(self, amount_usdc: Decimal) -> str:
        """Approve Aave Pool then supply USDC — delegated to the signer."""
        amount_raw = self._check_deposit_balance(amount_usdc)
        return self._signer.execute(self._deposit_calls(amount_raw))

    def withdraw(self, amount_usdc: Decimal) -> str:
        """Withdraw USDC from Aave back to the signer's address."""
        amount_raw = int(amount_usdc * 10 ** USDC_DECIMALS)
        return self._signer.execute(self._withdraw_calls(amount_raw))

    def estimate_deposit_cost(self, amount_usdc: Decimal) -> GasEstimate:
        """Quote the ETH cost of depositing ``amount_usdc``, without depositing.
        Raises ``NotImplementedError`` if the signer doesn't support cost
        estimation (see ``Signer.estimate_cost``'s docstring)."""
        amount_raw = self._check_deposit_balance(amount_usdc)
        return self._signer.estimate_cost(self._deposit_calls(amount_raw))

    def estimate_withdraw_cost(self, amount_usdc: Decimal) -> GasEstimate:
        """Quote the ETH cost of withdrawing ``amount_usdc``, without withdrawing.
        Raises ``NotImplementedError`` if the signer doesn't support cost
        estimation (see ``Signer.estimate_cost``'s docstring)."""
        amount_raw = int(amount_usdc * 10 ** USDC_DECIMALS)
        return self._signer.estimate_cost(self._withdraw_calls(amount_raw))
