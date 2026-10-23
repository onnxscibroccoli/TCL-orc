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

- `src/tcl/storage/db.py` (canonical; `src/tcl/ledger/storage.py` re-exports it): `LedgerStorage` abstract base class.
  - `SQLiteStorage` runs in WAL mode with `synchronous=FULL` and `BEGIN IMMEDIATE` transactions.
    Nested transactions use savepoints.
    Writes outside a transaction raise `StorageError` (fail-closed).
    Balances are stored as decimal text, so arbitrary-precision integers survive round trips.
    Schema version 2 adds `tree_nodes`, `leaves`, `journal`, and a `root` column on `events` (v1 databases migrate on open).
  - `InMemoryStorage` gives the same semantics with snapshot rollback.
  - `open_storage("memory://" | path)` factory.
- `src/tcl/crypto/poseidon.py`: Poseidon over the BN254 scalar field, a port of circomlibjs `poseidon_reference.js`.
  Constants for 1–4 inputs are copied verbatim from circomlibjs `poseidon_constants.js` @48b3ab3 into `poseidon_bn254_t2_t5.json`.
  Known-answer tests pin it to circomlib (`poseidon([1,2]) = 7853200120776062878684798364095072458815029376092732009249414926327459813530`).
- `src/tcl/ledger/poseidon_tree.py`: `PoseidonMerkleTree`, a persistent binary Merkle tree.
  - Depth is a parameter (1–64, default 20) and is pinned in `meta.tree_depth`; reopening with another depth raises `StorageError`.
  - Node `= Poseidon(left, right)`; empty subtrees use zero hashes `Z0 = 0`, `Z[k+1] = Poseidon(Z[k], Z[k])`. Only non-empty nodes are stored.
  - Account leaf `= Poseidon(sha256(label) mod p, balance, last_update)`.
  - `update`, `proof` (bottom-up siblings plus path bits, the circom `pathIndices` convention), `verify`, and `rebuild_root` for audits.
- `src/tcl/ledger/service.py`: `Ledger` applies lazy demurrage on touch (`settle`), sends fees to the vault, and records an event log.
  - Every account write updates the account's leaf and appends `(seq, root, account, pre-image)` to `journal` in the same transaction.
  - Each event row carries the state root after it.
  - `root()`, `roots()` (history, genesis first), `proof(account)`, `verify_state()` (rebuilds the root from account rows).
  - `rollback(root)` undoes journal entries newer than the most recent point with that root, verifies the resulting root, truncates the journal, and logs a `rollback` event. Spent nullifiers stay spent, so a rollback cannot enable a double spend.
  - Results equal the pure functions bit-for-bit, and balance plus vault is conserved.
- `src/tcl/ledger/demurrage.py`, `demurrage_fixed.py`, `nullifiers.py`: the original pure math, moved unchanged.
- `ledger/*.py`: backwards-compatible shims for the old import paths.

Crash safety (Acceptance Test 3, `tests/test_crash_recovery.py`): a child process applies 1,000 transitions at depth 20 and is SIGKILLed mid-run and after completion. On reopen, `PRAGMA integrity_check` returns `ok`, the stored root equals the root rebuilt from accounts, every reported committed root is still in history, and the root matches the last committed root.

Data flow for one transition:

```
deposit/settle/redistribute
  └─ BEGIN IMMEDIATE
       accounts ← new balance
       leaves   ← assign index (first touch)
       tree_nodes ← leaf..root path (Poseidon)
       journal  ← (root, account, pre-image)
       events   ← (kind, account, amount, ts, root)
     COMMIT   (WAL, synchronous=FULL)
```

Planned:

- RocksDB backend (the interface allows it; SQLite WAL is the shipped backend).
- Circuit-side inclusion proofs that use the same leaf and node encoding.

## Layer 3: Zero-Knowledge — Planned

`circuits/puf_verify.circom` is still a sketch.
Planned work: move it to `src/tcl/zk/`, harden constraints, and add witness tests that must fail on under-constrained inputs.

## Layer 4: Smart Contracts — Planned

`contracts/CirculatoryEngine.sol` is still a skeleton.
Planned work: lazy O(1) decay matching `Ledger.settle`, OpenZeppelin `ReentrancyGuard` and access control, and Foundry tests with differential checks against the Python ledger.

## Layer 5: Interface and Delivery — Partial

Implemented in Phase 1:

- `src/tcl/cli.py` (Typer): `tcl ledger init|deposit|balance|apply-decay|root|rollback|proof|verify-proof` and `tcl worktree list|create`. See `docs/API.md`.
  All output is JSON.
- `src/tcl/utils/logger.py` (`src/tcl/logs.py` re-exports it): JSON-lines structured logging under the `tcl.*` logger namespace.
- `src/tcl/config.py`: pydantic-settings `Settings`. Precedence: kwargs > `TCL_*` env > `.env` > `config.yaml` (or `$TCL_CONFIG`) > defaults. The CLI currently reads `TCL_DB`, `TCL_TREE_DEPTH`, and `TCL_LOG_LEVEL` directly; routing it fully through `Settings` is planned.
- `Containerfile`: rootless image (UID 65534) that runs the full test suite.
- `.github/workflows/ci.yml`: pytest on Python 3.11 to 3.13, plus a podman rootless container job.
- `provenance.json`: SHA-256 hashes of key artifacts, the base commit, and test results.

Planned:

- Release packaging and signed provenance.
