# defi-savings

Deposit USDC into Aave v3 on Base and let it accrue yield. The library is wallet-agnostic: bring your own signer.

## Install

```bash
pip install defi-savings
# or
uv add defi-savings
```

Requires Python 3.11+.

## Signers

The library separates what to call on Aave from how to sign the transaction. Pick the signer that matches your setup.

### EOA

Single private key. Submits approve and supply as two sequential transactions.

```python
from defi_savings import AaveProvider, EOASigner

provider = AaveProvider(
    EOASigner(
        private_key="0x...",
        rpc_url="https://mainnet.base.org",
    )
)
```

### Gnosis Safe

2-of-N multisig. Batches approve and supply into one atomic MultiSend transaction. No gnosis-py dependency required.

```python
from defi_savings import AaveProvider, GnosisSafeSigner

provider = AaveProvider(
    GnosisSafeSigner(
        safe_address="0x...",
        signer1_key="0x...",
        signer2_key="0x...",
        rpc_url="https://mainnet.base.org",
    )
)
```

### Custom wallet

Implement three methods to support any signing setup: Coinbase MPC, Fireblocks, hardware signers, or anything else that can sign an Ethereum transaction.

```python
from defi_savings import Signer, Call

class MyCoinbaseWalletSigner(Signer):
    @property
    def address(self) -> str:
        return "0x..."          # where the USDC lives

    @property
    def w3(self):
        return self._w3         # Web3 instance used for read-only calls

    def execute(self, calls: list[Call]) -> str:
        # sign and submit however your wallet works
        # return the tx hash
        ...
```

## Usage

```python
from decimal import Decimal

# Deposit $1000 USDC from the signer address into Aave
tx = provider.deposit(Decimal("1000"))

# Balance includes principal and all accrued yield
balance = provider.position_balance()   # Decimal("1004.823100")

# Live APY
apy = provider.current_apy()            # Decimal("4.82")

# Withdraw $500 back to the signer address
tx = provider.withdraw(Decimal("500"))
```

Provider methods are synchronous (Web3.py). In async code, wrap with `asyncio.to_thread()`.

```python
balance = await asyncio.to_thread(provider.position_balance)
```

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

## Custom protocols

Implement `YieldProvider` to use Morpho, Compound, or any other protocol. The signer stays the same.

```python
from defi_savings import YieldProvider, Signer
from decimal import Decimal

class MorphoProvider(YieldProvider):
    name = "morpho-base"

    def __init__(self, signer: Signer): ...
    def deposit(self, amount_usdc: Decimal) -> str: ...
    def withdraw(self, amount_usdc: Decimal) -> str: ...
    def position_balance(self) -> Decimal: ...
    def current_apy(self) -> Decimal: ...
```

## Running tests

```bash
uv sync --dev
pytest
```
