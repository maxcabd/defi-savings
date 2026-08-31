from abc import ABC, abstractmethod
from decimal import Decimal


class YieldProvider(ABC):
    """
    Abstract yield provider. Swap implementations (Aave, Morpho, etc.)
    without touching SavingsService or the DB schema.

    All methods are synchronous — the service layer calls them via
    asyncio.to_thread() to keep blocking RPC calls off the event loop.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier stored in the DB, e.g. 'aave-v3-base'."""

    @abstractmethod
    def deposit(self, amount_usdc: Decimal) -> str:
        """Deploy USDC from the treasury into the protocol. Returns tx hash."""

    @abstractmethod
    def withdraw(self, amount_usdc: Decimal) -> str:
        """Withdraw USDC from the protocol back to the treasury. Returns tx hash."""

    @abstractmethod
    def position_balance(self) -> Decimal:
        """
        Current total value of the treasury's position in USDC.
        Includes all deposited principal plus any accrued yield.
        """

    @abstractmethod
    def current_apy(self) -> Decimal:
        """
        Current annualised yield rate as a percentage, e.g. Decimal('4.82').
        Read live from the protocol — not cached.
        """
