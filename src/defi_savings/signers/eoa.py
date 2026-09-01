"""
EOA signer — single private key.

Suitable for:
  - Hot wallets controlled by a server
  - Developer testing
  - Personal wallets where you manage the key yourself

Multi-call note
---------------
Multiple calls are submitted as sequential transactions rather than batched.
For Aave deposit (approve → supply), this means two on-chain confirmations
instead of one. Each call is waited on before the next is submitted, so
nonce ordering is correct. If you need single-tx batching, use
``GnosisSafeSigner`` or implement a Multicall3-based signer.
"""

import logging
import threading

from eth_account import Account
from web3 import Web3

from .base import Call, Signer

logger = logging.getLogger(__name__)


class EOASigner(Signer):
    """
    Single-key EOA signer. Signs and submits transactions directly.

    Gas estimation
    --------------
    Because calls are submitted sequentially — each one waited on and
    confirmed before the next is built — every call is estimated against
    real, already-updated on-chain state. Unlike ``GnosisSafeSigner``'s
    batched MultiSend, there's no allowance-not-set-yet ambiguity here: a
    plain ``eth_estimateGas`` per call, with a safety buffer, is accurate.

    Args:
        private_key: Hex-encoded private key (0x-prefixed).
        rpc_url:     JSON-RPC endpoint, e.g. ``"https://mainnet.base.org"``.
        gas_buffer:  Multiplier applied to each call's gas estimate. Default
                     ``1.2`` (20% headroom).
        gas_floor:   Minimum gas limit for any call. Default ``100_000``.
        fallback_gas: Gas used if estimation itself fails (e.g. a transient
                     RPC error) — deliberately conservative so an estimator
                     hiccup doesn't sink an otherwise-valid call. Default
                     ``300_000``.
    """

    # Prevents concurrent nonce fetch/submit races from the same key.
    _tx_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        private_key: str,
        rpc_url: str,
        *,
        gas_buffer: float = 1.2,
        gas_floor: int = 100_000,
        fallback_gas: int = 300_000,
    ) -> None:
        self._w3      = Web3(Web3.HTTPProvider(rpc_url))
        self._account = Account.from_key(private_key)
        self._gas_buffer   = gas_buffer
        self._gas_floor    = gas_floor
        self._fallback_gas = fallback_gas

    @property
    def address(self) -> str:
        return self._account.address

    @property
    def w3(self) -> Web3:
        return self._w3

    def execute(self, calls: list[Call]) -> str:
        last_hash = ""
        with self._tx_lock:
            for call in calls:
                to = Web3.to_checksum_address(call.to)
                try:
                    est = self._w3.eth.estimate_gas({
                        "from":  self._account.address,
                        "to":    to,
                        "data":  call.data,
                        "value": call.value,
                    })
                    gas_limit = max(int(est * self._gas_buffer), self._gas_floor)
                except Exception as exc:
                    logger.warning(
                        "Gas estimation failed for call to %s (%s) — using fallback_gas=%d",
                        to, exc, self._fallback_gas,
                    )
                    gas_limit = self._fallback_gas

                nonce    = self._w3.eth.get_transaction_count(self._account.address, "pending")
                fee_hist = self._w3.eth.fee_history(1, "latest", [50])
                base_fee = fee_hist["baseFeePerGas"][-1]
                max_prio = self._w3.to_wei(1, "gwei")
                max_fee  = base_fee * 2 + max_prio

                raw_tx = {
                    "from":                 self._account.address,
                    "to":                   to,
                    "data":                 call.data,
                    "value":                call.value,
                    "nonce":                nonce,
                    "maxFeePerGas":         max_fee,
                    "maxPriorityFeePerGas": max_prio,
                    "chainId":              self._w3.eth.chain_id,
                    "gas":                  gas_limit,
                }

                signed  = self._account.sign_transaction(raw_tx)
                tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
                logger.info("Tx submitted: %s (gas_limit=%d)", tx_hash.hex(), gas_limit)

                receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                if receipt["status"] != 1:
                    gas_used = receipt["gasUsed"]
                    oog      = gas_used >= int(gas_limit * 0.95)
                    logger.error(
                        "Tx reverted: %s (gas_used=%d gas_limit=%d possible_oog=%s)",
                        tx_hash.hex(), gas_used, gas_limit, oog,
                    )
                    raise RuntimeError(
                        f"Transaction reverted: {tx_hash.hex()} "
                        f"(gas_used={gas_used}, gas_limit={gas_limit}, possible_oog={oog})"
                    )
                last_hash = tx_hash.hex()

        return last_hash
