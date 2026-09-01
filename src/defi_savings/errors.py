"""
Typed exceptions for conditions that are not ordinary transaction failures.

Distinguishing these from a generic ``RuntimeError`` lets callers show a
precise message — or skip retrying entirely — instead of treating every
failure as an on-chain revert that might succeed on a second attempt.
"""

from decimal import Decimal


class VaultDepositCapExceededError(RuntimeError):
    """Raised when an ERC-4626 vault's ``maxDeposit()`` is below the requested amount.

    ``ERC4626.deposit()`` checks ``maxDeposit(receiver)`` before anything
    else — before allowance, before balance, before touching any token
    transfer. So this is a protocol- or curator-level condition (an emptied
    supply queue, a reduced or zeroed cap, an access-gated vault) rather than
    a wallet balance, allowance, or gas problem, and every deposit attempt
    will revert on-chain regardless of gas limit until the cap changes.

    :class:`~defi_savings.providers.erc4626.Erc4626Provider` checks this
    before building a transaction, so a paused vault fails fast with this
    error instead of burning gas on a guaranteed revert. Catch it separately
    from other deposit failures when you want to surface a clear
    "temporarily unavailable" message rather than a generic transfer error,
    or when deciding whether to fall back to another provider.

    Attributes:
        requested:     Amount that was requested, in human-readable asset units.
        max_deposit:    The vault's current ``maxDeposit()``, in the same units.
        vault_address:  Checksummed address of the vault.
    """

    def __init__(self, requested: Decimal, max_deposit: Decimal, vault_address: str) -> None:
        self.requested = requested
        self.max_deposit = max_deposit
        self.vault_address = vault_address
        super().__init__(
            f"Vault {vault_address} is not accepting deposits right now "
            f"(maxDeposit={max_deposit}, requested={requested}). "
            "This is a protocol-level condition, not a wallet balance or gas issue."
        )
