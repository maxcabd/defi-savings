"""
Shared ERC-20 allowance helper.

Every provider in this library (and any hand-rolled one, like a non-ERC-4626
market such as Compound Comet) needs the same approve-before-deposit dance.
Centralizing it here means the allowance-skip behavior — and any future
tweak to it — only has to be written once.
"""

from .signers.base import Call

_MAX_UINT256 = 2 ** 256 - 1


def approve_if_needed(
    token_contract, token_address: str, owner: str, spender: str, amount_raw: int,
) -> list[Call]:
    """Return an approve() Call only if ``owner``'s current allowance to
    ``spender`` is below ``amount_raw`` — otherwise an empty list.

    Approves for ``_MAX_UINT256`` (not the exact amount) so the allowance
    stays sufficient going forward: the first deposit into a given
    vault/pool pays for an approve, every later one to the same spender
    doesn't. That matters when the caller is paying Gnosis Safe multisig
    overhead per call batch — skipping a whole call is a real gas saving,
    not just a rounding difference.

    Args:
        token_contract: web3 Contract instance whose ABI includes
                         ``allowance(owner, spender) -> uint256`` and
                         ``approve(spender, amount) -> bool``.
        token_address:   Address of that same token (passed separately
                         rather than read off the contract instance, so
                         callers don't need ``contract.address`` to be set).
        owner:           Address whose allowance is being checked (the
                         signer's address).
        spender:         Address being granted allowance (vault/pool/market).
        amount_raw:      Amount about to be deposited, in the token's raw
                         base units.
    """
    current_allowance = token_contract.functions.allowance(owner, spender).call()
    if current_allowance >= amount_raw:
        return []
    return [Call(
        to   = token_address,
        data = token_contract.encode_abi("approve", [spender, _MAX_UINT256]),
    )]
