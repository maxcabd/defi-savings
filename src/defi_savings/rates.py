"""
Protocol rate discovery via DefiLlama.

Fetch current yield rates for any asset across DeFi protocols to compare
opportunities before routing funds.

Quick start::

    from defi_savings.rates import fetch_rates

    pools = fetch_rates(chain="Base", symbol="USDC")
    for pool in pools[:5]:
        print(f"{pool.project:25s}  {pool.apy:.2f}%  TVL ${pool.tvl_usd:>12,.0f}")

    # Pick the best pool, then wire it up:
    best = pools[0]
    print(f"Best: {best.project} — pool_id: {best.pool_id}")

Combining with Erc4626Provider::

    from defi_savings import Erc4626Provider, GnosisSafeSigner
    from defi_savings.rates import fetch_rates
    from decimal import Decimal

    signer = GnosisSafeSigner(safe_address, key1, key2, rpc_url)

    # 1. Discover available rates
    pools = fetch_rates("Base", "USDC")
    for p in pools:
        print(f"{p.project:25s}  {p.apy:.2f}%  ${p.tvl_usd:>12,.0f}")

    # 2. Look up the vault address on DeFiLlama or the protocol's docs,
    #    then plug in with 5 lines:
    provider = Erc4626Provider(
        vault_address = "0xCBeeF01994E24a60f7DCB8De98e75AD8BD4Ad60d",
        signer        = signer,
        name          = "morpho-sirloin-usdc-base",
        apy_fn        = lambda: fetch_rates("Base", "SIRLOINUSDC")[0].apy,
    )
"""

from dataclasses import dataclass
from decimal import Decimal

import requests


@dataclass
class RatePool:
    """A yield pool returned by DefiLlama."""
    pool_id:    str      # DefiLlama pool UUID (not always the vault address)
    project:    str      # DefiLlama project slug, e.g. "morpho-blue"
    symbol:     str      # Pool symbol, e.g. "SIRLOINUSDC"
    apy:        Decimal  # Total APY including any reward tokens
    apy_base:   Decimal  # Supply APY only (no reward tokens)
    apy_reward: Decimal  # Reward-token APY (Decimal("0") if none)
    tvl_usd:    float    # Total value locked in USD
    chain:      str      # Chain name, e.g. "Base"


def fetch_rates(
    chain: str = "Base",
    symbol: str = "USDC",
    *,
    min_tvl_usd: float = 500_000,
    include_il_risk: bool = False,
    timeout: float = 10.0,
) -> list[RatePool]:
    """Return yield pools for an asset on a chain, sorted by total APY descending.

    Queries the DefiLlama yields API (``https://yields.llama.fi/pools``) and
    filters by chain, asset symbol substring, and minimum TVL.

    Args:
        chain:           Chain name as used by DefiLlama. Common values:
                         ``"Base"``, ``"Ethereum"``, ``"Arbitrum"``, ``"Optimism"``.
        symbol:          Asset symbol substring to match (case-insensitive).
                         ``"USDC"`` matches ``"USDC"``, ``"USDC-WETH"``, ``"SIRLOINUSDC"``.
                         Use ``"USDC"`` for supply-only stablecoin strategies, or a
                         more specific string (e.g. ``"SIRLOINUSDC"``) to target
                         a single vault.
        min_tvl_usd:     Ignore pools below this TVL threshold. Filters illiquid
                         edge pools and newly launched vaults. Default: $500k.
        include_il_risk: Include AMM LP positions with impermanent loss risk.
                         Default: ``False`` (supply-only pools only).
        timeout:         HTTP request timeout in seconds.

    Returns:
        List of :class:`RatePool` sorted by ``apy`` descending.
        Returns an empty list (never raises) if DefiLlama is unreachable.

    Example::

        from defi_savings.rates import fetch_rates

        # Compare all USDC supply opportunities on Base
        pools = fetch_rates("Base", "USDC")
        for p in pools:
            print(f"{p.project:25s}  {p.apy:.2f}%  ${p.tvl_usd:>12,.0f}")

        # Separate base APY from reward tokens
        for p in pools:
            print(f"{p.project}: base={p.apy_base:.2f}% + rewards={p.apy_reward:.2f}%")

        # Filter above a target rate
        high_yield = [p for p in pools if p.apy >= 5]

        # Ethereum too
        eth_pools = fetch_rates("Ethereum", "USDC")
    """
    try:
        resp = requests.get("https://yields.llama.fi/pools", timeout=timeout)
        resp.raise_for_status()
        all_pools = resp.json().get("data", [])
    except Exception:
        return []

    results: list[RatePool] = []
    for p in all_pools:
        if p.get("chain") != chain:
            continue
        if symbol.upper() not in (p.get("symbol") or "").upper():
            continue
        if (p.get("tvlUsd") or 0) < min_tvl_usd:
            continue
        if not include_il_risk and p.get("ilRisk") not in (None, "no", ""):
            continue

        results.append(RatePool(
            pool_id    = p.get("pool") or "",
            project    = p.get("project") or "",
            symbol     = p.get("symbol") or "",
            apy        = Decimal(str(p.get("apy")       or 0)),
            apy_base   = Decimal(str(p.get("apyBase")   or 0)),
            apy_reward = Decimal(str(p.get("apyReward") or 0)),
            tvl_usd    = float(p.get("tvlUsd") or 0),
            chain      = p.get("chain") or "",
        ))

    return sorted(results, key=lambda x: x.apy, reverse=True)
