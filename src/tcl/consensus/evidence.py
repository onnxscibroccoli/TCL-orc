"""Double-sign (equivocation) evidence. Submitted via bounty.report (13a)."""
from __future__ import annotations

from dataclasses import dataclass

from .types import Vote


class BadEvidence(ValueError):
    pass


@dataclass(frozen=True)
class DuplicateVoteEvidence:
    a: Vote
    b: Vote

    def verify(self, chain_id: str) -> str:
        """Return the offending validator pubkey or raise BadEvidence."""
        a, b = self.a, self.b
        if a.validator != b.validator:
            raise BadEvidence("different validators")
        if (a.kind, a.height, a.round) != (b.kind, b.height, b.round):
            raise BadEvidence("votes not for same (kind, height, round)")
        if a.block_hash == b.block_hash:
            raise BadEvidence("votes are not conflicting")
        if not (a.verify(chain_id) and b.verify(chain_id)):
            raise BadEvidence("BAD_SIG")
        return a.validator

    def key(self) -> tuple:
        x, y = sorted([self.a.sig, self.b.sig])
        return (self.a.validator, self.a.kind, self.a.height, self.a.round, x, y)
