#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline, fail-closed check of vendor/: every manifest entry present with the right
sha256 and size, a license file for each, no orphan blobs. Exit 1 on any mismatch.

  python scripts/bundle_verify.py [--require-assets]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tcl import offline  # noqa: E402

if __name__ == "__main__":
    problems, stats = offline.verify(ROOT, require_assets="--require-assets" in sys.argv)
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    print(f"bundle verify: {stats['git']} in-git blobs, {stats['release_present']} release "
          f"assets present, {stats['release_missing']} not fetched; "
          + ("FAILED" if problems else "OK"))
    raise SystemExit(1 if problems else 0)
