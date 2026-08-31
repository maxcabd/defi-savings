"""Unit tests for distribute_yield — no I/O, no mocks needed."""

from decimal import Decimal

import pytest

from defi_savings.distribution import AccountSnapshot, distribute_yield


def snap(account_id: str, balance: str, last_snapshot: str) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=account_id,
        balance=Decimal(balance),
        last_snapshot=Decimal(last_snapshot),
    )


# ── Basic cases ────────────────────────────────────────────────────────────────

def test_proportional_split():
    """Two accounts split 100 USDC growth 25/75 by balance."""
    accounts = [snap("a1", "1000", "1000"), snap("a2", "3000", "3000")]
    results  = dict(distribute_yield(accounts, Decimal("4100")))

    assert results["a1"] == Decimal("25.000000")
    assert results["a2"] == Decimal("75.000000")


def test_single_account_gets_all_yield():
    accounts = [snap("a1", "500", "500")]
    results  = dict(distribute_yield(accounts, Decimal("510")))
    assert results["a1"] == Decimal("10.000000")


def test_equal_balances_split_equally():
    accounts = [snap("a1", "1000", "1000"), snap("a2", "1000", "1000")]
    results  = dict(distribute_yield(accounts, Decimal("2020")))
    assert results["a1"] == Decimal("10.000000")
    assert results["a2"] == Decimal("10.000000")


# ── No-yield cases ─────────────────────────────────────────────────────────────

def test_no_growth_returns_empty():
    accounts = [snap("a1", "1000", "1000")]
    assert distribute_yield(accounts, Decimal("1000")) == []


def test_negative_growth_returns_empty():
    """Should not distribute negative amounts (e.g. rounding or slashing)."""
    accounts = [snap("a1", "1000", "1000")]
    assert distribute_yield(accounts, Decimal("999")) == []


def test_empty_accounts_returns_empty():
    assert distribute_yield([], Decimal("1000")) == []


def test_zero_balance_accounts_excluded():
    accounts = [snap("a1", "0", "0")]
    assert distribute_yield(accounts, Decimal("100")) == []


# ── Snapshot invariant ─────────────────────────────────────────────────────────

def test_snapshot_invariant_prevents_double_counting():
    """
    After one yield run, snapshots are advanced. A second run with the same
    protocol_balance should produce no further yield.
    """
    accounts = [snap("a1", "1000", "1000")]
    # First run: 10 USDC growth
    results = distribute_yield(accounts, Decimal("1010"))
    assert len(results) == 1
    _, yield_amt = results[0]
    assert yield_amt == Decimal("10.000000")

    # Simulate the DB update: new balance = 1010, new snapshot = 1010
    accounts_updated = [snap("a1", "1010", "1010")]

    # Second run: protocol hasn't grown further → no yield
    assert distribute_yield(accounts_updated, Decimal("1010")) == []


def test_multi_user_snapshot_invariant():
    """
    Growth is correctly isolated to new protocol gains, not deposits.
    If user B deposits and snapshot is set to their new balance,
    B's deposit does not inflate A's yield.
    """
    # After first run both users have balance == snapshot
    accounts = [
        snap("a1", "1000", "1000"),  # A: $1000
        snap("a2", "2000", "2000"),  # B: $2000
    ]
    # Protocol grew by $30
    results = dict(distribute_yield(accounts, Decimal("3030")))
    assert results["a1"] == Decimal("10.000000")  # 1/3 of 30
    assert results["a2"] == Decimal("20.000000")  # 2/3 of 30


# ── Precision ──────────────────────────────────────────────────────────────────

def test_dust_amounts_are_excluded():
    """Yield below 0.0000005 rounds to zero and is excluded."""
    # 1e-7 USDC growth, single account — rounds to 0.000000
    accounts = [snap("a1", "1000", "1000")]
    results  = distribute_yield(accounts, Decimal("1000.0000001"))
    assert results == []


def test_six_decimal_precision():
    accounts = [snap("a1", "1", "1")]
    # 0.0000006 > 0.0000005 → rounds up to 0.000001 (ROUND_HALF_EVEN safe)
    results  = distribute_yield(accounts, Decimal("1.0000006"))
    assert len(results) == 1
    _, amt = results[0]
    assert amt == Decimal("0.000001")
