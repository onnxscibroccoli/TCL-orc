"""Consecutive settleEpoch calls on ONE anvil deployment.

Each proof covers the full Ledger.settle (account + vault leaf), so after every
settlement the on-chain stateRoot equals the off-chain ledger root and the next
proof's oldRoot chains from it (no StaleRoot). Includes a fee == 0 settle.
"""
import time

import pytest

from tcl.chain import ChainError, anvil, deploy, settle_epoch, state_root
from tcl.ledger.demurrage_fixed import SCALE, T
from tcl.ledger.service import Ledger
from tcl.storage.db import SQLiteStorage
from tcl.zk import prove_settlement

DAY = 86_400


def test_consecutive_settlements_track_ledger_root(zk_art, evm_tools, tmp_path):
    now = int(time.time()) - 10
    t0 = now - 6 * DAY
    led = Ledger(SQLiteStorage(tmp_path / "ledger.db"), "fixed", depth=zk_art.depth)
    led.deposit("bob", 42 * SCALE, t0)                       # below threshold -> fee 0
    led.deposit("alice", T + 2_000_000 * SCALE, t0)
    led.deposit("carol", T + 500_000 * SCALE, t0)
    genesis = led.root()
    plan = [("alice", t0 + 2 * DAY), ("carol", t0 + 3 * DAY), ("bob", t0 + 4 * DAY),
            ("alice", t0 + 5 * DAY), ("carol", now)]
    with anvil() as rpc:
        dep = deploy(rpc, genesis, t0, tmp_path / "chain", zkey=zk_art.zkey)
        assert state_root(dep) == genesis
        fees = []
        for i, (acct, t) in enumerate(plan, 1):
            b = prove_settlement(led, acct, t, zk_art, tmp_path / f"zk{i}")
            ev = settle_epoch(dep, b["calldata"])
            assert ev["epoch"] == i
            assert int(ev["newRoot"]) == led.root() == state_root(dep)
            fees.append(int(ev["fee"]))
        # replaying the last proof is stale now
        with pytest.raises(ChainError):
            settle_epoch(dep, b["calldata"])
    assert 0 in fees and sum(f > 0 for f in fees) >= 4
    assert sum(fees) == led.vault()
    assert led.verify_state()
