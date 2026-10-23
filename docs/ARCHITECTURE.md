# TCL Production Architecture

This document describes the layered architecture of the Thresholded Circulatory Ledger (TCL).
Each layer is marked **Implemented**, **Partial**, or **Planned**.
A layer is only marked Implemented when it has passing tests in `tests/` or `ledger/`.

## Layer 1: Control Plane and Worktree Orchestration — Implemented (Phase 2)

- `src/tcl/orchestrator/state.py`: task state machine.
  Legal path is `PENDING → ALLOCATED → EXECUTING → VERIFYING → MERGED`.
  Any non-terminal state may go to `FAILED`; `MERGED` and `FAILED` are terminal.
  Illegal transitions raise `IllegalTransition` and leave state unchanged.
- `src/tcl/orchestrator/engine.py`: event-driven task DAG engine.
  - `submit()` validates the whole graph (unknown deps, self-deps and cycles are rejected via DFS; nothing is persisted on rejection).
  - A thread-safe ready queue feeds N worker threads. Terminal events re-evaluate dependents; a failed dependency fails dependents with `dependency_failed`.
  - Commands run sandboxed: new session/process group, minimal environment (host secrets are not inherited), `resource.setrlimit` for CPU seconds, address space, open files, file size, core dumps and (opt-in) process count.
  - A wall-clock timeout kills the whole process group and marks the task `FAILED`.
  - Task specs and state-machine records persist in SQLite (WAL, `synchronous=FULL`). On restart, tasks caught mid-flight are failed with `engine_restart` (never silently resumed); `PENDING` tasks resume.
  - With a `WorktreeManager`, each task gets its own worktree and is registered with the Conflict Guard. Verification and the merge decision are serialized under a lock.
- `src/tcl/orchestrator/conflict_guard.py` (re-exported at `src/tcl/worktree/conflict_guard.py`): per active worktree, modified files = `git diff --name-only <base>` ∪ untracked (`git ls-files --others --exclude-standard`), minus `.env.local`.
  Before a task may reach `MERGED`, the guard compares its set against every other active worktree. Any overlap emits a `ConflictAlert` (structured log, callbacks, daemon telemetry) and the task goes to `FAILED` with `conflict: <files>`.
- `src/tcl/worktree/manager.py`: `WorktreeManager` wraps `git worktree`.
  `layout="sibling"` (used by the engine and daemon) places worktrees at `<repo>/../worktrees/worktree-<id>`, all sharing the root `.git` object store (`$GIT_COMMON_DIR`). `layout="nested"` (`.worktrees/<id>`) remains the CLI default for compatibility.
  The registry (`.worktrees/registry.json`, `flock`-guarded) records ports, claims and task state. `health()` checks that each path exists, is known to git and has a resolvable `HEAD`.
- `src/tcl/daemon.py` (entrypoint `agents/daemon.py`): long-running daemon with a Unix-socket (mode `0600`) JSON-lines IPC: `health`, `submit`, `status`. It writes JSON telemetry (task states, conflict alerts, periodic worktree health) and runs a periodic Conflict Guard scan.
- `agents/orchestrator.py`: compatibility entrypoint; validates `PLAN` as a DAG and prints a topological order.
- `bin/worktree-harness`: thin shim over `tcl-cli worktree ...`.

Known limits (honest scope):

- `MERGED` means "verified and cleared by the Conflict Guard". The engine does not yet perform the `git merge` into the base branch; that lands with the integration pipeline.
- `RLIMIT_*` limits are per process; there are no cgroup quotas yet, and the filesystem is not restricted beyond the worktree working directory.

## Layer 2: Off-Chain Ledger and State — Partial

Implemented in Phase 1:

- `src/tcl/ledger/storage.py`: `LedgerStorage` abstract base class.
  - `SQLiteStorage` runs in WAL mode with `synchronous=FULL` and `BEGIN IMMEDIATE` transactions.
    Nested transactions use savepoints.
    Writes outside a transaction raise `StorageError` (fail-closed).
    Balances are stored as decimal text, so arbitrary-precision integers survive round trips.
  - `InMemoryStorage` gives the same semantics with snapshot rollback.
- `src/tcl/ledger/service.py`: `Ledger` applies lazy demurrage on touch (`settle`), sends fees to the vault, and records an event log.
  Results equal the pure functions bit-for-bit, and balance plus vault is conserved.
- `src/tcl/ledger/demurrage.py`, `demurrage_fixed.py`, `nullifiers.py`: the original pure math, moved unchanged.
  `NullifierSet` can optionally be storage-backed.
- `ledger/*.py`: backwards-compatible shims for the old import paths.

Planned:

- Poseidon Merkle tree over account state, with roots matching the circuit.
- Schema migrations beyond version 1.

## Layer 3: Zero-Knowledge — Planned

`circuits/puf_verify.circom` is still a sketch.
Planned work: move it to `src/tcl/zk/`, harden constraints, and add witness tests that must fail on under-constrained inputs.

## Layer 4: Smart Contracts — Planned

`contracts/CirculatoryEngine.sol` is still a skeleton.
Planned work: lazy O(1) decay matching `Ledger.settle`, OpenZeppelin `ReentrancyGuard` and access control, and Foundry tests with differential checks against the Python ledger.

## Layer 5: Interface and Delivery — Partial

Implemented in Phase 1:

- `src/tcl/cli.py` (Typer, console scripts `tcl` and `tcl-cli`): `ledger init|deposit|balance|apply-decay`, `worktree list|create|remove|health`, `task create|list|status`, `daemon start|health`. See `docs/API.md`.
  All output is JSON.
- `src/tcl/logs.py`: JSON-lines structured logging under the `tcl.*` logger namespace.
- `Containerfile`: rootless image (UID 65534) that runs the full test suite.
- `docs/ci/python-tests.yml` (staged; must be moved to `.github/workflows/` by a token with `workflow` scope): pytest on Python 3.11 to 3.13, plus a podman rootless container job.
- `provenance.json`: SHA-256 hashes of key artifacts, the base commit, and test results.

Planned:

- Release packaging and signed provenance.
