# defi-savings

On-chain yield layer for Python apps. Deposit USDC into Aave, Morpho, or any ERC-4626 vault. Wallet-agnostic: bring your own signer.

## Install

```bash
pip install defi-savings
# or
uv add defi-savings
```

Requires Python 3.11+.

---

## Discover rates

Before committing to a protocol, compare current yields across DeFi:

```python
from defi_savings.rates import fetch_rates

pools = fetch_rates(chain="Base", symbol="USDC")
for p in pools:
    print(f"{p.project:25s}  {p.apy:.2f}%  TVL ${p.tvl_usd:>12,.0f}")
```

```
morpho-blue               8.14%  TVL $  357,000,000
compound-v3               5.92%  TVL $   82,000,000
aave-v3                   4.81%  TVL $  610,000,000
moonwell                  4.23%  TVL $   35,000,000
```

`fetch_rates` queries DefiLlama's yields API and returns pools sorted by APY descending. Filter by chain and symbol substring — it works for any asset on any chain:

```python
# Ethereum USDC
pools = fetch_rates("Ethereum", "USDC")

# Only pure supply (no impermanent loss)
pools = fetch_rates("Base", "USDC", include_il_risk=False)

# Minimum TVL — default is $500k
pools = fetch_rates("Base", "USDC", min_tvl_usd=1_000_000)

# Pools above a rate target
high_yield = [p for p in pools if p.apy >= 5]
```

---

## Check rate stability before committing

A pool's spot APY is a single snapshot — it tells you nothing about whether that rate is a boring, reliable baseline or a number that happens to be having a good day. A thin pool can read higher than a deep one while actually swinging 3-8% week to week, which matters a lot for a savings product where users expect a predictable rate, not a lottery.

```python
from defi_savings.rates import fetch_rates
from defi_savings.stability import fetch_stability_scores

pools = fetch_rates("Base", "USDC")
stability = fetch_stability_scores([p.pool_id for p in pools[:5]])

for p in pools[:5]:
    s = stability.get(p.pool_id)
    if s:
        print(f"{p.project:20s} apy={p.apy:5.2f}%  30d mean={s.mean_apy:5.2f}%  "
              f"cv={s.coefficient_of_variation:.3f}  range=[{s.min_apy:.2f}, {s.max_apy:.2f}]")
```

`coefficient_of_variation` (stdev ÷ mean) is what makes pools at different rate levels comparable — a pool averaging 20% with a stdev of 2 is not "more stable" than one averaging 4% with a stdev of 1 just because its raw stdev is bigger; relative to its own rate, the second pool is actually far more volatile (CV 0.25 vs 0.10). **Lower CV = more stable.**

```python
from defi_savings.stability import fetch_stability

s = fetch_stability("e0672197-9f3e-4414-bca5-e6b4c90aa469", days=30)
# StabilityScore(samples=30, mean_apy=4.40, stdev_apy=0.35, coefficient_of_variation=0.08, ...)
```

Feed this straight into `score_pools` (below) to rank pools with stability as a first-class factor, not an afterthought.

---

## Plug into any ERC-4626 vault

Most modern DeFi protocols (Morpho MetaMorpho, Compound v3, Euler, etc.) expose an ERC-4626 interface. Use `Erc4626Provider` to connect to any of them in 5 lines — no protocol-specific boilerplate:

```python
from defi_savings import Erc4626Provider, GnosisSafeSigner
from defi_savings.rates import fetch_rates

signer = GnosisSafeSigner(
    safe_address="0x...",
    signer1_key="0x...",
    signer2_key="0x...",
    rpc_url="https://mainnet.base.org",
)

# Wire in a live APY with apy_fn — called on each current_apy() invocation
provider = Erc4626Provider(
    vault_address = "0xCBeeF01994E24a60f7DCB8De98e75AD8BD4Ad60d",  # sirloinUSDC
    signer        = signer,
    name          = "morpho-sirloin-usdc-base",
    apy_fn        = lambda: fetch_rates("Base", "SIRLOINUSDC")[0].apy,
)

provider.deposit(Decimal("1000"))
balance = provider.position_balance()   # shares → USDC at current price
apy     = provider.current_apy()        # from apy_fn
provider.withdraw(Decimal("500"))
```

### Custom APY logic via subclass

For protocols that need their own API (e.g. GraphQL, proprietary endpoints), subclass and override `current_apy`:

```python
import requests
from defi_savings import Erc4626Provider

class MyProtocolProvider(Erc4626Provider):
    def __init__(self, signer):
        super().__init__(
            vault_address = "0x...",
            signer        = signer,
            name          = "my-protocol-base",
        )

    def current_apy(self) -> Decimal:
        resp = requests.get("https://api.myprotocol.com/vaults/usdc", timeout=5)
        return Decimal(str(resp.json()["netApy"] * 100)).quantize(Decimal("0.01"))
```

### Custom asset (non-USDC vaults)

```python
provider = Erc4626Provider(
    vault_address  = "0x...",
    signer         = my_signer,
    name           = "some-weth-vault",
    asset_address  = "0x4200000000000000000000000000000000000006",  # WETH on Base
    asset_decimals = 18,
)
```

### Vault not accepting deposits

`ERC4626.deposit()` checks `maxDeposit(receiver)` before anything else — before allowance, before balance. A curator can empty a vault's supply queue or zero its cap at any time, and every deposit will revert on-chain regardless of gas until it changes. `Erc4626Provider.deposit()` checks this up front and raises a typed error instead of burning gas on a guaranteed revert:

```python
from defi_savings import VaultDepositCapExceededError

try:
    provider.deposit(Decimal("1000"))
except VaultDepositCapExceededError as exc:
    # exc.requested, exc.max_deposit, exc.vault_address
    print(f"Vault paused: cap is {exc.max_deposit}, wanted {exc.requested}")
    # show a clear "temporarily unavailable" message, or fall back to
    # another provider -- retrying the same deposit won't help until the
    # cap changes.
```

### Estimate cost before you commit

`deposit()`/`withdraw()` tell you what a transfer cost *after* it happens. `estimate_deposit_cost()`/`estimate_withdraw_cost()` quote it beforehand — same balance/cap checks, same gas-estimation logic as the real call, but nothing is signed or broadcast:

```python
estimate = provider.estimate_deposit_cost(Decimal("1000"))
print(f"up to {estimate.max_cost_eth} ETH (gas_limit={estimate.gas_limit})")

if estimate.max_cost_eth > Decimal("0.002"):
    print("skipping -- not worth it right now")
else:
    provider.deposit(Decimal("1000"))
```

`max_cost_eth` is a worst-case figure (`gas_limit × current max fee per gas`) — the real cost of a successful transaction is usually well below it, since `gas_limit` already includes the signer's own safety buffer over the estimated gas. Treat it as "what this could cost", not "what it will cost". Log it alongside the receipt's actual `gasUsed` after the real call to build a picture of how tight your buffer actually is over time.

Works with any provider built on `Erc4626Provider` or `AaveProvider`. Raises `NotImplementedError` if the underlying `Signer` doesn't support `estimate_cost()` — both built-in signers (`GnosisSafeSigner`, `EOASigner`) do.

---

## Aave v3 (built-in)

```python
from defi_savings import AaveProvider, EOASigner, GnosisSafeSigner

# Single key
provider = AaveProvider(EOASigner(private_key="0x...", rpc_url="https://mainnet.base.org"))

# Gnosis Safe 2-of-N
provider = AaveProvider(GnosisSafeSigner(safe_address, key1, key2, rpc_url))
```

---

## Signers

The library separates what to call on the protocol from how to sign the transaction.

### EOA

Single private key. Submits each call as a sequential transaction.

```python
from defi_savings import EOASigner

signer = EOASigner(private_key="0x...", rpc_url="https://mainnet.base.org")
```

Gas is estimated per call via `eth_estimateGas` (with a configurable buffer and floor) rather than using a fixed limit — calls run sequentially and are waited on before the next is built, so each estimate is against real, already-updated state:

```python
signer = EOASigner(
    private_key="0x...",
    rpc_url="https://mainnet.base.org",
    gas_buffer=1.2,      # 20% headroom over the estimate (default)
    gas_floor=100_000,   # minimum gas regardless of the estimate (default)
    fallback_gas=300_000,  # used only if estimation itself fails, e.g. an RPC hiccup
)
```

### Gnosis Safe

2-of-N multisig. Batches multiple calls into one atomic MultiSend transaction. No gnosis-py dependency required.

```python
from defi_savings import GnosisSafeSigner

signer = GnosisSafeSigner(
    safe_address="0x...",
    signer1_key="0x...",
    signer2_key="0x...",
    rpc_url="https://mainnet.base.org",
)
```

Gas is estimated dynamically here too, but batching makes it trickier: each call is simulated individually *from the Safe's own address* (matching the real MultiSend execution context) and summed. A call that depends on an earlier call in the same batch — the common case being `deposit()` needing the `approve()` before it to have landed — can't be estimated standalone, since nothing has actually been approved yet at simulation time. Rather than treat that as a real failure, a simulated allowance revert falls back to `fallback_call_gas`; every other simulated revert (a paused protocol, a deposit cap, a bad amount) is raised immediately, before anything is signed or broadcast:

```python
signer = GnosisSafeSigner(
    safe_address="0x...",
    signer1_key="0x...",
    signer2_key="0x...",
    rpc_url="https://mainnet.base.org",
    gas_buffer=1.4,             # protocols with spiky gas costs need more headroom —
                                #   e.g. MetaMorpho vaults that reallocate across
                                #   multiple underlying markets on deposit
    safe_overhead=150_000,      # fixed cost of execTransaction itself (default)
    gas_floor=300_000,          # minimum gas regardless of the estimate (default)
    fallback_call_gas=500_000,  # gas assumed for a call that can't be estimated
                                #   standalone (raise this for expensive vaults)
)
```

Both signers raise `RuntimeError` on a genuine on-chain revert, with `gas_used`, `gas_limit`, and `possible_oog` (gas used ≥ 95% of the limit) in the message — enough to tell an out-of-gas revert from any other failure without re-fetching the receipt yourself.

### Custom wallet

Implement three methods to support any signing setup: Coinbase MPC, Fireblocks, hardware signers, or anything that can sign an Ethereum transaction.

```python
from defi_savings import Signer, Call

class MyCoinbaseWalletSigner(Signer):
    @property
    def address(self) -> str:
        return "0x..."          # where the USDC lives

    @property
    def w3(self):
        return self._w3         # Web3 instance for read-only calls

    def execute(self, calls: list[Call]) -> str:
        # sign and submit however your wallet works
        # return the tx hash
        ...
```

---

## Usage

```python
from decimal import Decimal

# Deposit $1000 USDC into the protocol
tx = provider.deposit(Decimal("1000"))

# Balance includes principal and all accrued yield
balance = provider.position_balance()   # Decimal("1004.823100")

# Live APY
apy = provider.current_apy()            # Decimal("8.14")

# Withdraw $500 back to the signer address
tx = provider.withdraw(Decimal("500"))
```

Provider methods are synchronous (Web3.py). In async code, wrap with `asyncio.to_thread()`:

```python
balance = await asyncio.to_thread(provider.position_balance)
```

---

## Yield distribution

If multiple users share a single treasury, use `distribute_yield` to split accrued interest proportionally. It is a pure function with no I/O.

```python
from defi_savings import AccountSnapshot, distribute_yield
from decimal import Decimal

snapshots = [
    AccountSnapshot("alice", balance=Decimal("1000"), last_snapshot=Decimal("1000")),
    AccountSnapshot("bob",   balance=Decimal("3000"), last_snapshot=Decimal("3000")),
]

distributions = distribute_yield(snapshots, provider.position_balance())
# [("alice", Decimal("25.000000")), ("bob", Decimal("75.000000"))]
```

After crediting each user, set `last_snapshot = balance + yield_amt` so the next run only measures new growth.

---

## Rank pools by score

`fetch_rates` sorts by spot APY alone. `score_pools` ranks by a weighted composite of APY, 30-day rate stability, deposit gas cost, and TVL — so the pool that wins isn't just whichever one happens to read highest today.

```python
from defi_savings.rates import fetch_rates
from defi_savings.stability import fetch_stability_scores
from defi_savings.scoring import score_pools

pools = fetch_rates("Base", "USDC")
stability = fetch_stability_scores([p.pool_id for p in pools])

ranked = score_pools(
    pools,
    gas_cost_usd={"morpho-blue": 0.12, "aave-v3": 0.05, "compound-v3": 0.03},
    stability=stability,
)
for s in ranked[:5]:
    print(f"{s.pool.project:20s} score={s.score:.3f}  apy={s.pool.apy:.2f}%  "
          f"cv={s.stability_cv}  gas=${s.gas_cost_usd:.4f}")
```

Default weights: **APY 35%, stability 30%, gas 20%, TVL 15%.** Stability is weighted nearly as high as raw APY on purpose — a pool whose rate swings 3-8% is a worse fit for a savings product than one that sits at a boring, predictable 4.3%, even though the volatile one's spot APY often reads higher. Override with `weights={"apy": ..., "stability": ..., "gas": ..., "tvl": ...}` (auto-normalised to sum to 1; a `0` weight fully disables a dimension).

Two dimensions default differently for missing data, on purpose:

- **Gas** — a pool absent from `gas_cost_usd` scores as *free* (best case). You may just not have written that provider's cost estimate yet; don't penalise it for that.
- **Stability** — a pool absent from `stability` (or fetch failed) scores as the *worst* pool in the set (highest CV). The whole point of this dimension is risk awareness — rewarding "we don't know" with a good score defeats it.

---

## Running tests

```bash
uv sync --dev
pytest
```
