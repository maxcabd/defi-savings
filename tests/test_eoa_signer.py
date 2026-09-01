"""
Unit tests for EOASigner's dynamic gas estimation.

Same technique as test_gnosis_safe_signer.py: signer._w3 is replaced with a
MagicMock after construction (building a real Web3 instance never dials out),
so every RPC call is controlled here. eth_account's signing runs for real --
account.sign_transaction is wrapped (not replaced) so we can inspect the
exact tx dict that was signed while still producing a real signed payload.
"""

from unittest.mock import MagicMock

import pytest
from web3 import Web3

from defi_savings import Call, EOASigner

PRIVATE_KEY = "0x" + "33" * 32
TOKEN_ADDR  = "0x2222222222222222222222222222222222222222"
VAULT_ADDR  = "0x3333333333333333333333333333333333333333"


def _mock_w3(chain_id: int = 8453) -> MagicMock:
    w3 = MagicMock()
    w3.eth.chain_id = chain_id
    w3.eth.get_transaction_count.return_value = 3
    w3.eth.fee_history.return_value = {"baseFeePerGas": [1_000_000_000]}
    w3.to_wei.return_value = 1_000_000_000  # 1 gwei
    w3.eth.send_raw_transaction.return_value = b"\xcd" * 32
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 1, "gasUsed": 21_000}
    return w3


def _make_signer(**kwargs) -> tuple[EOASigner, MagicMock, list]:
    signer = EOASigner(private_key=PRIVATE_KEY, rpc_url="http://localhost:1", **kwargs)
    w3 = _mock_w3()
    signer._w3 = w3

    signed_txs: list[dict] = []
    real_sign = signer._account.sign_transaction

    def _capture_and_sign(tx: dict):
        signed_txs.append(tx)
        return real_sign(tx)

    signer._account.sign_transaction = _capture_and_sign
    return signer, w3, signed_txs


def approve_call() -> Call:
    return Call(to=TOKEN_ADDR, data=b"\xaa\xaa\xaa\xaa")


def deposit_call() -> Call:
    return Call(to=VAULT_ADDR, data=b"\xbb\xbb\xbb\xbb")


# ── Gas estimation math ──────────────────────────────────────────────────────

def test_gas_below_floor_is_clamped_up():
    signer, w3, signed = _make_signer(gas_buffer=1.2, gas_floor=100_000)
    w3.eth.estimate_gas.return_value = 50_000  # * 1.2 = 60_000 -- below the floor

    signer.execute([approve_call()])

    assert signed[0]["gas"] == 100_000


def test_gas_above_floor_applies_buffer_only():
    signer, w3, signed = _make_signer(gas_buffer=1.2, gas_floor=100_000)
    w3.eth.estimate_gas.return_value = 200_000

    signer.execute([approve_call()])

    assert signed[0]["gas"] == 240_000  # 200_000 * 1.2, well above the floor


def test_each_call_estimated_against_updated_state_sequentially():
    """Two calls -> two separate estimate_gas invocations, one per call,
    each waited on and confirmed before the next is even built."""
    signer, w3, signed = _make_signer(gas_buffer=1.0, gas_floor=0)
    w3.eth.estimate_gas.side_effect = [40_000, 60_000]

    signer.execute([approve_call(), deposit_call()])

    assert w3.eth.estimate_gas.call_count == 2
    assert w3.eth.wait_for_transaction_receipt.call_count == 2
    assert w3.eth.get_transaction_count.call_count == 2
    assert [tx["gas"] for tx in signed] == [40_000, 60_000]
    assert signed[0]["to"] == Web3.to_checksum_address(TOKEN_ADDR)
    assert signed[1]["to"] == Web3.to_checksum_address(VAULT_ADDR)


# ── Estimation failure fallback ──────────────────────────────────────────────

def test_estimation_failure_falls_back_instead_of_raising():
    signer, w3, signed = _make_signer(fallback_gas=123_456)
    w3.eth.estimate_gas.side_effect = Exception("rpc timeout")

    tx_hash = signer.execute([approve_call()])

    assert tx_hash == ("cd" * 32)  # succeeded despite the estimator hiccup
    assert signed[0]["gas"] == 123_456
    w3.eth.send_raw_transaction.assert_called_once()


# ── Revert handling ───────────────────────────────────────────────────────────

def test_reverted_receipt_raises_with_gas_details():
    signer, w3, signed = _make_signer(gas_buffer=1.0, gas_floor=0)
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 0, "gasUsed": 98_000}

    with pytest.raises(RuntimeError) as exc_info:
        signer.execute([approve_call()])

    msg = str(exc_info.value)
    assert "reverted" in msg
    assert "gas_used=98000" in msg
    assert "gas_limit=100000" in msg
    assert "possible_oog=True" in msg


def test_reverted_receipt_not_oog_when_gas_used_well_below_limit():
    signer, w3, signed = _make_signer(gas_buffer=1.0, gas_floor=0)
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 0, "gasUsed": 20_000}

    with pytest.raises(RuntimeError, match="possible_oog=False"):
        signer.execute([approve_call()])


def test_reverted_receipt_stops_sequential_execution():
    """If the first call reverts, the second must never be submitted."""
    signer, w3, signed = _make_signer()
    w3.eth.estimate_gas.return_value = 50_000
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 0, "gasUsed": 10_000}

    with pytest.raises(RuntimeError):
        signer.execute([approve_call(), deposit_call()])

    assert w3.eth.send_raw_transaction.call_count == 1


# ── Happy path ────────────────────────────────────────────────────────────────

def test_successful_execute_returns_last_tx_hash():
    signer, w3, signed = _make_signer()
    w3.eth.estimate_gas.return_value = 50_000

    tx_hash = signer.execute([approve_call(), deposit_call()])

    assert tx_hash == ("cd" * 32)
    assert w3.eth.send_raw_transaction.call_count == 2
