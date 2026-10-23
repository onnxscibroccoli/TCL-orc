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
    led = _ledger_with(T + 1_000_000 * SCALE)
    return build_transition_input(led, "alice", 1_000 + 2 * DAY)


@pytest.mark.parametrize("field,value", [
    ("oldBalance", lambda v: str(int(v) + 1)),           # not the committed leaf
    ("oldTime", lambda v: str(int(v) - 1)),
    ("accountId", lambda v: str(int(v) + 1)),
    ("oldRoot", lambda v: str(int(v) + 1)),
    ("newTime", lambda v: str(int(v) + 8 * DAY)),        # dt > MAX_STEP
    ("newTime", lambda v: "999"),                        # time goes backwards
    ("pathIndices", lambda v: ["2"] + v[1:]),            # non-boolean path bit
    ("pathElements", lambda v: [str(int(v[0]) + 1)] + v[1:]),
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
    assert fx["publicSignals"] == [fx["public"][k] for k in ("newRoot", "fee", "oldRoot", "newTime")]


def test_committed_fixture_verifies(tc, tmp_path):
    from tcl.zk.prover import REPO_ROOT
    fx = json.loads((REPO_ROOT / "test/contracts/fixtures/state_transition.json").read_text())
    vkey = REPO_ROOT / "test/contracts/fixtures/verification_key.json"
    assert verify(vkey, fx["proof"], fx["publicSignals"], tmp_path, tc)
