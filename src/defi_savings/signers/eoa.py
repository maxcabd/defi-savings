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

import threading

from eth_account import Account
from web3 import Web3

from .base import Call, Signer


class EOASigner(Signer):
    """
    Single-key EOA signer. Signs and submits transactions directly.

    Args:
        private_key: Hex-encoded private key (0x-prefixed).
        rpc_url:     JSON-RPC endpoint, e.g. ``"https://mainnet.base.org"``.
    """

    # Prevents concurrent nonce fetch/submit races from the same key.
    _tx_lock: threading.Lock = threading.Lock()

    def __init__(self, private_key: str, rpc_url: str) -> None:
        self._w3      = Web3(Web3.HTTPProvider(rpc_url))
        self._account = Account.from_key(private_key)

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
                nonce    = self._w3.eth.get_transaction_count(self._account.address, "pending")
                fee_hist = self._w3.eth.fee_history(1, "latest", [50])
                base_fee = fee_hist["baseFeePerGas"][-1]
                max_prio = self._w3.to_wei(1, "gwei")
                max_fee  = base_fee * 2 + max_prio

                raw_tx = {
                    "from":                 self._account.address,
                    "to":                   Web3.to_checksum_address(call.to),
                    "data":                 call.data,
                    "value":                call.value,
                    "nonce":                nonce,
                    "maxFeePerGas":         max_fee,
                    "maxPriorityFeePerGas": max_prio,
                    "chainId":              self._w3.eth.chain_id,
                    "gas":                  300_000,
                }

                signed  = self._account.sign_transaction(raw_tx)
                tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                if receipt["status"] != 1:
                    raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
                last_hash = tx_hash.hex()

        return last_hash
