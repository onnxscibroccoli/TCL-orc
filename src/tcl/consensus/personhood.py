"""Personhood provider interface.

!!! MockPersonhoodProvider is a MOCK. It trusts whatever is enrolled and verifies NO ZK proof of
!!! personhood. Capture-resistance claims do NOT hold until a real provider (ZK membership proof over
!!! the personhood registry root, doc 13 §3) replaces it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PersonhoodProvider(Protocol):
    def tier(self, nullifier: str) -> int:
        """Attestation tier for a personhood nullifier (0 = unknown)."""
        ...

    def is_enrolled(self, nullifier: str) -> bool: ...


@dataclass
class MockPersonhoodProvider:
    """MOCK / DEV ONLY. Not capture-resistant."""
    tiers: dict[str, int] = None  # type: ignore[assignment]
    MOCK: bool = True

    def __post_init__(self) -> None:
        if self.tiers is None:
            self.tiers = {}

    def enroll(self, nullifier: str, tier: int) -> None:
        self.tiers[nullifier] = tier

    def tier(self, nullifier: str) -> int:
        return self.tiers.get(nullifier, 0)

    def is_enrolled(self, nullifier: str) -> bool:
        return nullifier in self.tiers
