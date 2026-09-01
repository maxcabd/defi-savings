"""Unit tests for fetch_stability / fetch_stability_scores -- uses unittest.mock
to avoid hitting DefiLlama."""

from unittest.mock import MagicMock, patch

import pytest

from defi_savings.stability import fetch_stability, fetch_stability_scores


def _chart_response(apys: list[float | None]) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"data": [{"apy": a} for a in apys]}
    return mock


# ── fetch_stability ───────────────────────────────────────────────────────────

def test_computes_mean_stdev_and_cv():
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([4.0, 4.0, 4.0, 4.0])):
        s = fetch_stability("pool-1")

    assert s is not None
    assert s.samples == 4
    assert s.mean_apy == 4.0
    assert s.stdev_apy == 0.0
    assert s.coefficient_of_variation == 0.0


def test_higher_relative_spread_gives_higher_cv():
    """A pool bouncing between 3% and 5% (mean 4%) is more volatile,
    relative to its own rate, than one bouncing between 19% and 21%
    (mean 20%) even though the second has the same absolute spread."""
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([3.0, 5.0])):
        volatile = fetch_stability("pool-volatile")
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([19.0, 21.0])):
        calmer = fetch_stability("pool-calmer")

    assert volatile.coefficient_of_variation > calmer.coefficient_of_variation


def test_only_last_n_days_used():
    apys = [100.0] * 50 + [4.0, 4.0]  # only the last 2 should be picked up with days=2
    with patch("defi_savings.stability.requests.get", return_value=_chart_response(apys)):
        s = fetch_stability("pool-1", days=2)

    assert s.samples == 2
    assert s.mean_apy == 4.0


def test_none_apy_rows_are_skipped():
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([4.0, None, 4.0, None, 4.0])):
        s = fetch_stability("pool-1")

    assert s.samples == 3


def test_negative_or_zero_mean_gives_none_cv():
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([0.0, 0.0])):
        s = fetch_stability("pool-1")

    assert s is not None
    assert s.mean_apy == 0.0
    assert s.coefficient_of_variation is None


def test_min_max_apy_tracked():
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([3.0, 7.0, 5.0])):
        s = fetch_stability("pool-1")

    assert s.min_apy == 3.0
    assert s.max_apy == 7.0


# ── Error handling ──────────────────────────────────────────────────────────

def test_returns_none_on_network_error():
    with patch("defi_savings.stability.requests.get", side_effect=Exception("timeout")):
        assert fetch_stability("pool-1") is None


def test_returns_none_on_http_error():
    mock = MagicMock()
    mock.raise_for_status.side_effect = Exception("404")
    with patch("defi_savings.stability.requests.get", return_value=mock):
        assert fetch_stability("pool-1") is None


def test_returns_none_with_fewer_than_two_samples():
    with patch("defi_savings.stability.requests.get", return_value=_chart_response([4.0])):
        assert fetch_stability("pool-1") is None

    with patch("defi_savings.stability.requests.get", return_value=_chart_response([])):
        assert fetch_stability("pool-1") is None


# ── fetch_stability_scores (batch) ───────────────────────────────────────────

def test_batch_fetches_each_pool_independently():
    def _get(url, timeout):
        if "pool-good" in url:
            return _chart_response([4.0, 4.0])
        raise Exception("network error")

    with patch("defi_savings.stability.requests.get", side_effect=_get):
        results = fetch_stability_scores(["pool-good", "pool-bad"])

    assert "pool-good" in results
    assert "pool-bad" not in results  # skipped, not raised


def test_batch_returns_empty_dict_for_empty_input():
    assert fetch_stability_scores([]) == {}
