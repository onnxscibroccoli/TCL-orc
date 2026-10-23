#!/usr/bin/env python3
"""Record immutable provenance of key artifacts."""
import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path

ARTIFACTS = [
    "ledger/demurrage.py",
    "ledger/demurrage_fixed.py",
    "ledger/nullifiers.py",
    "src/tcl/ledger/demurrage.py",
    "src/tcl/ledger/demurrage_fixed.py",
    "src/tcl/ledger/nullifiers.py",
    "src/tcl/ledger/storage.py",
    "src/tcl/storage/db.py",
    "src/tcl/crypto/poseidon.py",
    "src/tcl/crypto/poseidon_bn254_t2_t5.json",
    "src/tcl/ledger/poseidon_tree.py",
    "src/tcl/config.py",
    "src/tcl/utils/logger.py",
    "config.yaml",
    ".env.example",
    "tests/test_poseidon.py",
    "tests/test_poseidon_tree.py",
    "tests/test_ledger_state.py",
    "tests/test_crash_recovery.py",
    "tests/test_config.py",
    "tests/test_cli_tree.py",
    "test/unit/test_ledger.py",
    "src/tcl/ledger/service.py",
    "src/tcl/orchestrator/state.py",
    "src/tcl/worktree/manager.py",
    "src/tcl/cli.py",
    "src/tcl/logs.py",
    "tests/test_storage.py",
    "tests/test_state_machine.py",
    "tests/test_worktree.py",
    "tests/test_cli.py",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    "ledger/test_demurrage.py",
    "ledger/test_demurrage_fixed.py",
    "ledger/test_nullifiers.py",
    "contracts/CirculatoryEngine.sol",
    "contracts/PhysicalMintController.sol",
    "Containerfile",
    "circuits/puf_verify.circom",
    "circuits/lib/merkle.circom",
    "circuits/lib/fixedpoint.circom",
    "circuits/main/state_transition_d4.circom",
    "src/tcl/zk/prover.py",
    "contracts/verifiers/StateVerifier.sol",
    "test/contracts/CirculatoryEngine.t.sol",
    "test/contracts/fixtures/state_transition.json",
    "test/contracts/fixtures/verification_key.json",
    "tests/test_zk_prover.py",
    "tests/test_circuit_hygiene.py",
    "scripts/gen_zk_fixture.py",
    "foundry.toml",
    "package.json",
    "package-lock.json",
]

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

def main():
    root = Path(__file__).resolve().parent.parent
    def git(*a):
        return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True).stdout.strip()

    manifest = {
        "base_commit": os.environ.get("TCL_BASE_COMMIT") or git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "artifacts": {
            rel: sha256(root / rel) for rel in ARTIFACTS if (root / rel).exists()
        },
    }
    tr = os.environ.get("TCL_TEST_RESULTS")
    if tr:
        manifest["test_results"] = json.loads(tr)
        manifest["test_results"]["python"] = platform.python_version()
    out = root / "provenance.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
