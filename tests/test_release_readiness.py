"""Release-readiness checks: version single-source, bundled artifacts, doctor, probes."""
import json
import re
import socket
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

import tcl
from tcl.cli import app
from tcl.daemon import Daemon


def test_version_single_source_pep440():
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?", tcl.__version__)
    r = CliRunner().invoke(app, ["version"])
    assert r.exit_code == 0 and r.stdout.strip() == tcl.__version__


def test_resource_root_checkout():
    from tcl.resources import resource_root
    assert (resource_root("zk") / "circuits/puf_verify.circom").is_file()


def test_resource_root_override_fails_closed(tmp_path, monkeypatch):
    from tcl.resources import ResourcesMissing, resource_root
    monkeypatch.setenv("TCL_RESOURCE_ROOT", str(tmp_path))
    for kind in ("zk", "chain"):
        with pytest.raises(ResourcesMissing, match="TCL_RESOURCE_ROOT"):
            resource_root(kind)
    from tcl.zk.prover import ToolchainMissing, circuits_dir
    with pytest.raises(ToolchainMissing):
        circuits_dir()


def test_doctor_core_json_ok():
    r = CliRunner().invoke(app, ["doctor", "-c", "core", "--json"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["ok"] is True


def test_doctor_fails_closed_without_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("TCL_RESOURCE_ROOT", str(tmp_path))
    r = CliRunner().invoke(app, ["doctor", "-c", "chain", "--json"])
    assert r.exit_code == 1
    assert any(c["check"].startswith("artifacts") and not c["ok"]
               for c in json.loads(r.stdout)["checks"])


def test_doctor_rejects_unknown_component():
    assert CliRunner().invoke(app, ["doctor", "-c", "bogus"]).exit_code != 0


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_probes(tmp_path):
    import tempfile
    sockdir = tempfile.mkdtemp(prefix="tcl", dir="/tmp")
    port = _free_port()
    d = Daemon(f"{sockdir}/d.sock", db=tmp_path / "t.db", http=("127.0.0.1", port)).start()
    base = f"http://127.0.0.1:{port}"
    try:
        assert _get(base + "/livez")[0] == 200
        code, body = _get(base + "/readyz")
        assert code == 200 and body["status"] == "ready", body
        assert _get(base + "/version")[1]["version"] == tcl.__version__
        assert _get(base + "/health")[0] == 200
        assert _get(base + "/nope")[0] == 404
        d._stop.set()  # shutting down -> not ready (fail-closed)
        code, body = _get(base + "/readyz")
        assert code == 503 and body["checks"]["not_stopping"] is False
    finally:
        d.stop()


def test_dev_key_refused_on_non_dev_chain(monkeypatch):
    from tcl.chain import client
    monkeypatch.setattr(client, "discover", lambda: {"forge": "f", "cast": "c", "anvil": "a"})
    monkeypatch.setattr(client, "wait_rpc", lambda *a, **k: 11155111)
    with pytest.raises(client.ChainError, match="dev key"):
        client.deploy("http://x", 1, 1, work=__import__("pathlib").Path("/tmp/none"))
