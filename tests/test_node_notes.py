"""Native notes module (port of NoteController) incl. conservation invariants."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from tcl.node.groth16 import VerifyingKey, solidity_to_snarkjs
from tcl.node.notes import (DORMANT, LIVE, DictBank, NoteError, NotesModule,
                            make_groth16_verifier)

FX = Path(__file__).resolve().parents[1] / "test/contracts/fixtures"
U = 10**6


def mk(verify=lambda p, s: p == {"ok": True}, pool=0, unit=U):
    bank = DictBank({"m": 10**15, "funder": 10**15})
    m = NotesModule.genesis(unit, 77, bank, verify)
    if pool:
        m.fund_pool("funder", pool)
    return m, bank


def total(m, bank):
    return bank.total() + m.s.escrow


def test_mint_cost_credit_reward_and_fallback():
    m, bank = mk(pool=10**9)
    t0 = total(m, bank)
    cost, reward = m.mint("m", 1, 100, 10**9, now=1000)        # attested cost above cap
    assert cost == m.s.denoms["100"][1] and reward == 100 * U * 100 // 10_000
    assert m.s.notes["1"]["state"] == LIVE and total(m, bank) == t0
    m.check_invariants()
    m2, b2 = mk(pool=0)                                         # empty pool -> pure 1:1 swap
    assert m2.mint("m", 1, 100, 10**9, now=1000) == (0, 0)
    assert b2.bal["m"] == 10**15 - 100 * U
    m2.check_invariants()


def test_epoch_budget_falls_back():
    m, bank = mk(pool=10**12)
    m.set_params(100, 86_400, 3 * U, 10**12, 86_400, 86_400)
    assert m.mint("m", 1, 100, 0, 10) == (0, U)
    assert m.mint("m", 2, 100, 0, 20) == (0, U)
    assert m.mint("m", 3, 100, 0, 30) == (0, U)
    assert m.mint("m", 4, 100, 0, 40) == (0, 0)                 # budget exhausted
    assert m.mint("m", 5, 100, 0, 86_400 + 1) == (0, U)         # new epoch
    m.check_invariants()


def test_states_era_nullifiers_and_rearm():
    m, bank = mk()
    m.mint("m", 9, 50, 0, 0)
    with pytest.raises(NoteError, match="BAD_STATE"):
        m.mint("m", 9, 50, 0, 0)
    with pytest.raises(NoteError, match="BAD_STATE"):
        m.rearm("m", 9, 0, 0)                                    # LIVE cannot rearm
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(9, 5, "bob", {"ok": False}, 10)
    assert m.deposit(9, 5, "bob", {"ok": True}, 10) == (50 * U, 0)
    assert m.s.notes["9"]["state"] == DORMANT
    with pytest.raises(NoteError, match="BAD_STATE"):
        m.deposit(9, 6, "bob", {"ok": True}, 11)                 # DORMANT cannot deposit
    m.rearm("m", 9, 0, 20)
    assert m.s.notes["9"]["era"] == 1
    with pytest.raises(NoteError, match="NULLIFIER_SPENT"):
        m.deposit(9, 5, "bob", {"ok": True}, 30)
    m.deposit(9, 6, "bob", {"ok": True}, 30)
    m.check_invariants()
    for bad in ((0, 50), (2**254, 50), (3, 7), (3, 0)):
        with pytest.raises(NoteError):
            m.mint("m", bad[0], bad[1], 0, 0)


def test_cost_cap_hard_bound():
    m, _ = mk()
    m.set_denomination(100, True, 100 * U * 2000 // 10_000)     # exactly 20%: ok
    with pytest.raises(NoteError, match="OUT_OF_BOUNDS"):
        m.set_denomination(100, True, 100 * U * 2000 // 10_000 + 1)
    with pytest.raises(NoteError):
        m.set_params(101, 86_400, 0, 1, 86_400, 86_400)          # reward > 1%


def test_hoarding_threshold_locks_and_vests():
    m, bank = mk()
    m.set_params(100, 86_400, 0, 150 * U, 86_400, 10 * 86_400)
    for i in (1, 2):
        m.mint("m", i, 100, 0, 0)
    assert m.deposit(1, 11, "h", {"ok": True}, 0) == (100 * U, 0)
    assert m.deposit(2, 12, "h", {"ok": True}, 0) == (50 * U, 50 * U)
    assert m.s.locked_total == 50 * U
    assert m.release("h", 5 * 86_400) == 25 * U
    assert m.release("h", 10 * 86_400) == 25 * U and m.s.locked_total == 0
    m.check_invariants()


def test_conservation_random_walk():
    rng = random.Random(1)
    m, bank = mk(pool=10**10)
    t0 = total(m, bank)
    ids = []
    for step in range(400):
        now = step * 3600
        op = rng.choice(["mint", "dep", "rearm", "rel", "fund"])
        try:
            if op == "mint":
                i = rng.randrange(1, 60)
                m.mint("m", i, rng.choice([5, 10, 20, 50, 100, 200, 500]),
                       rng.randrange(0, 10 * U), now)
                ids.append(i)
            elif op == "dep" and ids:
                m.deposit(rng.choice(ids), rng.randrange(1, 10**6), rng.choice("abc"),
                          {"ok": True}, now)
            elif op == "rearm" and ids:
                m.rearm("m", rng.choice(ids), rng.randrange(0, U), now)
            elif op == "rel":
                m.release(rng.choice("abc"), now)
            else:
                m.fund_pool("funder", rng.randrange(1, 10**6))
        except NoteError:
            pass
        m.check_invariants()
        assert total(m, bank) == t0


def test_real_groth16_deposit_from_fixture():
    n = json.loads((FX / "note_deposit.json").read_text())
    vk = VerifyingKey.load(Path(__file__).resolve().parents[1] /
                           "circuits/note/verification_key.json")
    ctx = int(n["era0"]["pubSignals"][3], 16)
    bank = DictBank({"m": 10**9})
    m = NotesModule.genesis(1, ctx, bank, make_groth16_verifier(vk))
    nid, rcpt = int(n["id"], 16), int(n["recipient"], 16)
    p = {k: solidity_to_snarkjs(n[k]["a"], n[k]["b"], n[k]["c"]) for k in ("era0", "era1",
                                                                            "wrongCtx")}
    nf = {k: int(n[k]["pubSignals"][5], 16) for k in p}
    m.mint("m", nid, 100, 0, 0)
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["era1"], rcpt, p["era1"], 1)           # future-era proof
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["wrongCtx"], rcpt, p["wrongCtx"], 1)   # other deployment's proof
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["era0"], rcpt + 1, p["era0"], 1)       # redirected recipient
    assert m.deposit(nid, nf["era0"], rcpt, p["era0"], 1) == (100, 0)
    m.rearm("m", nid, 0, 2)
    with pytest.raises(NoteError, match="BAD_PROOF"):
        m.deposit(nid, nf["era0"] + 1, rcpt, p["era0"], 3)       # old-era proof
    m.deposit(nid, nf["era1"], rcpt, p["era1"], 3)
    m.check_invariants()
