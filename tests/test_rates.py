"""Unit tests for fetch_rates — uses unittest.mock to avoid hitting DefiLlama."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from defi_savings.rates import RatePool, fetch_rates


# ── Fixtures ────────────────────────────────────────────────────────────────────

FAKE_POOLS = [
    {
        "pool":      "uuid-aave",
        "project":   "aave-v3",
        "symbol":    "USDC",
        "chain":     "Base",
        "apy":       4.81,
        "apyBase":   4.81,
        "apyReward": None,
        "tvlUsd":    610_000_000,
        "ilRisk":    "no",
    },
    {
        "pool":      "uuid-morpho",
        "project":   "morpho-blue",
        "symbol":    "SIRLOINUSDC",
        "chain":     "Base",
        "apy":       8.14,
        "apyBase":   7.50,
        "apyReward": 0.64,
        "tvlUsd":    357_000_000,
        "ilRisk":    "no",
    },
    {
        "pool":      "uuid-compound",
        "project":   "compound-v3",
        "symbol":    "USDC",
        "chain":     "Base",
        "apy":       5.92,
        "apyBase":   5.92,
        "apyReward": None,
        "tvlUsd":    82_000_000,
        "ilRisk":    None,
    },
    {
        "pool":      "uuid-eth-aave",
        "project":   "aave-v3",
        "symbol":    "USDC",
        "chain":     "Ethereum",
        "apy":       3.10,
        "apyBase":   3.10,
        "apyReward": None,
        "tvlUsd":    1_200_000_000,
        "ilRisk":    "no",
    },
    {
        "pool":      "uuid-illiquid",
        "project":   "some-protocol",
        "symbol":    "USDC",
        "chain":     "Base",
        "apy":       99.0,
        "apyBase":   99.0,
        "apyReward": None,
        "tvlUsd":    100_000,        # below default min_tvl_usd — should be filtered
        "ilRisk":    "no",
    },
    {
        "pool":      "uuid-lp",
        "project":   "uniswap-v3",
        "symbol":    "USDC-ETH",
        "chain":     "Base",
        "apy":       50.0,
        "apyBase":   50.0,
        "apyReward": None,
        "tvlUsd":    20_000_000,
        "ilRisk":    "yes",          # IL risk — excluded unless include_il_risk=True
    },
]


def _mock_response(data: list) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"data": data}
    return mock


# ── Basic filtering ─────────────────────────────────────────────────────────────

def test_returns_base_usdc_pools_sorted_by_apy():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC")

    apys = [p.apy for p in pools]
    assert apys == sorted(apys, reverse=True), "Should be sorted by APY descending"


def test_filters_by_chain():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Ethereum", "USDC")

    assert all(p.chain == "Ethereum" for p in pools)
    assert len(pools) == 1
    assert pools[0].project == "aave-v3"


def test_filters_by_symbol_substring():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "SIRLOINUSDC")

    assert len(pools) == 1
    assert pools[0].symbol == "SIRLOINUSDC"
    assert pools[0].project == "morpho-blue"


def test_usdc_symbol_matches_sirloinusdc():
    """USDC substring match should include SIRLOINUSDC pools."""
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC")

    symbols = [p.symbol for p in pools]
    assert "SIRLOINUSDC" in symbols


def test_filters_below_min_tvl():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC")

    pool_ids = [p.pool_id for p in pools]
    assert "uuid-illiquid" not in pool_ids, "Illiquid pool should be filtered out"


def test_excludes_il_risk_by_default():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC")

    pool_ids = [p.pool_id for p in pools]
    assert "uuid-lp" not in pool_ids, "LP pool with IL risk should be excluded"


def test_includes_il_risk_when_requested():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC", include_il_risk=True)

    pool_ids = [p.pool_id for p in pools]
    assert "uuid-lp" in pool_ids


# ── RatePool fields ─────────────────────────────────────────────────────────────

def test_rate_pool_fields_are_correct():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC")

    morpho = next(p for p in pools if p.project == "morpho-blue")
    assert morpho.pool_id    == "uuid-morpho"
    assert morpho.symbol     == "SIRLOINUSDC"
    assert morpho.apy        == Decimal("8.14")
    assert morpho.apy_base   == Decimal("7.5")
    assert morpho.apy_reward == Decimal("0.64")
    assert morpho.tvl_usd    == 357_000_000.0
    assert morpho.chain      == "Base"


def test_none_apy_fields_default_to_zero():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC")

    aave = next(p for p in pools if p.project == "aave-v3")
    assert aave.apy_reward == Decimal("0")


# ── Error handling ──────────────────────────────────────────────────────────────

def test_returns_empty_list_on_network_error():
    with patch("defi_savings.rates.requests.get", side_effect=Exception("timeout")):
        result = fetch_rates("Base", "USDC")
    assert result == []


def test_returns_empty_list_on_http_error():
    mock = MagicMock()
    mock.raise_for_status.side_effect = Exception("404 Not Found")
    with patch("defi_savings.rates.requests.get", return_value=mock):
        result = fetch_rates("Base", "USDC")
    assert result == []


def test_returns_empty_list_when_no_matching_pools():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        result = fetch_rates("Polygon", "USDC")
    assert result == []


# ── Custom min_tvl ──────────────────────────────────────────────────────────────

def test_custom_min_tvl_includes_illiquid_pool():
    with patch("defi_savings.rates.requests.get", return_value=_mock_response(FAKE_POOLS)):
        pools = fetch_rates("Base", "USDC", min_tvl_usd=50_000)

    pool_ids = [p.pool_id for p in pools]
    assert "uuid-illiquid" in pool_ids
