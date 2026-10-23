"""Acceptance Test 3: off-chain ledger persistence & recovery after SIGKILL.

A child process applies 1,000 balance transitions to the Poseidon state tree,
reporting (count, journal_len, root) after each *committed* transition. The
parent SIGKILLs it (mid-run, and after completion), reopens the DB and checks:
PRAGMA integrity_check == ok, stored root == root recomputed from account rows,
and the root matches the last committed root reported before the crash.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tcl.ledger import Ledger
from tcl.storage import SQLiteStorage

N = 1000
DEPTH = 20
SRC = str(Path(__file__).resolve().parents[1] / "src")

CHILD = textwrap.dedent(f"""
    import sys, time
    sys.path.insert(0, {SRC!r})
    from tcl.ledger import Ledger
    from tcl.storage import SQLiteStorage
    db, out, hold = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
    lg = Ledger(SQLiteStorage(db), depth={DEPTH})
    with open(out, "w", buffering=1) as f:
        for i in range({N}):
            lg.deposit(f"acct{{i % 64}}", 10**21 + i, i * 3600)
            f.write(f"{{i + 1}} {{len(lg.s.journal())}} {{lg.root()}}\\n")
        f.write("DONE\\n")
    while hold:
        time.sleep(0.1)
""")


def _run_and_kill(tmp_path, kill_after_lines=None):
    db, out = tmp_path / "crash.db", tmp_path / "progress.txt"
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    p = subprocess.Popen([sys.executable, str(script), str(db), str(out), "1"])
    deadline = time.time() + 600
    try:
        while time.time() < deadline:
            txt = out.read_text() if out.exists() else ""
            lines = txt.splitlines()
            if kill_after_lines is None and "DONE" in lines:
                break
            if kill_after_lines is not None and len(lines) >= kill_after_lines:
                break
            assert p.poll() is None, "child died early"
            time.sleep(0.02)
        else:
            pytest.fail("child timed out")
        os.kill(p.pid, signal.SIGKILL)
        p.wait(10)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == -signal.SIGKILL
    lines = [l.split() for l in out.read_text().splitlines() if l and l != "DONE"]
    lines = [l for l in lines if len(l) == 3]  # drop a torn final line, if any
    return db, [(int(a), int(b), int(c)) for a, b, c in lines]


def _verify(db, progress, strict):
    s = SQLiteStorage(db)  # reopen = WAL recovery
    assert s.journal_mode.lower() == "wal"
    assert s.integrity_check() == "ok"
    lg = Ledger(s, depth=DEPTH)
    assert lg.verify_state(), "stored root != root rebuilt from accounts"
    count, jlen, last_root = progress[-1]
    history = set(lg.roots())
    assert all(r in history for _, _, r in progress), "a committed root was lost"
    if strict:
        assert lg.root() == last_root and len(s.journal()) == jlen
    else:
        # killed between commit and report: at most one extra committed deposit
        assert lg.root() == last_root or (len(s.journal()) > jlen and
                                         len(s.journal()) <= jlen + 2)
    return lg


def test_sigkill_after_1000_transitions(tmp_path):
    db, prog = _run_and_kill(tmp_path)
    assert len(prog) == N
    lg = _verify(db, prog, strict=True)
    assert lg.root() == prog[-1][2]


def test_sigkill_mid_run(tmp_path):
    db, prog = _run_and_kill(tmp_path, kill_after_lines=300)
    assert 300 <= len(prog) < N
    _verify(db, prog, strict=False)
