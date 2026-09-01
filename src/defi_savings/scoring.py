"""
Provider scoring and ranking.

Ranks yield pools by a weighted combination of APY, rate stability, deposit
gas cost, and TVL. Useful for picking the best protocol when multiple
options are available — and, just as importantly, for not picking the one
that merely reads highest on a given day.

Quick start::

    from defi_savings.rates import fetch_rates
    from defi_savings.stability import fetch_stability_scores
    from defi_savings.scoring import score_pools

    pools = fetch_rates("Base", "USDC")
    stability = fetch_stability_scores([p.pool_id for p in pools])
    ranked = score_pools(
        pools,
        gas_cost_usd={"morpho-blue": 0.12, "aave-v3": 0.05},
        stability=stability,
    )
    for s in ranked:
        print(f"{s.pool.project:25s}  APY {s.pool.apy:.2f}%  "
              f"gas ${s.gas_cost_usd:.4f}  score {s.score:.3f}")

Scoring formula
---------------
Each dimension is min-max normalised to [0, 1] across the pool list:

  - APY:        higher is better        → max APY scores 1.0
  - stability:  lower CV is better      → min coefficient of variation scores 1.0
  - gas cost:   lower is better         → min gas cost scores 1.0
  - TVL:        higher is better        → max TVL scores 1.0

Final score = w_apy*norm_apy + w_stability*norm_stability + w_gas*norm_gas + w_tvl*norm_tvl

Default weights: APY 35%, stability 30%, gas 20%, TVL 15%. Pass ``weights``
to override. Stability is weighted nearly as high as raw APY by default —
a pool whose rate swings 3-8% is a worse fit for a savings product than one
that sits at a boring, predictable 4.3%, even though the first one's spot
APY often reads higher. Chasing the highest number on a given day, on a
thin pool, is how you end up depositing right before the rate craters or
paying 10x the expected gas on a reallocation spike.

Gas cost awareness
------------------
``score_pools`` does not fetch gas prices itself — pass ``gas_cost_usd`` as a
dict mapping DefiLlama project slugs to USD deposit costs.  Pools not in the
dict are assigned 0 (unknown; treated as the cheapest in relative normalisation
so they are not penalised for missing data).

Stability awareness
--------------------
``score_pools`` does not fetch historical rates itself either — pass
``stability`` as a dict mapping ``RatePool.pool_id`` to a
:class:`~defi_savings.stability.StabilityScore` (from
``defi_savings.stability.fetch_stability_scores``). Unlike gas cost, a pool
missing from this dict is treated as the *worst* (highest CV) in relative
normalisation, not the best — the whole point of this dimension is risk
awareness, and rewarding a pool for the absence of data would defeat that.
If you deliberately don't want stability to affect a particular ranking,
set its weight to 0 rather than omitting the dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .rates import RatePool
from .stability import StabilityScore

_DEFAULT_WEIGHTS: dict[str, float] = {"apy": 0.35, "stability": 0.30, "gas": 0.20, "tvl": 0.15}


@dataclass
class ScoredPool:
    """A yield pool annotated with a composite ranking score."""

    pool:         RatePool
    gas_cost_usd: float          # estimated one-time deposit gas cost in USD; 0 = unknown
    stability_cv: float | None   # 30-day coefficient of variation; None = unknown (scored as worst-case)
    score:        float          # composite score in [0, 1]; higher is better


def score_pools(
    pools: list[RatePool],
    *,
    gas_cost_usd: Optional[dict[str, float]] = None,
    stability: Optional[dict[str, StabilityScore]] = None,
    weights: Optional[dict[str, float]] = None,
) -> list[ScoredPool]:
    """
    Rank yield pools by a weighted composite of APY, rate stability, deposit
    gas cost, and TVL.

    Args:
        pools:        Pool list from :func:`~defi_savings.rates.fetch_rates`.
        gas_cost_usd: Mapping from DefiLlama project slug to estimated deposit
                      cost in USD (e.g. ``{"morpho-blue": 0.12, "aave-v3": 0.05}``).
                      Pools not in this dict receive a cost of 0 (unknown,
                      not penalised).
        stability:    Mapping from ``RatePool.pool_id`` to a
                      :class:`~defi_savings.stability.StabilityScore` (from
                      ``defi_savings.stability.fetch_stability_scores``).
                      Pools not in this dict — or with a score whose
                      ``coefficient_of_variation`` is ``None`` — are scored
                      as the *worst* (highest CV) pool in the list, not
                      rewarded for missing data. Omit entirely (or set
                      ``weights={"stability": 0}``) to rank without this
                      dimension.
        weights:      Score weights for each dimension. Keys: ``"apy"``,
                      ``"stability"`` (lower CV → higher score), ``"gas"``
                      (lower cost → higher score), ``"tvl"``. Defaults to
                      ``{"apy": 0.35, "stability": 0.30, "gas": 0.20, "tvl": 0.15}``.
                      Values are automatically normalised to sum to 1.

    Returns:
        :class:`ScoredPool` list sorted by ``score`` descending (best first).
        Returns an empty list when ``pools`` is empty.

    Example — plain APY + TVL ranking (no gas or stability data)::

        ranked = score_pools(pools, weights={"apy": 0.7, "stability": 0, "gas": 0, "tvl": 0.3})

    Example — full gas- and stability-aware ranking::

        from defi_savings.stability import fetch_stability_scores

        stability = fetch_stability_scores([p.pool_id for p in pools])
        ranked = score_pools(
            pools,
            gas_cost_usd={"morpho-blue": 0.12, "aave-v3": 0.05},
            stability=stability,
        )
    """
    if not pools:
        return []

    w = dict(_DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    total_w = sum(w.values()) or 1.0
    w = {k: v / total_w for k, v in w.items()}

    gas_map        = gas_cost_usd or {}
    stability_map   = stability or {}
    apys            = [float(p.apy) for p in pools]
    tvls            = [p.tvl_usd    for p in pools]
    gas_vals        = [gas_map.get(p.project, 0.0) for p in pools]

    cv_raw: list[float | None] = []
    for p in pools:
        s = stability_map.get(p.pool_id)
        cv_raw.append(s.coefficient_of_variation if s is not None else None)
    known_cvs = [c for c in cv_raw if c is not None]
    # Nothing known for anyone -> can't penalise relative to a real worst
    # case, so treat the whole dimension as flat (every pool scores equally
    # on it, contributing nothing to the ranking regardless of its weight).
    worst_cv = max(known_cvs) if known_cvs else 0.0
    cv_vals = [c if c is not None else worst_cv for c in cv_raw]

    def _norm_high(vals: list[float]) -> list[float]:
        """Min-max normalise: highest value → 1.0 (more is better)."""
        lo, hi = min(vals), max(vals)
        if hi == lo:
            return [1.0] * len(vals)
        return [(v - lo) / (hi - lo) for v in vals]

    def _norm_low(vals: list[float]) -> list[float]:
        """Min-max normalise: lowest value → 1.0 (less is better)."""
        lo, hi = min(vals), max(vals)
        if hi == lo:
            return [1.0] * len(vals)
        return [(hi - v) / (hi - lo) for v in vals]

    norm_apy       = _norm_high(apys)
    norm_tvl       = _norm_high(tvls)
    norm_gas       = _norm_low(gas_vals)
    norm_stability = _norm_low(cv_vals)

    scored: list[ScoredPool] = []
    for i, pool in enumerate(pools):
        composite = (
            w["apy"] * norm_apy[i]
            + w.get("stability", 0.0) * norm_stability[i]
            + w["gas"] * norm_gas[i]
            + w["tvl"] * norm_tvl[i]
        )
        scored.append(ScoredPool(
            pool         = pool,
            gas_cost_usd = gas_vals[i],
            stability_cv = cv_raw[i],
            score        = round(composite, 4),
        ))

    return sorted(scored, key=lambda s: s.score, reverse=True)
