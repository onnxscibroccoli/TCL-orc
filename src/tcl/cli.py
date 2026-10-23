"""TCL command-line interface (Typer)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

import typer

from . import logs
from .ledger import Ledger, SQLiteStorage, StorageError
from .ledger.poseidon_tree import MerkleProof, verify_proof
from .worktree import WorktreeManager, WorktreeError

app = typer.Typer(help="Thresholded Circulatory Ledger", no_args_is_help=True)
ledger_app = typer.Typer(help="Off-chain ledger", no_args_is_help=True)
wt_app = typer.Typer(help="Worktree orchestration", no_args_is_help=True)
app.add_typer(ledger_app, name="ledger")
app.add_typer(wt_app, name="worktree")

DB = typer.Option(Path("tcl.db"), "--db", envvar="TCL_DB", help="SQLite ledger path")
MODE = typer.Option("fixed", "--mode", help="linear|fixed")
DEPTH = typer.Option(20, "--depth", envvar="TCL_TREE_DEPTH", help="Merkle tree depth")


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


@ledger_app.command("root")
def ledger_root(db: Path = DB, depth: int = DEPTH,
                history: bool = typer.Option(False, "--history", help="include all roots")) -> None:
    """Print current Poseidon state root (decimal + hex) and WAL integrity."""
    s = SQLiteStorage(db)
    lg = Ledger(s, depth=depth)
    r = lg.root()
    out = {"root": str(r), "root_hex": hex(r), "depth": depth,
           "transitions": len(s.journal()), "integrity": s.integrity_check(),
           "state_verified": lg.verify_state()}
    if history:
        out["history"] = [str(x) for x in lg.roots()]
    typer.echo(json.dumps(out))


@ledger_app.command("rollback")
def ledger_rollback(root: str = typer.Option(..., "--root", help="prior root (dec or 0x hex)"),
                    db: Path = DB, depth: int = DEPTH,
                    now: Optional[int] = typer.Option(None)) -> None:
    """Restore state to a prior verified root from the journal."""
    lg = Ledger(SQLiteStorage(db), depth=depth)
    try:
        undone = lg.rollback(int(root, 0), _now(now))
    except (StorageError, ValueError) as e:
        typer.echo(json.dumps({"error": str(e)}), err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps({"root": str(lg.root()), "undone": undone,
                           "state_verified": lg.verify_state()}))


@ledger_app.command("proof")
def ledger_proof(account: str, db: Path = DB, depth: int = DEPTH) -> None:
    """Emit a Merkle inclusion proof for an account leaf."""
    lg = Ledger(SQLiteStorage(db), depth=depth)
    try:
        pf = lg.proof(account)
    except KeyError as e:
        typer.echo(json.dumps({"error": str(e)}), err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(pf.to_json()))


@ledger_app.command("verify-proof")
def ledger_verify_proof(proof_file: Path) -> None:
    """Verify a proof JSON (from `tcl ledger proof`). Exit 1 if invalid."""
    d = json.loads(proof_file.read_text())
    pf = MerkleProof(index=int(d["index"]), leaf=int(d["leaf"]),
                     siblings=[int(x) for x in d["siblings"]], root=int(d["root"]))
    ok = verify_proof(pf.root, pf)
    typer.echo(json.dumps({"valid": ok}))
    if not ok:
        raise typer.Exit(1)


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
