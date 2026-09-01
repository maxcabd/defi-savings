"""
Generic ERC-4626 tokenized vault provider.

Any ERC-4626 compliant vault (Morpho MetaMorpho, Compound v3 wrappers,
Euler vaults, etc.) on any EVM chain can be wrapped in 5 lines:

    from defi_savings import Erc4626Provider, GnosisSafeSigner

    provider = Erc4626Provider(
        vault_address = "0x...",
        signer        = GnosisSafeSigner(safe_addr, key1, key2, rpc_url),
        name          = "my-vault-base",
    )
    provider.deposit(Decimal("1000"))
    balance = provider.position_balance()   # shares → assets at current price

Wiring in a live APY
--------------------
ERC-4626 has no on-chain APY standard. Pass ``apy_fn`` to plug in any rate
source — including the ``fetch_rates`` helper in this package:

    from defi_savings.rates import fetch_rates

    def live_apy() -> Decimal:
        pools = fetch_rates("Base", "SIRLOINUSDC")
        return pools[0].apy if pools else Decimal(0)

    provider = Erc4626Provider(
        vault_address = "0xCBeeF01994E24a60f7DCB8De98e75AD8BD4Ad60d",
        signer        = my_signer,
        name          = "morpho-sirloin-usdc-base",
        apy_fn        = live_apy,
    )

Custom asset
------------
Defaults to USDC on Base. Override ``asset_address`` and ``asset_decimals``
for any other ERC-20:

    provider = Erc4626Provider(
        vault_address  = "0x...",
        signer         = my_signer,
        name           = "some-weth-vault",
        asset_address  = "0x4200000000000000000000000000000000000006",  # WETH on Base
        asset_decimals = 18,
    )
"""

from decimal import Decimal

from web3 import Web3

from ..errors import VaultDepositCapExceededError
from ..signers.base import Call, GasEstimate, Signer
from .base import YieldProvider

# USDC on Base — default asset
_USDC_ADDRESS  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_USDC_DECIMALS = 6

_VAULT_ABI = [
    {
        "name": "deposit", "type": "function", "stateMutability": "nonpayable",
        "inputs":  [{"name": "assets",   "type": "uint256"},
                    {"name": "receiver", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "withdraw", "type": "function", "stateMutability": "nonpayable",
        "inputs":  [{"name": "assets",   "type": "uint256"},
                    {"name": "receiver", "type": "address"},
                    {"name": "owner",    "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs":  [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "convertToAssets", "type": "function", "stateMutability": "view",
        "inputs":  [{"name": "shares", "type": "uint256"}],
        "outputs": [{"type": "uint256"}],
    },
    {
        "name": "maxDeposit", "type": "function", "stateMutability": "view",
        "inputs":  [{"name": "receiver", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
]

_ERC20_ABI = [
    {
        "name": "approve", "type": "function", "stateMutability": "nonpayable",
        "inputs":  [{"name": "spender", "type": "address"},
                    {"name": "amount",  "type": "uint256"}],
        "outputs": [{"type": "bool"}],
    },
    {
        "name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs":  [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
]


class Erc4626Provider(YieldProvider):
    """
    ERC-4626 tokenized vault provider.

    Implements the full :class:`~defi_savings.providers.base.YieldProvider`
    interface — deposit, withdraw, and position_balance — for any vault that
    conforms to EIP-4626.

    Args:
        vault_address:   Checksummed or lowercase address of the ERC-4626 vault.
        signer:          Any :class:`~defi_savings.signers.base.Signer` instance
                         (EOA, Gnosis Safe, or custom). The signer's address is
                         where shares accumulate and from where the deposit asset
                         is sourced.
        name:            Stable identifier stored in the DB, e.g.
                         ``"morpho-sirloin-usdc-base"``. Should be unique across
                         all providers used by your app.
        asset_address:   ERC-20 address of the underlying asset. Defaults to USDC
                         on Base (``0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913``).
        asset_decimals:  Decimal precision of the underlying asset. Defaults to 6
                         (USDC). Set to 18 for WETH, DAI, etc.
        apy_fn:          Optional zero-argument callable that returns the current
                         APY as a :class:`~decimal.Decimal` percentage
                         (e.g. ``Decimal("5.82")`` for 5.82%). Called on each
                         invocation of :meth:`current_apy`. If ``None``,
                         :meth:`current_apy` returns ``Decimal(0)``.
                         Override :meth:`current_apy` in a subclass for more
                         complex rate-fetching logic.

    Example — any Morpho MetaMorpho vault::

        from defi_savings import Erc4626Provider, GnosisSafeSigner
        from defi_savings.rates import fetch_rates
        from decimal import Decimal

        signer = GnosisSafeSigner(safe_address, key1, key2, rpc_url)

        provider = Erc4626Provider(
            vault_address = "0xCBeeF01994E24a60f7DCB8De98e75AD8BD4Ad60d",
            signer        = signer,
            name          = "morpho-sirloin-usdc-base",
            apy_fn        = lambda: fetch_rates("Base", "SIRLOINUSDC")[0].apy,
        )

    Example — subclass with custom APY logic::

        class MyProtocolProvider(Erc4626Provider):
            def __init__(self, signer):
                super().__init__(
                    vault_address = "0x...",
                    signer        = signer,
                    name          = "my-protocol-base",
                )

            def current_apy(self) -> Decimal:
                # Call the protocol's own API, a subgraph, etc.
                resp = requests.get("https://api.myprotocol.com/apy", timeout=5)
                return Decimal(str(resp.json()["usdc_apy"]))
    """

    def __init__(
        self,
        vault_address: str,
        signer: Signer,
        name: str,
        *,
        asset_address: str = _USDC_ADDRESS,
        asset_decimals: int = _USDC_DECIMALS,
        apy_fn=None,
    ) -> None:
        self._name           = name
        self._signer         = signer
        self._asset_decimals = asset_decimals
        self._apy_fn         = apy_fn

        self.w3          = signer.w3
        self._vault_addr = Web3.to_checksum_address(vault_address)
        self._asset_addr = Web3.to_checksum_address(asset_address)

        self.vault = self.w3.eth.contract(address=self._vault_addr, abi=_VAULT_ABI)
        self.asset = self.w3.eth.contract(address=self._asset_addr, abi=_ERC20_ABI)

    # ── YieldProvider interface ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._name

    def position_balance(self) -> Decimal:
        """Return the signer's asset balance in the vault (shares → assets).

        Uses the vault's ``convertToAssets`` to convert the signer's share
        balance to the underlying asset at the current exchange rate.
        """
        shares = self.vault.functions.balanceOf(self._signer.address).call()
        if shares == 0:
            return Decimal(0)
        assets = self.vault.functions.convertToAssets(shares).call()
        return Decimal(assets) / Decimal(10 ** self._asset_decimals)

    def current_apy(self) -> Decimal:
        """Return the current APY from ``apy_fn``, or ``Decimal(0)`` if not set.

        To supply a live rate, pass ``apy_fn`` to the constructor or override
        this method in a subclass.
        """
        if self._apy_fn is None:
            return Decimal(0)
        return self._apy_fn()

    def _deposit_calls(self, amount_raw: int) -> list[Call]:
        return [
            Call(
                to   = self._asset_addr,
                data = self.asset.encode_abi("approve", [self._vault_addr, amount_raw]),
            ),
            Call(
                to   = self._vault_addr,
                data = self.vault.encode_abi("deposit", [amount_raw, self._signer.address]),
            ),
        ]

    def _withdraw_calls(self, amount_raw: int) -> list[Call]:
        return [
            Call(
                to   = self._vault_addr,
                data = self.vault.encode_abi(
                    "withdraw",
                    [amount_raw, self._signer.address, self._signer.address],
                ),
            ),
        ]

    def _check_deposit_preconditions(self, amount: Decimal) -> int:
        """Balance + maxDeposit checks shared by deposit() and
        estimate_deposit_cost(). Returns amount in raw asset units."""
        amount_raw = int(amount * 10 ** self._asset_decimals)

        bal = self.asset.functions.balanceOf(self._signer.address).call()
        if bal < amount_raw:
            have = Decimal(bal) / Decimal(10 ** self._asset_decimals)
            raise RuntimeError(
                f"Asset balance too low: have {have:.6f}, need {amount:.6f}."
            )

        max_dep_raw = self.vault.functions.maxDeposit(self._signer.address).call()
        if amount_raw > max_dep_raw:
            max_dep = Decimal(max_dep_raw) / Decimal(10 ** self._asset_decimals)
            raise VaultDepositCapExceededError(
                requested=amount, max_deposit=max_dep, vault_address=self._vault_addr,
            )
        return amount_raw

    def deposit(self, amount: Decimal) -> str:
        """Approve the vault then deposit assets — two calls in one Safe tx.

        Checks ``maxDeposit()`` before building any transaction. ERC-4626's
        ``deposit()`` checks this first internally too, before allowance or
        balance — so an undersized cap always reverts regardless of gas, and
        catching it here fails fast instead of burning gas on a guaranteed
        revert. See :class:`~defi_savings.errors.VaultDepositCapExceededError`.

        Args:
            amount: Asset amount in human-readable units (e.g. ``Decimal("1000")``
                    for 1000 USDC).

        Returns:
            Transaction hash of the confirmed deposit.

        Raises:
            RuntimeError: If the signer's asset balance is insufficient, or if
                          the on-chain transaction reverts.
            VaultDepositCapExceededError: If the vault's maxDeposit() for the
                          signer's address is below the requested amount.
        """
        amount_raw = self._check_deposit_preconditions(amount)
        return self._signer.execute(self._deposit_calls(amount_raw))

    def withdraw(self, amount: Decimal) -> str:
        """Withdraw an exact asset amount from the vault to the signer's address.

        The vault burns the corresponding shares and sends ``amount`` of the
        underlying asset to the signer.

        Args:
            amount: Asset amount to withdraw in human-readable units.

        Returns:
            Transaction hash of the confirmed withdrawal.
        """
        amount_raw = int(amount * 10 ** self._asset_decimals)
        return self._signer.execute(self._withdraw_calls(amount_raw))

    def estimate_deposit_cost(self, amount: Decimal) -> GasEstimate:
        """Quote the ETH cost of depositing ``amount``, without depositing.

        Runs the same balance/maxDeposit checks as :meth:`deposit` (so a
        cap that would block the real deposit raises here too, before you
        spend anything finding out) then delegates to the signer's own
        :meth:`~defi_savings.signers.base.Signer.estimate_cost`. Raises
        ``NotImplementedError`` if the signer doesn't support cost
        estimation (see that method's docstring).
        """
        amount_raw = self._check_deposit_preconditions(amount)
        return self._signer.estimate_cost(self._deposit_calls(amount_raw))

    def estimate_withdraw_cost(self, amount: Decimal) -> GasEstimate:
        """Quote the ETH cost of withdrawing ``amount``, without withdrawing.

        Delegates to the signer's own
        :meth:`~defi_savings.signers.base.Signer.estimate_cost`. Raises
        ``NotImplementedError`` if the signer doesn't support cost
        estimation (see that method's docstring).
        """
        amount_raw = int(amount * 10 ** self._asset_decimals)
        return self._signer.estimate_cost(self._withdraw_calls(amount_raw))
