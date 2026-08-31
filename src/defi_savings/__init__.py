"""
defi-savings — on-chain yield layer for Python apps.

Deposit USDC into Aave v3 on Base and let it accrue yield. The library is
wallet-agnostic: bring your own signer (EOA, Gnosis Safe, or anything else).

Quick start::

    from defi_savings import AaveProvider, EOASigner

    provider = AaveProvider(
        EOASigner(private_key="0x...", rpc_url="https://mainnet.base.org")
    )
    provider.deposit(Decimal("1000"))
    balance = provider.position_balance()   # principal + yield
    provider.withdraw(Decimal("500"))

    # Gnosis Safe treasury
    from defi_savings import GnosisSafeSigner
    provider = AaveProvider(
        GnosisSafeSigner(safe_address, key1, key2, rpc_url)
    )

    # Custom wallet — implement one interface
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
from .signers.base import Call, Signer

__all__ = [
    "YieldProvider",
    "Signer",
    "Call",
    "AccountSnapshot",
    "distribute_yield",
]

try:
    from .providers.aave_v3 import AaveProvider
    from .signers.eoa import EOASigner
    from .signers.gnosis_safe import GnosisSafeSigner
    __all__ += ["AaveProvider", "EOASigner", "GnosisSafeSigner"]
except ImportError:
    pass  # web3 not installed
