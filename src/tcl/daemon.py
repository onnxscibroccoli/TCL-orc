"""Long-running TCL daemon: Unix-socket JSON-lines IPC around the task Engine.

Requests (one JSON object per line)::

    {"op": "health"}
    {"op": "submit", "tasks": [<TaskSpec dict>, ...]}   # or "task": {...}
    {"op": "status"}                  # all tasks
    {"op": "status", "task_id": "x"}  # one task

Every response is one JSON line with ``"ok": true|false``. Telemetry (task
state changes, conflict alerts, worktree health) is emitted as JSON lines to
the ``tcl.daemon`` logger and optionally to a telemetry file.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import time
from pathlib import Path

from .logs import get_logger
from .orchestrator.engine import DAGError, Engine, TaskStore
from .worktree import WorktreeManager

log = get_logger("daemon")
MAX_LINE = 1 << 20


class Daemon:
    def __init__(self, socket_path: str | Path, db: str | Path = ":memory:",
                 repo: str | Path | None = None, workers: int = 4,
                 telemetry: str | Path | None = None, health_interval: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.started = time.time()
        self.manager = WorktreeManager(repo, layout="sibling") if repo else None
        self._tel_lock = threading.Lock()
        self.telemetry = Path(telemetry) if telemetry else None
        self.engine = Engine(TaskStore(db), workers=workers, manager=self.manager,
                             workdir=repo, on_event=self._telemetry)
        self.engine.guard.on_alert(lambda a: self._telemetry({"event": "conflict.alert",
                                                              **a.to_dict()}))
        self.health_interval = health_interval
        self._server: socketserver.ThreadingUnixStreamServer | None = None
        self._stop = threading.Event()

    def _telemetry(self, rec: dict) -> None:
        if self.telemetry:
            with self._tel_lock, open(self.telemetry, "a") as fh:
                fh.write(json.dumps(rec, default=str, sort_keys=True) + "\n")

    # -- request handling ------------------------------------------------------
    def health(self) -> dict:
        wts = self.manager.health() if self.manager else []
        return {"ok": True, "status": "healthy" if all(w["healthy"] for w in wts) else "degraded",
                "pid": os.getpid(), "uptime": round(time.time() - self.started, 3),
                "tasks": self.engine.status_map(), "worktrees": wts,
                "alerts": [a.to_dict() for a in self.engine.alerts]}

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        try:
            if op == "health":
                return self.health()
            if op == "submit":
                tasks = req.get("tasks") or ([req["task"]] if "task" in req else None)
                if not isinstance(tasks, list):
                    return {"ok": False, "error": "submit needs 'task' or 'tasks'"}
                return {"ok": True, "submitted": self.engine.submit(tasks)}
            if op == "status":
                if "task_id" in req:
                    return {"ok": True, "task": self.engine.status(req["task_id"])}
                return {"ok": True, "tasks": self.engine.status_map()}
            return {"ok": False, "error": f"unknown op {op!r}"}
        except (DAGError, TypeError) as e:
            return {"ok": False, "error": str(e)}
        except KeyError as e:
            return {"ok": False, "error": f"unknown task {e}"}

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> "Daemon":
        if self.socket_path.exists():
            # refuse to steal a live socket; clean up a stale one
            try:
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(str(self.socket_path))
                raise RuntimeError(f"daemon already listening on {self.socket_path}")
            except ConnectionRefusedError:
                self.socket_path.unlink()
        daemon = self

        class H(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                for raw in self.rfile:
                    if len(raw) > MAX_LINE:
                        resp = {"ok": False, "error": "request too large"}
                    else:
                        try:
                            req = json.loads(raw)
                            resp = daemon.handle(req) if isinstance(req, dict) else \
                                {"ok": False, "error": "request must be an object"}
                        except json.JSONDecodeError as e:
                            resp = {"ok": False, "error": f"bad json: {e}"}
                    self.wfile.write((json.dumps(resp, default=str) + "\n").encode())
                    self.wfile.flush()

        self._server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), H)
        os.chmod(self.socket_path, 0o600)
        self._server.daemon_threads = True
        self.engine.start()
        threading.Thread(target=self._server.serve_forever, daemon=True, name="tcl-ipc").start()
        threading.Thread(target=self._monitor, daemon=True, name="tcl-health").start()
        self._telemetry({"event": "daemon.start", "ts": time.time(), "socket": str(self.socket_path)})
        log.info("daemon.start", extra={"socket": str(self.socket_path)})
        return self

    def _monitor(self) -> None:
        while not self._stop.wait(self.health_interval):
            try:
                h = self.health()
                self._telemetry({"event": "daemon.health", "ts": time.time(),
                                 "status": h["status"], "worktrees": h["worktrees"]})
                self.engine.guard.scan()
            except Exception:  # pragma: no cover - monitor must never die
                log.exception("daemon.health_error")

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self.engine.stop()
        self.socket_path.unlink(missing_ok=True)
        self._telemetry({"event": "daemon.stop", "ts": time.time()})

    def serve_forever(self) -> None:  # pragma: no cover - exercised via CLI manually
        import signal
        self.start()
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        try:
            while not self._stop.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def request(socket_path: str | Path, req: dict, timeout: float = 10.0) -> dict:
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(str(socket_path))
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)
