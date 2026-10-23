# TCL API Reference

The console scripts `tcl` and `tcl-cli` are the same Typer app.
All command output is JSON. Errors exit with code 1.

## CLI

### `tcl-cli task`

Task commands talk to a running daemon over its Unix socket (`--socket`, env `TCL_SOCKET`, default `.tcl/daemon.sock`).
If the daemon can't be reached they fail with exit 1 and do not fall back to anything else.

| Command | Description |
|---|---|
| `task create ID [--depends ID]... [--timeout S] [--branch B] [--base REF] [--memory-mb N] [--cpu-seconds N] -- CMD ARGS...` | Submit one task. Prints `{"ok": true, "submitted": [ID]}`. |
| `task list` | Map of `task_id → state`. |
| `task status ID` | Full record: state, history, error, spec, start and finish times. |

### `tcl-cli daemon`

| Command | Description |
|---|---|
| `daemon start [--socket P] [--tasks-db P] [--repo DIR] [--workers N] [--telemetry FILE]` | Runs in the foreground. With `--repo`, each task runs in a sibling worktree `../worktrees/worktree-task_<id>`. Stops cleanly on SIGTERM. |
| `daemon health` | Health response (see below). |

### `tcl-cli worktree`

| Command | Description |
|---|---|
| `worktree create BRANCH [--repo DIR] [--base REF] [--claim FILE]... [--layout nested\|sibling]` | Allocate a worktree, port and task record. |
| `worktree list [--repo DIR]` | Registry records. |
| `worktree remove BRANCH [--force]` | Remove a worktree. Refused for active tasks unless `--force` is given. |
| `worktree health [--repo DIR]` | Per-worktree `exists`, `registered` and `healthy`. |

`bin/worktree-harness {create|remove|list|prune|health}` forwards to these commands.

### `tcl-cli ledger`

`init`, `deposit ACCOUNT AMOUNT`, `balance ACCOUNT`, `apply-decay` (`--db`, `--mode linear|fixed`, `--now`).

### `tcl zk`

| Command | Description |
|---|---|
| `zk compile [--build DIR] [--depth N] [--ptau-power P] [--force]` | Compile `StateTransition(N)` and run a DEV Groth16 setup; cached by depth. Prints paths + seconds. |
| `zk prove ACCOUNT [--now T] [--out proof.json] [--db] [--build] [--depth]` | Settle ACCOUNT on the ledger at T, prove, verify locally. Writes a bundle `{public, publicSignals, proof, calldata, verified}`. |
| `zk verify PROOF [--vkey F] [--build]` | snarkjs verify; `{"valid": bool}`, exit 1 if invalid. |
| `zk settle PROOF [--deployment F]` | Alias of `chain settle`. |

### `tcl chain`

| Command | Description |
|---|---|
| `chain deploy [--rpc-url U] [--db] [--build] [--depth] [--genesis-time T] [--committed-verifier] [--out F]` | Deploy StateVerifier + CirculatoryEngine (ERC1967/UUPS proxy, `initialize(owner, verifier, 0, 0, ledgerRoot, T)`). Writes `deployment.json` `{rpc_url, chain_id, owner, verifier, implementation, engine, genesis_root, genesis_time}`. |
| `chain settle PROOF [--deployment F]` | `settleEpoch(a, b, c, pubSignals)`; prints `{"event": "StateSettled", epoch, oldRoot, newRoot, fee, newTime, tx, block, gasUsed}`. Reverts (e.g. `StaleRoot` on replay) exit 1. |

### Daemon HTTP

`tcl daemon start --http-host H --http-port P` (defaults `127.0.0.1:8787`, `0` = off) serves `GET /health` (alias `/healthz`): the same JSON as the socket `health` op; 200 if `status == "healthy"`, 503 if degraded, 404 for other paths.

## Task spec (JSON)

```json
{
  "task_id": "build-1",
  "command": ["sh", "-c", "make test"],
  "depends": ["fetch-1"],
  "timeout": 300.0,
  "branch": null,
  "base": "HEAD",
  "limits": {"cpu_seconds": 60, "memory_bytes": 1073741824, "max_files": 256,
             "max_file_bytes": 268435456, "max_procs": null},
  "env": {}
}
```

Validation rules:

- `task_id` may use only `[A-Za-z0-9._-]`.
- `command` must be a non-empty list of strings. It is never run through a shell implicitly.
- `timeout` must be greater than 0 and at most 86400 seconds.
- Unknown fields, unknown dependencies, duplicate ids, self-dependencies and cycles are rejected.
- The default branch is `task/<task_id>`.

Child processes get only these environment variables: `PATH`, `HOME` (set to the worktree), `LANG`, `TCL_TASK_ID`, plus anything in `env`.

## Daemon IPC (Unix socket, JSON lines)

The daemon reads one JSON object per line and answers with one JSON line containing `"ok"`.
The socket is created with mode `0600`. Lines larger than 1 MiB are rejected.

| Request | Response |
|---|---|
| `{"op":"health"}` | `{"ok":true,"status":"healthy\|degraded","pid":…,"uptime":…,"tasks":{id:state},"worktrees":[…],"alerts":[…]}` |
| `{"op":"submit","tasks":[spec,…]}` or `{"op":"submit","task":spec}` | `{"ok":true,"submitted":[ids]}` |
| `{"op":"status"}` | `{"ok":true,"tasks":{id:state}}` |
| `{"op":"status","task_id":"x"}` | `{"ok":true,"task":{…}}` |
| invalid request | `{"ok":false,"error":"…"}` |

## Conflict alert

```json
{"alert_id": "uuid", "ts": 1761170000.0, "worktree": "pkg-1",
 "others": ["pkg-2"], "files": ["package.json"]}
```

The flagged task goes to `FAILED` with `error = "conflict: package.json"` and never reaches `MERGED`.

## Telemetry (JSON lines, `--telemetry FILE`)

Each line has an `event` field:

- `daemon.start`
- `daemon.stop`
- `daemon.health` (worktree health, written periodically)
- `task.submitted`
- `task.state` (`task_id`, `state`, `error`)
- `task.exited` (`returncode`, plus the last 2000 characters of stdout and stderr)
- `conflict.alert`

## Smart contract ABI

Planned for Phase 3; not yet stable.

## Ledger, Merkle and contract reference (Phase 1b/3)

Every command prints one JSON object on stdout. Errors print JSON or text on stderr and exit with status 1.
Shared options: `--db PATH` (env `TCL_DB`, default `tcl.db`) and `--log-level` (env `TCL_LOG_LEVEL`).
Ledger tree commands also take `--depth N` (env `TCL_TREE_DEPTH`, default 20). It must match the depth the database was created with.

Field elements and balances are decimal strings. `root_hex` is the same value in hex.

## `tcl ledger`

| Command | Output |
|---|---|
| `init` | `{"db", "journal_mode"}` |
| `deposit ACCOUNT AMOUNT [--now TS] [--mode linear\|fixed]` | `{"account", "balance"}` |
| `balance ACCOUNT` | `{"account", "balance", "vault"}` |
| `apply-decay [--now TS] [--mode]` | `{"extracted", "vault"}` |
| `root [--history]` | `{"root", "root_hex", "depth", "transitions", "integrity", "state_verified", "history"?}` |
| `rollback --root ROOT [--now TS]` | `{"root", "undone", "state_verified"}`. Exits 1 if the root is not in the journal. |
| `proof ACCOUNT` | `{"index", "leaf", "root", "siblings": [..], "path_bits": [..]}` |
| `verify-proof FILE` | `{"valid": bool}`. Exits 1 if invalid. |

Proof semantics: `siblings[k]` is the sibling at level `k` (leaf level = 0).
`path_bits[k] = (index >> k) & 1`, where 1 means the current node is the right child.
Node = `Poseidon(left, right)` (circomlib `Poseidon(2)`).

## `tcl worktree`

| Command | Output |
|---|---|
| `list [--repo PATH]` | array of worktree records |
| `create BRANCH [--repo PATH] [--base REF] [--claim PATH]...` | worktree record including `task.state`. Exits 1 on a conflict. |

## Python API

- `tcl.storage.SQLiteStorage(path)`, `InMemoryStorage()`, `open_storage(url)`
- `tcl.ledger.Ledger(storage, mode="fixed", depth=20)`: `deposit`, `settle`, `apply_decay_all`, `redistribute`, `spend_nullifier`, `root`, `roots`, `proof`, `verify_state`, `rollback`
- `tcl.ledger.PoseidonMerkleTree(storage, depth)`, `tcl.ledger.verify_proof(root, proof)`
- `tcl.crypto.poseidon.poseidon([x1..x4])`

Planned: task payload schemas, `tcl task`, `tcl prove`, contract ABI.

## ZK prover (`tcl.zk`)

```python
from tcl.zk import (Toolchain, compile_circuit, dev_ptau, groth16_setup,
                    build_transition_input, witness, prove, verify,
                    export_verifier, solidity_calldata)

art = groth16_setup(compile_circuit(4, Path("build")), dev_ptau(13, Path("dev.ptau")))
inputs, expected = build_transition_input(ledger, "alice", now)   # settles the account
proof, public = prove(art, witness(art, inputs, Path("w.wtns")))
assert verify(art.vkey, proof, public, Path("tmp"))
```

| Function | Notes |
|---|---|
| `Toolchain.discover()` | Reads `TCL_CIRCOM`, `TCL_SNARKJS` and `TCL_CIRCOMLIB`, falling back to `PATH` and `./node_modules`. Raises `ToolchainMissing`. Requires circom 2.1 or newer. |
| `compile_circuit(depth, out_dir)` | Compiles `StateTransition(depth)` to r1cs and wasm. Depth must be 1..32. |
| `dev_ptau(power, path)` | Makes a DEV-ONLY prepared powers-of-tau file, or reuses an existing one. Depth 4 needs power 13. |
| `groth16_setup(art, ptau)` | Produces the zkey (one local contribution) and `verification_key.json`. |
| `witness(art, inputs, out)` | Raises `ZkError` if any constraint fails. |
| `prove(art, wtns)` | Returns `(proof, publicSignals)`. The public signals are `[newRoot, fee, oldRoot, newTime]`. |
| `verify(vkey, proof, public, work)` | Returns a bool and never raises on a bad proof. |
| `export_verifier(zkey, out)` | Exports the snarkjs Solidity verifier, renamed to `StateVerifier`. |
| `solidity_calldata(proof, public)` | Returns `{a, b, c, pubSignals}` with G2 coordinates swapped for EIP-197. |
| `build_transition_input(ledger, account, now)` | Fixed mode only, with `0 <= now - last <= 604800`. |

Circuit input JSON (all values are decimal strings):
`oldRoot`, `newTime` (public), plus `accountId` (sha256(label) mod p), `oldBalance`, `oldTime`, `pathElements[depth]` (bottom-up siblings) and `pathIndices[depth]` (0 means the node is the left child).

## Contract ABI (`CirculatoryEngine`)

| Function | Access | Effect |
|---|---|---|
| `initialize(owner, verifier, threshold, ratePerSecond, genesisRoot, genesisTime)` | once, via proxy | Sets up Ownable, Pausable and ReentrancyGuard. |
| `deposit()` payable | anyone, not paused | Settles the caller, then credits `msg.value`. |
| `withdraw(uint256)` | anyone, not paused | Settles the caller, then sends ETH (CEI, nonReentrant). |
| `settle(address)` | anyone, not paused | Applies pending demurrage. The fee goes to `vault`. |
| `balanceOf(address)`, `pendingFee(address)`, `storedBalance(address)` | view | O(1). |
| `redistribute(address,uint256)` | owner | Moves `vault` into a balance. |
| `settleEpoch(uint256[2],uint256[2][2],uint256[2],uint256[4])` | settler or owner, not paused | Verifies the Groth16 proof and advances `stateRoot`. Emits `StateSettled`. |
| `setSettler`, `pause`, `unpause`, `upgradeToAndCall` | owner | |

Events: `Deposited`, `Withdrawn`, `DemurrageApplied`, `Redistributed`, `SettlerUpdated`, `StateSettled(uint64 indexed epoch, uint256 oldRoot, uint256 newRoot, uint256 fee, uint64 newTime)`.
