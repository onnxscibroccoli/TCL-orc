"""Canonical encoding + hashing per docs/13a (v0: sorted-key compact JSON, SHA-256)."""
import hashlib
import json
from typing import Any


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def H(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def signed_payload(chain_id: str, domain: str, body: Any) -> bytes:
    if domain not in ("tx", "prevote", "precommit", "proposal"):
        raise ValueError(f"bad domain {domain}")
    return canonical({"chain_id": chain_id, "domain": domain, "body": body})


def merkle_root(leaves_hex: list[str]) -> str:
    """Binary SHA-256 Merkle over hex ids in order; odd node is promoted. Empty -> sha256(b'')."""
    if not leaves_hex:
        return hashlib.sha256(b"").hexdigest()
    level = [bytes.fromhex(x) for x in leaves_hex]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(hashlib.sha256(level[i] + level[i + 1]).digest())
            else:
                nxt.append(level[i])
        level = nxt
    return level[0].hex()
