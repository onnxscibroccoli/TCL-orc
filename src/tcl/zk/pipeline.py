"""Cached build + one-shot prove helpers used by the CLI and integration tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from .prover import (Artifacts, Toolchain, ZkError, build_transition_input, compile_circuit,
                     dev_ptau, groth16_setup, prove, solidity_calldata, verify, witness)

META = "tcl-build.json"


def circuit_digest() -> str:
    """sha256 over all circuit sources; a cached build is reused only if it matches."""
    from .prover import circuits_dir
    CIRCUITS_DIR = circuits_dir()
    h = hashlib.sha256()
    for p in sorted(CIRCUITS_DIR.rglob("*.circom")):
        h.update(p.relative_to(CIRCUITS_DIR).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def ensure_artifacts(depth: int, build: Path, ptau_power: int = 13,
                     force: bool = False, tc: Optional[Toolchain] = None) -> Artifacts:
    """Compile + Groth16 dev setup into ``build``; reuse a previous build of the same depth."""
    build = Path(build)
    name = f"state_transition_d{depth}"
    meta = build / META
    art = Artifacts(depth, build, build / f"{name}.r1cs", build / f"{name}_js/{name}.wasm",
                    build / "circuit_final.zkey", build / "verification_key.json")
    if not force and meta.exists():
        m = json.loads(meta.read_text())
        fresh = m.get("depth") == depth and m.get("circuit") == circuit_digest()
        if fresh and all(p.exists() for p in (art.r1cs, art.wasm, art.zkey, art.vkey)):
            return art
    tc = tc or Toolchain.discover()
    art = groth16_setup(compile_circuit(depth, build, tc),
                        dev_ptau(ptau_power, build / f"dev_{ptau_power}.ptau", tc), tc)
    meta.write_text(json.dumps({"depth": depth, "ptau_power": ptau_power, "dev_only": True,
                                "circuit": circuit_digest()}))
    return art


def prove_settlement(ledger, account: str, now: int, art: Artifacts, work: Path) -> dict:
    """Settle ``account`` at ``now`` on the ledger, prove the transition, verify locally."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if ledger.tree.depth != art.depth:
        raise ZkError(f"ledger depth {ledger.tree.depth} != circuit depth {art.depth}")
    inputs, exp = build_transition_input(ledger, account, now)
    proof, public = prove(art, witness(art, inputs, work / "w.wtns"))
    if [int(x) for x in public] != [exp[k] for k in ("newRoot", "fee", "oldRoot", "newTime")]:
        raise ZkError("public signals disagree with ledger-computed values")
    if exp["newRoot"] != ledger.root():
        raise ZkError("proven newRoot != ledger root after settle")
    if not verify(art.vkey, proof, public, work):
        raise ZkError("generated proof failed local verification")
    return {"depth": art.depth, "account": account,
            "public": {k: str(v) for k, v in exp.items()}, "publicSignals": public,
            "proof": proof, "calldata": solidity_calldata(proof, public),
            "verified": True}
