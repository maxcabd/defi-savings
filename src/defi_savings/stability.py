"""
Historical APY stability — how much a pool's rate has actually moved, not
just what it reads right now.

A pool can look great on a single snapshot and still be unusable in
practice if its rate swings wildly day to day: thin liquidity where a
single large deposit moves the curve, active reallocation, an incentive
campaign winding down. ``fetch_rates`` only ever gives you today's number.
Use this module alongside it to see past the spot number before
committing capital — and feed the result into ``score_pools`` so ranking
accounts for it automatically.

Quick start::

    from defi_savings.rates import fetch_rates
    from defi_savings.stability import fetch_stability_scores

    pools = fetch_rates("Base", "USDC")
    stability = fetch_stability_scores([p.pool_id for p in pools[:5]])
    for p in pools[:5]:
        s = stability.get(p.pool_id)
        if s:
            print(f"{p.project:20s} apy={p.apy:.2f}%  "
                  f"30d mean={s.mean_apy:.2f}%  cv={s.coefficient_of_variation:.3f}")
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import requests

_CHART_URL = "https://yields.llama.fi/chart/{pool_id}"


@dataclass
class StabilityScore:
    """Summary of a pool's daily APY over a trailing window.

    ``coefficient_of_variation`` (stdev / mean) is what makes pools at
    different rate levels comparable: a pool averaging 20% with a stdev of
    2 is not "more stable" than one averaging 4% with a stdev of 1 just
    because its raw stdev is bigger — relative to its own rate, the second
    pool is actually far more volatile (CV 0.25 vs 0.10). Lower CV = more
    stable. ``None`` when mean_apy <= 0 (CV is undefined / meaningless
    there).
    """
    pool_id:                   str
    samples:                   int      # daily data points actually available (<= days requested)
    mean_apy:                  float
    stdev_apy:                 float
    coefficient_of_variation:  float | None
    min_apy:                   float
    max_apy:                   float


def fetch_stability(pool_id: str, *, days: int = 30, timeout: float = 10.0) -> StabilityScore | None:
    """Return a pool's APY stability over its trailing ``days`` days.

    Queries DefiLlama's per-pool historical chart endpoint. Returns
    ``None`` — never raises — if the request fails or the pool has fewer
    than 2 data points (not enough to compute a spread).

    Args:
        pool_id: DefiLlama pool UUID (``RatePool.pool_id`` / ``YieldPool.pool_id``).
        days:    Trailing window size in days. DefiLlama's chart data is
                 daily, so this is also roughly the number of samples used.
        timeout: HTTP request timeout in seconds.
    """
    try:
        resp = requests.get(_CHART_URL.format(pool_id=pool_id), timeout=timeout)
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except Exception:
        return None

    apys = [float(r["apy"]) for r in rows[-days:] if r.get("apy") is not None]
    if len(apys) < 2:
        return None

    mean  = statistics.mean(apys)
    stdev = statistics.pstdev(apys)
    return StabilityScore(
        pool_id                  = pool_id,
        samples                  = len(apys),
        mean_apy                 = mean,
        stdev_apy                = stdev,
        coefficient_of_variation = (stdev / mean) if mean > 0 else None,
        min_apy                  = min(apys),
        max_apy                  = max(apys),
    )


def fetch_stability_scores(
    pool_ids: list[str],
    *,
    days: int = 30,
    timeout: float = 10.0,
) -> dict[str, StabilityScore]:
    """Fetch stability for multiple pools — one request per pool.

    Skips (omits from the result) any pool whose history couldn't be
    fetched rather than raising, so one bad pool_id doesn't fail the whole
    batch. Callers that need to distinguish "not fetched" from "genuinely
    unstable" should call :func:`fetch_stability` directly per pool.
    """
    results: dict[str, StabilityScore] = {}
    for pid in pool_ids:
        score = fetch_stability(pid, days=days, timeout=timeout)
        if score is not None:
            results[pid] = score
    return results
