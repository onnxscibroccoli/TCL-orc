"""Offline bundle (vendor/) integrity + no-ethereum gate. No network, no toolchain needed."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from tcl import offline

ROOT = Path(__file__).resolve().parents[1]


def _gate():
    spec = importlib.util.spec_from_file_location("gate", ROOT / "scripts/check_no_ethereum.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_bundle_verifies():
    problems, stats = offline.verify(ROOT)
    assert problems == [], [str(p) for p in problems]
    assert stats["git"] > 0


def test_bundle_covers_every_pinned_requirement_for_each_python():
    m = offline.load_manifest(ROOT)
    reqs = {re.sub(r"[-_.]+", "-", ln.split("==")[0]).lower(): ln.split("==")[1]
            for ln in (ROOT / "vendor/requirements-offline.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}
    for py in m["target"]["python"]:
        have = {e["name"]: e["version"] for e in offline.wheels_for(m, py)}
        assert have == reqs, py


def test_runtime_deps_of_pyproject_are_bundled():
    m = offline.load_manifest(ROOT)
    names = {e["name"] for e in m["artifacts"]}
    for dep in ("typer", "pydantic", "pydantic-settings", "pyyaml", "pydantic-core"):
        assert dep in names


def test_large_artifacts_are_release_assets_not_git():
    m = offline.load_manifest(ROOT)
    for e in m["artifacts"]:
        if e["kind"] != "wheel":
            assert e["storage"] == "release" and len(e["sha256"]) == 64, e["name"]
    for blob in (ROOT / "vendor/store/sha256").iterdir():
        assert blob.stat().st_size < 5 * 2**20, blob


def _mini(tmp: Path, payload: bytes = b"hello wheel") -> tuple[Path, dict]:
    (tmp / "vendor/store/sha256").mkdir(parents=True)
    (tmp / "vendor/licenses").mkdir()
    (tmp / "vendor/licenses/x.txt").write_text("MIT")
    h = hashlib.sha256(payload).hexdigest()
    (tmp / "vendor/store/sha256" / h).write_bytes(payload)
    e = {"name": "x", "version": "1", "kind": "wheel", "storage": "git",
         "filename": "x-1-py3-none-any.whl", "python": ["py3"], "platform": "any",
         "sha256": h, "size": len(payload), "license": "MIT",
         "license_file": "vendor/licenses/x.txt"}
    m = {"schema": 1, "target": {}, "artifacts": [e]}
    (tmp / "vendor/manifest.json").write_text(json.dumps(m))
    return tmp, e


def test_verify_ok_then_fails_closed_on_tamper(tmp_path):
    root, e = _mini(tmp_path)
    assert offline.verify(root)[0] == []
    (root / "vendor/store/sha256" / e["sha256"]).write_bytes(b"hello whee1")  # same size
    probs = [str(p) for p in offline.verify(root)[0]]
    assert any("sha256 mismatch" in p for p in probs)


@pytest.mark.parametrize("breakage", ["orphan", "nolicense", "missing", "size", "field"])
def test_verify_fails_closed(tmp_path, breakage):
    root, e = _mini(tmp_path)
    store = root / "vendor/store/sha256"
    m = json.loads((root / "vendor/manifest.json").read_text())
    if breakage == "orphan":
        (store / ("0" * 64)).write_bytes(b"x")
    elif breakage == "nolicense":
        (root / "vendor/licenses/x.txt").unlink()
    elif breakage == "missing":
        (store / e["sha256"]).unlink()
    elif breakage == "size":
        m["artifacts"][0]["size"] += 1
    elif breakage == "field":
        del m["artifacts"][0]["license"]
    (root / "vendor/manifest.json").write_text(json.dumps(m))
    assert offline.verify(root)[0]


def test_missing_release_asset_only_fails_when_required(tmp_path):
    root, e = _mini(tmp_path)
    m = json.loads((root / "vendor/manifest.json").read_text())
    m["artifacts"].append(dict(e, storage="release", kind="binary", filename="tool",
                               sha256="a" * 64))
    (root / "vendor/manifest.json").write_text(json.dumps(m))
    assert offline.verify(root)[0] == []
    assert offline.verify(root, require_assets=True)[0]


def test_materialize_refuses_corrupt_wheel(tmp_path):
    root, e = _mini(tmp_path)
    offline.materialize_wheelhouse(root, tmp_path / "wh", "3.12")
    assert (tmp_path / "wh" / e["filename"]).is_file()
    (root / "vendor/store/sha256" / e["sha256"]).write_bytes(b"evil")
    with pytest.raises(RuntimeError):
        offline.materialize_wheelhouse(root, tmp_path / "wh2", "3.12")


def test_no_ethereum_gate_passes_on_repo():
    assert _gate().main([]) == 0


@pytest.mark.parametrize("src", [
    "import web3\n",
    "from eth_account import Account\n",
    "from tcl.chain import client\n",
    "def f():\n    return {'method': 'eth_chainId'}\n",
    "import subprocess\nsubprocess.run(['anvil', '--port', '1'])\n",
    "def keccak256(b):\n    return b\n",
])
def test_no_ethereum_gate_rejects(tmp_path, monkeypatch, src):
    g = _gate()
    monkeypatch.setattr(g, "ROOT", tmp_path)
    (tmp_path / "circuits").mkdir()
    (tmp_path / "src/tcl/localnet").mkdir(parents=True)
    (tmp_path / "src/tcl/localnet/bad.py").write_text(src)
    assert g.main([]) == 1


def test_no_ethereum_gate_allows_docstring_mentions(tmp_path, monkeypatch):
    g = _gate()
    monkeypatch.setattr(g, "ROOT", tmp_path)
    (tmp_path / "circuits").mkdir()
    (tmp_path / "src/tcl/localnet").mkdir(parents=True)
    (tmp_path / "src/tcl/localnet/ok.py").write_text('"""Replaces anvil; no eth_ RPC."""\nX = 1\n')
    assert g.main([]) == 0


def test_offline_module_is_stdlib_only():
    import ast
    import sys
    tree = ast.parse((ROOT / "src/tcl/offline.py").read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module]
            for name in names:
                if name and name != "__future__":
                    assert name.split(".")[0] in sys.stdlib_module_names, name
