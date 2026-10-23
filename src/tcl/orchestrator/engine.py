"""Event-driven task DAG engine.

* Tasks declare ``depends``; graphs are validated on submit (unknown deps and
  cycles are rejected, nothing is persisted on rejection).
* A thread-safe ready queue feeds N worker threads. When a task reaches a
  terminal state its dependents are re-evaluated (event-driven, no polling);
  a FAILED dependency fails all dependents (``dependency_failed``).
* Every state change goes through :class:`Task.transition` (the state machine)
  and is persisted to SQLite (WAL) together with its history.
* Commands run in a sandboxed subprocess: own process group/session, empty-ish
  env, ``resource.setrlimit`` for CPU seconds, address space, open files,
  file size and process count, plus a wall-clock timeout that kills the whole
  process group and marks the task FAILED.
* With a :class:`WorktreeManager`, each task gets its own worktree; before
  MERGED the :class:`ConflictGuard` checks for overlapping edits with every
  other active worktree. Overlap -> alert -> FAILED (fail-closed, nothing merged).
"""
from __future__ import annotations

import json
import os
import queue
import resource
import signal
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable

from ..logs import get_logger
from .conflict_guard import ConflictAlert, ConflictGuard
from .state import TERMINAL, Task, TaskState

log = get_logger("engine")


class DAGError(ValueError):
    pass


class CycleError(DAGError):
    pass


@dataclass
class Limits:
    cpu_seconds: int | None = 60
    memory_bytes: int | None = 1 << 30
    max_files: int | None = 256
    max_file_bytes: int | None = 1 << 28
    max_procs: int | None = None  # RLIMIT_NPROC is per-user; opt-in


@dataclass
class TaskSpec:
    task_id: str
    command: list[str]
    depends: list[str] = field(default_factory=list)
    timeout: float = 300.0
    branch: str | None = None
    base: str = "HEAD"
    limits: Limits = field(default_factory=Limits)
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id or not all(c.isalnum() or c in "-_." for c in self.task_id):
            raise DAGError(f"invalid task_id {self.task_id!r}")
        if not isinstance(self.command, list) or not self.command or \
                not all(isinstance(c, str) for c in self.command):
            raise DAGError(f"{self.task_id}: command must be a non-empty list of strings")
        if not (0 < float(self.timeout) <= 86400):
            raise DAGError(f"{self.task_id}: timeout out of range")
        if isinstance(self.limits, dict):
            self.limits = Limits(**self.limits)
        self.depends = list(dict.fromkeys(self.depends))
        if self.task_id in self.depends:
            raise CycleError(f"{self.task_id} depends on itself")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskSpec":
        allowed = set(cls.__dataclass_fields__)
        extra = set(d) - allowed
        if extra:
            raise DAGError(f"unknown task fields {sorted(extra)}")
        return cls(**d)


def detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return a cycle path if ``graph`` (node -> deps) has one, else None."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = GREY
        stack.append(n)
        for d in graph.get(n, []):
            if color.get(d, BLACK) == GREY:
                return stack[stack.index(d):] + [d]
            if color.get(d) == WHITE and (c := visit(d)):
                return c
        stack.pop()
        color[n] = BLACK
        return None

    for n in graph:
        if color[n] == WHITE and (c := visit(n)):
            return c
    return None


class TaskStore:
    """SQLite (WAL) persistence of task specs and state-machine records."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS tasks(
            task_id TEXT PRIMARY KEY, spec TEXT NOT NULL, record TEXT NOT NULL,
            seq INTEGER NOT NULL, updated REAL NOT NULL)""")

    def put(self, spec: TaskSpec, task: Task, seq: int) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?)",
                             (spec.task_id, json.dumps(spec.to_dict()), json.dumps(task.to_dict()),
                              seq, time.time()))

    def save(self, task: Task) -> None:
        with self._lock:
            n = self._db.execute("UPDATE tasks SET record=?, updated=? WHERE task_id=?",
                                 (json.dumps(task.to_dict()), time.time(), task.task_id)).rowcount
            if n != 1:
                raise KeyError(task.task_id)

    def load(self) -> list[tuple[TaskSpec, Task]]:
        with self._lock:
            rows = self._db.execute("SELECT spec, record FROM tasks ORDER BY seq").fetchall()
        return [(TaskSpec.from_dict(json.loads(s)), Task.from_dict(json.loads(r))) for s, r in rows]

    def close(self) -> None:
        self._db.close()


def _sandbox(limits: Limits) -> Callable[[], None]:
    def apply() -> None:
        os.setsid()
        pairs = [(resource.RLIMIT_CPU, limits.cpu_seconds), (resource.RLIMIT_AS, limits.memory_bytes),
                 (resource.RLIMIT_NOFILE, limits.max_files),
                 (resource.RLIMIT_FSIZE, limits.max_file_bytes),
                 (resource.RLIMIT_NPROC, limits.max_procs), (resource.RLIMIT_CORE, 0)]
        for res, val in pairs:
            if val is not None:
                resource.setrlimit(res, (val, val))
    return apply


class Engine:
    def __init__(self, store: TaskStore | None = None, workers: int = 4, manager=None,
                 guard: ConflictGuard | None = None, workdir: str | Path | None = None,
                 on_event: Callable[[dict], None] | None = None) -> None:
        self.store = store or TaskStore()
        self.manager = manager
        self.guard = guard or ConflictGuard()
        self.workdir = Path(workdir or os.getcwd())
        self.on_event = on_event
        self.specs: dict[str, TaskSpec] = {}
        self.tasks: dict[str, Task] = {}
        self.timeline: dict[str, tuple[float, float]] = {}
        self.alerts: list[ConflictAlert] = []
        self.guard.on_alert(self.alerts.append)
        self._lock = threading.RLock()
        self._done = threading.Condition(self._lock)
        self._ready: queue.Queue[str | None] = queue.Queue()
        self._queued: set[str] = set()
        self._seq = 0
        self._nworkers = workers
        self._threads: list[threading.Thread] = []
        self._merge_lock = threading.Lock()
        self._recover()

    # -- persistence / recovery ------------------------------------------------
    def _recover(self) -> None:
        for spec, task in self.store.load():
            self._seq += 1
            if not task.terminal and task.state is not TaskState.PENDING:
                # crashed mid-flight: never silently resume a half-run task
                task.transition(TaskState.FAILED, "engine_restart")
                self.store.save(task)
            self.specs[spec.task_id], self.tasks[spec.task_id] = spec, task

    def _emit(self, event: str, **kw) -> None:
        rec = {"event": event, "ts": time.time(), **kw}
        log.info(event, extra=kw)
        if self.on_event:
            self.on_event(rec)

    def _set(self, tid: str, to: TaskState, error: str | None = None) -> None:
        with self._lock:
            t = self.tasks[tid]
            t.transition(to, error)
            self.store.save(t)
            self._emit("task.state", task_id=tid, state=to.value, error=error)
            if t.terminal:
                self._on_terminal(tid)
                self._done.notify_all()

    # -- submission ------------------------------------------------------------
    def submit(self, specs: TaskSpec | dict | Iterable[TaskSpec | dict]) -> list[str]:
        if isinstance(specs, (TaskSpec, dict)):
            specs = [specs]
        batch = [s if isinstance(s, TaskSpec) else TaskSpec.from_dict(s) for s in specs]
        with self._lock:
            ids = [s.task_id for s in batch]
            if len(set(ids)) != len(ids) or set(ids) & set(self.specs):
                raise DAGError(f"duplicate task ids in {ids}")
            graph = {tid: s.depends for tid, s in self.specs.items()}
            graph.update({s.task_id: s.depends for s in batch})
            for s in batch:
                missing = [d for d in s.depends if d not in graph]
                if missing:
                    raise DAGError(f"{s.task_id}: unknown dependencies {missing}")
            if cyc := detect_cycle(graph):
                raise CycleError("cycle: " + " -> ".join(cyc))
            for s in batch:
                self._seq += 1
                t = Task(s.task_id, s.branch or f"task/{s.task_id}")
                self.store.put(s, t, self._seq)
                self.specs[s.task_id], self.tasks[s.task_id] = s, t
                self._emit("task.submitted", task_id=s.task_id, depends=s.depends)
            for s in batch:
                self._evaluate(s.task_id)
        return ids

    def _evaluate(self, tid: str) -> None:
        t = self.tasks[tid]
        if t.state is not TaskState.PENDING or tid in self._queued:
            return
        deps = [self.tasks[d] for d in self.specs[tid].depends]
        if any(d.state is TaskState.FAILED for d in deps):
            self._set(tid, TaskState.FAILED, "dependency_failed")
        elif all(d.state is TaskState.MERGED for d in deps):
            self._queued.add(tid)
            self._ready.put(tid)

    def _on_terminal(self, tid: str) -> None:
        for other, s in self.specs.items():
            if tid in s.depends:
                self._evaluate(other)

    # -- workers -----------------------------------------------------------------
    def start(self) -> "Engine":
        with self._lock:
            if self._threads:
                return self
            for i in range(self._nworkers):
                th = threading.Thread(target=self._worker, name=f"tcl-worker-{i}", daemon=True)
                th.start()
                self._threads.append(th)
            for tid in list(self.tasks):  # recovered PENDING tasks
                self._evaluate(tid)
        return self

    def stop(self) -> None:
        for _ in self._threads:
            self._ready.put(None)
        for th in self._threads:
            th.join(timeout=10)
        self._threads.clear()

    def wait(self, ids: Iterable[str] | None = None, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            while True:
                want = list(ids) if ids is not None else list(self.tasks)
                if all(self.tasks[i].state in TERMINAL for i in want):
                    return True
                rem = None if deadline is None else deadline - time.monotonic()
                if rem is not None and rem <= 0:
                    return False
                self._done.wait(rem)

    def run(self, specs, timeout: float | None = None) -> dict[str, str]:
        ids = self.submit(specs)
        self.start()
        self.wait(ids, timeout)
        return self.status_map(ids)

    def status_map(self, ids: Iterable[str] | None = None) -> dict[str, str]:
        with self._lock:
            return {i: self.tasks[i].state.value for i in (ids or self.tasks)}

    def status(self, tid: str) -> dict:
        with self._lock:
            if tid not in self.tasks:
                raise KeyError(tid)
            d = self.tasks[tid].to_dict()
            d["spec"] = self.specs[tid].to_dict()
            if tid in self.timeline:
                d["started"], d["finished"] = self.timeline[tid]
            return d

    def _worker(self) -> None:
        while True:
            tid = self._ready.get()
            if tid is None:
                return
            try:
                self._execute(tid)
            except Exception as e:  # fail-closed: any engine error fails the task
                log.exception("task.crash", extra={"task_id": tid})
                with self._lock:
                    if not self.tasks[tid].terminal:
                        self._set(tid, TaskState.FAILED, f"engine_error: {e}")
            finally:
                with self._lock:
                    self._queued.discard(tid)

    def _execute(self, tid: str) -> None:
        spec = self.specs[tid]
        task = self.tasks[tid]
        cwd = self.workdir
        if self.manager is not None:
            rec = self.manager.create(task.branch, base=spec.base, task_id=tid)
            cwd = Path(rec["path"])
            self.guard.register(tid, cwd, spec.base)
        self._set(tid, TaskState.ALLOCATED)
        self._sync_manager(task.branch, TaskState.EXECUTING)
        self._set(tid, TaskState.EXECUTING)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd),
               "LANG": "C.UTF-8", "TCL_TASK_ID": tid, **spec.env}
        t0 = time.time()
        proc = subprocess.Popen(spec.command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                preexec_fn=_sandbox(spec.limits), close_fds=True)
        try:
            out, err = proc.communicate(timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            self.timeline[tid] = (t0, time.time())
            self._finish_fail(tid, f"timeout after {spec.timeout}s")
            return
        self.timeline[tid] = (t0, time.time())
        self._emit("task.exited", task_id=tid, returncode=proc.returncode,
                   stdout=out.decode(errors="replace")[-2000:],
                   stderr=err.decode(errors="replace")[-2000:])
        if proc.returncode != 0:
            self._finish_fail(tid, f"exit {proc.returncode}")
            return
        self._sync_manager(task.branch, TaskState.VERIFYING)
        self._set(tid, TaskState.VERIFYING)
        if self.manager is not None:
            with self._merge_lock:  # serialize verify+merge across workers
                alert = self.guard.check(tid)
                if alert:
                    self._finish_fail(tid, "conflict: " + ",".join(alert.files))
                    return
                self.guard.unregister(tid)
        self._sync_manager(task.branch, TaskState.MERGED)
        self._set(tid, TaskState.MERGED)

    def _finish_fail(self, tid: str, err: str) -> None:
        self.guard.unregister(tid)
        self._sync_manager(self.tasks[tid].branch, TaskState.FAILED, err)
        self._set(tid, TaskState.FAILED, err)

    def _sync_manager(self, branch: str, to: TaskState, err: str | None = None) -> None:
        if self.manager is not None:
            self.manager.transition(branch, to, err)
