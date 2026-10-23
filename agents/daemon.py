#!/usr/bin/env python3
"""TCL agent daemon (compat entrypoint). Real implementation: ``tcl.daemon``.

``dispatch_task`` is kept for backwards compatibility; ``main_loop`` now starts
the long-running IPC daemon instead of the old polling placeholder.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tcl.daemon import Daemon, request  # noqa: E402


def dispatch_task(task_id: str, worktree: str):
    print(f"[daemon] dispatching {task_id} into {worktree}")
    return subprocess.run(["echo", f"Running quality gates in {worktree}"], check=False)


def main_loop(socket_path: str = ".tcl/daemon.sock", db: str = ".tcl/tasks.db", repo: str = "."):
    Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
    Daemon(socket_path, db=db, repo=repo).serve_forever()


__all__ = ["Daemon", "request", "dispatch_task", "main_loop"]

if __name__ == "__main__":
    main_loop()
