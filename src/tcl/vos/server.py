"""Loopback-only playable demo HTTP host for the TCL virtual gaming OS."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, urlsplit
from .runtime import GameError, GameOS, canonical

MAX_REQUEST = 32 * 1024
MAX_SAVE = 16 * 1024 * 1024
STATIC = Path(__file__).with_name("web")
# Build artifact generated from vos/wasm/q32.c and checked against Python parity.
Q32_WASM_HASH = "6830f68d6e1b72b17426c0f121641c0c908aa2489ab5e10b860d7929bb4906fe"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/guest_worker.js": ("guest_worker.js", "text/javascript; charset=utf-8"),
}


class SessionStore:
    """Canonical, replay-checked local JSON session with an atomic file replace."""

    def __init__(self, path: Path | None = None, *, seed: int = 99):
        self.path = Path(path) if path is not None else None
        self.lock = RLock()
        self.os = GameOS(seed=seed)
        if self.path is None or not self.path.is_file():
            self._seed_city_market()
        if self.path is not None and self.path.is_file():
            if self.path.stat().st_size > MAX_SAVE:
                raise GameError("saved world exceeds the local prototype limit")
            try:
                self.os = GameOS.restore(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, TypeError, KeyError) as exc:
                raise GameError("saved world failed deterministic replay") from exc

    def _seed_city_market(self) -> None:
        """Provide a clearly fictional NPC counterparty for first-time solo play."""
        bot = "citybot"
        self.os.dispatch({"game": "civitas99", "actor": bot, "action": "join", "params": {}})
        for resource, bid, ask in (("energy", 7, 12), ("water", 9, 15),
                                   ("alloy", 11, 18)):
            for side, price, qty in (("sell", ask, 5), ("buy", bid, 12)):
                self.os.dispatch({"game": "civitas99", "actor": bot,
                                  "action": "order", "params": {"side": side,
                                  "resource": resource, "price": price, "quantity": qty}})

    def persist(self) -> None:
        if self.path is None:
            return
        blob = canonical(self.os.export()) + b"\n"
        if len(blob) > MAX_SAVE:
            raise GameError("local game save reached size limit")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            with open(tmp, "xb") as f:
                os.chmod(tmp, 0o600)
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def execute(self, command: dict) -> dict:
        with self.lock:
            backup = self.os.export()
            result = self.os.dispatch(command)
            try:
                self.persist()
            except (OSError, GameError):
                self.os = GameOS.restore(backup)
                raise
            return result


def make_handler(store: SessionStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TCL-vOS-Local/0.1"
        sys_version = ""

        def _headers(self, status: int, content_type: str, length: int = 0):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()

        def _json(self, code: int, data: dict):
            body = canonical(data)
            self._headers(code, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - required stdlib HTTP API
            parsed = urlsplit(self.path)
            if parsed.path == "/healthz":
                return self._json(200, {"status": "local-dev-only", "game": "civitas99"})
            if parsed.path == "/api/state":
                actor = parse_qs(parsed.query).get("actor", [""])[0]
                return self._json(200, store.os.snapshot(actor))
            if parsed.path == "/q32.wasm":
                try:
                    raw = base64.b64decode(
                        (STATIC / "q32.wasm.b64").read_bytes().strip(), validate=True
                    )
                except (OSError, ValueError):
                    return self._json(500, {"error": "fixed-point WASM asset unavailable"})
                if len(raw) > 16 * 1024 or sha256(raw).hexdigest() != Q32_WASM_HASH:
                    return self._json(500, {"error": "fixed-point WASM integrity check failed"})
                self._headers(200, "application/wasm", len(raw))
                self.wfile.write(raw)
                return
            if parsed.path in ASSETS:
                filename, mime = ASSETS[parsed.path]
                try:
                    body = (STATIC / filename).read_bytes()
                except OSError:
                    return self._json(500, {"error": "game asset unavailable"})
                self._headers(200, mime, len(body))
                self.wfile.write(body)
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - required stdlib HTTP API
            if urlsplit(self.path).path != "/api/commands":
                return self._json(404, {"error": "not found"})
            origin = self.headers.get("Origin")
            port = self.server.server_address[1]
            if origin and origin not in (f"http://localhost:{port}", f"http://127.0.0.1:{port}"):
                return self._json(403, {"error": "cross-origin writes forbidden"})
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return self._json(415, {"error": "application/json required"})
            try:
                count = int(self.headers.get("Content-Length", "-1"))
                if not 0 < count <= MAX_REQUEST:
                    return self._json(413, {"error": "request too large or empty"})
                data = json.loads(self.rfile.read(count))
                return self._json(200, store.execute(data))
            except (ValueError, TypeError, GameError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError:
                return self._json(500, {"error": "session could not be saved"})

        def log_message(self, format, *args):
            # The OS should not print player IDs or command bodies by default.
            pass

    return Handler


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  path: Path | None = None) -> HTTPServer:
    if host not in ("127.0.0.1", "localhost"):
        raise GameError("vOS demo is unauthenticated; bind to loopback only")
    if type(port) is not int or not 0 <= port <= 65535:
        raise GameError("port must be integer between 0 and 65535")
    return HTTPServer((host, port), make_handler(SessionStore(path)))

