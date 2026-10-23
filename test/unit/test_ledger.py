"""Spec-layout entry point (test/unit); full suites live in tests/."""
from tcl.ledger import Ledger
from tcl.storage import SQLiteStorage


def test_ledger_tree_persists(tmp_path):
    p = tmp_path / "u.db"
    lg = Ledger(SQLiteStorage(p), depth=16)
    lg.deposit("alice", 10**21, 0)
    lg.apply_decay_all(86_400)
    root = lg.root()
    lg2 = Ledger(SQLiteStorage(p), depth=16)
    assert lg2.root() == root and lg2.verify_state()
