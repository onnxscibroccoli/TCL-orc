#!/usr/bin/env python3
"""Fail-closed storage-layout check for upgradeable contracts.

Compares `forge inspect <C> storageLayout` against the committed snapshot in
docs/storage-layout/<C>.json. Existing slots must keep label, slot, offset and
type; appending new variables after the last slot is allowed. Run with
--update to rewrite the snapshot (only for intentional, reviewed changes).
"""
import json
import subprocess
import sys
from pathlib import Path

CONTRACTS = ["CirculatoryEngine", "NoteController"]
SNAP = Path(__file__).resolve().parents[2] / "docs" / "storage-layout"


def layout(name: str) -> list[dict]:
    out = subprocess.run(
        ["forge", "inspect", name, "storageLayout", "--json"],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    types = data.get("types") or {}
    return [
        {
            "label": s["label"],
            "slot": s["slot"],
            "offset": s["offset"],
            "type": types.get(s["type"], {}).get("label", s["type"]),
        }
        for s in data["storage"]
    ]


def main() -> int:
    update = "--update" in sys.argv
    SNAP.mkdir(parents=True, exist_ok=True)
    rc = 0
    for name in CONTRACTS:
        cur = layout(name)
        path = SNAP / f"{name}.json"
        if update or not path.exists():
            if not update:
                print(f"FAIL {name}: no snapshot at {path} (run with --update)")
                rc = 1
                continue
            path.write_text(json.dumps(cur, indent=2) + "\n")
            print(f"updated {path}")
            continue
        old = json.loads(path.read_text())
        if cur[: len(old)] != old:
            print(f"FAIL {name}: storage layout changed incompatibly")
            for i, o in enumerate(old):
                n = cur[i] if i < len(cur) else None
                if n != o:
                    print(f"  slot entry {i}: {o} -> {n}")
            rc = 1
        else:
            print(f"ok {name}: {len(old)} entries preserved, {len(cur) - len(old)} appended")
    return rc


if __name__ == "__main__":
    sys.exit(main())
