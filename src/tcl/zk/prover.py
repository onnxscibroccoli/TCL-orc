"""Circom/snarkjs Groth16 pipeline for the TCL state-transition circuit.

Pipeline: compile (circom -> r1cs + wasm) -> witness (snarkjs wtns calculate)
-> setup (groth16 setup with a *development* powers-of-tau) -> prove -> verify.

Toolchain discovery (env overrides first):
  TCL_CIRCOM     path to circom 2.1+ binary            (default: PATH)
  TCL_SNARKJS    path to snarkjs CLI                    (default: PATH, ./node_modules/.bin)
  TCL_CIRCOMLIB  directory containing ``circomlib/``    (default: <repo>/node_modules)

The dev ptau and zkeys produced here use local, single-party randomness and are
NOT suitable for production; a real deployment needs a public ceremony ptau and
a multi-party phase-2 contribution.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..crypto.poseidon import FIELD_P, hash_account_id
from ..ledger.demurrage_fixed import MAX_STEP_SECONDS
from ..storage.db import VAULT
from ..ledger.poseidon_tree import account_leaf, compute_root
from ..utils.logger import get_logger
from ..resources import checkout_root

log = get_logger("tcl.zk")

REPO_ROOT = checkout_root()  # dev checkout (node_modules, fixtures); may not exist in a wheel


def circuits_dir() -> Path:
    """Circuit sources from the checkout or the wheel's bundled data (fail-closed)."""
    from ..resources import ResourcesMissing, resource_root
    try:
        return resource_root() / "circuits"
    except ResourcesMissing as e:
        raise ToolchainMissing(str(e)) from e
MAIN_TEMPLATE = "StateTransition"
# order of snarkjs public signals: outputs first, then public inputs
PUBLIC_SIGNALS = ("newRoot", "fee", "oldRoot", "newTime")


class ZkError(RuntimeError):
    """A pipeline step failed (bad witness, failed verification, tool error)."""


class ToolchainMissing(ZkError):
    pass


@dataclass(frozen=True)
class Toolchain:
    circom: str
    snarkjs: str
    circomlib: Path

    @classmethod
    def discover(cls) -> "Toolchain":
        circom = os.environ.get("TCL_CIRCOM") or shutil.which("circom")
        snarkjs = (os.environ.get("TCL_SNARKJS") or shutil.which("snarkjs")
                   or _existing(REPO_ROOT / "node_modules/.bin/snarkjs"))
        lib = Path(os.environ.get("TCL_CIRCOMLIB", REPO_ROOT / "node_modules"))
        missing = [n for n, v in (("circom", circom), ("snarkjs", snarkjs)) if not v]
        if not (lib / "circomlib/circuits/poseidon.circom").exists():
            missing.append(f"circomlib (looked in {lib})")
        if missing:
            raise ToolchainMissing("missing ZK toolchain: " + ", ".join(missing))
        ver = subprocess.run([circom, "--version"], capture_output=True, text=True).stdout
        parts = ver.strip().split()[-1].split(".") if ver.strip() else ["0", "0"]
        if (int(parts[0]), int(parts[1])) < (2, 1):
            raise ToolchainMissing(f"circom >= 2.1 required, got {ver.strip()!r}")
        return cls(circom=circom, snarkjs=snarkjs, circomlib=lib)


def _existing(p: Path) -> Optional[str]:
    return str(p) if p.exists() else None


def _run(cmd: list[str], what: str, cwd: Optional[Path] = None) -> str:
    log.info("zk.run", extra={"step": what, "cmd": " ".join(map(str, cmd[:3]))})
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise ZkError(f"{what} failed (exit {r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    return r.stdout


@dataclass(frozen=True)
class Artifacts:
    depth: int
    out_dir: Path
    r1cs: Path
    wasm: Path
    zkey: Optional[Path] = None
    vkey: Optional[Path] = None


def compile_circuit(depth: int, out_dir: Path, tc: Optional[Toolchain] = None) -> Artifacts:
    """Compile StateTransition(depth) to r1cs + wasm witness generator."""
    if not 1 <= depth <= 32:
        raise ValueError("depth must be in 1..32")
    tc = tc or Toolchain.discover()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"state_transition_d{depth}"
    main = out_dir / f"{name}.circom"
    main.write_text(
        "pragma circom 2.1.6;\n"
        f'include "{(circuits_dir() / "puf_verify.circom").as_posix()}";\n'
        f"component main {{public [oldRoot, newTime]}} = {MAIN_TEMPLATE}({depth});\n")
    _run([tc.circom, main, "--r1cs", "--wasm", "--O2", "-l", tc.circomlib, "-l", circuits_dir(),
          "-o", out_dir], "compile")
    return Artifacts(depth, out_dir, out_dir / f"{name}.r1cs",
                     out_dir / f"{name}_js" / f"{name}.wasm")


def dev_ptau(power: int, path: Path, tc: Optional[Toolchain] = None) -> Path:
    """Create (or reuse) a DEV-ONLY prepared powers-of-tau file of size 2^power."""
    path = Path(path)
    if path.exists():
        return path
    tc = tc or Toolchain.discover()
    path.parent.mkdir(parents=True, exist_ok=True)
    p0, p1 = path.with_suffix(".0"), path.with_suffix(".1")
    _run([tc.snarkjs, "powersoftau", "new", "bn128", str(power), p0], "ptau-new")
    _run([tc.snarkjs, "powersoftau", "contribute", p0, p1, "--name=tcl-dev",
          f"-e={secrets.token_hex(32)}"], "ptau-contribute")
    _run([tc.snarkjs, "powersoftau", "prepare", "phase2", p1, path], "ptau-prepare")
    p0.unlink(missing_ok=True)
    p1.unlink(missing_ok=True)
    return path


def groth16_setup(art: Artifacts, ptau: Path, tc: Optional[Toolchain] = None) -> Artifacts:
    tc = tc or Toolchain.discover()
    z0 = art.out_dir / "circuit_0000.zkey"
    zkey = art.out_dir / "circuit_final.zkey"
    vkey = art.out_dir / "verification_key.json"
    _run([tc.snarkjs, "groth16", "setup", art.r1cs, ptau, z0], "groth16-setup")
    _run([tc.snarkjs, "zkey", "contribute", z0, zkey, "--name=tcl-dev",
          f"-e={secrets.token_hex(32)}"], "zkey-contribute")
    _run([tc.snarkjs, "zkey", "export", "verificationkey", zkey, vkey], "export-vkey")
    z0.unlink(missing_ok=True)
    return Artifacts(art.depth, art.out_dir, art.r1cs, art.wasm, zkey, vkey)


def witness(art: Artifacts, inputs: dict[str, Any], out: Path,
            tc: Optional[Toolchain] = None) -> Path:
    """Compute a witness; raises ZkError if any circuit constraint fails."""
    tc = tc or Toolchain.discover()
    inp = Path(out).with_suffix(".input.json")
    inp.write_text(json.dumps(inputs))
    _run([tc.snarkjs, "wtns", "calculate", art.wasm, inp, out], "witness")
    return Path(out)


def prove(art: Artifacts, wtns: Path, tc: Optional[Toolchain] = None) -> tuple[dict, list[str]]:
    if art.zkey is None:
        raise ZkError("artifacts have no zkey; run groth16_setup first")
    tc = tc or Toolchain.discover()
    proof_p = Path(wtns).with_suffix(".proof.json")
    pub_p = Path(wtns).with_suffix(".public.json")
    _run([tc.snarkjs, "groth16", "prove", art.zkey, wtns, proof_p, pub_p], "prove")
    return json.loads(proof_p.read_text()), json.loads(pub_p.read_text())


def verify(vkey: Path, proof: dict, public: list[str], work: Path,
           tc: Optional[Toolchain] = None) -> bool:
    """True iff snarkjs accepts the proof. Never raises on a bad proof."""
    tc = tc or Toolchain.discover()
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    pp, sp = work / "verify.proof.json", work / "verify.public.json"
    pp.write_text(json.dumps(proof))
    sp.write_text(json.dumps(public))
    r = subprocess.run([tc.snarkjs, "groth16", "verify", str(vkey), str(sp), str(pp)],
                       capture_output=True, text=True)
    ok = r.returncode == 0 and "OK!" in r.stdout
    log.info("zk.verify", extra={"ok": ok})
    return ok


def export_verifier(zkey: Path, out: Path, contract_name: str = "StateVerifier",
                    tc: Optional[Toolchain] = None) -> Path:
    tc = tc or Toolchain.discover()
    _run([tc.snarkjs, "zkey", "export", "solidityverifier", zkey, out], "export-verifier")
    src = Path(out).read_text().replace("contract Groth16Verifier", f"contract {contract_name}")
    Path(out).write_text(src)
    return Path(out)


def solidity_calldata(proof: dict, public: list[str]) -> dict:
    """Groth16 proof in the (a, b, c, pubSignals) layout of the snarkjs verifier."""
    def h(x: str) -> str:
        return hex(int(x))
    return {
        "a": [h(proof["pi_a"][0]), h(proof["pi_a"][1])],
        # snarkjs/EIP-197: G2 coordinates are passed as [x1, x0], [y1, y0]
        "b": [[h(proof["pi_b"][0][1]), h(proof["pi_b"][0][0])],
              [h(proof["pi_b"][1][1]), h(proof["pi_b"][1][0])]],
        "c": [h(proof["pi_c"][0]), h(proof["pi_c"][1])],
        "pubSignals": [h(p) for p in public],
    }


VAULT_INDEX = 0


def build_transition_input(ledger, account: str, now: int) -> tuple[dict[str, Any], dict[str, int]]:
    """Settle ``account`` on the ledger and return (circuit input, expected public values).

    The circuit proves the whole settle: the account leaf update followed by the
    vault leaf update (vault += fee), so the proof's newRoot must equal
    ``ledger.root()`` after settle. Checked fail-closed.
    """
    if ledger.mode != "fixed":
        raise ZkError("circuit implements the 'fixed' demurrage mode only")
    if account == VAULT:
        raise ZkError("the vault cannot be settled as an account")
    if ledger.s.leaf_index(VAULT) != VAULT_INDEX:
        raise ZkError(f"vault must occupy leaf index {VAULT_INDEX} (legacy store?)")
    a = ledger.s.get_account(account)
    if a is None:
        raise KeyError(account)
    old_bal, old_t = a
    dt = now - old_t
    if not 0 <= dt <= MAX_STEP_SECONDS:
        raise ZkError(f"dt={dt} outside single-step range 0..{MAX_STEP_SECONDS}")
    pre = ledger.proof(account)
    if pre.index == VAULT_INDEX:
        raise ZkError("account occupies the vault leaf index")
    old_root = ledger.root()
    if pre.root != old_root or pre.leaf != account_leaf(account, old_bal, old_t):
        raise ZkError("ledger proof inconsistent with stored account")
    va = ledger.s.get_account(VAULT)
    v_exists, (v_bal, v_t) = (1, va) if va is not None else (0, (0, 0))
    new_bal = ledger.settle(account, now)
    fee = ledger.vault() - v_bal
    if old_bal - new_bal != fee:
        raise ZkError("settle did not conserve value")
    mid_root = compute_root(account_leaf(account, new_bal, now), pre.index, pre.siblings)
    # vault siblings are identical in the intermediate and final trees
    vp = ledger.tree.proof(VAULT_INDEX)
    v_old_leaf = account_leaf(VAULT, v_bal, v_t) if v_exists else 0
    if compute_root(v_old_leaf, VAULT_INDEX, vp.siblings) != mid_root:
        raise ZkError("vault path inconsistent with intermediate root")
    new_root = ledger.root()
    v_new_leaf = account_leaf(VAULT, v_bal + fee, now) if fee else v_old_leaf
    if compute_root(v_new_leaf, VAULT_INDEX, vp.siblings) != new_root:
        raise ZkError("ledger root after settle disagrees with circuit model")
    inputs = {
        "oldRoot": str(old_root), "newTime": str(now),
        "accountId": str(hash_account_id(account)), "oldBalance": str(old_bal),
        "oldTime": str(old_t),
        "pathElements": [str(s) for s in pre.siblings],
        "pathIndices": [str(b) for b in pre.path_bits()],
        "vaultExists": str(v_exists), "vaultBalance": str(v_bal), "vaultTime": str(v_t),
        "vaultPathElements": [str(s) for s in vp.siblings],
    }
    expected = {"newRoot": new_root, "fee": fee, "oldRoot": old_root, "newTime": now}
    assert all(0 <= v < FIELD_P for v in expected.values())
    return inputs, expected
