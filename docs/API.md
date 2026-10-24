# TCL API Reference

> **In plain words.** This is the reference list of every command and data format TCL offers. An *API* is the set of ways one program talks to another. A *CLI* is a program you control by typing commands. Every command prints its answer as *JSON*, a simple text format of labels and values like `{"balance": 10}`. If something goes wrong, the command stops with exit code 1 (a number meaning "failed") and does not guess. See the [Glossary](GLOSSARY.md) for other terms.
>
> **What this means for you:** look up the command you need here. You don't need to read it top to bottom.

`tcl` and `tcl-cli` are two names for the same program. It is built with Typer, a Python tool for making command-line programs.

Two rules hold for every command:

- The answer is printed as JSON.
- If something goes wrong, the command stops with exit code 1.

**How to read the command lists.** Words in CAPITALS, like `ACCOUNT`, are values you fill in. Parts in square brackets, like `[--now T]`, are optional. `...` means you may repeat the part before it. `a|b` means "pick a or b."

**Example.** `tcl ledger deposit alice 100` adds 100 base units to the account named `alice`. It prints something like `{"account": "alice", "balance": "100"}`.

## CLI

### `tcl-cli task`

A *task* is one job for the work manager, such as "run the tests." These commands send tasks to the *daemon*, a helper program running in the background. They reach it through its *Unix socket*, a private connection point on the same computer.

- Choose the socket with `--socket` or the environment variable `TCL_SOCKET`. The default is `.tcl/daemon.sock`.
- If the daemon can't be reached, the command fails with exit 1. It does not try anything else.

In `task create`, everything after `--` is the command the task will run. `--depends` names a task that must finish first.

| Command | Description |
|---|---|
| `task create ID [--depends ID]... [--timeout S] [--branch B] [--base REF] [--memory-mb N] [--cpu-seconds N] -- CMD ARGS...` | Submit one task. Prints `{"ok": true, "submitted": [ID]}`. |
| `task list` | Map of `task_id → state`. |
| `task status ID` | Full record: state, history, error, spec, start and finish times. |

### `tcl-cli daemon`

These start the daemon or ask whether it is healthy. *SIGTERM* is the normal "please stop" signal. A *worktree* is a separate working copy of the code.

| Command | Description |
|---|---|
| `daemon start [--socket P] [--tasks-db P] [--repo DIR] [--workers N] [--telemetry FILE]` | Runs in the foreground. With `--repo`, each task runs in a sibling worktree `../worktrees/worktree-task_<id>`. Stops cleanly on SIGTERM. |
| `daemon health` | Health response (see below). |

### `tcl-cli worktree`

These manage worktrees. A *claim* is a file a task says it will change. The *registry* is the list of all worktrees.

| Command | Description |
|---|---|
| `worktree create BRANCH [--repo DIR] [--base REF] [--claim FILE]... [--layout nested\|sibling]` | Allocate a worktree, port and task record. |
| `worktree list [--repo DIR]` | Registry records. |
| `worktree remove BRANCH [--force]` | Remove a worktree. Refused for active tasks unless `--force` is given. |
| `worktree health [--repo DIR]` | Per-worktree `exists`, `registered` and `healthy`. |

`bin/worktree-harness {create|remove|list|prune|health}` is a shortcut that passes your request on to these commands.

### `tcl-cli ledger`

These work with the account book (the *ledger*). The commands are `init`, `deposit ACCOUNT AMOUNT`, `balance ACCOUNT` and `apply-decay`. Options: `--db` (the database file), `--mode linear|fixed` (which decay rule to use) and `--now` (the time to use). The full list is in [`tcl ledger`](#tcl-ledger) below.

### `tcl zk`

These make and check *zero-knowledge proofs*. A zero-knowledge proof shows the math was done right without revealing private details. *Compile* turns the proof circuit into files the tools can use. The *DEV Groth16 setup* makes test-only keys; it is **not** safe for real use. A *bundle* is one file holding the proof and its public values.

| Command | Description |
|---|---|
| `zk compile [--build DIR] [--depth N] [--ptau-power P] [--force]` | Compile `StateTransition(N)` and run a DEV Groth16 setup; cached by depth. Prints paths + seconds. |
| `zk prove ACCOUNT [--now T] [--out proof.json] [--db] [--build] [--depth]` | Settle ACCOUNT on the ledger at T, prove, verify locally. Writes a bundle `{public, publicSignals, proof, calldata, verified}`. |
| `zk verify PROOF [--vkey F] [--build]` | snarkjs verify; `{"valid": bool}`, exit 1 if invalid. |
| `zk settle PROOF [--deployment F]` | Alias of `chain settle`. |

### `tcl chain`

These talk to a blockchain. *Deploy* means install the contracts. An *RPC URL* is the web address of a blockchain node. The *genesis root* is the account book's starting fingerprint. `deployment.json` records where everything was installed. A *revert* is when the blockchain refuses a call. `StaleRoot` means the proof is out of date, for example because it was already used.

| Command | Description |
|---|---|
| `chain deploy [--rpc-url U] [--db] [--build] [--depth] [--genesis-time T] [--committed-verifier] [--out F]` | Deploy StateVerifier + CirculatoryEngine (ERC1967/UUPS proxy, `initialize(owner, verifier, 0, 0, ledgerRoot, T)`). Writes `deployment.json` `{rpc_url, chain_id, owner, verifier, implementation, engine, genesis_root, genesis_time}`. |
| `chain settle PROOF [--deployment F]` | `settleEpoch(a, b, c, pubSignals)`; prints `{"event": "StateSettled", epoch, oldRoot, newRoot, fee, newTime, tx, block, gasUsed}`. Reverts (e.g. `StaleRoot` on replay) exit 1. |

### Daemon HTTP

The daemon can also answer a simple web health check.

- Start it with `tcl daemon start --http-host H --http-port P`. The default is `127.0.0.1:8787` (this computer only, port 8787). A port of `0` turns it off.
- It answers `GET /health` (also `/healthz`). The reply is the same JSON as the socket's `health` request.
- The status code tells you the result: 200 means healthy, 503 means degraded (partly broken), and 404 means you asked for a page that doesn't exist.

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

This is the format of one task. In plain words: give it a name (`task_id`), the command to run, the tasks it must wait for (`depends`), a time limit in seconds (`timeout`), and resource limits. `memory_bytes` of 1,073,741,824 is 1 GiB. `max_file_bytes` of 268,435,456 is 256 MiB.

Validation rules (checks every task must pass):

- `task_id` may use only `[A-Za-z0-9._-]`.
- `command` must be a non-empty list of text items. It is never secretly run through a *shell* (the program that reads typed commands). To use shell features, call `sh -c` yourself, as in the example.
- `timeout` must be greater than 0 and at most 86400 seconds.
- These are rejected: unknown fields, unknown dependencies, repeated ids, a task that depends on itself, and loops (*cycles*) such as A waits for B while B waits for A.
- The default branch is `task/<task_id>`.

*Environment variables* are settings passed to a program. Each task's program gets only these: `PATH`, `HOME` (set to the worktree), `LANG`, `TCL_TASK_ID`, plus anything you list in `env`. Secrets on the host are not passed in.

## Daemon IPC (Unix socket, JSON lines)

*IPC* (inter-process communication) means programs talking to each other. Here, you send the daemon one JSON message per line. It answers with one JSON line that contains `"ok"` (true or false).

- The socket has mode `0600`, so only its owner can use it.
- Lines larger than 1 MiB are rejected.

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

A *conflict alert* means two tasks changed the same file. In this example, worktrees `pkg-1` and `pkg-2` both changed `package.json`. `ts` is the time in seconds since 1970. The flagged task goes to `FAILED` with `error = "conflict: package.json"`. It never reaches `MERGED` (accepted).

## Telemetry (JSON lines, `--telemetry FILE`)

*Telemetry* is a running stream of status reports, written to the file you name. Each line has an `event` field saying what happened:

- `daemon.start`
- `daemon.stop`
- `daemon.health` (worktree health, written periodically)
- `task.submitted`
- `task.state` (`task_id`, `state`, `error`)
- `task.exited` (`returncode`, plus the last 2000 characters of stdout and stderr)
- `conflict.alert`

## Smart contract ABI

This section was a placeholder. The contract's functions are now listed in [Contract ABI](#contract-abi-circulatoryengine) at the end. An *ABI* is the list of a contract's functions and how to call them.

## Ledger, Merkle and contract reference (Phase 1b/3)

Every command prints one JSON object on *stdout* (normal output). Errors are printed on *stderr* (the error output), as JSON or text, and the command exits with status 1.

Options all these commands share:

- `--db PATH` picks the database file. Environment variable `TCL_DB`. Default `tcl.db`.
- `--log-level` sets how much detail is logged. Environment variable `TCL_LOG_LEVEL`.
- Commands that use the Merkle tree also take `--depth N` (environment variable `TCL_TREE_DEPTH`, default 20). It must match the depth the database was made with.

*Field elements* (the large numbers used in proofs) and balances are written as decimal text. `root_hex` is the same root written in hexadecimal (base 16).

## `tcl ledger`

What each output means: `extracted` is the decay collected. `vault` is the shared pot. `root` is the fingerprint of the account book. `integrity` and `state_verified` show whether the database and root checked out. `undone` is how many changes a rollback undid. `proof` returns what you need to show one account is in the book.

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

How to read a proof, step by step:

1. Start at the account's leaf (level 0).
2. At each level `k`, `siblings[k]` is the code next to yours.
3. `path_bits[k]` says which side you are on: 0 means left, 1 means right. It equals `(index >> k) & 1`, which is bit `k` of the slot number.
4. Combine the two as `Poseidon(left, right)` (circomlib `Poseidon(2)`) to get the code one level up.
5. After the last level you have the root. If it matches, the proof is valid.

**Example.** Slot `index = 5` is 101 in binary. So `path_bits` starts `[1, 0, 1, 0, …]`: right, left, right, then left at every higher level.

## `tcl worktree`

| Command | Output |
|---|---|
| `list [--repo PATH]` | array of worktree records |
| `create BRANCH [--repo PATH] [--base REF] [--claim PATH]...` | worktree record including `task.state`. Exits 1 on a conflict. |

## Python API

These are the main pieces you can use from your own Python code.

- `tcl.storage.SQLiteStorage(path)`, `InMemoryStorage()`, `open_storage(url)`
- `tcl.ledger.Ledger(storage, mode="fixed", depth=20)`: `deposit`, `settle`, `apply_decay_all`, `redistribute`, `spend_nullifier`, `root`, `roots`, `proof`, `verify_state`, `rollback`
- `tcl.ledger.PoseidonMerkleTree(storage, depth)`, `tcl.ledger.verify_proof(root, proof)`
- `tcl.crypto.poseidon.poseidon([x1..x4])`

Planned (not built): formal task payload schemas, `tcl task` and `tcl prove` commands. The contract ABI is listed at the end of this page.

## ZK prover (`tcl.zk`)

**In plain words.** This is how to make a proof from Python. The example below does four steps: (1) build the circuit and a test-only setup, (2) settle the account `alice` and prepare the inputs, (3) make the proof, (4) check it.

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

Some words in the table: *G2 coordinates* are the parts of a proof point; EIP-197 is the blockchain standard that needs them in swapped order. *r1cs* and *wasm* are the compiled circuit files.

Circuit input JSON. All values are decimal text.

- Public (everyone sees): `oldRoot` and `newTime`.
- Private (hidden by the proof):
  - `accountId`: the account name hashed with SHA-256, then reduced to fit the field (sha256(label) mod p).
  - `oldBalance` and `oldTime`.
  - `pathElements[depth]`: the sibling codes, bottom to top.
  - `pathIndices[depth]`: 0 means the node is the left child, 1 means right.

## Contract ABI (`CirculatoryEngine`)

**In plain words.** These are the functions of TCL's main blockchain contract. *Access* says who may call each one. *Payable* means you send coins with the call. *View* means it only reads and costs nothing to ask. *Not paused* means it is blocked while the contract is paused in an emergency. `msg.value` is the amount of coin sent. CEI and nonReentrant are safety patterns that stop a caller from taking money twice. An *event* is a public announcement written to the blockchain log.

**Example.** You call `deposit()` and send 1 ETH. The contract first applies any decay you owe (`settle`), then adds 1 ETH to your balance and announces `Deposited`.

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
