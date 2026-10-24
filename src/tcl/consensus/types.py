"""Tx / Header / Block / Commit / Vote / Proposal formats per docs/13a (plain dicts, validated here)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import ed25519
from .encoding import H, merkle_root, signed_payload

GENESIS_PREV = "00" * 32
TX_FIELDS = {"chain_id", "module", "method", "sender", "nonce", "args", "proof", "sig", "pow_stamp"}
HEADER_FIELDS = {"chain_id", "height", "time", "prev_hash", "tx_root", "state_root", "aux_roots",
                 "validators_hash", "next_validators_hash", "proposer", "app_version",
                 "module_code_hash"}
AUX_KEYS = {"notes", "nullifiers", "personhood", "governance"}


class Invalid(ValueError):
    pass


def tx_id(tx: dict) -> str:
    return H({k: v for k, v in tx.items() if k != "sig"})


def check_tx_shape(tx: dict) -> None:
    if set(tx) != TX_FIELDS:
        raise Invalid("BAD_ARGS: tx fields")
    if "fee" in tx.get("args", {}):
        raise Invalid("BAD_ARGS: no protocol fees")
    if tx["sender"] is None:
        if tx["sig"] is not None or tx["proof"] is None or "nullifier" not in tx["args"]:
            raise Invalid("BAD_PROOF: anonymous tx needs proof+nullifier and no sig")


def sign_tx(tx: dict, secret: bytes) -> dict:
    body = {k: v for k, v in tx.items() if k != "sig"}
    sig = ed25519.sign(secret, signed_payload(tx["chain_id"], "tx", body)).hex()
    return {**tx, "sig": sig}


def block_hash(header: dict) -> str:
    return H(header)


def validators_hash(pubkeys: list[str]) -> str:
    return H(sorted(pubkeys))


def make_header(**kw: Any) -> dict:
    h = {"aux_roots": {k: "0" for k in sorted(AUX_KEYS)}, "app_version": "0.1.0",
         "module_code_hash": "00" * 32, "state_root": "0"}
    h.update(kw)
    if set(h) != HEADER_FIELDS:
        raise Invalid(f"header fields {set(h) ^ HEADER_FIELDS}")
    return h


def make_block(header: dict, txs: list[dict], last_commit: Optional[dict]) -> dict:
    if header["tx_root"] != merkle_root([tx_id(t) for t in txs]):
        raise Invalid("tx_root mismatch")
    return {"header": header, "txs": txs, "last_commit": last_commit}


@dataclass(frozen=True)
class Vote:
    """Prevote / precommit. block_hash None == nil."""
    kind: str  # "prevote" | "precommit"
    height: int
    round: int
    block_hash: Optional[str]
    validator: str  # hex Ed25519 pubkey
    sig: str

    def body(self) -> dict:
        return {"height": self.height, "round": self.round, "block_hash": self.block_hash,
                "validator": self.validator}

    def verify(self, chain_id: str) -> bool:
        return ed25519.verify(bytes.fromhex(self.validator),
                              signed_payload(chain_id, self.kind, self.body()),
                              bytes.fromhex(self.sig))

    def to_dict(self) -> dict:
        return {**self.body(), "kind": self.kind, "sig": self.sig}


def sign_vote(kind: str, chain_id: str, height: int, rnd: int, bh: Optional[str],
              secret: bytes) -> Vote:
    pub = ed25519.public_key(secret).hex()
    v = Vote(kind, height, rnd, bh, pub, "")
    sig = ed25519.sign(secret, signed_payload(chain_id, kind, v.body())).hex()
    return Vote(kind, height, rnd, bh, pub, sig)


@dataclass(frozen=True)
class Proposal:
    height: int
    round: int
    pol_round: int  # -1 if none
    block: dict = None  # type: ignore[assignment]
    proposer: str = ""
    sig: str = ""

    def body(self) -> dict:
        return {"height": self.height, "round": self.round, "pol_round": self.pol_round,
                "block_hash": block_hash(self.block["header"]), "proposer": self.proposer}

    def verify(self, chain_id: str) -> bool:
        return ed25519.verify(bytes.fromhex(self.proposer),
                              signed_payload(chain_id, "proposal", self.body()),
                              bytes.fromhex(self.sig))

    def __hash__(self) -> int:  # block dict is unhashable
        return hash((self.height, self.round, self.sig))


def sign_proposal(chain_id: str, height: int, rnd: int, pol: int, block: dict,
                  secret: bytes) -> Proposal:
    pub = ed25519.public_key(secret).hex()
    pr = Proposal(height, rnd, pol, block, pub, "")
    sig = ed25519.sign(secret, signed_payload(chain_id, "proposal", pr.body())).hex()
    return Proposal(height, rnd, pol, block, pub, sig)


def quorum(n: int) -> int:
    """Smallest count strictly greater than 2n/3 (equal weight)."""
    return (2 * n) // 3 + 1


def make_commit(height: int, rnd: int, bh: str, precommits: list[Vote]) -> dict:
    sigs = sorted(({"validator": v.validator, "sig": v.sig} for v in precommits),
                  key=lambda s: s["validator"])
    return {"height": height, "round": rnd, "block_hash": bh, "sigs": sigs}


def verify_commit(commit: dict, chain_id: str, validators: list[str]) -> bool:
    """Valid iff > 2/3 of the validator COUNT signed precommit(h, r, block_hash). Equal weight."""
    vs = set(validators)
    seen: set[str] = set()
    for s in commit["sigs"]:
        val = s["validator"]
        if val not in vs or val in seen:
            return False
        v = Vote("precommit", commit["height"], commit["round"], commit["block_hash"], val, s["sig"])
        if not v.verify(chain_id):
            return False
        seen.add(val)
    return len(seen) >= quorum(len(vs))


@dataclass(frozen=True)
class CommitCert:
    """Self-certifying decision (block + >2/3 precommits) used for catch-up / block sync."""
    height: int
    block: dict = None  # type: ignore[assignment]
    commit: dict = None  # type: ignore[assignment]
    round: int = -1

    def __hash__(self) -> int:
        return hash((self.height, self.commit["block_hash"]))
