"""
Provider scoring and ranking.

Ranks yield pools by a weighted combination of APY, deposit gas cost, and TVL.
Useful for picking the best protocol when multiple options are available.

Quick start::

    from defi_savings.rates import fetch_rates
    from defi_savings.scoring import score_pools

    pools = fetch_rates("Base", "USDC")
    ranked = score_pools(
        pools,
        gas_cost_usd={"morpho-blue": 0.12, "aave-v3": 0.05},
    )
    for s in ranked:
        print(f"{s.pool.project:25s}  APY {s.pool.apy:.2f}%  "
              f"gas ${s.gas_cost_usd:.4f}  score {s.score:.3f}")

Scoring formula
---------------
Each dimension is min-max normalised to [0, 1] across the pool list:

  - APY:      higher is better  → max APY scores 1.0
  - gas cost: lower is better   → min gas cost scores 1.0
  - TVL:      higher is better  → max TVL scores 1.0

Final score = w_apy * norm_apy + w_gas * norm_gas + w_tvl * norm_tvl

Default weights: APY 60%, gas 25%, TVL 15%.  Pass ``weights`` to override.

Gas cost awareness
------------------
``score_pools`` does not fetch gas prices itself — pass ``gas_cost_usd`` as a
dict mapping DefiLlama project slugs to USD deposit costs.  Pools not in the
dict are assigned 0 (unknown; treated as the cheapest in relative normalisation
so they are not penalised for missing data).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .rates import RatePool

_DEFAULT_WEIGHTS: dict[str, float] = {"apy": 0.60, "gas": 0.25, "tvl": 0.15}


@dataclass
class ScoredPool:
    """A yield pool annotated with a composite ranking score."""

    pool:         RatePool
    gas_cost_usd: float   # estimated one-time deposit gas cost in USD; 0 = unknown
    score:        float   # composite score in [0, 1]; higher is better


def score_pools(
    pools: list[RatePool],
    *,
    gas_cost_usd: Optional[dict[str, float]] = None,
    weights: Optional[dict[str, float]] = None,
) -> list[ScoredPool]:
    """
    Rank yield pools by a weighted composite of APY, deposit gas cost, and TVL.

    Args:
        pools:        Pool list from :func:`~defi_savings.rates.fetch_rates`.
        gas_cost_usd: Mapping from DefiLlama project slug to estimated deposit
                      cost in USD (e.g. ``{"morpho-blue": 0.12, "aave-v3": 0.05}``).
                      Pools not in this dict receive a cost of 0 (unknown).
        weights:      Score weights for each dimension.  Keys: ``"apy"``,
                      ``"gas"`` (lower cost → higher score), ``"tvl"``.
                      Defaults to ``{"apy": 0.60, "gas": 0.25, "tvl": 0.15}``.
                      Values are automatically normalised to sum to 1.

    Returns:
        :class:`ScoredPool` list sorted by ``score`` descending (best first).
        Returns an empty list when ``pools`` is empty.

    Example — plain APY + TVL ranking (no gas data)::

        ranked = score_pools(pools, weights={"apy": 0.7, "gas": 0.0, "tvl": 0.3})

    Example — gas-aware ranking with live costs::

        ranked = score_pools(
            pools,
            gas_cost_usd={"morpho-blue": 0.12, "aave-v3": 0.05},
        )
    """
    if not pools:
        return []

    w = dict(_DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    total_w = sum(w.values()) or 1.0
    w = {k: v / total_w for k, v in w.items()}

    gas_map  = gas_cost_usd or {}
    apys     = [float(p.apy)                for p in pools]
    tvls     = [p.tvl_usd                   for p in pools]
    gas_vals = [gas_map.get(p.project, 0.0) for p in pools]

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

    norm_apy = _norm_high(apys)
    norm_tvl = _norm_high(tvls)
    norm_gas = _norm_low(gas_vals)

    scored: list[ScoredPool] = []
    for i, pool in enumerate(pools):
        composite = (
            w["apy"] * norm_apy[i]
            + w["gas"] * norm_gas[i]
            + w["tvl"] * norm_tvl[i]
        )
        scored.append(ScoredPool(
            pool         = pool,
            gas_cost_usd = gas_vals[i],
            score        = round(composite, 4),
        ))

    return sorted(scored, key=lambda s: s.score, reverse=True)
