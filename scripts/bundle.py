#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Maintainer tool for the offline bundle (vendor/). See docs/OFFLINE.md.

  python scripts/bundle.py build    # ONLINE, once: pip-download pinned wheels for
                                    # linux-x86_64 CPython 3.11-3.13 into the sha256 store,
                                    # extract licenses, (re)write vendor/manifest.json
  python scripts/bundle.py fetch    # ONLINE, optional: download release assets (circom,
                                    # node, snarkjs, ...) into vendor/assets, hash-checked
  python scripts/bundle.py verify   # OFFLINE: same as scripts/bundle_verify.py
  python scripts/bundle.py list     # print the manifest

Nothing in the runtime or CI ever calls `build`/`fetch`; installs use only the store.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tcl import offline  # noqa: E402  (stdlib-only module)

VENDOR = ROOT / "vendor"
PYTHONS = ["3.11", "3.12", "3.13"]
PLATFORMS = ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"]
LICENSE_RE = re.compile(r"\.dist-info/(licenses/.*|LICEN[CS]E.*|COPYING.*)$", re.I)


def _wheel_meta(path: Path) -> tuple[str, str, str]:
    with zipfile.ZipFile(path) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        text = z.read(meta).decode("utf-8", "replace").split("\n\n", 1)[0]
        lic_names = [n for n in z.namelist() if LICENSE_RE.search(n)]
        lic_text = "\n\n".join(f"==> {n} <==\n" + z.read(n).decode("utf-8", "replace")
                               for n in lic_names)
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fields.setdefault(k.strip(), []).append(v.strip())
    lic = (fields.get("License-Expression") or [""])[0]
    if not lic:
        cls = [c.split("::")[-1].strip() for c in fields.get("Classifier", [])
               if c.startswith("License ::")]
        lic = " OR ".join(cls) or (fields.get("License") or ["UNKNOWN"])[0][:80]
    return lic, lic_text, fields.get("Name", ["?"])[0]


def _tags(fname: str) -> tuple[list[str], str]:
    parts = fname[:-4].split("-")
    py, plat = parts[-3], parts[-1]
    pys = sorted(set(py.split(".")))
    pys = ["py3" if p in ("py3", "py2") else p for p in pys]
    platform = "any" if plat == "any" else "linux_x86_64"
    return sorted(set(pys)), platform


def build(_: argparse.Namespace) -> int:
    reqs = VENDOR / "requirements-offline.txt"
    store = VENDOR / "store" / "sha256"
    store.mkdir(parents=True, exist_ok=True)
    (VENDOR / "licenses").mkdir(exist_ok=True)
    entries: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as td:
        for py in PYTHONS:
            d = Path(td) / py
            cmd = [sys.executable, "-m", "pip", "download", "--only-binary", ":all:",
                   "--no-deps", "--implementation", "cp", "--python-version", py,
                   "-r", str(reqs), "-d", str(d)]
            for p in PLATFORMS:
                cmd += ["--platform", p]
            subprocess.run(cmd, check=True)
            for whl in sorted(d.glob("*.whl")):
                if whl.name in entries:
                    continue
                digest = offline.sha256_file(whl)
                shutil.copyfile(whl, store / digest)
                lic, lic_text, name = _wheel_meta(whl)
                norm = re.sub(r"[-_.]+", "-", name).lower()
                version = whl.name.split("-")[1]
                lic_file = VENDOR / "licenses" / f"{norm}-{version}.txt"
                lic_file.write_text(lic_text or f"{name} {version}: license '{lic}' "
                                    "(no license file shipped in the wheel)\n")
                pys, plat = _tags(whl.name)
                entries[whl.name] = {
                    "name": norm, "version": version, "kind": "wheel", "storage": "git",
                    "filename": whl.name, "python": pys, "platform": plat,
                    "sha256": digest, "size": whl.stat().st_size, "license": lic,
                    "license_file": str(lic_file.relative_to(ROOT)),
                    "source_url": f"https://pypi.org/project/{norm}/{version}/",
                }
    keep = {e["sha256"] for e in entries.values()}
    for blob in store.iterdir():
        if blob.name not in keep:
            blob.unlink()
    assets = json.loads((VENDOR / "release-assets.json").read_text())
    arts = sorted(entries.values(), key=lambda e: e["filename"])
    for a in assets["assets"]:
        e = {k: a[k] for k in ("name", "version", "kind", "platform", "filename", "sha256",
                               "size", "license", "license_file", "purpose")}
        e.update(storage="release", source_url=a["upstream_url"],
                 release_url=f"https://github.com/onnxscibroccoli/TCL-orc/releases/download/"
                             f"{assets['release_tag']}/{a['filename']}")
        arts.append(e)
    manifest = {"schema": offline.SCHEMA, "target": {"os": "linux", "arch": "x86_64",
                "python": PYTHONS}, "requirements": "vendor/requirements-offline.txt",
                "artifacts": arts}
    (VENDOR / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"bundle: {len(entries)} wheels in store, {len(assets['assets'])} release assets")
    return verify(argparse.Namespace(require_assets=False))


def fetch(_: argparse.Namespace) -> int:
    m = offline.load_manifest(ROOT)
    bad = 0
    for e in m["artifacts"]:
        if e["storage"] != "release":
            continue
        dst = offline.blob_path(ROOT, e)
        if dst.is_file() and offline.sha256_file(dst) == e["sha256"]:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        for url in (e.get("release_url"), e["source_url"]):
            if not url:
                continue
            try:
                tmp = dst.with_suffix(".part")
                with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f)
            except Exception as exc:  # try the next mirror
                print(f"fetch: {e['filename']}: {url}: {exc}", file=sys.stderr)
                continue
            if offline.sha256_file(tmp) == e["sha256"]:
                tmp.replace(dst)
                print(f"fetch: {e['filename']} OK ({url})")
                break
            tmp.unlink()
            print(f"fetch: {e['filename']}: HASH MISMATCH from {url}", file=sys.stderr)
        else:
            bad += 1
    return 1 if bad else 0


def verify(a: argparse.Namespace) -> int:
    problems, stats = offline.verify(ROOT, require_assets=a.require_assets)
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    print(f"bundle verify: {stats['git']} in-git blobs, {stats['release_present']} release "
          f"assets present, {stats['release_missing']} not fetched; "
          + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


def list_(_: argparse.Namespace) -> int:
    for e in offline.load_manifest(ROOT)["artifacts"]:
        print(f"{e['storage']:7} {e['kind']:9} {e['name']:20} {e['version']:10} "
              f"{e['license'][:28]:28} {e['sha256'][:12]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").set_defaults(fn=build)
    sub.add_parser("fetch").set_defaults(fn=fetch)
    v = sub.add_parser("verify")
    v.add_argument("--require-assets", action="store_true")
    v.set_defaults(fn=verify)
    sub.add_parser("list").set_defaults(fn=list_)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
