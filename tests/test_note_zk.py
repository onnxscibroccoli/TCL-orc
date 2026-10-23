"""Groth16 note-deposit proof tests. Skips without toolchain unless TCL_REQUIRE_ZK=1."""
import json
import os
from pathlib import Path

import pytest

from tcl.zk import note as zn
from tcl.zk.prover import Toolchain, ToolchainMissing, ZkError, verify

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def art(tmp_path_factory):
    try:
        Toolchain.discover()
    except ToolchainMissing:
        if os.environ.get("TCL_REQUIRE_ZK") == "1":
            raise
        pytest.skip("circom/snarkjs not installed")
    return zn.setup_note_circuit(tmp_path_factory.mktemp("note"))


NOTE = zn.SimulatedNote(0x5EC12E7, 0x9F0F, 100, 7)
CTX = zn.note_ctx(31337, "0xC0ffee0000000000000000000000000000000001")
BOB = "0x0000000000000000000000000000000000000B0b"


def test_valid_proof_verifies(art, tmp_path):
    proof, pub = zn.prove_deposit(art, NOTE, 0, CTX, BOB, tmp_path)
    assert int(pub[5]) == zn.nullifier(NOTE.id, 0, CTX)
    bad = list(pub)
    bad[2] = "1"  # different era
    assert not verify(art.vkey, proof, bad, tmp_path / "x")
    bad = list(pub)
    bad[4] = "1"  # different recipient
    assert not verify(art.vkey, proof, bad, tmp_path / "y")


@pytest.mark.parametrize("field,val", [("nf", "1"), ("id", "1"), ("denom", "50"),
                                       ("noteSecret", "999"), ("era", str(2**64))])
def test_bad_witness_rejected(art, tmp_path, field, val):
    inp = zn.deposit_input(NOTE, 0, CTX, BOB)
    inp[field] = val
    with pytest.raises(ZkError):
        zn.witness(art, inp, tmp_path / "w.wtns")


def test_fixture_matches_reference():
    fx = json.loads((REPO / "test/contracts/fixtures/note_deposit.json").read_text())
    assert int(fx["id"], 16) == NOTE.id
    assert int(fx["era1"]["pubSignals"][5], 16) == zn.nullifier(NOTE.id, 1, CTX)
