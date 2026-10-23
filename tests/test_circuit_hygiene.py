"""Static hygiene checks on the Circom sources (no toolchain needed)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CIRCUITS = sorted((ROOT / "circuits").rglob("*.circom"))


def _templates(src: str):
    for m in re.finditer(r"template\s+(\w+)\s*\([^)]*\)\s*\{", src):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(src[i], 0)
            i += 1
        yield m.group(1), src[m.end():i]


def test_circuits_present_and_versioned():
    assert CIRCUITS
    for p in CIRCUITS:
        assert re.search(r"pragma circom 2\.1\.\d+;", p.read_text()), p


def test_every_hint_is_bound_by_a_constraint():
    """Each `x <-- ...` must have x appear in an `===` in the same template."""
    hints = 0
    for p in CIRCUITS:
        for name, body in _templates(p.read_text()):
            constraints = " ".join(l for l in body.splitlines() if "===" in l)
            for target in re.findall(r"(\w+)\s*<--", body):
                hints += 1
                assert re.search(rf"\b{target}\b", constraints), f"{p.name}:{name}:{target}"
    assert hints == 2  # only DivConst's quotient/remainder hints


def test_depth_is_parameterized():
    src = (ROOT / "circuits/puf_verify.circom").read_text()
    assert "template StateTransition(depth)" in src
    assert "component main" not in src  # instantiated by prover / circuits/main
