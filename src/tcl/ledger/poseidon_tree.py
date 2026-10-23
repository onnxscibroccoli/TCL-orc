"""Persistent binary Merkle tree with circomlib-compatible Poseidon(2) nodes.

* Fixed, parameterized depth (1..64). Leaf ``i`` lives at (level 0, index i);
  the root is (level depth, index 0).
* Only non-empty nodes are persisted; absent nodes equal the precomputed zero
  hash for their level (Z0 = 0, Z[k+1] = Poseidon(Z[k], Z[k])).
* All writes go through the storage transaction, so a crash can never leave a
  half-updated path: either the whole leaf->root update commits or none of it.
* Account leaves: Poseidon(account_id, balance, last_update) where
  account_id = sha256(label) mod p (see :func:`account_leaf`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..crypto.poseidon import FIELD_P, hash_account_id, poseidon
from ..storage.db import LedgerStorage, StorageError

MAX_DEPTH = 64


def hash2(a: int, b: int) -> int:
    return poseidon([a, b])


def zero_hashes(depth: int) -> list[int]:
    z = [0]
    for _ in range(depth):
        z.append(hash2(z[-1], z[-1]))
    return z


def account_leaf(account: str, balance: int, last_update: int) -> int:
    if not 0 <= balance < FIELD_P or not 0 <= last_update < FIELD_P:
        raise ValueError("leaf values must be in the BN254 field")
    return poseidon([hash_account_id(account), balance, last_update])


@dataclass(frozen=True)
class MerkleProof:
    index: int
    leaf: int
    siblings: list[int] = field(default_factory=list)  # bottom-up
    root: int = 0

    def path_bits(self) -> list[int]:
        """0 = current node is left child, 1 = right (circom pathIndices)."""
        return [(self.index >> i) & 1 for i in range(len(self.siblings))]

    def to_json(self) -> dict:
        return {"index": self.index, "leaf": str(self.leaf), "root": str(self.root),
                "siblings": [str(s) for s in self.siblings], "path_bits": self.path_bits()}


def compute_root(leaf: int, index: int, siblings: list[int]) -> int:
    h = leaf
    for lvl, sib in enumerate(siblings):
        h = hash2(sib, h) if (index >> lvl) & 1 else hash2(h, sib)
    return h


def verify_proof(root: int, proof: MerkleProof) -> bool:
    if proof.index < 0 or proof.index >= (1 << len(proof.siblings)):
        return False
    try:
        return compute_root(proof.leaf, proof.index, proof.siblings) == root
    except (ValueError, TypeError):
        return False


class PoseidonMerkleTree:
    def __init__(self, storage: LedgerStorage, depth: int = 20) -> None:
        if not 1 <= depth <= MAX_DEPTH:
            raise ValueError(f"depth must be in 1..{MAX_DEPTH}")
        self.s = storage
        stored = storage.get_meta("tree_depth")
        if stored is None:
            with storage.transaction():
                storage.put_meta("tree_depth", str(depth))
        elif int(stored) != depth:
            raise StorageError(f"tree depth mismatch: db={stored} requested={depth}")
        self.depth = depth
        self.zeros = zero_hashes(depth)

    @property
    def capacity(self) -> int:
        return 1 << self.depth

    def node(self, level: int, index: int) -> int:
        v = self.s.get_node(level, index)
        return self.zeros[level] if v is None else v

    def root(self) -> int:
        return self.node(self.depth, 0)

    def leaf(self, index: int) -> int:
        return self.node(0, index)

    def update(self, index: int, leaf: int) -> int:
        """Set leaf and recompute path to root atomically. Returns new root."""
        if not 0 <= index < self.capacity:
            raise IndexError("leaf index out of range")
        if not 0 <= leaf < FIELD_P:
            raise ValueError("leaf outside field")
        with self.s.transaction():
            h, i = leaf, index
            for lvl in range(self.depth + 1):
                self.s.put_node(lvl, i, None if h == self.zeros[lvl] else h)
                if lvl == self.depth:
                    break
                sib = self.node(lvl, i ^ 1)
                h = hash2(sib, h) if i & 1 else hash2(h, sib)
                i >>= 1
        return h

    def proof(self, index: int) -> MerkleProof:
        if not 0 <= index < self.capacity:
            raise IndexError("leaf index out of range")
        sibs, i = [], index
        for lvl in range(self.depth):
            sibs.append(self.node(lvl, i ^ 1))
            i >>= 1
        return MerkleProof(index=index, leaf=self.leaf(index), siblings=sibs, root=self.root())

    def verify(self, proof: MerkleProof, root: Optional[int] = None) -> bool:
        return len(proof.siblings) == self.depth and verify_proof(
            self.root() if root is None else root, proof)

    def rebuild_root(self, leaves: dict[int, int]) -> int:
        """Recompute root from scratch from a {index: leaf} map (audit check)."""
        level = {i: v for i, v in leaves.items() if v != 0}
        for lvl in range(self.depth):
            nxt: dict[int, int] = {}
            for i in {k >> 1 for k in level}:
                l = level.get(2 * i, self.zeros[lvl])
                r = level.get(2 * i + 1, self.zeros[lvl])
                nxt[i] = hash2(l, r)
            level = nxt
        return level.get(0, self.zeros[self.depth])
