"""
Unit tests for GnosisSafeSigner's dynamic gas estimation.

The signer's ``_w3`` (a real, lazily-constructed Web3 instance — building one
never makes a network call) is swapped for a MagicMock after construction, so
every RPC call it would make is fully controlled here. eth_account's actual
signing code runs for real against dummy deterministic private keys — no
crypto is mocked, only the network layer.
"""

from unittest.mock import MagicMock

import pytest
from web3 import Web3

from defi_savings import Call, GnosisSafeSigner

SAFE_ADDRESS = "0x1111111111111111111111111111111111111111"
SIGNER1_KEY  = "0x" + "11" * 32
SIGNER2_KEY  = "0x" + "22" * 32
TOKEN_ADDR   = "0x2222222222222222222222222222222222222222"
VAULT_ADDR   = "0x3333333333333333333333333333333333333333"


def _mock_w3(chain_id: int = 8453) -> MagicMock:
    """A MagicMock standing in for signer._w3, wired for the happy path.

    Individual tests override specific attributes (estimate_gas, the
    receipt status, etc.) as needed.
    """
    w3 = MagicMock()
    w3.eth.chain_id = chain_id
    w3.eth.get_transaction_count.return_value = 7
    w3.eth.fee_history.return_value = {"baseFeePerGas": [1_000_000_000]}
    w3.to_wei.return_value = 1_000_000_000  # 1 gwei

    safe_contract = MagicMock()
    safe_contract.functions.nonce.return_value.call.return_value = 42
    # getTransactionHash must return real 32 bytes -- it's fed straight into
    # eth_account's encode_defunct/sign_message, which is not mocked.
    safe_contract.functions.getTransactionHash.return_value.call.return_value = b"\xab" * 32

    def _build_transaction(params: dict) -> dict:
        # Mirrors what a real ContractFunction.build_transaction would merge
        # in: the resolved call target/value/data, plus whatever the caller
        # passed (nonce, fees, gas, chainId, from).
        return {
            **params,
            "to":    Web3.to_checksum_address(SAFE_ADDRESS),
            "value": 0,
            "data":  b"\x00\x00\x00\x00",
        }

    safe_contract.functions.execTransaction.return_value.build_transaction.side_effect = _build_transaction
    w3.eth.contract.return_value = safe_contract

    w3.eth.send_raw_transaction.return_value = b"\xcd" * 32
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 1, "gasUsed": 100_000}

    return w3


def _make_signer(**kwargs) -> tuple[GnosisSafeSigner, MagicMock]:
    signer = GnosisSafeSigner(
        safe_address=SAFE_ADDRESS,
        signer1_key=SIGNER1_KEY,
        signer2_key=SIGNER2_KEY,
        rpc_url="http://localhost:1",  # never actually dialed -- w3 is replaced below
        **kwargs,
    )
    w3 = _mock_w3()
    signer._w3 = w3
    return signer, w3


def approve_call() -> Call:
    return Call(to=TOKEN_ADDR, data=b"\xaa\xaa\xaa\xaa")


def deposit_call() -> Call:
    return Call(to=VAULT_ADDR, data=b"\xbb\xbb\xbb\xbb")


# ── Gas estimation math ──────────────────────────────────────────────────────

def test_single_call_gas_uses_buffer_overhead_and_floor():
    signer, w3 = _make_signer(gas_buffer=1.2, safe_overhead=150_000, gas_floor=300_000)
    w3.eth.estimate_gas.return_value = 100_000

    signer.execute([approve_call()])

    sent_tx = w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.call_args[0][0]
    # inner=100_000 * 1.2 + 150_000 = 270_000 -- below the 300_000 floor
    assert sent_tx["gas"] == 300_000


def test_gas_above_floor_is_not_clamped():
    signer, w3 = _make_signer(gas_buffer=1.2, safe_overhead=150_000, gas_floor=300_000)
    w3.eth.estimate_gas.return_value = 500_000

    signer.execute([approve_call()])

    sent_tx = w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.call_args[0][0]
    # inner=500_000 * 1.2 + 150_000 = 750_000
    assert sent_tx["gas"] == 750_000


def test_multi_call_gas_sums_estimates():
    signer, w3 = _make_signer(gas_buffer=1.0, safe_overhead=0, gas_floor=0)
    w3.eth.estimate_gas.side_effect = [40_000, 60_000]

    signer.execute([approve_call(), deposit_call()])

    sent_tx = w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.call_args[0][0]
    assert sent_tx["gas"] == 100_000


def test_custom_gas_params_are_honoured():
    signer, w3 = _make_signer(gas_buffer=2.0, safe_overhead=10_000, gas_floor=0)
    w3.eth.estimate_gas.return_value = 50_000

    signer.execute([approve_call()])

    sent_tx = w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.call_args[0][0]
    # 50_000 * 2.0 + 10_000 = 110_000
    assert sent_tx["gas"] == 110_000


# ── Allowance-fallback vs genuine revert ─────────────────────────────────────

def test_allowance_error_on_second_call_falls_back_instead_of_raising():
    """The deposit call depends on the approve() before it -- simulating it
    standalone against pre-batch state always reports insufficient allowance.
    That must fall back, not abort the whole deposit."""
    signer, w3 = _make_signer(gas_buffer=1.0, safe_overhead=0, gas_floor=0, fallback_call_gas=444_000)
    w3.eth.estimate_gas.side_effect = [40_000, Exception("execution reverted: transfer amount exceeds allowance")]

    tx_hash = signer.execute([approve_call(), deposit_call()])

    assert tx_hash == ("cd" * 32)
    sent_tx = w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.call_args[0][0]
    assert sent_tx["gas"] == 40_000 + 444_000


@pytest.mark.parametrize("message", [
    "execution reverted: insufficient allowance",
    "ERC20: transfer amount exceeds allowance",  # case-insensitive match
])
def test_allowance_error_variants_all_fall_back(message):
    signer, w3 = _make_signer(gas_buffer=1.0, safe_overhead=0, gas_floor=0, fallback_call_gas=1_000)
    w3.eth.estimate_gas.side_effect = [10_000, Exception(message)]

    signer.execute([approve_call(), deposit_call()])

    sent_tx = w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.call_args[0][0]
    assert sent_tx["gas"] == 10_000 + 1_000


def test_genuine_revert_raises_before_any_signing_or_broadcast():
    """A revert unrelated to allowance (paused vault, deposit cap, bad
    amount, ...) is a real failure -- must raise immediately, before the
    Safe transaction is ever built, signed, or sent."""
    signer, w3 = _make_signer()
    w3.eth.estimate_gas.side_effect = [40_000, Exception("execution reverted: Pausable: paused")]

    with pytest.raises(RuntimeError, match="would revert"):
        signer.execute([approve_call(), deposit_call()])

    w3.eth.contract.return_value.functions.execTransaction.return_value.build_transaction.assert_not_called()
    w3.eth.send_raw_transaction.assert_not_called()


def test_first_call_genuine_revert_raises():
    signer, w3 = _make_signer()
    w3.eth.estimate_gas.side_effect = [Exception("execution reverted: bad amount")]

    with pytest.raises(RuntimeError, match="would revert"):
        signer.execute([approve_call()])

    w3.eth.send_raw_transaction.assert_not_called()


# ── Happy path / receipt handling ────────────────────────────────────────────

def test_successful_execute_returns_tx_hash_hex():
    signer, w3 = _make_signer()
    w3.eth.estimate_gas.return_value = 50_000

    tx_hash = signer.execute([approve_call()])

    assert tx_hash == ("cd" * 32)
    w3.eth.send_raw_transaction.assert_called_once()


def test_reverted_receipt_raises_with_gas_details():
    signer, w3 = _make_signer(gas_buffer=1.0, safe_overhead=0, gas_floor=0)
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 0, "gasUsed": 99_000}

    with pytest.raises(RuntimeError) as exc_info:
        signer.execute([approve_call()])

    msg = str(exc_info.value)
    assert "reverted" in msg
    assert "gas_used=99000" in msg
    assert "gas_limit=100000" in msg
    assert "possible_oog=True" in msg  # 99_000 >= 0.95 * 100_000


def test_reverted_receipt_not_oog_when_gas_used_well_below_limit():
    signer, w3 = _make_signer(gas_buffer=1.0, safe_overhead=0, gas_floor=0)
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 0, "gasUsed": 20_000}

    with pytest.raises(RuntimeError, match="possible_oog=False"):
        signer.execute([approve_call()])


# ── Single vs multi call routing ─────────────────────────────────────────────

def test_single_call_bypasses_multisend():
    """A single call executes as a direct CALL, not through MultiSend."""
    signer, w3 = _make_signer()
    w3.eth.estimate_gas.return_value = 50_000

    signer.execute([approve_call()])

    tx_hash_call_args = w3.eth.contract.return_value.functions.getTransactionHash.call_args[0]
    exec_to, value, data, operation = tx_hash_call_args[0], tx_hash_call_args[1], tx_hash_call_args[2], tx_hash_call_args[3]
    assert exec_to == Web3.to_checksum_address(TOKEN_ADDR)
    assert operation == 0  # CALL


def test_multi_call_routes_through_multisend():
    from defi_savings.signers.gnosis_safe import MULTISEND_ADDR

    signer, w3 = _make_signer()
    w3.eth.estimate_gas.return_value = 50_000

    signer.execute([approve_call(), deposit_call()])

    tx_hash_call_args = w3.eth.contract.return_value.functions.getTransactionHash.call_args[0]
    exec_to, operation = tx_hash_call_args[0], tx_hash_call_args[3]
    assert exec_to == Web3.to_checksum_address(MULTISEND_ADDR)
    assert operation == 1  # DELEGATECALL
