"""Pure-Python BN254 pairing + Groth16 verifier vs snarkjs-produced fixtures (stdlib only)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcl.node import bn254 as c
from tcl.node.groth16 import VerifyingKey, solidity_to_snarkjs, verify

FX = Path(__file__).resolve().parents[1] / "test/contracts/fixtures"
ROOT = Path(__file__).resolve().parents[1]


def test_pairing_bilinear_and_nondegenerate():
    a, b = 6, 35
    assert c.pairing_product_is_one([(c.g1_mul(c.G1_GEN, a), c.g2_mul(c.G2_GEN, b)),
                                     (c.g1_neg(c.G1_GEN), c.g2_mul(c.G2_GEN, a * b))])
    assert not c.pairing_product_is_one([(c.g1_mul(c.G1_GEN, a + 1), c.g2_mul(c.G2_GEN, b)),
                                         (c.g1_neg(c.G1_GEN), c.g2_mul(c.G2_GEN, a * b))])
    assert c.final_exp(c.miller_loop(c.G2_GEN, c.G1_GEN)) != c.ONE12
    assert c.g2_in_subgroup(c.G2_GEN) and c.g1_mul(c.G1_GEN, c.R) is None


@pytest.fixture(scope="module")
def st():
    vk = VerifyingKey.load(FX / "verification_key.json")
    return vk, json.loads((FX / "state_transition.json").read_text())


def test_state_transition_proof_verifies(st):
    vk, f = st
    assert verify(vk, f["proof"], f["publicSignals"])


@pytest.mark.parametrize("tamper", ["signal", "a", "c", "b_swap", "signal_overflow",
                                    "count", "offcurve", "garbage"])
def test_tampered_state_transition_rejected(st, tamper):
    vk, f = st
    proof = json.loads(json.dumps(f["proof"]))
    pub = list(f["publicSignals"])
    if tamper == "signal":
        pub[1] = str(int(pub[1]) + 1)
    elif tamper == "a":
        pa = c.g1_add((int(proof["pi_a"][0]), int(proof["pi_a"][1])), c.G1_GEN)
        proof["pi_a"] = [str(pa[0]), str(pa[1]), "1"]
    elif tamper == "c":
        proof["pi_c"] = [str(c.G1_GEN[0]), str(c.G1_GEN[1]), "1"]
    elif tamper == "b_swap":  # EVM coordinate order passed as snarkjs order
        b = proof["pi_b"]
        proof["pi_b"] = [[b[0][1], b[0][0]], [b[1][1], b[1][0]], ["1", "0"]]
    elif tamper == "signal_overflow":  # s + r aliases s mod r -> must be rejected
        pub[0] = str(int(pub[0]) + c.R)
    elif tamper == "count":
        pub = pub[:-1]
    elif tamper == "offcurve":
        proof["pi_a"] = [proof["pi_a"][0], str(int(proof["pi_a"][1]) + 1), "1"]
    else:
        proof = {"dev": True}
    assert not verify(vk, proof, pub)


def test_note_deposit_proofs_and_wrong_context():
    vk = VerifyingKey.load(ROOT / "circuits/note/verification_key.json")
    n = json.loads((FX / "note_deposit.json").read_text())
    for k in ("era0", "era1"):
        e = n[k]
        assert verify(vk, solidity_to_snarkjs(e["a"], e["b"], e["c"]), e["pubSignals"])
    # era-1 proof replayed with era-0 signals (old era) fails
    e1, e0 = n["era1"], n["era0"]
    assert not verify(vk, solidity_to_snarkjs(e1["a"], e1["b"], e1["c"]), e0["pubSignals"])
    # the wrongCtx proof is valid for ITS context but not for the controller's context
    w = n["wrongCtx"]
    sig = list(w["pubSignals"])
    sig[3] = e0["pubSignals"][3]
    assert not verify(vk, solidity_to_snarkjs(w["a"], w["b"], w["c"]), sig)


def test_bundled_vks_match_repo_sources():
    from tcl.node.chain import VK_DIR
    assert json.loads((VK_DIR / "state_transition.json").read_text()) == \
        json.loads((FX / "verification_key.json").read_text())
    assert json.loads((VK_DIR / "note_deposit.json").read_text()) == \
        json.loads((ROOT / "circuits/note/verification_key.json").read_text())
