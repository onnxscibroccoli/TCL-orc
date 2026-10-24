"""JSON-RPC 2.0 for the localnet over HTTP (127.0.0.1 only) and a Unix socket.

Method names follow docs/13a_consensus_interfaces.md. Dev-only methods are
refused (fail-closed) unless the node runs with dev=True. Stdlib only.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from .node import Localnet, LocalnetError, Reject

DEV_ONLY = {"tcl_mine", "tcl_warp", "tcl_setTime", "tcl_snapshot", "tcl_revert",
            "tcl_faucet", "tcl_injectFault", "tcl_shutdown", "tcl_devAccounts",
            "tcl_devSend"}
UNAUTHORIZED = -32001
REJECTED = -32002
LOCALNET_ERR = -32003


class Dispatcher:
    def __init__(self, net: Localnet, on_shutdown: Optional[Callable[[], None]] = None) -> None:
        self.net = net
        self.lock = threading.Lock()
        # all ledger/SQLite access happens on ONE worker thread (deterministic, no
        # cross-thread connections)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="localnet")
        self.on_shutdown = on_shutdown
        n = net
        self.methods: dict[str, Callable[..., Any]] = {
            "tcl_chainId": lambda: n.chain_id,
            "tcl_status": n.status,
            "tcl_getBlock": n.get_block,
            "tcl_getTx": n.get_tx,
            "tcl_sendTx": n.send_tx,
            "tcl_getAccount": n.get_account,
            "tcl_getProof": n.get_proof,
            "tcl_getValidators": lambda height=None: n.status()["validators"],
            "tcl_getParams": lambda: n.genesis.to_json(),
            "tcl_verifyState": n.verify,
            "tcl_mine": lambda count=1: n.mine(count),
            "tcl_warp": n.warp,
            "tcl_setTime": n.set_time,
            "tcl_snapshot": n.snapshot,
            "tcl_revert": n.revert,
            "tcl_faucet": n.faucet,
            "tcl_injectFault": n.inject_fault,
            "tcl_shutdown": self._shutdown,
            "tcl_devAccounts": n.dev_accounts,
            "tcl_devSend": n.dev_send,
        }

    def _shutdown(self) -> bool:
        if self.on_shutdown:
            threading.Thread(target=self.on_shutdown, daemon=True).start()
        return True

    def handle(self, req: Any) -> dict:
        rid = req.get("id") if isinstance(req, dict) else None
        def err(code: int, msg: str, data: Any = None) -> dict:
            e = {"code": code, "message": msg}
            if data is not None:
                e["data"] = data
            return {"jsonrpc": "2.0", "id": rid, "error": e}
        if not isinstance(req, dict) or not isinstance(req.get("method"), str):
            return err(-32600, "invalid request")
        m = req["method"]
        fn = self.methods.get(m)
        if fn is None:
            return err(-32601, f"method not found: {m}")
        if m in DEV_ONLY and not self.net.dev:
            return err(UNAUTHORIZED, "UNAUTHORIZED: dev-only method (start with --dev)",
                       {"reject": "UNAUTHORIZED"})
        p = req.get("params", [])
        try:
            with self.lock:
                res = self.pool.submit(
                    lambda: fn(**p) if isinstance(p, dict) else fn(*p)).result()
        except Reject as r:
            return err(REJECTED, str(r), {"reject": r.code})
        except LocalnetError as e:
            return err(LOCALNET_ERR, str(e))
        except (TypeError, ValueError, KeyError) as e:
            return err(-32602, f"invalid params: {e}")
        return {"jsonrpc": "2.0", "id": rid, "result": res}

    def handle_bytes(self, body: bytes) -> bytes:
        try:
            req = json.loads(body)
        except ValueError:
            return json.dumps({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "parse error"}}).encode()
        out = [self.handle(r) for r in req] if isinstance(req, list) else self.handle(req)
        return json.dumps(out).encode()


def _http_handler(d: Dispatcher) -> type:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:  # quiet
            pass

        def _send(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, b'{"status":"ok"}')
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length", 0))
            if n > 1 << 20:
                self._send(413, b'{"error":"too large"}')
                return
            self._send(200, d.handle_bytes(self.rfile.read(n)))
    return H


class _UnixHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # newline-delimited JSON-RPC
        for line in self.rfile:
            if line.strip():
                self.wfile.write(self.server.dispatcher.handle_bytes(line) + b"\n")  # type: ignore


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class Server:
    """Runs HTTP on 127.0.0.1:port (port 0 = ephemeral) and optionally a Unix socket."""

    def __init__(self, net: Localnet, port: int = 8645, sock: Optional[Path] = None) -> None:
        self.net = net
        self.dispatcher = Dispatcher(net, on_shutdown=self.shutdown)
        self.http = HTTPServer(("127.0.0.1", port), _http_handler(self.dispatcher))
        self.port = self.http.server_address[1]
        self.sock = sock
        self.unix: Optional[_UnixServer] = None
        if sock is not None:
            Path(sock).unlink(missing_ok=True)
            self.unix = _UnixServer(str(sock), _UnixHandler)
            self.unix.dispatcher = self.dispatcher  # type: ignore[attr-defined]
            os.chmod(sock, 0o600)
        self._threads: list[threading.Thread] = []
        self._stopped = threading.Event()

    def start(self) -> "Server":
        for srv in filter(None, (self.http, self.unix)):
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def wait(self) -> None:
        self._stopped.wait()

    def shutdown(self) -> None:
        if self._stopped.is_set():
            return
        for srv in filter(None, (self.http, self.unix)):
            srv.shutdown()
            srv.server_close()
        if self.sock:
            Path(self.sock).unlink(missing_ok=True)
        with self.dispatcher.lock:
            self.dispatcher.pool.submit(self.net.close).result()
            self.dispatcher.pool.shutdown()
        self._stopped.set()


def call(url: str, method: str, params: Any = None, timeout: float = 30) -> Any:
    """Minimal client. url = http://127.0.0.1:PORT or unix:/path/to.sock."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params if params is not None else []}).encode()
    if url.startswith("unix:"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(url[5:])
            s.sendall(body + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf)
    else:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - localhost only
            resp = json.loads(r.read())
    if "error" in resp:
        raise RPCError(resp["error"])
    return resp["result"]


class RPCError(Exception):
    def __init__(self, e: dict) -> None:
        super().__init__(e.get("message"))
        self.code = e.get("code")
        self.data = e.get("data")
