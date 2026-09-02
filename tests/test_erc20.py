"""Unit tests for the shared approve_if_needed() ERC-20 helper."""

from unittest.mock import MagicMock

from defi_savings import approve_if_needed

TOKEN_ADDR  = "0x1111111111111111111111111111111111111111"
OWNER_ADDR  = "0x2222222222222222222222222222222222222222"
SPENDER_ADDR = "0x3333333333333333333333333333333333333333"
_MAX_UINT256 = 2 ** 256 - 1


def _make_token(allowance_raw: int) -> MagicMock:
    token = MagicMock()
    token.functions.allowance.return_value.call.return_value = allowance_raw
    token.encode_abi.return_value = b"\xde\xad\xbe\xef"
    return token


def test_returns_empty_list_when_allowance_sufficient():
    token = _make_token(allowance_raw=1_000_000)

    calls = approve_if_needed(token, TOKEN_ADDR, OWNER_ADDR, SPENDER_ADDR, 1_000_000)

    assert calls == []


def test_returns_empty_list_when_allowance_exceeds_amount():
    token = _make_token(allowance_raw=_MAX_UINT256)

    calls = approve_if_needed(token, TOKEN_ADDR, OWNER_ADDR, SPENDER_ADDR, 1_000_000)

    assert calls == []


def test_returns_approve_call_when_allowance_insufficient():
    token = _make_token(allowance_raw=0)

    calls = approve_if_needed(token, TOKEN_ADDR, OWNER_ADDR, SPENDER_ADDR, 1_000_000)

    assert len(calls) == 1
    assert calls[0].to == TOKEN_ADDR
    token.encode_abi.assert_called_once_with("approve", [SPENDER_ADDR, _MAX_UINT256])


def test_approves_max_uint256_not_exact_amount():
    """So the next call with the same owner/spender skips approve entirely."""
    token = _make_token(allowance_raw=500)

    approve_if_needed(token, TOKEN_ADDR, OWNER_ADDR, SPENDER_ADDR, 1_000_000)

    approved_amount = token.encode_abi.call_args[0][1][1]
    assert approved_amount == _MAX_UINT256


def test_checks_allowance_for_correct_owner_and_spender():
    token = _make_token(allowance_raw=0)

    approve_if_needed(token, TOKEN_ADDR, OWNER_ADDR, SPENDER_ADDR, 1_000_000)

    token.functions.allowance.assert_called_once_with(OWNER_ADDR, SPENDER_ADDR)
