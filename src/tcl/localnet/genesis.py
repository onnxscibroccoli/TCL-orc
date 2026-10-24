"""Deterministic genesis for the localnet (same inputs -> same genesis hash)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from ..consensus import ed25519

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
    # --- native modules (docs/15) ---
    seats: int = 0              # active validator seats; 0 = all `validators` nodes are seated
    epoch_blocks: int = 100     # validator rotation every N blocks (0 disables)
    term_epochs: int = 4
    cooldown_epochs: int = 4
    validator_bond: int = 1_000_000
    note_unit: int = 1_000_000  # base units per face-value 1
    dev_proofs: bool = False    # DEV ONLY: accept {"dev": true} instead of Groth16 proofs

    def __post_init__(self) -> None:
        if not 1 <= self.validators <= 64:
            raise ValueError("validators must be in [1, 64]")
        if not 0 <= self.seats <= self.validators:
            raise ValueError("seats must be in [0, validators]")
        if self.epoch_blocks < 0 or self.term_epochs < 1 or self.cooldown_epochs < 0:
            raise ValueError("bad epoch parameters")
        if self.block_time <= 0:
            raise ValueError("block_time must be positive")

    def dev_secret(self, i: int) -> bytes:
        """DEV ONLY: deterministic Ed25519 seed for dev account i (public, never use for value)."""
        return hashlib.sha256(f"{self.seed}/dev/{i}".encode()).digest()

    def validator_secret(self, i: int) -> bytes:
        """DEV ONLY: deterministic Ed25519 seed for validator i."""
        return hashlib.sha256(f"{self.seed}/val/{i}".encode()).digest()

    def dev_keys(self) -> dict[str, bytes]:
        """address (Ed25519 pubkey hex) -> secret, for the generated dev accounts."""
        return {ed25519.public_key(self.dev_secret(i)).hex(): self.dev_secret(i)
                for i in range(self.dev_accounts)}

    def dev_account_list(self) -> list[DevAccount]:
        out = list(self.accounts)
        for i, addr in enumerate(self.dev_keys()):
            out.append(DevAccount(name=addr, balance=self.dev_balance, personhood_tier=1 + i % 3))
        return out

    def validator_ids(self) -> list[str]:
        """Validator Ed25519 pubkeys (hex), in index order."""
        return [ed25519.public_key(self.validator_secret(i)).hex()
                for i in range(self.validators)]

    def to_json(self) -> dict:
        d = asdict(self)
        d["accounts"] = [asdict(a) for a in self.accounts]
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
