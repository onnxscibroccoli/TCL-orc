"""Output equality: TCL's Apache-2.0 gadgets (circuits/lib/tcl) vs circomlib 2.0.5.

The same wrapper circuit is compiled twice, once against each library, and the witness
outputs are compared on fixed vectors. Poseidon is also checked against the in-repo Python
reference (tcl.crypto.poseidon). Needs circom + node + node_modules/circomlib; skipped
otherwise unless TCL_REQUIRE_ZK=1 (then it fails closed).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from tcl.crypto.poseidon import FIELD_P, poseidon

ROOT = Path(__file__).resolve().parents[1]
TCL_LIB = ROOT / "circuits/lib/tcl"
CIRCOMLIB = ROOT / "node_modules/circomlib/circuits"

WRAPPER = """pragma circom 2.1.6;
include "{lib}/poseidon.circom";
include "{lib}/comparators.circom";
include "{lib}/bitify.circom";
include "{lib}/{mux}";
template W() {{
  signal input a[4];
  signal input x;
  signal input y;
  signal input s;
  signal output o[16];
  component p1 = Poseidon(1); p1.inputs[0] <== a[0];
  component p2 = Poseidon(2); for (var i = 0; i < 2; i++) p2.inputs[i] <== a[i];
  component p3 = Poseidon(3); for (var i = 0; i < 3; i++) p3.inputs[i] <== a[i];
  component p4 = Poseidon(4); for (var i = 0; i < 4; i++) p4.inputs[i] <== a[i];
  component lt = LessThan(64); lt.in[0] <== x; lt.in[1] <== y;
  component le = LessEqThan(64); le.in[0] <== x; le.in[1] <== y;
  component gt = GreaterThan(64); gt.in[0] <== x; gt.in[1] <== y;
  component ge = GreaterEqThan(64); ge.in[0] <== x; ge.in[1] <== y;
  component eq = IsEqual(); eq.in[0] <== x; eq.in[1] <== y;
  component z = IsZero(); z.in <== x;
  component nb = Num2Bits(64); nb.in <== x;
  component bn = Bits2Num(64); for (var i = 0; i < 64; i++) bn.in[i] <== nb.out[i];
  component mx = Mux1(); mx.c[0] <== x; mx.c[1] <== y; mx.s <== s;
  o[0] <== p1.out; o[1] <== p2.out; o[2] <== p3.out; o[3] <== p4.out;
  o[4] <== lt.out; o[5] <== le.out; o[6] <== gt.out; o[7] <== ge.out;
  o[8] <== eq.out; o[9] <== z.out; o[10] <== bn.out; o[11] <== mx.out;
  o[12] <== nb.out[0]; o[13] <== nb.out[63]; o[14] <== nb.out[31]; o[15] <== nb.out[7];
}}
component main = W();
"""


def _tools_ok() -> bool:
    return bool(shutil.which("circom") and shutil.which("node") and CIRCOMLIB.is_dir())


pytestmark = pytest.mark.skipif(
    not _tools_ok() and os.environ.get("TCL_REQUIRE_ZK") != "1",
    reason="circom/node/circomlib not available",
)


def _read_wtns(path: Path) -> list[int]:
    b = path.read_bytes()
    assert b[:4] == b"wtns"
    _ver, nsec = struct.unpack_from("<II", b, 4)
    off, n8, vals = 12, None, None
    for _ in range(nsec):
        typ, size = struct.unpack_from("<IQ", b, off)
        off += 12
        if typ == 1:
            n8 = struct.unpack_from("<I", b, off)[0]
        elif typ == 2:
            vals = [int.from_bytes(b[off + i:off + i + n8], "little") for i in range(0, size, n8)]
        off += size
    return vals


def _build(tmp: Path, name: str, lib: str, mux: str) -> Path:
    src = tmp / f"{name}.circom"
    src.write_text(WRAPPER.format(lib=lib, mux=mux))
    subprocess.run(["circom", str(src), "--wasm", "-o", str(tmp)], check=True,
                   capture_output=True)
    return tmp / f"{name}_js"


def _outputs(js: Path, name: str, inp: dict, tmp: Path) -> list[int]:
    i, w = tmp / f"{name}.in.json", tmp / f"{name}.wtns"
    i.write_text(json.dumps(inp))
    subprocess.run(["node", str(js / "generate_witness.js"), str(js / f"{name}.wasm"), str(i),
                    str(w)], check=True, capture_output=True)
    return _read_wtns(w)[1:17]


def _vectors():
    rng = random.Random(0x7C1)
    edge = [(0, 0), (0, 1), (1, 0), (5, 5), (2**64 - 1, 0), (0, 2**64 - 1),
            (2**64 - 1, 2**64 - 1), (2**63, 2**63 - 1)]
    out = []
    for k in range(24):
        x, y = edge[k] if k < len(edge) else (rng.getrandbits(64), rng.getrandbits(64))
        if k % 5 == 0 and k >= len(edge):
            y = x
        a = [0, 1, FIELD_P - 1, 2**253] if k == 0 else [rng.randrange(FIELD_P) for _ in range(4)]
        out.append({"a": [str(v) for v in a], "x": str(x), "y": str(y), "s": str(k % 2)})
    return out


def test_tcl_gadgets_match_circomlib_and_python(tmp_path):
    js_t = _build(tmp_path, "w_tcl", str(TCL_LIB), "mux.circom")
    js_c = _build(tmp_path, "w_clib", str(CIRCOMLIB), "mux1.circom")
    for inp in _vectors():
        got = _outputs(js_t, "w_tcl", inp, tmp_path)
        ref = _outputs(js_c, "w_clib", inp, tmp_path)
        assert got == ref, inp
        a = [int(v) for v in inp["a"]]
        assert got[:4] == [poseidon(a[:n]) for n in range(1, 5)]
        x, y = int(inp["x"]), int(inp["y"])
        assert got[4:12] == [x < y, x <= y, x > y, x >= y, x == y, x == 0, x,
                             y if inp["s"] == "1" else x]


def test_constraint_counts_match_circomlib_plus_selector_check(tmp_path):
    def count(lib: str, mux: str, name: str) -> str:
        src = tmp_path / f"{name}.circom"
        src.write_text(WRAPPER.format(lib=lib, mux=mux))
        r = subprocess.run(["circom", str(src), "--r1cs", "-o", str(tmp_path)], check=True,
                           capture_output=True, text=True)
        return [ln for ln in r.stdout.splitlines() if "non-linear constraints" in ln][0]
    n_tcl = int(count(str(TCL_LIB), "mux.circom", "c_tcl").split()[-1])
    n_lib = int(count(str(CIRCOMLIB), "mux1.circom", "c_lib").split()[-1])
    # TCL's Mux1 additionally constrains the selector to be boolean (+1); all else is equal.
    assert n_tcl == n_lib + 1


def test_poseidon_constants_regenerate():
    r = subprocess.run(["python3", str(ROOT / "scripts/gen_poseidon_constants.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_production_circuits_use_in_house_lib():
    for p in (ROOT / "circuits").rglob("*.circom"):
        for line in p.read_text().splitlines():
            if line.strip().startswith("include"):
                assert "circomlib" not in line, f"{p}: {line}"
