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

## Running tests

```bash
uv sync --dev
pytest
```
