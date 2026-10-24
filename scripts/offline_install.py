#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bootstrap install from vendor/ with no network (stdlib only; same as `tcl offline install`).

  python3 scripts/offline_install.py [--python /path/to/python] [--runtime-only] [--no-editable]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tcl import offline  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--python", default=None)
ap.add_argument("--runtime-only", action="store_true")
ap.add_argument("--no-editable", action="store_true")
a = ap.parse_args()
raise SystemExit(offline.install(ROOT, python=a.python, editable=not a.no_editable,
                                 test=not a.runtime_only))
