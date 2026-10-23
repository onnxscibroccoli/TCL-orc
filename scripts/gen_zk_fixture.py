#!/usr/bin/env python3
"""Regenerate the Groth16 dev artifacts consumed by the Solidity tests.

Outputs (committed):
  contracts/verifiers/StateVerifier.sol            snarkjs-exported verifier (renamed)
  test/contracts/fixtures/verification_key.json    matching verification key
  test/contracts/fixtures/state_transition.json    real proof from a real ledger settle

DEV ONLY: single-party local ptau + zkey randomness. Rerunning changes the
verifier and the proof together; commit all three files at once.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tcl.ledger.demurrage_fixed import SCALE, T  # noqa: E402
from tcl.ledger.service import Ledger  # noqa: E402
from tcl.storage.db import InMemoryStorage  # noqa: E402
from tcl.zk import (build_transition_input, compile_circuit, dev_ptau, export_verifier,  # noqa: E402
                    groth16_setup, prove, solidity_calldata, verify, witness)

DEPTH = 4
T0 = 1_700_000_000
DT = 3 * 86_400


def main() -> int:
    fx = ROOT / "test/contracts/fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        art = groth16_setup(compile_circuit(DEPTH, d / "build"), dev_ptau(13, d / "dev.ptau"))
        led = Ledger(InMemoryStorage(), mode="fixed", depth=DEPTH)
        led.deposit("bob", 42 * SCALE, T0)
        led.deposit("alice", T + 2_000_000 * SCALE, T0)
        inputs, exp = build_transition_input(led, "alice", T0 + DT)
        proof, public = prove(art, witness(art, inputs, d / "w.wtns"))
        if not verify(art.vkey, proof, public, d):
            print("generated proof does not verify", file=sys.stderr)
            return 1
        export_verifier(art.zkey, ROOT / "contracts/verifiers/StateVerifier.sol")
        shutil.copy(art.vkey, fx / "verification_key.json")
        out = {"depth": DEPTH, "scenario": {"account": "alice", "t0": T0, "dt": DT},
               "public": {k: str(v) for k, v in exp.items()},
               "publicSignals": public, "proof": proof,
               "calldata": solidity_calldata(proof, public)}
        (fx / "state_transition.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["public"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
