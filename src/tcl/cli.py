"""TCL command-line interface (Typer)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

import typer

from . import logs
from .ledger import Ledger, SQLiteStorage
from .worktree import WorktreeManager, WorktreeError

app = typer.Typer(help="Thresholded Circulatory Ledger", no_args_is_help=True)
ledger_app = typer.Typer(help="Off-chain ledger", no_args_is_help=True)
wt_app = typer.Typer(help="Worktree orchestration", no_args_is_help=True)
app.add_typer(ledger_app, name="ledger")
app.add_typer(wt_app, name="worktree")
task_app = typer.Typer(help="Task DAG orchestration", no_args_is_help=True)
daemon_app = typer.Typer(help="Long-running orchestrator daemon", no_args_is_help=True)
app.add_typer(task_app, name="task")
app.add_typer(daemon_app, name="daemon")
SOCK = typer.Option(Path(".tcl/daemon.sock"), "--socket", envvar="TCL_SOCKET")
TASK_DB = typer.Option(Path(".tcl/tasks.db"), "--tasks-db", envvar="TCL_TASKS_DB")

DB = typer.Option(Path("tcl.db"), "--db", envvar="TCL_DB", help="SQLite ledger path")
MODE = typer.Option("fixed", "--mode", help="linear|fixed")


@app.callback()
def main(log_level: str = typer.Option("WARNING", "--log-level", envvar="TCL_LOG_LEVEL")) -> None:
    logs.configure(log_level)


def _now(now: Optional[int]) -> int:
    return int(time.time()) if now is None else now


@ledger_app.command("init")
def ledger_init(db: Path = DB) -> None:
    s = SQLiteStorage(db)
    typer.echo(json.dumps({"db": str(db), "journal_mode": s.journal_mode}))


@ledger_app.command("deposit")
def ledger_deposit(account: str, amount: int, db: Path = DB, mode: str = MODE,
                   now: Optional[int] = typer.Option(None)) -> None:
    bal = Ledger(SQLiteStorage(db), mode).deposit(account, amount, _now(now))
    typer.echo(json.dumps({"account": account, "balance": str(bal)}))


@ledger_app.command("balance")
def ledger_balance(account: str, db: Path = DB) -> None:
    lg = Ledger(SQLiteStorage(db))
    typer.echo(json.dumps({"account": account, "balance": str(lg.balance(account)),
                           "vault": str(lg.vault())}))


@ledger_app.command("apply-decay")
def ledger_apply_decay(db: Path = DB, mode: str = MODE,
                       now: Optional[int] = typer.Option(None)) -> None:
    lg = Ledger(SQLiteStorage(db), mode)
    total = lg.apply_decay_all(_now(now))
    typer.echo(json.dumps({"extracted": str(total), "vault": str(lg.vault())}))


@wt_app.command("list")
def wt_list(repo: Path = typer.Option(Path("."), "--repo")) -> None:
    typer.echo(json.dumps(WorktreeManager(repo).list(), indent=2))


@wt_app.command("create")
def wt_create(branch: str, repo: Path = typer.Option(Path("."), "--repo"),
              base: str = "HEAD", claim: List[str] = typer.Option([], "--claim"),
              layout: str = typer.Option("nested", "--layout", help="nested|sibling")) -> None:
    try:
        rec = WorktreeManager(repo, layout=layout).create(branch, base=base, claims=claim)
    except WorktreeError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(rec))


@wt_app.command("remove")
def wt_remove(branch: str, repo: Path = typer.Option(Path("."), "--repo"),
              force: bool = False) -> None:
    try:
        WorktreeManager(repo).remove(branch, force=force)
    except WorktreeError as e:
        _fail(str(e))
    typer.echo(json.dumps({"removed": branch}))


@wt_app.command("health")
def wt_health(repo: Path = typer.Option(Path("."), "--repo")) -> None:
    typer.echo(json.dumps(WorktreeManager(repo).health(), indent=2))


def _fail(msg: str) -> None:
    typer.echo(json.dumps({"ok": False, "error": msg}), err=True)
    raise typer.Exit(1)


def _ipc(sock: Path, req: dict) -> dict:
    from .daemon import request
    try:
        resp = request(sock, req)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        _fail(f"daemon not reachable at {sock}: {e}")
    if not resp.get("ok"):
        typer.echo(json.dumps(resp), err=True)
        raise typer.Exit(1)
    return resp


@task_app.command("create")
def task_create(task_id: str, command: List[str] = typer.Argument(..., help="argv after --"),
                depends: List[str] = typer.Option([], "--depends"),
                timeout: float = typer.Option(300.0, "--timeout"),
                branch: Optional[str] = typer.Option(None, "--branch"),
                base: str = typer.Option("HEAD", "--base"),
                memory_mb: int = typer.Option(1024, "--memory-mb"),
                cpu_seconds: int = typer.Option(60, "--cpu-seconds"),
                sock: Path = SOCK) -> None:
    """Submit a task to the running daemon: tcl-cli task create ID -- cmd args..."""
    spec = {"task_id": task_id, "command": list(command), "depends": list(depends),
            "timeout": timeout, "branch": branch, "base": base,
            "limits": {"memory_bytes": memory_mb << 20, "cpu_seconds": cpu_seconds}}
    typer.echo(json.dumps(_ipc(sock, {"op": "submit", "task": spec})))


@task_app.command("list")
def task_list(sock: Path = SOCK) -> None:
    typer.echo(json.dumps(_ipc(sock, {"op": "status"})["tasks"], indent=2))


@task_app.command("status")
def task_status(task_id: str, sock: Path = SOCK) -> None:
    typer.echo(json.dumps(_ipc(sock, {"op": "status", "task_id": task_id})["task"]))


@daemon_app.command("start")
def daemon_start(sock: Path = SOCK, db: Path = TASK_DB,
                 repo: Optional[Path] = typer.Option(None, "--repo"),
                 workers: int = typer.Option(4, "--workers"),
                 telemetry: Optional[Path] = typer.Option(None, "--telemetry")) -> None:
    """Run the daemon in the foreground (use a supervisor for background)."""
    from .daemon import Daemon
    sock.parent.mkdir(parents=True, exist_ok=True)
    db.parent.mkdir(parents=True, exist_ok=True)
    Daemon(sock, db=db, repo=repo, workers=workers, telemetry=telemetry).serve_forever()


@daemon_app.command("health")
def daemon_health(sock: Path = SOCK) -> None:
    typer.echo(json.dumps(_ipc(sock, {"op": "health"})))


if __name__ == "__main__":  # pragma: no cover
    app()
