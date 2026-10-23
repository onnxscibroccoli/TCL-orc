# TCL Production Architecture

This document describes the layered architecture of the Thresholded Circulatory Ledger (TCL).
Each layer is marked **Implemented**, **Partial**, or **Planned**.
A layer is only marked Implemented when it has passing tests in `tests/` or `ledger/`.

## Layer 1: Control Plane and Worktree Orchestration — Partial

Implemented in Phase 1:

- `src/tcl/orchestrator/state.py`: task state machine.
  Legal path is `PENDING → ALLOCATED → EXECUTING → VERIFYING → MERGED`.
  Any non-terminal state may go to `FAILED`.
  `MERGED` and `FAILED` are terminal.
  Every other transition raises `IllegalTransition` and leaves state unchanged.
- `src/tcl/worktree/manager.py`: `WorktreeManager` wraps `git worktree`.
  It allocates unique ports, writes `.env.local`, and keeps a registry at `.worktrees/registry.json` under an exclusive `flock`.
  The conflict guard rejects a second active task on the same branch or path, and overlapping file claims between active tasks.
  Claims are released when a task reaches a terminal state.
- `bin/worktree-harness` remains for backwards compatibility; the Python manager supersedes it.

Planned:

- `agents/daemon.py` driving tasks through the manager, including automated verification and merge.
- Persisting task state in the ledger SQLite database instead of JSON.

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

- `src/tcl/cli.py` (Typer): `tcl ledger init|deposit|balance|apply-decay` and `tcl worktree list|create`.
  All output is JSON.
- `src/tcl/logs.py`: JSON-lines structured logging under the `tcl.*` logger namespace.
- `Containerfile`: rootless image (UID 65534) that runs the full test suite.
- `.github/workflows/ci.yml`: pytest on Python 3.11 to 3.13, plus a podman rootless container job.
- `provenance.json`: SHA-256 hashes of key artifacts, the base commit, and test results.

Planned:

- Release packaging and signed provenance.
