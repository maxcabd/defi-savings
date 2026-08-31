from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web3 import Web3


@dataclass
class Call:
    """A single on-chain call to be executed by a Signer."""
    to: str        # target contract address
    data: bytes    # ABI-encoded calldata
    value: int = 0 # ETH value (almost always 0 for ERC-20 operations)


class Signer(ABC):
    """
    Abstract execution layer — who holds the funds and how do they authorize
    moving them on-chain.

    Implement this interface to support any wallet type:

    - ``EOASigner``        — single private key (hot wallet, dev key)
    - ``GnosisSafeSigner`` — 2-of-N multisig (treasury Safe)
    - Your own             — Coinbase API, Fireblocks MPC, hardware wallet, etc.

    The ``YieldProvider`` builds the calldata; the ``Signer`` decides how to
    sign and submit it.
    """

    @property
    @abstractmethod
    def address(self) -> str:
        """
        The on-chain address that holds the treasury funds.
        This is where the USDC lives and where aTokens accumulate.
        """

    @property
    @abstractmethod
    def w3(self) -> Web3:
        """Web3 instance connected to the target chain."""

    @abstractmethod
    def execute(self, calls: list[Call]) -> str:
        """
        Execute one or more on-chain calls.

        Implementations may batch calls into a single transaction (Safe MultiSend,
        ERC-4337 UserOperation) or execute them sequentially (plain EOA).

        Returns the tx hash of the last (or only) confirmed transaction.
        Raises ``RuntimeError`` if any transaction reverts.
        """
