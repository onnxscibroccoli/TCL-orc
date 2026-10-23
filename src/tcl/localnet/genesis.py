"""Deterministic genesis for the localnet (same inputs -> same genesis hash)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

DEFAULT_GENESIS_TIME = 1_767_225_600  # 2026-01-01T00:00:00Z, fixed for determinism


@dataclass(frozen=True)
class DevAccount:
    name: str
    balance: int
    personhood_tier: int = 1  # MOCK personhood: tier = number of vouches (no real proof)


@dataclass(frozen=True)
class Genesis:
    chain_id: str = "tcl-localnet-1"
    validators: int = 4
    genesis_time: int = DEFAULT_GENESIS_TIME
    block_time: int = 12
    seed: str = "tcl-dev"
    dev_accounts: int = 10
    dev_balance: int = 1_000_000_000_000  # base units
    notes: tuple[int, ...] = (5, 10, 20, 50, 100, 500)  # mock physical-note denominations
    accounts: tuple[DevAccount, ...] = field(default=())

    def __post_init__(self) -> None:
        if not 1 <= self.validators <= 64:
            raise ValueError("validators must be in [1, 64]")
        if self.block_time <= 0:
            raise ValueError("block_time must be positive")

    def dev_account_list(self) -> list[DevAccount]:
        if self.accounts:
            return list(self.accounts)
        out = []
        for i in range(self.dev_accounts):
            h = hashlib.sha256(f"{self.seed}/dev/{i}".encode()).hexdigest()[:40]
            out.append(DevAccount(name=f"dev{i}:{h}", balance=self.dev_balance,
                                  personhood_tier=1 + i % 3))
        return out

    def validator_ids(self) -> list[str]:
        return [f"val{i}:" + hashlib.sha256(f"{self.seed}/val/{i}".encode()).hexdigest()[:16]
                for i in range(self.validators)]

    def to_json(self) -> dict:
        d = asdict(self)
        d["accounts"] = [asdict(a) for a in self.dev_account_list()]
        d["notes"] = list(self.notes)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Genesis":
        d = dict(d)
        d["accounts"] = tuple(DevAccount(**a) for a in d.get("accounts", []))
        d["notes"] = tuple(d.get("notes", ()))
        return cls(**d)

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
