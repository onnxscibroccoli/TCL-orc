"""Python worktree manager (supersedes bin/worktree-harness).

Each task gets an isolated ``git worktree`` under ``.worktrees/<sanitized>``,
a unique port, and a Task state record. Conflict guard: a branch, path, or
file-claim may be held by at most one active (non-terminal) task.
"""
from __future__ import annotations

import fcntl
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from ..logs import get_logger
from ..orchestrator.state import Task, TaskState

log = get_logger("worktree")
BASE_PORT = 8000


class WorktreeError(RuntimeError):
    pass


class WorktreeConflict(WorktreeError):
    pass


class WorktreeManager:
    def __init__(self, repo: str | Path, base_dir: str = ".worktrees",
                 layout: str = "nested") -> None:
        """``layout="nested"``: ``<repo>/.worktrees/<branch>`` (Phase 1 default).
        ``layout="sibling"``: flat ``<repo>/../worktrees/worktree-<id>``; all share
        the root repository's object store via ``$GIT_COMMON_DIR``.
        The registry always lives in ``<repo>/.worktrees``."""
        if layout not in ("nested", "sibling"):
            raise WorktreeError(f"unknown layout {layout!r}")
        self.repo = Path(repo).resolve()
        self.layout = layout
        self.state_dir = self.repo / base_dir
        self.base = self.state_dir if layout == "nested" else self.repo.parent / "worktrees"
        self.registry = self.state_dir / "registry.json"
        self.lockfile = self.state_dir / ".lock"

    def path_for(self, branch: str) -> Path:
        name = self.sanitize(branch)
        return self.base / (name if self.layout == "nested" else f"worktree-{name}")

    # -- infra ---------------------------------------------------------
    def _git(self, *args: str, cwd: Path | None = None) -> str:
        r = subprocess.run(["git", *args], cwd=cwd or self.repo, capture_output=True, text=True)
        if r.returncode != 0:
            raise WorktreeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.lockfile, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                data = json.loads(self.registry.read_text()) if self.registry.exists() else {}
                yield data
                tmp = self.registry.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
                tmp.replace(self.registry)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    @staticmethod
    def sanitize(branch: str) -> str:
        if not branch or ".." in branch or branch.startswith(("-", "/")):
            raise WorktreeError(f"invalid branch name: {branch!r}")
        return branch.replace("/", "_")

    # -- api -----------------------------------------------------------
    def tasks(self) -> dict[str, Task]:
        with self._locked() as data:
            return {k: Task.from_dict(v["task"]) for k, v in data.items()}

    def list(self) -> list[dict]:
        with self._locked() as data:
            return [dict(v, branch=k) for k, v in sorted(data.items())]

    def git_worktrees(self) -> list[str]:
        out = self._git("worktree", "list", "--porcelain")
        return [l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("worktree ")]

    def create(self, branch: str, base: str = "HEAD", claims: Iterable[str] = (),
               task_id: str | None = None) -> dict:
        name = self.sanitize(branch)
        path = self.path_for(branch)
        self.base.mkdir(parents=True, exist_ok=True)
        claims = sorted(set(claims))
        with self._locked() as data:
            # conflict guard
            for b, rec in data.items():
                if TaskState(rec["task"]["state"]).value in ("MERGED", "FAILED"):
                    continue
                if b == branch:
                    raise WorktreeConflict(f"branch {branch} already has an active worktree")
                overlap = set(rec.get("claims", [])) & set(claims)
                if overlap:
                    raise WorktreeConflict(f"claims {sorted(overlap)} held by {b}")
            if path.exists():
                raise WorktreeConflict(f"path {path} already exists")
            task = Task(task_id or name, branch)
            used = {r["port"] for r in data.values()}
            port = BASE_PORT
            while port in used:
                port += 1
            exists = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                                    cwd=self.repo).returncode == 0
            if exists:
                self._git("worktree", "add", str(path), branch)
            else:
                self._git("worktree", "add", "-b", branch, str(path), base)
            (path / ".env.local").write_text(f"PORT={port}\n")
            task.transition(TaskState.ALLOCATED)
            rec = {"path": str(path), "port": port, "claims": claims, "task": task.to_dict()}
            data[branch] = rec
        log.info("worktree.create", extra={"branch": branch, "port": port, "path": str(path)})
        return dict(rec, branch=branch)

    def transition(self, branch: str, to: TaskState | str, error: str | None = None) -> Task:
        with self._locked() as data:
            if branch not in data:
                raise WorktreeError(f"unknown branch {branch}")
            t = Task.from_dict(data[branch]["task"])
            t.transition(TaskState(to), error)
            data[branch]["task"] = t.to_dict()
        log.info("task.transition", extra={"branch": branch, "state": t.state.value})
        return t

    def remove(self, branch: str, force: bool = False) -> None:
        with self._locked() as data:
            if branch not in data:
                raise WorktreeError(f"unknown branch {branch}")
            t = Task.from_dict(data[branch]["task"])
            if not t.terminal and not force:
                raise WorktreeConflict(f"task on {branch} is {t.state.value}; finish or force")
            (Path(data[branch]["path"]) / ".env.local").unlink(missing_ok=True)
            args = ["worktree", "remove", data[branch]["path"]] + (["--force"] if force else [])
            self._git(*args)
            del data[branch]
            self._git("worktree", "prune")
        log.info("worktree.remove", extra={"branch": branch})

    def health(self) -> list[dict]:
        """Health of each registered worktree: path exists, git knows it, HEAD resolves."""
        known = set(self.git_worktrees())
        out = []
        for rec in self.list():
            p = Path(rec["path"])
            head_ok = p.exists() and subprocess.run(
                ["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=p,
                capture_output=True).returncode == 0
            ok = p.exists() and str(p) in known and head_ok
            out.append({"branch": rec["branch"], "path": str(p), "state": rec["task"]["state"],
                        "exists": p.exists(), "registered": str(p) in known, "healthy": ok})
        return out

    def common_dir(self, path: str | Path) -> Path:
        return Path(self._git("rev-parse", "--path-format=absolute", "--git-common-dir",
                              cwd=Path(path)).strip())
