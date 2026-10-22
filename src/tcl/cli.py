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
              base: str = "HEAD", claim: List[str] = typer.Option([], "--claim")) -> None:
    try:
        rec = WorktreeManager(repo).create(branch, base=base, claims=claim)
    except WorktreeError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(rec))


if __name__ == "__main__":  # pragma: no cover
    app()
