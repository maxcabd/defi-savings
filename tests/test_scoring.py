"""Unit tests for score_pools() -- pure function, no mocking needed."""

from decimal import Decimal

from defi_savings.rates import RatePool
from defi_savings.scoring import score_pools
from defi_savings.stability import StabilityScore


def pool(project: str, apy: float, tvl: float, pool_id: str | None = None) -> RatePool:
    return RatePool(
        pool_id    = pool_id or f"pool-{project}",
        project    = project,
        symbol     = "USDC",
        apy        = Decimal(str(apy)),
        apy_base   = Decimal(str(apy)),
        apy_reward = Decimal("0"),
        tvl_usd    = tvl,
        chain      = "Base",
    )


def stability(pool_id: str, cv: float | None) -> StabilityScore:
    return StabilityScore(
        pool_id=pool_id, samples=30, mean_apy=4.0, stdev_apy=0.0,
        coefficient_of_variation=cv, min_apy=4.0, max_apy=4.0,
    )


# ── Basic behaviour ──────────────────────────────────────────────────────────

def test_empty_pools_returns_empty():
    assert score_pools([]) == []


def test_sorted_descending_by_score():
    pools = [pool("low", 2.0, 1_000_000), pool("high", 8.0, 1_000_000)]
    ranked = score_pools(pools)
    assert [s.pool.project for s in ranked] == ["high", "low"]


def test_higher_apy_scores_higher_all_else_equal():
    pools = [pool("a", 4.0, 1_000_000), pool("b", 6.0, 1_000_000)]
    ranked = score_pools(pools, weights={"apy": 1, "stability": 0, "gas": 0, "tvl": 0})
    assert ranked[0].pool.project == "b"


def test_higher_tvl_scores_higher_all_else_equal():
    pools = [pool("a", 4.0, 100), pool("b", 4.0, 1_000_000)]
    ranked = score_pools(pools, weights={"apy": 0, "stability": 0, "gas": 0, "tvl": 1})
    assert ranked[0].pool.project == "b"


# ── Gas dimension (pre-existing behaviour, unchanged) ────────────────────────

def test_lower_gas_cost_scores_higher():
    pools = [pool("cheap", 4.0, 1_000_000), pool("pricey", 4.0, 1_000_000)]
    ranked = score_pools(
        pools,
        gas_cost_usd={"cheap": 0.01, "pricey": 5.00},
        weights={"apy": 0, "stability": 0, "gas": 1, "tvl": 0},
    )
    assert ranked[0].pool.project == "cheap"


def test_missing_gas_data_defaults_to_zero_not_penalised():
    """Existing convention: a pool absent from gas_cost_usd is treated as
    free (best case), not worst case -- unlike stability, below."""
    pools = [pool("known", 4.0, 1_000_000), pool("unknown", 4.0, 1_000_000)]
    ranked = score_pools(
        pools,
        gas_cost_usd={"known": 5.00},  # "unknown" absent entirely
        weights={"apy": 0, "stability": 0, "gas": 1, "tvl": 0},
    )
    assert ranked[0].pool.project == "unknown"


# ── Stability dimension ──────────────────────────────────────────────────────

def test_lower_cv_scores_higher():
    pools = [pool("stable", 4.0, 1_000_000, "p-stable"), pool("wild", 4.0, 1_000_000, "p-wild")]
    ranked = score_pools(
        pools,
        stability={"p-stable": stability("p-stable", 0.02), "p-wild": stability("p-wild", 0.40)},
        weights={"apy": 0, "stability": 1, "gas": 0, "tvl": 0},
    )
    assert ranked[0].pool.project == "stable"


def test_missing_stability_data_defaults_to_worst_not_best():
    """Opposite convention from gas: a pool absent from `stability` (or with
    a None CV) must not win by default -- risk-unknown should never
    outscore risk-known-low on this dimension."""
    pools = [pool("known_stable", 4.0, 1_000_000, "p1"), pool("unknown", 4.0, 1_000_000, "p2")]
    ranked = score_pools(
        pools,
        stability={"p1": stability("p1", 0.05)},  # p2 absent entirely
        weights={"apy": 0, "stability": 1, "gas": 0, "tvl": 0},
    )
    assert ranked[0].pool.project == "known_stable"


def test_none_coefficient_of_variation_treated_same_as_missing():
    pools = [pool("known_stable", 4.0, 1_000_000, "p1"), pool("undefined_cv", 4.0, 1_000_000, "p2")]
    ranked = score_pools(
        pools,
        stability={"p1": stability("p1", 0.05), "p2": stability("p2", None)},
        weights={"apy": 0, "stability": 1, "gas": 0, "tvl": 0},
    )
    assert ranked[0].pool.project == "known_stable"


def test_no_stability_data_at_all_is_flat_not_penalising():
    """When nobody has stability data, the dimension can't distinguish
    pools -- it should contribute equally to everyone, not arbitrarily
    favour pool order."""
    pools = [pool("a", 4.0, 1_000_000), pool("b", 6.0, 1_000_000)]
    ranked = score_pools(pools, weights={"apy": 1, "stability": 1, "gas": 0, "tvl": 0})
    # apy alone should decide -- stability contributes the same to both
    assert ranked[0].pool.project == "b"


def test_stability_weight_zero_ignores_dimension_entirely():
    pools = [pool("a", 4.0, 1_000_000, "p1"), pool("b", 4.0, 1_000_000, "p2")]
    ranked = score_pools(
        pools,
        stability={"p1": stability("p1", 0.01), "p2": stability("p2", 0.99)},
        weights={"apy": 0.5, "stability": 0, "gas": 0, "tvl": 0.5},
    )
    # identical apy/tvl, stability weighted out -> tie -> order preserved (stable sort)
    assert ranked[0].score == ranked[1].score


def test_scored_pool_exposes_raw_cv():
    pools = [pool("a", 4.0, 1_000_000, "p1")]
    ranked = score_pools(pools, stability={"p1": stability("p1", 0.15)})
    assert ranked[0].stability_cv == 0.15


def test_scored_pool_cv_none_when_no_data():
    pools = [pool("a", 4.0, 1_000_000, "p1")]
    ranked = score_pools(pools)
    assert ranked[0].stability_cv is None


# ── Weight normalisation ──────────────────────────────────────────────────────

def test_weights_need_not_sum_to_one():
    pools = [pool("a", 4.0, 1_000_000), pool("b", 6.0, 1_000_000)]
    ranked = score_pools(pools, weights={"apy": 60, "stability": 0, "gas": 0, "tvl": 0})
    assert 0.0 <= ranked[0].score <= 1.0


def test_default_weights_sum_to_one():
    from defi_savings.scoring import _DEFAULT_WEIGHTS
    assert abs(sum(_DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
