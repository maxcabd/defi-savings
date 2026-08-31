from .base import YieldProvider

__all__ = ["YieldProvider"]

try:
    from .aave_v3 import AaveProvider
    __all__.append("AaveProvider")
except ImportError:
    pass
