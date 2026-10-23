import json
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tcl.cli import app
from tcl.daemon import Daemon, request

r = CliRunner()


@pytest.fixture
def sockdir():
    d = Path(tempfile.mkdtemp(prefix="tcl", dir="/tmp"))  # AF_UNIX path length limit
    yield d
    shutil.rmtree(d, ignore_errors=True)


def wait_terminal(sock, ids, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = request(sock, {"op": "status"})["tasks"]
        if all(st.get(i) in ("MERGED", "FAILED") for i in ids):
            return {i: st[i] for i in ids}
        time.sleep(0.1)
    raise AssertionError(st)


def test_ipc_health_submit_status(sockdir, tmp_path):
    sock = sockdir / "d.sock"
    tel = tmp_path / "tel.jsonl"
    d = Daemon(sock, db=tmp_path / "t.db", telemetry=tel, health_interval=0.2).start()
    try:
        assert oct(sock.stat().st_mode & 0o777) == "0o600"
        h = request(sock, {"op": "health"})
        assert h["ok"] and h["status"] == "healthy"
        assert request(sock, {"op": "submit", "tasks": [
            {"task_id": "a", "command": ["true"]},
            {"task_id": "b", "command": ["true"], "depends": ["a"]}]})["submitted"] == ["a", "b"]
        assert wait_terminal(sock, ["a", "b"]) == {"a": "MERGED", "b": "MERGED"}
        st = request(sock, {"op": "status", "task_id": "b"})["task"]
        assert st["state"] == "MERGED" and st["spec"]["depends"] == ["a"]
        # error paths are JSON, never crashes
        assert not request(sock, {"op": "submit", "tasks": [
            {"task_id": "x", "command": ["true"], "depends": ["x"]}]})["ok"]
        assert not request(sock, {"op": "nope"})["ok"]
        assert not request(sock, {"op": "status", "task_id": "zzz"})["ok"]
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(sock)); s.sendall(b"not json\n")
            assert json.loads(s.makefile().readline())["ok"] is False
        with pytest.raises(RuntimeError):
            Daemon(sock).start()  # refuses to steal a live socket
        time.sleep(0.5)
        # CLI against the daemon
        out = r.invoke(app, ["daemon", "health", "--socket", str(sock)])
        assert out.exit_code == 0 and json.loads(out.stdout)["ok"]
    finally:
        d.stop()
    events = [json.loads(l)["event"] for l in tel.read_text().splitlines()]
    assert {"daemon.start", "task.state", "daemon.health", "daemon.stop"} <= set(events)
    assert not sock.exists()


def test_cli_without_daemon_fails_closed(sockdir):
    out = r.invoke(app, ["task", "list", "--socket", str(sockdir / "none.sock")])
    assert out.exit_code == 1


def test_compat_agents():
    import agents.daemon as ad
    import agents.orchestrator as ao
    assert ad.dispatch_task("t", "/tmp").returncode == 0
    order = ao.topo_order()
    assert order.index("task-05-quality") > order.index("task-03-math")
