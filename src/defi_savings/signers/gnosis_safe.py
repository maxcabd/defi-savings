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

import threading

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from .base import Call, Signer

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

    Args:
        safe_address: Checksummed Gnosis Safe address.
        signer1_key:  Private key (0x-prefixed) of the first Safe owner.
        signer2_key:  Private key (0x-prefixed) of the second Safe owner.
        rpc_url:      JSON-RPC endpoint.
    """

    _tx_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        safe_address: str,
        signer1_key: str,
        signer2_key: str,
        rpc_url: str,
    ) -> None:
        self._safe_addr  = Web3.to_checksum_address(safe_address)
        self._signer1_key = signer1_key
        self._signer2_key = signer2_key
        self._w3          = Web3(Web3.HTTPProvider(rpc_url))

    @property
    def address(self) -> str:
        return self._safe_addr

    @property
    def w3(self) -> Web3:
        return self._w3

    def execute(self, calls: list[Call]) -> str:
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

            nonce    = self._w3.eth.get_transaction_count(signer1.address, "pending")
            fee_hist = self._w3.eth.fee_history(1, "latest", [50])
            base_fee = fee_hist["baseFeePerGas"][-1]
            max_prio = self._w3.to_wei(1, "gwei")
            max_fee  = base_fee * 2 + max_prio

            exec_tx = safe.functions.execTransaction(
                exec_to, 0, exec_data, operation,
                0, 0, 0, ZERO_ADDR, ZERO_ADDR, packed_sigs,
            ).build_transaction({
                "from":                 signer1.address,
                "nonce":                nonce,
                "maxFeePerGas":         max_fee,
                "maxPriorityFeePerGas": max_prio,
                "chainId":              self._w3.eth.chain_id,
                "gas":                  500_000,
            })

            signed  = signer1.sign_transaction(exec_tx)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt["status"] != 1:
                raise RuntimeError(f"Safe transaction reverted: {tx_hash.hex()}")
            return tx_hash.hex()
