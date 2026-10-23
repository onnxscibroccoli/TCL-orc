"""Phase 4 unit tests: HTTP /health, CLI honoring Settings, chain helpers (no tools needed)."""
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tcl.chain import client
from tcl.cli import app
from tcl.config import get_settings
from tcl.daemon import Daemon, request

r = CliRunner()


@pytest.fixture
def sockdir():
    d = Path(tempfile.mkdtemp(prefix="tcl", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_http_health_200_alongside_socket(sockdir, tmp_path):
    d = Daemon(sockdir / "d.sock", db=tmp_path / "t.db", http=("127.0.0.1", 0)).start()
    try:
        host, port = d.http_addr
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
        assert body["ok"] and body["status"] == "healthy"
        assert request(d.socket_path, {"op": "health"})["pid"] == body["pid"]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://{host}:{port}/nope", timeout=5)
        assert e.value.code == 404
    finally:
        d.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1)


def test_http_health_503_when_degraded(sockdir, tmp_path, monkeypatch):
    d = Daemon(sockdir / "d.sock", db=tmp_path / "t.db", http=("127.0.0.1", 0)).start()
    try:
        monkeypatch.setattr(d, "health", lambda: {"ok": True, "status": "degraded"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://{d.http_addr[0]}:{d.http_addr[1]}/health", timeout=5)
        assert e.value.code == 503
    finally:
        d.stop()


def test_cli_honors_config_yaml_and_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("TCL_DB", "TCL_TREE_DEPTH", "TCL_LOG_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "c.yaml").write_text(f"db: {tmp_path / 'from_yaml.db'}\ntree_depth: 6\n")
    monkeypatch.setenv("TCL_CONFIG", str(tmp_path / "c.yaml"))
    out = r.invoke(app, ["ledger", "deposit", "a", "5", "--now", "1"])
    assert out.exit_code == 0, out.output
    assert (tmp_path / "from_yaml.db").exists()
    root = json.loads(r.invoke(app, ["ledger", "root"]).stdout)
    assert root["depth"] == 6 and root["transitions"] >= 1
    # env beats yaml, flag beats env
    monkeypatch.setenv("TCL_DB", str(tmp_path / "from_env.db"))
    assert r.invoke(app, ["ledger", "init"]).exit_code == 0
    assert (tmp_path / "from_env.db").exists()
    assert r.invoke(app, ["ledger", "init", "--db", str(tmp_path / "flag.db")]).exit_code == 0
    assert (tmp_path / "flag.db").exists()


def test_cli_rejects_invalid_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TCL_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("TCL_TREE_DEPTH", "999")
    out = r.invoke(app, ["ledger", "root", "--db", str(tmp_path / "x.db")])
    assert out.exit_code == 1 and "invalid configuration" in out.output


def test_settings_phase4_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("TCL_CONFIG", str(tmp_path / "none.yaml"))
    s = get_settings()
    assert s.http_port == 8787 and s.zk_depth == 4 and s.rpc_url.startswith("http://")
    assert get_settings(http_port=0).http_port == 0


def test_chain_arg_encoding_and_forge_json(monkeypatch):
    assert client._arr([["0x1", "0x2"], ["0x3", "0x4"]]) == "[[0x1,0x2],[0x3,0x4]]"
    out = 'Compiling...\n{\n  "deployer": "0xA",\n  "deployedTo": "0xB",\n  "transactionHash": "0xC"\n}\n'
    monkeypatch.setattr(client, "_run", lambda *a, **k: out)
    assert client._create({"forge": "forge"}, Path("."), "x:y", "rpc", "k", []) == "0xB"
    monkeypatch.setattr(client, "_run", lambda *a, **k: "no json here")
    with pytest.raises(client.ChainError):
        client._create({"forge": "forge"}, Path("."), "x:y", "rpc", "k", [])


def test_chain_missing_tools_fail_closed(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda n: None)
    monkeypatch.setattr(client.Path, "home", lambda: Path("/nonexistent"))
    for n in ("FORGE", "CAST", "ANVIL"):
        monkeypatch.delenv(f"TCL_{n}", raising=False)
    with pytest.raises(client.ChainError, match="missing EVM toolchain"):
        client.discover()
