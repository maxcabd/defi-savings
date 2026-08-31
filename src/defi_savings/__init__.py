"""
defi-savings — on-chain yield layer for Python apps.

Deposit USDC into any ERC-4626 vault (Aave, Morpho, Compound, etc.) on Base
and let it accrue yield. The library is wallet-agnostic: bring your own signer.

Quick start — Aave v3::

    from defi_savings import AaveProvider, EOASigner

    provider = AaveProvider(
        EOASigner(private_key="0x...", rpc_url="https://mainnet.base.org")
    )
    provider.deposit(Decimal("1000"))
    balance = provider.position_balance()   # principal + yield
    provider.withdraw(Decimal("500"))

Quick start — any ERC-4626 vault (Morpho, Compound, Euler, ...)::

    from defi_savings import Erc4626Provider, GnosisSafeSigner
    from defi_savings.rates import fetch_rates

    signer = GnosisSafeSigner(safe_address, key1, key2, rpc_url)

    # 1. Find which protocol is paying the most right now
    pools = fetch_rates("Base", "USDC")
    for p in pools[:5]:
        print(f"{p.project:25s}  {p.apy:.2f}%  ${p.tvl_usd:>10,.0f}")

    # 2. Plug in — vault address from DeFiLlama or the protocol's docs
    provider = Erc4626Provider(
        vault_address = "0xCBeeF01994E24a60f7DCB8De98e75AD8BD4Ad60d",
        signer        = signer,
        name          = "morpho-sirloin-usdc-base",
        apy_fn        = lambda: fetch_rates("Base", "SIRLOINUSDC")[0].apy,
    )

Custom wallet — implement one interface::

    from defi_savings import Signer, Call

    class MyCoinbaseWalletSigner(Signer):
        @property
        def address(self) -> str: ...
        @property
        def w3(self): ...
        def execute(self, calls: list[Call]) -> str: ...
"""

from .distribution import AccountSnapshot, distribute_yield
from .providers.base import YieldProvider
from .rates import RatePool, fetch_rates
from .signers.base import Call, Signer

__all__ = [
    "YieldProvider",
    "Signer",
    "Call",
    "AccountSnapshot",
    "distribute_yield",
    "RatePool",
    "fetch_rates",
]

try:
    from .providers.aave_v3 import AaveProvider
    from .providers.erc4626 import Erc4626Provider
    from .signers.eoa import EOASigner
    from .signers.gnosis_safe import GnosisSafeSigner
    __all__ += ["AaveProvider", "Erc4626Provider", "EOASigner", "GnosisSafeSigner"]
except ImportError:
    pass  # web3 not installed
