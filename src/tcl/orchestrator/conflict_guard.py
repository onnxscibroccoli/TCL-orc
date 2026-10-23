"""Conflict Guard: detect overlapping file modifications across active worktrees
*before* any merge is attempted.

For each registered worktree the modified set is::

    git diff --name-only <base>        (committed + staged + unstaged vs base)
  ∪ git ls-files --others --exclude-standard   (untracked)

minus runtime files (``.env.local``). Two active worktrees conflict when their
modified sets intersect. Alerts are logged as structured JSON, kept in
``alerts`` and passed to every registered callback.
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from ..logs import get_logger

log = get_logger("conflict_guard")
IGNORED = frozenset({".env.local"})


class ConflictGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConflictAlert:
    worktree: str
    others: tuple[str, ...]
    files: tuple[str, ...]
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["others"], d["files"] = list(self.others), list(self.files)
        return d


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ConflictGuardError(f"git {' '.join(args)} in {cwd}: {r.stderr.strip()}")
    return r.stdout


def modified_files(path: str | Path, base: str) -> set[str]:
    p = Path(path)
    files = set(_git(p, "diff", "--name-only", base).split("\n"))
    files |= set(_git(p, "ls-files", "--others", "--exclude-standard").split("\n"))
    return {f for f in files if f and f not in IGNORED}


class ConflictGuard:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, tuple[Path, str]] = {}
        self.alerts: list[ConflictAlert] = []
        self._callbacks: list[Callable[[ConflictAlert], None]] = []

    def on_alert(self, cb: Callable[[ConflictAlert], None]) -> None:
        self._callbacks.append(cb)

    def register(self, wid: str, path: str | Path, base: str) -> None:
        with self._lock:
            # resolve base to a commit now so later moves of the ref don't change the diff
            self._active[wid] = (Path(path), _git(Path(path), "rev-parse", base).strip())

    def unregister(self, wid: str) -> None:
        with self._lock:
            self._active.pop(wid, None)

    def active(self) -> list[str]:
        with self._lock:
            return sorted(self._active)

    def snapshot(self) -> dict[str, set[str]]:
        with self._lock:
            items = list(self._active.items())
        return {w: modified_files(p, b) for w, (p, b) in items if p.exists()}

    def check(self, wid: str) -> ConflictAlert | None:
        """Compare ``wid`` against all other active worktrees. Returns an alert
        (and notifies) on overlap, else None. Fail-closed: git errors raise."""
        with self._lock:
            if wid not in self._active:
                raise ConflictGuardError(f"worktree {wid} not registered")
            snap = self.snapshot()
            mine = snap.get(wid, set())
            others, files = [], set()
            for other, theirs in snap.items():
                if other == wid:
                    continue
                ov = mine & theirs
                if ov:
                    others.append(other)
                    files |= ov
            if not files:
                return None
            alert = ConflictAlert(wid, tuple(sorted(others)), tuple(sorted(files)))
            self.alerts.append(alert)
        log.warning("conflict.alert", extra=alert.to_dict())
        for cb in self._callbacks:
            cb(alert)
        return alert

    def scan(self) -> list[ConflictAlert]:
        """Check every active worktree (used by the daemon's periodic monitor)."""
        return [a for w in self.active() if (a := self.check(w))]
