"""
Unit tests for Erc4626Provider -- primarily the maxDeposit() pre-flight check
and VaultDepositCapExceededError.

The provider is built with a lightweight fake Signer (no real Web3 network
calls anywhere -- fake_w3.eth.contract returns fully-mocked contract objects),
so these tests never touch the network.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from defi_savings import Call, Erc4626Provider, VaultDepositCapExceededError

VAULT_ADDRESS = "0x3333333333333333333333333333333333333333"
SIGNER_ADDR   = "0x4444444444444444444444444444444444444444"
USDC_ADDRESS  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


class FakeSigner:
    """Minimal Signer stand-in -- records the calls it was asked to execute."""

    def __init__(self, w3):
        self._w3 = w3
        self.executed_calls: list[Call] = []
        self.execute_return = "0x" + "cd" * 32

    @property
    def address(self) -> str:
        return SIGNER_ADDR

    @property
    def w3(self):
        return self._w3

    def execute(self, calls: list[Call]) -> str:
        self.executed_calls.append(calls)
        return self.execute_return


def _make_provider(*, balance_raw: int, max_deposit_raw: int) -> tuple[Erc4626Provider, FakeSigner]:
    fake_w3 = MagicMock()

    vault_contract = MagicMock()
    vault_contract.functions.maxDeposit.return_value.call.return_value = max_deposit_raw

    asset_contract = MagicMock()
    asset_contract.functions.balanceOf.return_value.call.return_value = balance_raw

    def _contract(address, abi):
        # Erc4626Provider.__init__ builds the vault contract first (ABI
        # includes "deposit"), then the asset/ERC-20 contract.
        names = [f.get("name") for f in abi]
        return vault_contract if "deposit" in names else asset_contract

    fake_w3.eth.contract.side_effect = _contract

    signer = FakeSigner(fake_w3)
    provider = Erc4626Provider(
        vault_address=VAULT_ADDRESS,
        signer=signer,
        name="test-vault",
    )
    return provider, signer


# ── maxDeposit() cap check ───────────────────────────────────────────────────

def test_deposit_within_cap_succeeds():
    provider, signer = _make_provider(balance_raw=2_000_000_000, max_deposit_raw=5_000_000_000)

    tx_hash = provider.deposit(Decimal("1000"))

    assert tx_hash == signer.execute_return
    assert len(signer.executed_calls) == 1
    assert len(signer.executed_calls[0]) == 2  # approve + deposit


def test_deposit_exceeding_cap_raises_typed_error_without_executing():
    provider, signer = _make_provider(balance_raw=2_000_000_000, max_deposit_raw=0)

    with pytest.raises(VaultDepositCapExceededError) as exc_info:
        provider.deposit(Decimal("1000"))

    assert signer.executed_calls == []  # never built/signed/broadcast a tx
    err = exc_info.value
    assert err.requested == Decimal("1000")
    assert err.max_deposit == Decimal("0")
    assert err.vault_address == VAULT_ADDRESS


def test_deposit_cap_error_message_is_informative():
    provider, _ = _make_provider(balance_raw=2_000_000_000, max_deposit_raw=500_000_000)  # cap = 500 USDC

    with pytest.raises(VaultDepositCapExceededError) as exc_info:
        provider.deposit(Decimal("1000"))

    msg = str(exc_info.value)
    assert "500" in msg
    assert "1000" in msg
    assert VAULT_ADDRESS in msg


def test_deposit_cap_error_is_a_runtime_error():
    """Callers catching the generic RuntimeError contract still catch this."""
    provider, _ = _make_provider(balance_raw=2_000_000_000, max_deposit_raw=0)

    with pytest.raises(RuntimeError):
        provider.deposit(Decimal("1000"))


def test_deposit_exactly_at_cap_succeeds():
    """amount == maxDeposit is allowed -- only amount > maxDeposit is rejected."""
    provider, signer = _make_provider(balance_raw=2_000_000_000, max_deposit_raw=1_000_000_000)

    provider.deposit(Decimal("1000"))

    assert len(signer.executed_calls) == 1


# ── Balance check still takes priority ───────────────────────────────────────

def test_insufficient_balance_raises_before_checking_cap():
    provider, signer = _make_provider(balance_raw=100_000_000, max_deposit_raw=5_000_000_000)

    with pytest.raises(RuntimeError, match="balance too low"):
        provider.deposit(Decimal("1000"))

    assert signer.executed_calls == []
