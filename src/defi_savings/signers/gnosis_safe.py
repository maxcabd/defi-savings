"""
Gnosis Safe signer — 2-of-N multisig.

Suitable for:
  - Shared treasuries requiring multiple approvals
  - DAOs and team wallets
  - Any existing Gnosis Safe setup

Executes calls via Safe's ``execTransaction``, batching multiple calls through
MultiSend (one on-chain transaction, atomic). Does not require the gnosis-py
library — signing is done directly with eth_account and the Safe's own
``getTransactionHash`` view function.
"""

import logging
import threading
from decimal import Decimal

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from .base import Call, GasEstimate, Signer

logger = logging.getLogger(__name__)

MULTISEND_ADDR = "0x38869bf66a61cF6bDB996A6aE40D5853Fd43B526"
ZERO_ADDR      = "0x0000000000000000000000000000000000000000"

SAFE_ABI = [
    {"name": "nonce", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "getTransactionHash", "type": "function", "stateMutability": "view",
     "inputs": [
         {"name": "to",             "type": "address"},
         {"name": "value",          "type": "uint256"},
         {"name": "data",           "type": "bytes"},
         {"name": "operation",      "type": "uint8"},
         {"name": "safeTxGas",      "type": "uint256"},
         {"name": "baseGas",        "type": "uint256"},
         {"name": "gasPrice",       "type": "uint256"},
         {"name": "gasToken",       "type": "address"},
         {"name": "refundReceiver", "type": "address"},
         {"name": "nonce",          "type": "uint256"},
     ],
     "outputs": [{"type": "bytes32"}]},
    {"name": "execTransaction", "type": "function", "stateMutability": "payable",
     "inputs": [
         {"name": "to",             "type": "address"},
         {"name": "value",          "type": "uint256"},
         {"name": "data",           "type": "bytes"},
         {"name": "operation",      "type": "uint8"},
         {"name": "safeTxGas",      "type": "uint256"},
         {"name": "baseGas",        "type": "uint256"},
         {"name": "gasPrice",       "type": "uint256"},
         {"name": "gasToken",       "type": "address"},
         {"name": "refundReceiver", "type": "address"},
         {"name": "signatures",     "type": "bytes"},
     ],
     "outputs": [{"name": "success", "type": "bool"}]},
]


def _to_bytes(val) -> bytes:
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    if isinstance(val, str):
        return bytes.fromhex(val.removeprefix("0x"))
    raise TypeError(f"Cannot convert {type(val)} to bytes")


def _pack_multisend(calls: list[Call]) -> bytes:
    """Encode calls for the Gnosis MultiSend contract."""
    packed = b""
    for call in calls:
        data = _to_bytes(call.data) if isinstance(call.data, (str, bytes, bytearray)) else call.data
        packed += (
            bytes([0])                                              # operation: CALL
            + bytes.fromhex(call.to.removeprefix("0x"))            # to: 20 bytes
            + call.value.to_bytes(32, "big")                       # value
            + len(data).to_bytes(32, "big")                        # dataLength
            + data
        )
    return packed


def _encode_multisend_calldata(packed: bytes) -> bytes:
    selector = Web3.keccak(text="multiSend(bytes)")[:4]
    offset   = (32).to_bytes(32, "big")
    length   = len(packed).to_bytes(32, "big")
    padding  = b"\x00" * ((32 - len(packed) % 32) % 32)
    return selector + offset + length + packed + padding


class GnosisSafeSigner(Signer):
    """
    Gnosis Safe 2-of-N signer.

    Multiple calls are batched into a single MultiSend transaction (atomic).
    A class-level lock serialises concurrent calls so Safe nonce races are
    impossible regardless of how many threads call ``execute`` simultaneously.

    Gas estimation
    --------------
    Gas is estimated dynamically per transaction rather than using a fixed
    limit. A hardcoded limit is either wasteful (too high for a plain ERC-20
    transfer) or dangerous (too low for a protocol whose gas cost is
    variable or unusually high — MetaMorpho's market reallocation during
    deposit, for example, can cost 600k-900k+ gas, far above what a typical
    ERC-4626 vault needs).

    Each call is simulated individually via ``eth_estimateGas``, "from" the
    Safe's own address — this matches the real execution context, since the
    Safe is ``msg.sender`` for every inner call under MultiSend's
    delegatecall. The estimates are summed, a fixed overhead is added for
    the Safe wrapper itself (signature validation, the MultiSend call,
    event emission), and the total is multiplied by a safety buffer.

    A batched call that depends on an earlier call in the *same* batch
    having already landed on-chain — the textbook case being
    ``deposit()`` needing the ``approve()`` before it to have set the
    allowance — can't be estimated in isolation: simulating it against
    pre-batch state reports the same allowance revert every time, since
    nothing has actually been approved yet at simulation time. Rather than
    treat that as a real failure, a call whose simulation fails with what
    looks like an allowance error falls back to ``fallback_call_gas``.
    Every *other* simulated revert (a paused protocol, a deposit cap, a bad
    amount, insufficient balance) is raised immediately, before anything is
    signed or broadcast, so a genuine failure never costs gas.

    Args:
        safe_address:       Checksummed Gnosis Safe address.
        signer1_key:         Private key (0x-prefixed) of the first Safe owner.
        signer2_key:         Private key (0x-prefixed) of the second Safe owner.
        rpc_url:             JSON-RPC endpoint.
        gas_buffer:          Multiplier applied to the summed inner-call gas
                             estimate. Default ``1.2`` (20% headroom). Raise
                             this for protocols with gas costs that spike
                             under certain conditions (e.g. ``1.4`` for
                             MetaMorpho vaults that reallocate across
                             multiple underlying markets on deposit).
        safe_overhead:       Fixed gas added on top of the inner-call estimate
                             for the Safe wrapper itself. Default ``150_000``.
        gas_floor:           Minimum gas limit regardless of the estimate.
                             Default ``300_000``.
        fallback_call_gas:   Gas assumed for a call that can't be estimated
                             standalone because it depends on an earlier call
                             in the same batch (see above). Default
                             ``500_000`` — raise this for vaults with
                             expensive deposit logic.
    """

    _tx_lock: threading.Lock = threading.Lock()

    # Substrings (matched case-insensitively) that identify an ERC-20
    # allowance revert — these are state-dependency artifacts of estimating
    # a batched call in isolation, not real failures.
    _ALLOWANCE_ERRORS = (
        "transfer amount exceeds allowance",
        "insufficient allowance",
    )

    def __init__(
        self,
        safe_address: str,
        signer1_key: str,
        signer2_key: str,
        rpc_url: str,
        *,
        gas_buffer: float = 1.2,
        safe_overhead: int = 150_000,
        gas_floor: int = 300_000,
        fallback_call_gas: int = 500_000,
    ) -> None:
        self._safe_addr   = Web3.to_checksum_address(safe_address)
        self._signer1_key = signer1_key
        self._signer2_key = signer2_key
        self._w3           = Web3(Web3.HTTPProvider(rpc_url))
        self._gas_buffer        = gas_buffer
        self._safe_overhead     = safe_overhead
        self._gas_floor         = gas_floor
        self._fallback_call_gas = fallback_call_gas

    @property
    def address(self) -> str:
        return self._safe_addr

    @property
    def w3(self) -> Web3:
        return self._w3

    def _estimate_inner_gas(self, calls: list[Call]) -> int:
        """Sum gas estimates for each inner call, simulated from the Safe address.

        Simulating from the Safe address matches the real execution context
        (the Safe is msg.sender for every inner call via MultiSend).

        For batch transactions like (approve, deposit), the deposit call
        cannot be estimated in isolation because the allowance it depends on
        hasn't been set on-chain yet. These calls fall back to
        ``fallback_call_gas`` rather than aborting — the receipt check after
        submission catches any actual revert.

        All other reverts (vault paused, bad amount, balance too low, etc.)
        are treated as real failures and raise ``RuntimeError`` immediately,
        before anything is signed or broadcast.
        """
        total = 0
        for call in calls:
            data = _to_bytes(call.data) if isinstance(call.data, (str, bytes, bytearray)) else call.data
            try:
                est = self._w3.eth.estimate_gas({
                    "from": self._safe_addr,
                    "to":   Web3.to_checksum_address(call.to),
                    "data": data,
                })
                total += est
            except Exception as exc:
                err = str(exc).lower()
                if any(msg in err for msg in self._ALLOWANCE_ERRORS):
                    logger.warning(
                        "Inner call to %s could not be estimated standalone "
                        "(allowance not yet set at simulation time) — using "
                        "fallback_call_gas=%d",
                        call.to, self._fallback_call_gas,
                    )
                    total += self._fallback_call_gas
                else:
                    logger.error("Inner call to %s would revert: %s", call.to, exc)
                    raise RuntimeError(f"Inner call to {call.to} would revert: {exc}") from exc
        return total

    def _current_fee(self) -> tuple[int, int, int]:
        """Return (base_fee, max_priority_fee, max_fee_per_gas) at this moment,
        in wei. Shared by execute() (fetched fresh right before signing) and
        estimate_cost() (a point-in-time quote)."""
        fee_hist = self._w3.eth.fee_history(1, "latest", [50])
        base_fee = fee_hist["baseFeePerGas"][-1]
        max_prio = self._w3.to_wei(1, "gwei")
        max_fee  = base_fee * 2 + max_prio
        return base_fee, max_prio, max_fee

    def estimate_cost(self, calls: list[Call]) -> GasEstimate:
        inner_gas = self._estimate_inner_gas(calls)
        gas_limit = max(int(inner_gas * self._gas_buffer) + self._safe_overhead, self._gas_floor)
        _, _, max_fee = self._current_fee()
        max_cost_wei = gas_limit * max_fee
        return GasEstimate(
            gas_limit           = gas_limit,
            max_fee_per_gas_wei = max_fee,
            max_cost_wei        = max_cost_wei,
            max_cost_eth        = Decimal(max_cost_wei) / Decimal(10 ** 18),
        )

    def execute(self, calls: list[Call]) -> str:
        # Estimate gas before acquiring the lock — read-only, no nonce impact.
        inner_gas = self._estimate_inner_gas(calls)
        gas_limit = max(int(inner_gas * self._gas_buffer) + self._safe_overhead, self._gas_floor)
        logger.debug("Estimated gas: inner=%d limit=%d", inner_gas, gas_limit)

        signer1 = Account.from_key(self._signer1_key)
        signer2 = Account.from_key(self._signer2_key)
        safe    = self._w3.eth.contract(address=self._safe_addr, abi=SAFE_ABI)

        if len(calls) == 1:
            exec_to   = Web3.to_checksum_address(calls[0].to)
            exec_data = _to_bytes(calls[0].data)
            operation = 0   # CALL
        else:
            exec_to   = MULTISEND_ADDR
            exec_data = _encode_multisend_calldata(_pack_multisend(calls))
            operation = 1   # DELEGATECALL — Safe is msg.sender for every inner call

        with self._tx_lock:
            safe_nonce = safe.functions.nonce().call()

            tx_hash_bytes: bytes = safe.functions.getTransactionHash(
                exec_to, 0, exec_data, operation,
                0, 0, 0, ZERO_ADDR, ZERO_ADDR, safe_nonce,
            ).call()

            # eth_sign style — Safe recognises v=31/32 and strips the prefix
            msg  = encode_defunct(primitive=tx_hash_bytes)
            sig1 = signer1.sign_message(msg)
            sig2 = signer2.sign_message(msg)

            # Signatures must be ordered by signer address (ascending uint160)
            pairs = sorted(
                [(signer1.address, sig1), (signer2.address, sig2)],
                key=lambda x: int(x[0], 16),
            )
            packed_sigs = b""
            for _, sig in pairs:
                packed_sigs += (
                    sig.r.to_bytes(32, "big")
                    + sig.s.to_bytes(32, "big")
                    + bytes([sig.v + 4])   # +4 → v=31/32 signals eth_sign to the Safe
                )

            nonce = self._w3.eth.get_transaction_count(signer1.address, "pending")
            _, max_prio, max_fee = self._current_fee()

            exec_tx = safe.functions.execTransaction(
                exec_to, 0, exec_data, operation,
                0, 0, 0, ZERO_ADDR, ZERO_ADDR, packed_sigs,
            ).build_transaction({
                "from":                 signer1.address,
                "nonce":                nonce,
                "maxFeePerGas":         max_fee,
                "maxPriorityFeePerGas": max_prio,
                "chainId":              self._w3.eth.chain_id,
                "gas":                  gas_limit,
            })

            signed  = signer1.sign_transaction(exec_tx)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info("Safe tx submitted: %s (gas_limit=%d)", tx_hash.hex(), gas_limit)

            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt["status"] != 1:
                gas_used = receipt["gasUsed"]
                oog      = gas_used >= int(gas_limit * 0.95)
                logger.error(
                    "Safe tx reverted: %s (gas_used=%d gas_limit=%d possible_oog=%s)",
                    tx_hash.hex(), gas_used, gas_limit, oog,
                )
                raise RuntimeError(
                    f"Safe transaction reverted: {tx_hash.hex()} "
                    f"(gas_used={gas_used}, gas_limit={gas_limit}, possible_oog={oog})"
                )
            return tx_hash.hex()
