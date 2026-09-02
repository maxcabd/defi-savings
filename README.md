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

Sorted by APY descending. Works for any chain/asset:

```python
# Ethereum USDC
pools = fetch_rates("Ethereum", "USDC")

# Only pure supply (no impermanent loss)
pools = fetch_rates("Base", "USDC", include_il_risk=False)

# Minimum TVL (default is $500k)
pools = fetch_rates("Base", "USDC", min_tvl_usd=1_000_000)

# Pools above a rate target
high_yield = [p for p in pools if p.apy >= 5]
```

---

## Check rate stability before committing

Spot APY is a single snapshot. A thin pool can read higher than a deep one while actually swinging 3-8% week to week:

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

`coefficient_of_variation` (stdev ÷ mean) makes pools at different rate levels comparable. 20% APY with stdev 2 (CV 0.10) is more stable than 4% APY with stdev 1 (CV 0.25); raw stdev alone is misleading here. **Lower CV = more stable.**

```python
from defi_savings.stability import fetch_stability

s = fetch_stability("e0672197-9f3e-4414-bca5-e6b4c90aa469", days=30)
# StabilityScore(samples=30, mean_apy=4.40, stdev_apy=0.35, coefficient_of_variation=0.08, ...)
```

Feed this straight into `score_pools` (below) to rank pools with stability as a first-class factor, not an afterthought.

---

## Plug into any ERC-4626 vault

Connects to any ERC-4626 vault (Morpho MetaMorpho, Compound v3, Euler, etc.) in 5 lines, no protocol-specific boilerplate:

```python
from defi_savings import Erc4626Provider, GnosisSafeSigner
from defi_savings.rates import fetch_rates

signer = GnosisSafeSigner(
    safe_address="0x...",
    signer1_key="0x...",
    signer2_key="0x...",
    rpc_url="https://mainnet.base.org",
)

# Wire in a live APY with apy_fn, called on each current_apy() invocation
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

Subclass and override `current_apy` for a protocol-specific API:

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

A curator can zero a vault's deposit cap at any time. `deposit()` checks `maxDeposit()` up front and raises a typed error instead of burning gas on a guaranteed revert:

```python
from defi_savings import VaultDepositCapExceededError

try:
    provider.deposit(Decimal("1000"))
except VaultDepositCapExceededError as exc:
    # exc.requested, exc.max_deposit, exc.vault_address
    print(f"Vault paused: cap is {exc.max_deposit}, wanted {exc.requested}")
    # retrying won't help until the cap changes -- fall back to another provider
```

### Estimate cost before you commit

Quote a transfer's cost before committing to it. Same checks as the real call, nothing signed or broadcast:

```python
estimate = provider.estimate_deposit_cost(Decimal("1000"))
print(f"up to {estimate.max_cost_eth} ETH (gas_limit={estimate.gas_limit})")

if estimate.max_cost_eth > Decimal("0.002"):
    print("skipping -- not worth it right now")
else:
    provider.deposit(Decimal("1000"))
```

`max_cost_eth` is a worst case (`gas_limit × current max fee per gas`); actual cost is usually lower. Works with `Erc4626Provider` and `AaveProvider`, on any signer that implements `estimate_cost()` (`GnosisSafeSigner` and `EOASigner` both do).

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

Gas is estimated per call via `eth_estimateGas`, not a fixed limit. Calls run sequentially, so each estimate sees real, already-updated state:

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

Gas is estimated dynamically here too: each call is simulated individually from the Safe's own address and summed. A call that depends on an earlier one in the same batch (e.g. `deposit()` needing `approve()` to have landed first) can't be estimated standalone, so a simulated allowance revert falls back to `fallback_call_gas` instead of aborting; every other simulated revert raises immediately, before anything is signed or broadcast:

```python
signer = GnosisSafeSigner(
    safe_address="0x...",
    signer1_key="0x...",
    signer2_key="0x...",
    rpc_url="https://mainnet.base.org",
    gas_buffer=1.4,             # protocols with spiky gas costs need more headroom,
                                #   e.g. MetaMorpho vaults that reallocate across
                                #   multiple underlying markets on deposit
    safe_overhead=150_000,      # fixed cost of execTransaction itself (default)
    gas_floor=300_000,          # minimum gas regardless of the estimate (default)
    fallback_call_gas=500_000,  # gas assumed for a call that can't be estimated
                                #   standalone (raise this for expensive vaults)
)
```

Both signers raise `RuntimeError` on a genuine revert, with `gas_used`, `gas_limit`, and `possible_oog` (gas used ≥ 95% of limit) in the message.

### Custom wallet

Three methods, any signing setup: Coinbase MPC, Fireblocks, hardware signers, anything that can sign a transaction.

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

Ranks by a weighted composite of APY, 30-day stability, gas cost, and TVL, not raw APY alone:

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

Default weights: **APY 35%, stability 30%, gas 20%, TVL 15%.** A pool swinging 3-8% loses to one steady at 4.3%, even when its spot APY reads higher. Override with `weights={"apy": ..., "stability": ..., "gas": ..., "tvl": ...}` (normalised to sum to 1; `0` disables a dimension).

Two dimensions default differently for missing data, on purpose:

- **Gas**: missing from `gas_cost_usd` scores as *free* (best case), you may just not have written that estimate yet.
- **Stability**: missing from `stability` scores as *worst* (highest CV), rewarding "we don't know" would defeat the point.

---

## Running tests

```bash
uv sync --dev
pytest
```
