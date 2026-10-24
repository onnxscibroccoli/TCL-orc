# SPDX-License-Identifier: Apache-2.0
"""Offline bundle: content-addressed vendor store + manifest (stdlib only).

Layout (docs/14_tcl_localnet_plan.md section 5, docs/OFFLINE.md):

    vendor/manifest.json            every vendored artifact: name, version, kind, platform,
                                    python tags, sha256, size, license, upstream URL
                                    (provenance only, never fetched at install time)
    vendor/store/sha256/<hex>       blob whose file name IS its sha256 (storage = "git")
    vendor/licenses/<name>.txt      license text shipped with each artifact
    vendor/assets/sha256/<hex>      large blobs (storage = "release"), gitignored; fetched
                                    once from the release asset or upstream, then verified

This module must import nothing outside the standard library: it runs *before* any
wheel is installed (scripts/bundle_verify.py, scripts/offline_install.py, `make offline`).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = 1
STORAGES = ("git", "release")
KINDS = ("wheel", "binary", "source", "npm", "toolchain")


def repo_root(start: Optional[Path] = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for c in [p, *p.parents]:
        if (c / "vendor" / "manifest.json").is_file():
            return c
    raise FileNotFoundError("vendor/manifest.json not found (run from a TCL checkout)")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Problem:
    entry: str
    what: str

    def __str__(self) -> str:
        return f"{self.entry}: {self.what}"


def load_manifest(root: Path) -> dict:
    m = json.loads((root / "vendor" / "manifest.json").read_text())
    if m.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema {m.get('schema')!r}")
    return m


def blob_path(root: Path, e: dict) -> Path:
    base = "store" if e["storage"] == "git" else "assets"
    return root / "vendor" / base / "sha256" / e["sha256"]


def verify(root: Path, require_assets: bool = False) -> tuple[list[Problem], dict]:
    """Check every manifest entry. Fails closed: any mismatch is a Problem."""
    m = load_manifest(root)
    problems: list[Problem] = []
    stats = {"git": 0, "release_present": 0, "release_missing": 0}
    seen: set[str] = set()
    names: set[tuple] = set()
    for e in m["artifacts"]:
        label = e.get("filename") or e.get("name", "?")
        missing = [k for k in ("name", "version", "kind", "storage", "sha256", "size",
                               "license", "filename") if k not in e]
        if missing:
            problems.append(Problem(label, f"missing fields {missing}"))
            continue
        if e["storage"] not in STORAGES or e["kind"] not in KINDS:
            problems.append(Problem(label, "bad storage/kind"))
            continue
        key = (e["filename"], e["sha256"])
        if key in names:
            problems.append(Problem(label, "duplicate entry"))
        names.add(key)
        lic = e.get("license_file")
        if not lic or not (root / lic).is_file():
            problems.append(Problem(label, f"license file missing: {lic}"))
        p = blob_path(root, e)
        if e["storage"] == "git":
            seen.add(e["sha256"])
            stats["git"] += 1
            if not p.is_file():
                problems.append(Problem(label, f"blob missing: {p.relative_to(root)}"))
                continue
        elif not p.is_file():
            stats["release_missing"] += 1
            if require_assets:
                problems.append(Problem(label, "release asset not fetched "
                                        "(python scripts/bundle.py fetch)"))
            continue
        else:
            stats["release_present"] += 1
        if p.stat().st_size != e["size"]:
            problems.append(Problem(label, f"size {p.stat().st_size} != {e['size']}"))
        got = sha256_file(p)
        if got != e["sha256"]:
            problems.append(Problem(label, f"sha256 mismatch: {got} != {e['sha256']}"))
    store = root / "vendor" / "store" / "sha256"
    if store.is_dir():
        for blob in store.iterdir():
            if blob.name not in seen:
                problems.append(Problem(blob.name, "orphan blob in store (not in manifest)"))
    return problems, stats


def wheels_for(m: dict, py: str, platform_tag: str = "linux_x86_64") -> list[dict]:
    """Wheel entries usable on CPython ``py`` (e.g. "3.12") on ``platform_tag``."""
    cp = "cp" + py.replace(".", "")
    out = []
    for e in m["artifacts"]:
        if e["kind"] != "wheel":
            continue
        pys = e.get("python", ["py3"])
        if "py3" in pys or cp in pys:
            if e.get("platform", "any") in ("any", platform_tag):
                out.append(e)
    return out


def materialize_wheelhouse(root: Path, dest: Path, py: Optional[str] = None) -> list[Path]:
    """Copy the hash-verified wheels for this interpreter into ``dest`` under real names."""
    py = py or f"{sys.version_info.major}.{sys.version_info.minor}"
    m = load_manifest(root)
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for e in wheels_for(m, py):
        src = blob_path(root, e)
        if sha256_file(src) != e["sha256"]:
            raise RuntimeError(f"{e['filename']}: sha256 mismatch, refusing to install")
        dst = dest / e["filename"]
        shutil.copyfile(src, dst)
        out.append(dst)
    if not out:
        raise RuntimeError(f"no bundled wheels for CPython {py}")
    return out


def install(root: Path, python: Optional[str] = None, editable: bool = True,
            extra: Iterable[str] = (), test: bool = True) -> int:
    """``pip install --no-index`` from the verified bundle; never touches the network."""
    problems, _ = verify(root)
    if problems:
        for p in problems:
            print(f"bundle: {p}", file=sys.stderr)
        return 1
    py = python or sys.executable
    ver = subprocess.run([py, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                         check=True, capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="tcl-wheelhouse-") as td:
        wh = Path(td)
        materialize_wheelhouse(root, wh, ver)
        base = [py, "-m", "pip", "install", "--no-index", "--find-links", str(wh),
                "--disable-pip-version-check", "--no-cache-dir"]
        reqs = root / "vendor" / "requirements-offline.txt"
        steps = []
        if test:
            steps.append(base + ["-r", str(reqs)])
        else:
            steps.append(base + ["setuptools"])
        steps.append(base + ["--no-build-isolation"] + (["-e"] if editable else [])
                     + [str(root)] + list(extra))
        for cmd in steps:
            r = subprocess.run(cmd, env=_offline_env())
            if r.returncode:
                return r.returncode
    return 0


def _offline_env() -> dict:
    import os
    env = dict(os.environ)
    env.update({"PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1"})
    for k in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
        env.pop(k, None)
    return env
