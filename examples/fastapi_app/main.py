"""
Minimal FastAPI integration for defi-savings.

Usage:
    uv add defi-savings fastapi uvicorn  # or: pip install ...
    cp .env.example .env && $EDITOR .env
    uvicorn examples.fastapi_app.main:app --reload
"""

import asyncio
import os
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI
from defi_savings import AaveProvider, AccountSnapshot, distribute_yield
from defi_savings import EOASigner, GnosisSafeSigner


def _build_provider() -> AaveProvider:
    wallet = os.environ.get("WALLET_TYPE", "eoa")

    if wallet == "safe":
        signer = GnosisSafeSigner(
            safe_address = os.environ["SAFE_ADDRESS"],
            signer1_key  = os.environ["SAFE_SIGNER1_KEY"],
            signer2_key  = os.environ["SAFE_SIGNER2_KEY"],
            rpc_url      = os.environ["BASE_RPC_URL"],
        )
    else:
        signer = EOASigner(
            private_key = os.environ["EOA_PRIVATE_KEY"],
            rpc_url     = os.environ["BASE_RPC_URL"],
        )

    return AaveProvider(signer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.provider = _build_provider()
    yield


app = FastAPI(title="defi-savings example", lifespan=lifespan)


@app.get("/treasury/balance")
async def balance():
    bal = await asyncio.to_thread(app.state.provider.position_balance)
    apy = await asyncio.to_thread(app.state.provider.current_apy)
    return {"balance": str(bal), "apy": str(apy)}


@app.post("/treasury/deposit")
async def deposit(amount: Decimal):
    tx_hash = await asyncio.to_thread(app.state.provider.deposit, amount)
    return {"tx_hash": tx_hash}


@app.post("/treasury/withdraw")
async def withdraw(amount: Decimal):
    tx_hash = await asyncio.to_thread(app.state.provider.withdraw, amount)
    return {"tx_hash": tx_hash}


@app.post("/yield/distribute")
async def distribute(contributors: list[dict]):
    """
    Calculate proportional yield for each contributor.
    Body: [{"address": "0x...", "balance": "1000", "last_snapshot": "1000"}, ...]
    """
    protocol_balance = await asyncio.to_thread(app.state.provider.position_balance)
    snapshots = [
        AccountSnapshot(
            account_id    = c["address"],
            balance       = Decimal(c["balance"]),
            last_snapshot = Decimal(c["last_snapshot"]),
        )
        for c in contributors
    ]
    distributions = distribute_yield(snapshots, protocol_balance)
    return [{"address": addr, "yield": str(amt)} for addr, amt in distributions]
