"""
Pure yield distribution math — no I/O, no async, fully unit-testable.

The snapshot invariant
----------------------
Each savings account stores a ``last_protocol_snapshot`` which equals the
account's own balance at the time of the last yield credit. After a full
yield run, ``sum(last_protocol_snapshot for all accounts)`` equals
``sum(balance for all accounts)``.

Growth is therefore:

    total_growth = protocol_balance - sum(last_protocol_snapshot)

This correctly excludes yield already credited in previous runs and avoids
double-counting when users deposit or withdraw between runs.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class AccountSnapshot:
    account_id: str
    balance: Decimal
    last_snapshot: Decimal


def distribute_yield(
    accounts: list[AccountSnapshot],
    protocol_balance: Decimal,
) -> list[tuple[str, Decimal]]:
    """
    Compute proportional yield for each account.

    Returns ``[(account_id, yield_amt), ...]`` for accounts that receive
    positive yield. Accounts receiving dust (≤ 0 after 6 d.p. rounding)
    are excluded from the result.

    Pure — performs no I/O and mutates nothing.
    """
    if not accounts:
        return []

    total_balance = sum(a.balance for a in accounts)
    if total_balance == 0:
        return []

    last_snapshot_sum = sum(a.last_snapshot for a in accounts)
    total_growth = protocol_balance - last_snapshot_sum

    if total_growth <= 0:
        return []

    results: list[tuple[str, Decimal]] = []
    for account in accounts:
        share = account.balance / total_balance
        yield_amt = (total_growth * share).quantize(Decimal("0.000001"))
        if yield_amt > 0:
            results.append((account.account_id, yield_amt))

    return results
