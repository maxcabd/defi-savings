from .base import YieldProvider

__all__ = ["YieldProvider"]

try:
    from .aave_v3 import AaveProvider
    from .erc4626 import Erc4626Provider
    __all__ += ["AaveProvider", "Erc4626Provider"]
except ImportError:
    pass
