"""Groth16 prover wrapper for the reusable-note deposit circuit (docs/11 §4).

SIMULATION: the note secret stands in for a secure-element key and ``puf_hash``
for a PUF response digest. Nothing here talks to real hardware.

Fail-closed: every helper raises on malformed input; :func:`prove_deposit`
re-verifies its own proof and raises :class:`ZkError` if it does not verify.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..crypto.poseidon import FIELD_P, poseidon
from .prover import (Artifacts, circuits_dir, Toolchain, ZkError, _run, dev_ptau,
                     groth16_setup, prove, verify, witness)

DOMAIN_ID = 10
DOMAIN_NF = 12


def note_circuit():
    """Resolved lazily so installed wheels find bundled circuits (see tcl.resources)."""
    return circuits_dir() / "note" / "note_deposit.circom"


# snarkjs public-signal order (all public inputs, no outputs)
PUBLIC_SIGNALS = ("id", "denom", "era", "ctx", "recipient", "nf")


def _bounded(name: str, v: int, bits: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v >= 1 << bits:
        raise ValueError(f"{name} must be an int in [0, 2^{bits})")
    return v


def note_ctx(chain_id: int, contract: int | str) -> int:
    """ctx = chainId * 2^160 + contract address (unique per chain+contract, < p)."""
    addr = int(contract, 16) if isinstance(contract, str) else contract
    _bounded("chain_id", chain_id, 64)
    _bounded("contract", addr, 160)
    return (chain_id << 160) | addr


def pk_hash(secret: int) -> int:
    if not 0 < secret < FIELD_P:
        raise ValueError("note secret must be in (0, p)")
    return poseidon([secret])


def note_id(secret: int, puf_hash: int, denom: int, serial: int) -> int:
    _bounded("denom", denom, 32)
    _bounded("serial", serial, 64)
    if not 0 <= puf_hash < FIELD_P:
        raise ValueError("puf_hash outside field")
    return poseidon([DOMAIN_ID, pk_hash(secret), puf_hash, poseidon([denom, serial])])


def nullifier(nid: int, era: int, ctx: int) -> int:
    """nf = H(12, id, k, ctx)."""
    _bounded("era", era, 64)
    if not (0 <= nid < FIELD_P and 0 <= ctx < FIELD_P):
        raise ValueError("id/ctx outside field")
    return poseidon([DOMAIN_NF, nid, era, ctx])


@dataclass(frozen=True)
class SimulatedNote:
    """Software stand-in for a physical note's SE + PUF."""
    secret: int
    puf_hash: int
    denom: int
    serial: int

    @property
    def id(self) -> int:
        return note_id(self.secret, self.puf_hash, self.denom, self.serial)


def deposit_input(note: SimulatedNote, era: int, ctx: int, recipient: int | str) -> dict[str, Any]:
    rec = int(recipient, 16) if isinstance(recipient, str) else recipient
    _bounded("recipient", rec, 160)
    nid = note.id
    return {
        "id": str(nid), "denom": str(note.denom), "era": str(era), "ctx": str(ctx),
        "recipient": str(rec), "nf": str(nullifier(nid, era, ctx)),
        "noteSecret": str(note.secret), "pufHash": str(note.puf_hash),
        "serial": str(note.serial),
    }


def compile_note_circuit(out_dir: Path, tc: Optional[Toolchain] = None) -> Artifacts:
    tc = tc or Toolchain.discover()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([tc.circom, note_circuit(), "--r1cs", "--wasm", "--O2", "-l", tc.circomlib,
          "-o", out_dir], "note-compile")
    return Artifacts(0, out_dir, out_dir / "note_deposit.r1cs",
                     out_dir / "note_deposit_js" / "note_deposit.wasm")


def setup_note_circuit(out_dir: Path, ptau_power: int = 12,
                       tc: Optional[Toolchain] = None) -> Artifacts:
    """DEV-ONLY trusted setup (single-party randomness)."""
    art = compile_note_circuit(out_dir, tc)
    ptau = dev_ptau(ptau_power, Path(out_dir) / f"dev_{ptau_power}.ptau", tc)
    return groth16_setup(art, ptau, tc)


def prove_deposit(art: Artifacts, note: SimulatedNote, era: int, ctx: int,
                  recipient: int | str, work: Path,
                  tc: Optional[Toolchain] = None) -> tuple[dict, list[str]]:
    if art.vkey is None:
        raise ZkError("artifacts have no vkey; run setup_note_circuit first")
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    inp = deposit_input(note, era, ctx, recipient)
    w = witness(art, inp, work / f"note_{era}.wtns", tc)
    proof, public = prove(art, w, tc)
    expected = [inp[k] for k in PUBLIC_SIGNALS]
    if public != expected:
        raise ZkError(f"public signals mismatch: {public} != {expected}")
    if not verify(art.vkey, proof, public, work / "v", tc):
        raise ZkError("self-verification of note deposit proof failed")
    return proof, public


def dump(path: Path, obj: Any) -> None:
    Path(path).write_text(json.dumps(obj, indent=1))
