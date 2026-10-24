"""TCL command-line interface (Typer)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

import typer

from pydantic import ValidationError

from . import logs
from .config import Settings, get_settings
from .ledger import Ledger, SQLiteStorage, StorageError
from .ledger.poseidon_tree import MerkleProof, verify_proof
from .worktree import WorktreeManager, WorktreeError

app = typer.Typer(help="Thresholded Circulatory Ledger", no_args_is_help=True,
                  pretty_exceptions_enable=False)
ledger_app = typer.Typer(help="Off-chain ledger", no_args_is_help=True)
wt_app = typer.Typer(help="Worktree orchestration", no_args_is_help=True)
app.add_typer(ledger_app, name="ledger")
app.add_typer(wt_app, name="worktree")
task_app = typer.Typer(help="Task DAG orchestration", no_args_is_help=True)
daemon_app = typer.Typer(help="Long-running orchestrator daemon", no_args_is_help=True)
app.add_typer(task_app, name="task")
app.add_typer(daemon_app, name="daemon")
zk_app = typer.Typer(help="Groth16 proving (circom + snarkjs)", no_args_is_help=True)
chain_app = typer.Typer(help="EVM settlement (anvil/forge/cast)", no_args_is_help=True)
app.add_typer(zk_app, name="zk")
app.add_typer(chain_app, name="chain")

# Every option defaults to None and is resolved through tcl.config.Settings, so
# precedence is: CLI flag > TCL_* env > .env > config.yaml ($TCL_CONFIG) > defaults.
SOCK = typer.Option(None, "--socket", help="daemon socket [settings.socket]")
TASK_DB = typer.Option(None, "--tasks-db", help="[settings.tasks_db]")
DB = typer.Option(None, "--db", help="SQLite ledger path [settings.db]")
MODE = typer.Option(None, "--mode", help="linear|fixed [settings.mode]")
DEPTH = typer.Option(None, "--depth", help="Merkle tree depth [settings.tree_depth]")


def cfg(**overrides) -> Settings:
    try:
        return get_settings(**overrides)
    except ValidationError as e:
        _fail(f"invalid configuration: {e}")


@app.callback()
def main(log_level: Optional[str] = typer.Option(None, "--log-level")) -> None:
    logs.configure(cfg(log_level=log_level).log_level if log_level is None else log_level)


def _now(now: Optional[int]) -> int:
    return int(time.time()) if now is None else now


@ledger_app.command("init")
def ledger_init(db: Optional[Path] = DB) -> None:
    db = cfg(db=db).db
    s = SQLiteStorage(db)
    typer.echo(json.dumps({"db": str(db), "journal_mode": s.journal_mode}))


@ledger_app.command("deposit")
def ledger_deposit(account: str, amount: int, db: Optional[Path] = DB,
                   mode: Optional[str] = MODE, depth: Optional[int] = DEPTH,
                   now: Optional[int] = typer.Option(None)) -> None:
    c = cfg(db=db, mode=mode, tree_depth=depth)
    bal = Ledger(SQLiteStorage(c.db), c.mode, depth=c.tree_depth).deposit(account, amount, _now(now))
    typer.echo(json.dumps({"account": account, "balance": str(bal)}))


@ledger_app.command("balance")
def ledger_balance(account: str, db: Optional[Path] = DB, depth: Optional[int] = DEPTH) -> None:
    c = cfg(db=db, tree_depth=depth)
    lg = Ledger(SQLiteStorage(c.db), c.mode, depth=c.tree_depth)
    typer.echo(json.dumps({"account": account, "balance": str(lg.balance(account)),
                           "vault": str(lg.vault())}))


@ledger_app.command("apply-decay")
def ledger_apply_decay(db: Optional[Path] = DB, mode: Optional[str] = MODE,
                       depth: Optional[int] = DEPTH,
                       now: Optional[int] = typer.Option(None)) -> None:
    c = cfg(db=db, mode=mode, tree_depth=depth)
    lg = Ledger(SQLiteStorage(c.db), c.mode, depth=c.tree_depth)
    total = lg.apply_decay_all(_now(now))
    typer.echo(json.dumps({"extracted": str(total), "vault": str(lg.vault())}))


@ledger_app.command("root")
def ledger_root(db: Optional[Path] = DB, depth: Optional[int] = DEPTH,
                history: bool = typer.Option(False, "--history", help="include all roots")) -> None:
    """Print current Poseidon state root (decimal + hex) and WAL integrity."""
    c = cfg(db=db, tree_depth=depth)
    depth = c.tree_depth
    s = SQLiteStorage(c.db)
    lg = Ledger(s, c.mode, depth=depth)
    r = lg.root()
    out = {"root": str(r), "root_hex": hex(r), "depth": depth,
           "transitions": len(s.journal()), "integrity": s.integrity_check(),
           "state_verified": lg.verify_state()}
    if history:
        out["history"] = [str(x) for x in lg.roots()]
    typer.echo(json.dumps(out))


@ledger_app.command("rollback")
def ledger_rollback(root: str = typer.Option(..., "--root", help="prior root (dec or 0x hex)"),
                    db: Optional[Path] = DB, depth: Optional[int] = DEPTH,
                    now: Optional[int] = typer.Option(None)) -> None:
    """Restore state to a prior verified root from the journal."""
    c = cfg(db=db, tree_depth=depth)
    lg = Ledger(SQLiteStorage(c.db), c.mode, depth=c.tree_depth)
    try:
        undone = lg.rollback(int(root, 0), _now(now))
    except (StorageError, ValueError) as e:
        typer.echo(json.dumps({"error": str(e)}), err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps({"root": str(lg.root()), "undone": undone,
                           "state_verified": lg.verify_state()}))


@ledger_app.command("proof")
def ledger_proof(account: str, db: Optional[Path] = DB, depth: Optional[int] = DEPTH) -> None:
    """Emit a Merkle inclusion proof for an account leaf."""
    c = cfg(db=db, tree_depth=depth)
    lg = Ledger(SQLiteStorage(c.db), c.mode, depth=c.tree_depth)
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


def _ipc(sock: Optional[Path], req: dict) -> dict:
    from .daemon import request
    sock = cfg(socket=sock).socket
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
                sock: Optional[Path] = SOCK) -> None:
    """Submit a task to the running daemon: tcl-cli task create ID -- cmd args..."""
    spec = {"task_id": task_id, "command": list(command), "depends": list(depends),
            "timeout": timeout, "branch": branch, "base": base,
            "limits": {"memory_bytes": memory_mb << 20, "cpu_seconds": cpu_seconds}}
    typer.echo(json.dumps(_ipc(sock, {"op": "submit", "task": spec})))


@task_app.command("list")
def task_list(sock: Optional[Path] = SOCK) -> None:
    typer.echo(json.dumps(_ipc(sock, {"op": "status"})["tasks"], indent=2))


@task_app.command("status")
def task_status(task_id: str, sock: Optional[Path] = SOCK) -> None:
    typer.echo(json.dumps(_ipc(sock, {"op": "status", "task_id": task_id})["task"]))


@daemon_app.command("start")
def daemon_start(sock: Optional[Path] = SOCK, db: Optional[Path] = TASK_DB,
                 repo: Optional[Path] = typer.Option(None, "--repo"),
                 workers: int = typer.Option(4, "--workers"),
                 telemetry: Optional[Path] = typer.Option(None, "--telemetry"),
                 http_host: Optional[str] = typer.Option(None, "--http-host"),
                 http_port: Optional[int] = typer.Option(
                     None, "--http-port", help="HTTP /health port, 0 = off [settings.http_port]"),
                 ) -> None:
    """Run the daemon in the foreground (use a supervisor for background)."""
    from .daemon import Daemon
    c = cfg(socket=sock, tasks_db=db, http_host=http_host, http_port=http_port)
    c.socket.parent.mkdir(parents=True, exist_ok=True)
    c.tasks_db.parent.mkdir(parents=True, exist_ok=True)
    Daemon(c.socket, db=c.tasks_db, repo=repo, workers=workers, telemetry=telemetry,
           http=(c.http_host, c.http_port) if c.http_port else None).serve_forever()


@daemon_app.command("health")
def daemon_health(sock: Optional[Path] = SOCK) -> None:
    typer.echo(json.dumps(_ipc(sock, {"op": "health"})))


@daemon_app.command("ready")
def daemon_ready(sock: Optional[Path] = SOCK) -> None:
    """Readiness over IPC; exit 1 when not ready (fail-closed)."""
    r = _ipc(sock, {"op": "ready"})
    typer.echo(json.dumps(r))
    if not r.get("ok"):
        raise typer.Exit(1)


# ------------------------------------------------------------------- doctor
@app.command("version")
def version() -> None:
    """Print the installed tcl version."""
    from . import __version__
    typer.echo(__version__)


@app.command("doctor")
def doctor(component: List[str] = typer.Option(
               ["core", "zk", "chain"], "--component", "-c",
               help="core|zk|chain (repeatable); default: all"),
           rpc: bool = typer.Option(False, "--rpc", help="also probe settings.rpc_url"),
           as_json: bool = typer.Option(False, "--json")) -> None:
    """Check toolchain + config. Exit 1 if any selected check fails (fail-closed)."""
    import platform
    import sys
    from . import __version__
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = "", level: str = "error") -> None:
        results.append({"check": name, "ok": bool(ok), "level": level, "detail": detail})

    bad = set(component) - {"core", "zk", "chain"}
    if bad:
        _fail(f"unknown component(s): {sorted(bad)}")
    c = None
    check("python>=3.11", sys.version_info >= (3, 11), platform.python_version())
    check("tcl version", True, __version__, level="info")
    try:
        from .config import get_settings
        c = get_settings()
        check("config", True, f"rpc_url={c.rpc_url} db={c.db}")
    except Exception as e:  # noqa: BLE001
        check("config", False, f"{type(e).__name__}: {e}")
    from .resources import ResourcesMissing, resource_root
    for kind, label in (("zk", "artifacts (circuits)"), ("chain", "artifacts (contracts)")):
        if kind in component:
            try:
                check(label, True, str(resource_root(kind)))
            except ResourcesMissing as e:
                check(label, False, str(e))
    if "zk" in component:
        from .zk import Toolchain, ZkError
        try:
            tc = Toolchain.discover()
            check("zk toolchain (circom>=2.1, snarkjs, circomlib)", True,
                  f"circom={tc.circom} snarkjs={tc.snarkjs} circomlib={tc.circomlib}")
        except ZkError as e:
            check("zk toolchain (circom>=2.1, snarkjs, circomlib)", False, str(e))
    if "chain" in component:
        from .chain import ChainError
        from .chain.client import ANVIL_DEV_KEY, DEV_CHAIN_IDS, discover, wait_rpc
        try:
            tools = discover()
            check("foundry (forge, cast, anvil)", True, " ".join(tools.values()))
        except ChainError as e:
            tools = None
            check("foundry (forge, cast, anvil)", False, str(e))
        dev_key = c is not None and c.chain_private_key.lower() == ANVIL_DEV_KEY
        check("chain key", not dev_key or not rpc,
              "public anvil dev key in use (local only); set TCL_CHAIN_PRIVATE_KEY or use a "
              "forge keystore for real networks" if dev_key else "custom key configured",
              level="warn" if dev_key and not rpc else "error")
        if rpc and c is not None and tools:
            try:
                cid = wait_rpc(c.rpc_url, timeout=5, cast=tools["cast"])
                check("rpc reachable", True, f"chain_id={cid}")
                if dev_key and cid in DEV_CHAIN_IDS:
                    results[-2].update(ok=True, level="warn")
            except ChainError as e:
                check("rpc reachable", False, str(e))
    failed = [r for r in results if not r["ok"] and r["level"] == "error"]
    if as_json:
        typer.echo(json.dumps({"ok": not failed, "checks": results}))
    else:
        for r in results:
            mark = "ok  " if r["ok"] and r["level"] != "warn" else (
                "WARN" if r["level"] == "warn" else "FAIL")
            typer.echo(f"[{mark}] {r['check']}: {r['detail']}")
        typer.echo("doctor: " + ("all required checks passed" if not failed
                                 else f"{len(failed)} check(s) failed"))
    if failed:
        raise typer.Exit(1)


# ----------------------------------------------------------------------- zk
BUILD = typer.Option(None, "--build", help="circuit build dir [settings.zk_build]")
ZDEPTH = typer.Option(None, "--depth", help="circuit/tree depth [settings.zk_depth]")


def _zk_guard(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        from .chain import ChainError
        from .zk import ZkError
        try:
            return fn(*a, **kw)
        except (ZkError, ChainError, KeyError, ValueError, StorageError, OSError) as e:
            _fail(f"{type(e).__name__}: {e}")
    return wrapper


@zk_app.command("compile")
@_zk_guard
def zk_compile(build: Optional[Path] = BUILD, depth: Optional[int] = ZDEPTH,
               ptau_power: Optional[int] = typer.Option(None, "--ptau-power"),
               force: bool = typer.Option(False, "--force", help="rebuild even if cached")) -> None:
    """Compile StateTransition(depth) and run a DEV-ONLY Groth16 setup (cached)."""
    from .zk import ensure_artifacts
    c = cfg(zk_build=build, zk_depth=depth, ptau_power=ptau_power)
    t0 = time.time()
    art = ensure_artifacts(c.zk_depth, c.zk_build, c.ptau_power, force=force)
    typer.echo(json.dumps({"depth": art.depth, "r1cs": str(art.r1cs), "wasm": str(art.wasm),
                           "zkey": str(art.zkey), "vkey": str(art.vkey),
                           "seconds": round(time.time() - t0, 3), "dev_only": True}))


@zk_app.command("prove")
@_zk_guard
def zk_prove(account: str, out: Path = typer.Option(Path("proof.json"), "--out"),
             db: Optional[Path] = DB, build: Optional[Path] = BUILD,
             depth: Optional[int] = ZDEPTH, now: Optional[int] = typer.Option(None)) -> None:
    """Settle ACCOUNT on the ledger at NOW and write a verified Groth16 proof bundle."""
    from .zk import ensure_artifacts, prove_settlement
    c = cfg(db=db, zk_build=build, zk_depth=depth)
    art = ensure_artifacts(c.zk_depth, c.zk_build, c.ptau_power)
    lg = Ledger(SQLiteStorage(c.db), "fixed", depth=c.zk_depth)
    t0 = time.time()
    bundle = prove_settlement(lg, account, _now(now), art, c.zk_build / "work")
    out.write_text(json.dumps(bundle, indent=2) + "\n")
    typer.echo(json.dumps({"out": str(out), "public": bundle["public"], "verified": True,
                           "seconds": round(time.time() - t0, 3)}))


@zk_app.command("verify")
@_zk_guard
def zk_verify(proof_file: Path, vkey: Optional[Path] = typer.Option(None, "--vkey"),
              build: Optional[Path] = BUILD) -> None:
    """Verify a proof bundle with snarkjs. Exit 1 if invalid."""
    from .zk import verify
    c = cfg(zk_build=build)
    b = json.loads(proof_file.read_text())
    vk = vkey or c.zk_build / "verification_key.json"
    ok = verify(vk, b["proof"], b["publicSignals"], c.zk_build / "work")
    typer.echo(json.dumps({"valid": ok}))
    if not ok:
        raise typer.Exit(1)


@zk_app.command("settle")
@_zk_guard
def zk_settle(proof_file: Path, deployment: Optional[Path] = typer.Option(None, "--deployment")
              ) -> None:
    """Submit a proof bundle to the deployed CirculatoryEngine (alias of `chain settle`)."""
    chain_settle(proof_file, deployment)


# -------------------------------------------------------------------- chain
@chain_app.command("deploy")
@_zk_guard
def chain_deploy(rpc_url: Optional[str] = typer.Option(None, "--rpc-url"),
                 db: Optional[Path] = DB, build: Optional[Path] = BUILD,
                 depth: Optional[int] = ZDEPTH,
                 genesis_time: Optional[int] = typer.Option(
                     None, "--genesis-time", help="default: latest ledger timestamp"),
                 committed_verifier: bool = typer.Option(
                     False, "--committed-verifier",
                     help="use contracts/verifiers/StateVerifier.sol instead of exporting "
                          "one from the build zkey"),
                 out: Optional[Path] = typer.Option(None, "--out")) -> None:
    """Deploy StateVerifier + CirculatoryEngine (UUPS proxy) with genesis = ledger root."""
    from .chain import deploy
    c = cfg(db=db, zk_build=build, zk_depth=depth, rpc_url=rpc_url, deployment=out)
    s = SQLiteStorage(c.db)
    lg = Ledger(s, "fixed", depth=c.zk_depth)
    if genesis_time is None:
        times = [a[1] for a in (s.get_account(n) for n in s.accounts()) if a]
        genesis_time = max(times) if times else 0
    zkey = None if committed_verifier else c.zk_build / "circuit_final.zkey"
    if zkey is not None and not zkey.exists():
        _fail(f"{zkey} not found; run `tcl zk compile` or pass --committed-verifier")
    dep = deploy(c.rpc_url, lg.root(), genesis_time, c.zk_build / "chain", zkey=zkey,
                 key=c.chain_private_key)
    c.deployment.parent.mkdir(parents=True, exist_ok=True)
    c.deployment.write_text(json.dumps(dep.to_json(), indent=2) + "\n")
    typer.echo(json.dumps({**dep.to_json(), "file": str(c.deployment)}))


@chain_app.command("settle")
@_zk_guard
def chain_settle(proof_file: Path,
                 deployment: Optional[Path] = typer.Option(None, "--deployment")) -> None:
    """Call settleEpoch with a proof bundle; print the StateSettled event (exit 1 if absent)."""
    from .chain import Deployment, settle_epoch
    c = cfg(deployment=deployment)
    dep = Deployment.load(c.deployment)
    ev = settle_epoch(dep, json.loads(proof_file.read_text())["calldata"], key=c.chain_private_key)
    typer.echo(json.dumps(ev))


# --- localnet (offline, EVM-free local testnet; see docs/14_tcl_localnet_plan.md) -------
localnet_app = typer.Typer(help="Offline TCL-native local testnet (replaces anvil)",
                           no_args_is_help=True)
ln_time_app = typer.Typer(help="Chain time controls (dev only)", no_args_is_help=True)
localnet_app.add_typer(ln_time_app, name="time")
app.add_typer(localnet_app, name="localnet")
LN_DIR = typer.Option(Path(".tcl/localnet"), "--data-dir", help="localnet state directory")


def _ln_url(data_dir: Path) -> str:
    f = data_dir / "node.json"
    if not f.exists():
        typer.echo(f"localnet not running (no {f})", err=True)
        raise typer.Exit(1)
    return json.loads(f.read_text())["url"]


def _ln(data_dir: Path, method: str, *params: object) -> None:
    from .localnet.rpc import RPCError, call
    try:
        typer.echo(json.dumps(call(_ln_url(data_dir), method, list(params)), indent=2))
    except RPCError as e:
        typer.echo(json.dumps({"error": str(e), "code": e.code, "data": e.data}), err=True)
        raise typer.Exit(1)
    except OSError as e:
        typer.echo(f"localnet unreachable: {e}", err=True)
        raise typer.Exit(1)


@localnet_app.command("start")
def ln_start(data_dir: Path = LN_DIR, validators: int = typer.Option(4, "--validators", "-n"),
             port: int = typer.Option(8645, "--port"), seed: str = typer.Option("tcl-dev"),
             dev: bool = typer.Option(True, "--dev/--no-dev",
                                      help="enable dev-only RPC (mine/warp/snapshot/...)"),
             consensus: str = typer.Option("bft", "--consensus",
                                           help="bft (TendermintEngine, default) | fast "
                                                "(PLACEHOLDER round-robin)"),
             foreground: bool = typer.Option(False, "--foreground")) -> None:
    """Start N in-process validators with deterministic genesis and a localhost JSON-RPC."""
    import subprocess
    import sys
    from .localnet.rpc import call
    argv = [sys.executable, "-m", "tcl.localnet", "--data-dir", str(data_dir), "--port",
            str(port), "--validators", str(validators), "--seed", seed, "--consensus", consensus] + (["--dev"] if dev else [])
    if foreground:
        from .localnet.__main__ import main
        main(argv[3:])
        return
    if (data_dir / "node.json").exists():
        typer.echo("localnet already running (or stale node.json; run `tcl localnet stop`)",
                   err=True)
        raise typer.Exit(1)
    data_dir.mkdir(parents=True, exist_ok=True)
    log = open(data_dir / "node.log", "ab")
    proc = subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        if (data_dir / "node.json").exists():
            try:
                typer.echo(json.dumps(call(_ln_url(data_dir), "tcl_status"), indent=2))
                return
            except OSError:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    typer.echo(f"localnet failed to start; see {data_dir / 'node.log'}", err=True)
    raise typer.Exit(1)


@localnet_app.command("stop")
def ln_stop(data_dir: Path = LN_DIR) -> None:
    """Stop the localnet node (state persists in --data-dir)."""
    import os
    import signal
    f = data_dir / "node.json"
    if not f.exists():
        typer.echo("localnet not running")
        return
    pid = json.loads(f.read_text())["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        f.unlink(missing_ok=True)
    for _ in range(50):
        if not f.exists():
            break
        time.sleep(0.1)
    typer.echo(json.dumps({"stopped": pid}))


@localnet_app.command("status")
def ln_status(data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_status")


@localnet_app.command("mine")
def ln_mine(count: int = typer.Argument(1), data_dir: Path = LN_DIR) -> None:
    """Produce COUNT blocks now (dev only)."""
    _ln(data_dir, "tcl_mine", count)


@ln_time_app.command("advance")
def ln_time_advance(seconds: int, data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_warp", seconds)


@ln_time_app.command("set")
def ln_time_set(unix_ts: int, data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_setTime", unix_ts)


@localnet_app.command("snapshot")
def ln_snapshot(data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_snapshot")


@localnet_app.command("revert")
def ln_revert(snapshot_id: str, data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_revert", snapshot_id)


@localnet_app.command("faucet")
def ln_faucet(address: str, amount: int, data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_faucet", address, amount)


@localnet_app.command("fault")
def ln_fault(validator: int, kind: str = typer.Argument(..., help="offline|online|byzantine|honest"),
             data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_injectFault", {"validator": validator, "kind": kind})


@localnet_app.command("accounts")
def ln_accounts(data_dir: Path = LN_DIR) -> None:
    """List genesis dev accounts (Ed25519 pubkeys; dev only)."""
    _ln(data_dir, "tcl_devAccounts")


@localnet_app.command("transfer")
def ln_transfer(sender: str, to: str, amount: int, data_dir: Path = LN_DIR) -> None:
    """Sign (dev key) and submit a transfer from a genesis dev account (dev only)."""
    _ln(data_dir, "tcl_devSend", sender, "transfer", {"to": to, "amount": amount})


@localnet_app.command("account")
def ln_account(address: str, data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_getAccount", address)


@localnet_app.command("block")
def ln_block(height: int, data_dir: Path = LN_DIR) -> None:
    _ln(data_dir, "tcl_getBlock", height)


if __name__ == "__main__":  # pragma: no cover
    app()
