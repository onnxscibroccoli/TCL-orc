"""Groth16 pipeline tests for circuits/puf_verify.circom (StateTransition).

Skipped when the ZK toolchain is absent, unless TCL_REQUIRE_ZK=1 (CI), in which
case a missing toolchain is a hard failure (fail-closed).
"""
import json
import os
import subprocess

import pytest

from tcl.ledger.demurrage_fixed import SCALE, T, apply_demurrage_fixed
from tcl.ledger.service import Ledger
from tcl.storage.db import InMemoryStorage
from tcl.zk import (Toolchain, ToolchainMissing, ZkError, build_transition_input,
                    compile_circuit, dev_ptau, groth16_setup, prove, verify, witness)

DEPTH = 4
DAY = 86_400


@pytest.fixture(scope="session")
def tc():
    try:
        return Toolchain.discover()
    except ToolchainMissing as e:
        if os.environ.get("TCL_REQUIRE_ZK") == "1":
            pytest.fail(f"TCL_REQUIRE_ZK=1 but {e}")
        pytest.skip(str(e))


@pytest.fixture(scope="session")
def art(tc, tmp_path_factory):
    d = tmp_path_factory.mktemp("zk")
    a = compile_circuit(DEPTH, d / "build", tc)
    ptau = dev_ptau(13, d / "dev_13.ptau", tc)
    return groth16_setup(a, ptau, tc)


def _ledger_with(balance, t0=1_000):
    led = Ledger(InMemoryStorage(), mode="fixed", depth=DEPTH)
    led.deposit("alice", balance, t0)
    led.deposit("bob", 7 * SCALE, t0)
    return led


def _outputs(tc, wtns, tmp):
    j = tmp / "w.json"
    subprocess.run([tc.snarkjs, "wtns", "export", "json", str(wtns), str(j)], check=True,
                   capture_output=True)
    w = json.loads(j.read_text())
    return int(w[1]), int(w[2])  # newRoot, fee


def test_valid_transition_from_ledger_verifies(tc, art, tmp_path):
    led = _ledger_with(T + 1_000_000 * SCALE)
    inputs, exp = build_transition_input(led, "alice", 1_000 + 3 * DAY)
    assert exp["fee"] > 0
    w = witness(art, inputs, tmp_path / "ok.wtns", tc)
    proof, public = prove(art, w, tc)
    assert [int(x) for x in public] == [exp["newRoot"], exp["fee"], exp["oldRoot"], exp["newTime"]]
    assert verify(art.vkey, proof, public, tmp_path, tc)


@pytest.mark.parametrize("bal,dt", [
    (T - 1, 5 * DAY),                    # sub-threshold: no fee
    (T + 1, DAY),                        # tiny surplus
    (T + 123_456_789 * SCALE, 3_600),    # < 1 day: no -55 correction
    (T + 10**9 * SCALE, 7 * DAY),        # max single step
    (2**127, 2 * DAY),                   # large balance
])
def test_circuit_matches_python_demurrage(tc, art, tmp_path, bal, dt):
    led = _ledger_with(bal)
    inputs, exp = build_transition_input(led, "alice", 1_000 + dt)
    _, py_fee = apply_demurrage_fixed(bal, dt)
    assert exp["fee"] == py_fee
    root, fee = _outputs(tc, witness(art, inputs, tmp_path / "p.wtns", tc), tmp_path)
    assert (root, fee) == (exp["newRoot"], py_fee)


def _valid_inputs():
    """Two settles so the proven one has an existing (non-empty) vault leaf."""
    led = _ledger_with(T + 1_000_000 * SCALE)
    build_transition_input(led, "alice", 1_000 + DAY)
    inputs, exp = build_transition_input(led, "alice", 1_000 + 2 * DAY)
    assert inputs["vaultExists"] == "1" and int(inputs["vaultBalance"]) > 0
    return inputs, exp


def test_new_root_is_ledger_root_after_settle_for_consecutive_settles(tc, art, tmp_path):
    """The proven newRoot is the full post-settle ledger root (account AND vault),
    so consecutive proofs chain: oldRoot_{i+1} == newRoot_i == ledger.root()."""
    led = _ledger_with(T + 1_000_000 * SCALE)
    prev = led.root()
    vault_before = led.vault()
    for acct, t in (("alice", 1_000 + DAY), ("bob", 1_000 + 2 * DAY),
                    ("alice", 1_000 + 4 * DAY)):
        inputs, exp = build_transition_input(led, acct, t)
        assert exp["oldRoot"] == prev and exp["newRoot"] == led.root()
        assert led.vault() == vault_before + exp["fee"]
        assert led.verify_state()
        root, fee = _outputs(tc, witness(art, inputs, tmp_path / "c.wtns", tc), tmp_path)
        assert (root, fee) == (exp["newRoot"], exp["fee"])
        prev, vault_before = led.root(), led.vault()


def test_vault_cannot_be_proven_as_account():
    led = _ledger_with(T + SCALE)
    with pytest.raises(ZkError):
        build_transition_input(led, "__vault__", 1_000 + DAY)


def test_account_at_vault_index_rejected_by_circuit(tc, art, tmp_path):
    """Forcing pathIndices to the vault slot (index 0) must not satisfy the circuit."""
    inputs, _ = _valid_inputs()
    inputs["pathIndices"] = ["0"] * DEPTH
    with pytest.raises(ZkError):
        witness(art, inputs, tmp_path / "bad.wtns", tc)


@pytest.mark.parametrize("field,value", [
    ("oldBalance", lambda v: str(int(v) + 1)),           # not the committed leaf
    ("oldTime", lambda v: str(int(v) - 1)),
    ("accountId", lambda v: str(int(v) + 1)),
    ("oldRoot", lambda v: str(int(v) + 1)),
    ("newTime", lambda v: str(int(v) + 8 * DAY)),        # dt > MAX_STEP
    ("newTime", lambda v: "999"),                        # time goes backwards
    ("pathIndices", lambda v: ["2"] + v[1:]),            # non-boolean path bit
    ("pathElements", lambda v: [str(int(v[0]) + 1)] + v[1:]),
    # vault leaf update (second half of the settle)
    ("vaultBalance", lambda v: str(int(v) + 1)),
    ("vaultTime", lambda v: str(int(v) + 1)),
    ("vaultExists", lambda v: str(1 - int(v))),
    ("vaultExists", lambda v: "2"),
    ("vaultPathElements", lambda v: [str(int(v[0]) + 1)] + v[1:]),
    ("vaultPathElements", lambda v: v[:-1] + [str(int(v[-1]) + 1)]),
])
def test_tampered_inputs_fail_witness(tc, art, tmp_path, field, value):
    inputs, _ = _valid_inputs()
    inputs[field] = value(inputs[field])
    with pytest.raises(ZkError):
        witness(art, inputs, tmp_path / "bad.wtns", tc)


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_tampered_public_signals_fail_verify(tc, art, tmp_path, idx):
    inputs, _ = _valid_inputs()
    proof, public = prove(art, witness(art, inputs, tmp_path / "v.wtns", tc), tc)
    assert verify(art.vkey, proof, public, tmp_path, tc)
    bad = list(public)
    bad[idx] = str(int(bad[idx]) + 1)
    assert not verify(art.vkey, proof, bad, tmp_path, tc)


def test_tampered_proof_fails_verify(tc, art, tmp_path):
    inputs, _ = _valid_inputs()
    proof, public = prove(art, witness(art, inputs, tmp_path / "v.wtns", tc), tc)
    proof["pi_a"][0] = str(int(proof["pi_a"][0]) + 1)
    assert not verify(art.vkey, proof, public, tmp_path, tc)


def test_build_input_rejects_multi_step_dt():
    led = _ledger_with(T + SCALE)
    with pytest.raises(ZkError):
        build_transition_input(led, "alice", 1_000 + 8 * DAY)


def test_committed_fixture_matches_ledger_scenario():
    """The Solidity proof fixture's public signals are reproducible from the Python ledger."""
    import pathlib

    from tcl.zk.prover import REPO_ROOT
    fx = json.loads(pathlib.Path(REPO_ROOT / "test/contracts/fixtures/state_transition.json").read_text())
    sc = fx["scenario"]
    led = Ledger(InMemoryStorage(), mode="fixed", depth=fx["depth"])
    led.deposit("bob", 42 * SCALE, sc["t0"])
    led.deposit(sc["account"], T + 2_000_000 * SCALE, sc["t0"])
    _, exp = build_transition_input(led, sc["account"], sc["t0"] + sc["dt"])
    assert {k: str(v) for k, v in exp.items()} == fx["public"]
    assert exp["newRoot"] == led.root()
    assert fx["publicSignals"] == [fx["public"][k] for k in ("newRoot", "fee", "oldRoot", "newTime")]


def _chain_fixture():
    from tcl.zk.prover import REPO_ROOT
    return json.loads((REPO_ROOT / "test/contracts/fixtures/state_transition_chain.json").read_text())


def test_chain_fixture_matches_ledger_and_links():
    fx = _chain_fixture()
    led = Ledger(InMemoryStorage(), mode="fixed", depth=fx["depth"])
    led.deposit("bob", 42 * SCALE, fx["t0"])
    led.deposit("alice", T + 2_000_000 * SCALE, fx["t0"])
    prev = str(led.root())
    fees = []
    for st in fx["steps"]:
        _, exp = build_transition_input(led, st["account"], st["now"])
        assert {k: str(v) for k, v in exp.items()} == st["public"]
        assert st["public"]["oldRoot"] == prev and st["ledgerRoot"] == str(led.root())
        assert st["vault"] == str(led.vault())
        prev = st["public"]["newRoot"]
        fees.append(exp["fee"])
    assert len(fx["steps"]) >= 3 and 0 in fees and sum(f > 0 for f in fees) >= 2


def test_chain_fixture_verifies(tc, tmp_path):
    from tcl.zk.prover import REPO_ROOT
    vkey = REPO_ROOT / "test/contracts/fixtures/verification_key.json"
    for st in _chain_fixture()["steps"]:
        assert verify(vkey, st["proof"], st["publicSignals"], tmp_path, tc)


def test_committed_fixture_verifies(tc, tmp_path):
    from tcl.zk.prover import REPO_ROOT
    fx = json.loads((REPO_ROOT / "test/contracts/fixtures/state_transition.json").read_text())
    vkey = REPO_ROOT / "test/contracts/fixtures/verification_key.json"
    assert verify(vkey, fx["proof"], fx["publicSignals"], tmp_path, tc)
