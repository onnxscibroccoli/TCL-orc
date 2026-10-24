"""Small shared helpers for clients building native txs."""
from __future__ import annotations

from ..consensus.encoding import H
from .bn254 import R


def anon_nullifier(public: list) -> int:
    """Spend nullifier bound to a proven anonymous statement (see ChainState.verify_anon)."""
    return int(H(["tcl-anon-nullifier", [str(x) for x in public]]), 16) % R
