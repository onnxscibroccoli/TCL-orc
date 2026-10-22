#!/usr/bin/env python3
"""Record immutable provenance of key artifacts."""
import hashlib
import json
from pathlib import Path

ARTIFACTS = [
    "ledger/demurrage.py",
    "ledger/test_demurrage.py",
    "contracts/CirculatoryEngine.sol",
    "contracts/PhysicalMintController.sol",
    "Containerfile",
]

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

def main():
    root = Path(__file__).resolve().parent.parent
    manifest = {
        "artifacts": {
            rel: sha256(root / rel) for rel in ARTIFACTS if (root / rel).exists()
        }
    }
    out = root / "provenance.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
