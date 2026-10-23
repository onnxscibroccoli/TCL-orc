#!/usr/bin/env python3
"""Regenerate the note-deposit verifier + Foundry proof fixtures (DEV setup only).

Usage: python scripts/gen_note_fixtures.py [build_dir]
Writes contracts/verifiers/NoteDepositVerifier.sol and
test/contracts/fixtures/note_deposit.json. The zkey is dev-only (single-party).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tcl.zk.note import SimulatedNote, note_ctx, prove_deposit, setup_note_circuit
from tcl.zk.prover import export_verifier, solidity_calldata

REPO = Path(__file__).resolve().parents[1]
CHAIN_ID = 31337
CONTROLLER = "0xC0ffee0000000000000000000000000000000001"  # fixed proxy address in tests
OTHER = "0xC0ffee0000000000000000000000000000000002"
RECIPIENT = "0x0000000000000000000000000000000000000B0b"
NOTE = SimulatedNote(secret=0x5EC12E7, puf_hash=0x9F0F, denom=100, serial=7)


def main() -> None:
    build = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/tcl-note-build")
    art = setup_note_circuit(build)
    export_verifier(art.zkey, REPO / "contracts/verifiers/NoteDepositVerifier.sol",
                    "NoteDepositVerifier")
    (REPO / "circuits/note/verification_key.json").write_text(art.vkey.read_text())
    ctx = note_ctx(CHAIN_ID, CONTROLLER)
    out = {"controller": CONTROLLER, "recipient": RECIPIENT, "chainId": CHAIN_ID,
           "id": hex(NOTE.id), "denom": NOTE.denom}
    for name, era, c in (("era0", 0, ctx), ("era1", 1, ctx),
                         ("wrongCtx", 0, note_ctx(CHAIN_ID, OTHER))):
        proof, public = prove_deposit(art, NOTE, era, c, RECIPIENT, build / name)
        out[name] = solidity_calldata(proof, public)
    (REPO / "test/contracts/fixtures/note_deposit.json").write_text(json.dumps(out, indent=1))
    print("ok", out["id"])


if __name__ == "__main__":
    main()
