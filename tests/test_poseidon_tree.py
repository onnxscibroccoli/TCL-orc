import pytest

from tcl.crypto.poseidon import poseidon
from tcl.ledger.poseidon_tree import (
    MerkleProof, PoseidonMerkleTree, compute_root, verify_proof, zero_hashes)
from tcl.storage import InMemoryStorage, SQLiteStorage, StorageError


def test_zero_hashes_chain():
    z = zero_hashes(3)
    assert z[0] == 0 and z[1] == poseidon([0, 0]) and z[2] == poseidon([z[1], z[1]])


def test_manual_depth2_root():
    t = PoseidonMerkleTree(InMemoryStorage(), depth=2)
    t.update(0, 11); t.update(1, 22); t.update(3, 44)
    exp = poseidon([poseidon([11, 22]), poseidon([0, 44])])
    assert t.root() == exp


@pytest.mark.parametrize("backend", ["mem", "sqlite"])
def test_proof_roundtrip_and_negative(tmp_path, backend):
    s = InMemoryStorage() if backend == "mem" else SQLiteStorage(tmp_path / "t.db")
    t = PoseidonMerkleTree(s, depth=8)
    for i, v in [(0, 5), (7, 9), (200, 123), (255, 1)]:
        t.update(i, v)
    for i in (0, 7, 200, 255, 3):
        pf = t.proof(i)
        assert t.verify(pf) and verify_proof(t.root(), pf)
    pf = t.proof(7)
    assert not verify_proof(t.root(), MerkleProof(7, pf.leaf + 1, pf.siblings, pf.root))
    assert not verify_proof(t.root(), MerkleProof(6, pf.leaf, pf.siblings, pf.root))
    bad = list(pf.siblings); bad[3] ^= 1
    assert not verify_proof(t.root(), MerkleProof(7, pf.leaf, bad, pf.root))
    assert not verify_proof(t.root(), MerkleProof(1 << 8, pf.leaf, pf.siblings, pf.root))
    assert not t.verify(MerkleProof(7, pf.leaf, pf.siblings[:-1], pf.root))


def test_resetting_leaf_restores_root_and_prunes_nodes():
    s = InMemoryStorage()
    t = PoseidonMerkleTree(s, depth=6)
    empty = t.root()
    t.update(5, 77)
    assert t.root() != empty
    t.update(5, 0)
    assert t.root() == empty and s._nodes == {}


def test_persistence_across_reopen(tmp_path):
    p = tmp_path / "t.db"
    t = PoseidonMerkleTree(SQLiteStorage(p), depth=10)
    for i in range(20):
        t.update(i * 37 % 1024, i + 1)
    r = t.root()
    t2 = PoseidonMerkleTree(SQLiteStorage(p), depth=10)
    assert t2.root() == r
    assert t2.rebuild_root({i * 37 % 1024: i + 1 for i in range(20)}) == r
    with pytest.raises(StorageError):
        PoseidonMerkleTree(SQLiteStorage(p), depth=11)


def test_update_is_atomic_on_failure(tmp_path):
    s = SQLiteStorage(tmp_path / "t.db")
    t = PoseidonMerkleTree(s, depth=4)
    t.update(1, 3)
    r = t.root()
    with pytest.raises(RuntimeError):
        with s.transaction():
            t.update(2, 99)
            raise RuntimeError("boom")
    assert t.root() == r and s.get_node(0, 2) is None


def test_bounds():
    t = PoseidonMerkleTree(InMemoryStorage(), depth=3)
    with pytest.raises(IndexError):
        t.update(8, 1)
    with pytest.raises(ValueError):
        PoseidonMerkleTree(InMemoryStorage(), depth=0)
    assert compute_root(0, 0, zero_hashes(3)[:3]) == t.root()
