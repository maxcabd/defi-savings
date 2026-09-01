from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web3 import Web3


@dataclass
class Call:
    """A single on-chain call to be executed by a Signer."""
    to: str        # target contract address
    data: bytes    # ABI-encoded calldata
    value: int = 0 # ETH value (almost always 0 for ERC-20 operations)


@dataclass
class GasEstimate:
    """A pre-flight cost quote for a batch of calls — computed the same way
    ``execute()`` computes its actual gas limit, but without signing or
    broadcasting anything.

    ``max_cost_wei``/``max_cost_eth`` are worst-case figures: gas_limit ×
    the fee this signer would actually offer if it executed right now
    (``maxFeePerGas`` for EIP-1559 chains). The real cost of a successful
    transaction is usually well below this, since gas_limit already
    includes a safety buffer over the estimated gas — see each Signer's
    own gas-estimation docs for how large that buffer is. Treat this as
    "what could this cost me", not "what will this cost me".
    """
    gas_limit:          int
    max_fee_per_gas_wei: int
    max_cost_wei:        int
    max_cost_eth:         Decimal


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

    def estimate_cost(self, calls: list[Call]) -> GasEstimate:
        """
        Quote the cost of executing these calls, without executing them.

        Read-only — simulates gas the same way ``execute()`` would, and
        reads the current fee market, but never signs or broadcasts
        anything. Use this to check a deposit/withdrawal is worth doing
        before spending the gas to find out, or to log an expected-vs-actual
        cost comparison over time.

        Not abstract, unlike the other three methods: adding this after
        ``EOASigner``/``GnosisSafeSigner`` already shipped would otherwise
        break any custom ``Signer`` implementation written against the
        original three-method interface. The default here raises so a
        custom signer without support fails loudly and specifically at the
        call site, rather than silently returning a nonsense estimate.
        Override it for real support — see ``GnosisSafeSigner`` or
        ``EOASigner`` for a reference implementation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement estimate_cost() — "
            "override it to support pre-flight cost quotes."
        )
