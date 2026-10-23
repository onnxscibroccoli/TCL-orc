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

## Layer 3: Zero-Knowledge — Implemented (Phase 3, dev setup)

Circuit: `circuits/puf_verify.circom` defines `StateTransition(depth)` (Circom 2.1, depth is a template parameter).
Helper templates live in `circuits/lib/`:

- `merkle.circom`: `OrderedPair` (boolean-constrained path bit, `bit*(bit-1) === 0`) and `MerkleRoot(depth)` using circomlib `Poseidon(2)`. Node = `Poseidon(left, right)`, which matches `src/tcl/ledger/poseidon_tree.py`.
- `fixedpoint.circom`: `DivConst` (constrained floor division), `GtConst128`, and `DemurrageStep`. `DemurrageStep` is a constraint-for-constraint port of one step of `demurrage_fixed.apply_demurrage_fixed`: 5th-order Taylor, the -55 correction for steps of at least one day, and the threshold `T`.

The statement one proof makes:

1. `Poseidon(accountId, oldBalance, oldTime)` sits at a hidden index under the public `oldRoot`.
2. `dt = newTime - oldTime` is in `0..604800` (one Taylor step), and `fee` and `newBalance` follow the fixed-point rule exactly.
3. `Poseidon(accountId, newBalance, newTime)` at the same index and with the same siblings gives the public `newRoot`.

The public signals, in snarkjs order, are `[newRoot, fee, oldRoot, newTime]`.
The account, its balances and its path stay private.

On constraints: every signal is bound with `<==` or `===`.
The only two `<--` hints are the quotient and remainder in `DivConst`.
Both are bound by `num === q*den + r` together with 64- and 128-bit range checks, which leave no room for field wrap.
`tests/test_circuit_hygiene.py` enforces this.
circomspect 0.9.0 reports nothing beyond CS0005 on those two lines.

Prover: `src/tcl/zk/prover.py` runs compile (r1cs + wasm), witness (`snarkjs wtns calculate`), `groth16_setup` (dev ptau plus one local zkey contribution), `prove`, `verify`, `export_verifier` and `solidity_calldata`.
`build_transition_input(ledger, account, now)` settles a real `Ledger` account and returns the circuit input.
It checks fail-closed that the computed `newRoot` is in the ledger's journaled root history.

The newRoot is the root right after the account-leaf update.
`Ledger.settle` then updates the vault leaf in a second journaled step.
Proving the vault leaf is not done yet: on-chain, `fee` is accumulated as `offchainFeesSettled`.

The ptau and zkey are DEV ONLY and come from single-party local randomness.
Production needs a public ceremony ptau and a multi-party phase 2.

## Layer 4: Smart Contracts — Implemented (Phase 3)

- `contracts/CirculatoryEngine.sol` (Solidity 0.8.24, OpenZeppelin Upgradeable v5.1.0) uses `UUPSUpgradeable`, `OwnableUpgradeable`, `PausableUpgradeable` and `ReentrancyGuardUpgradeable`, behind an `ERC1967Proxy`.
  - Lazy O(1) decay: `fee = min(surplus, surplus * ratePerSecond * dt / 1e18)`, applied on touch (`deposit`, `withdraw`, `settle`, `redistribute`). `balanceOf` and `pendingFee` are O(1) views.
    This on-chain rule is linear per second. It intentionally differs from the off-chain Taylor rule, which is proven in ZK instead.
  - Vault: fees accumulate in `vault`, and the owner calls `redistribute` to move them into balances.
    Conservation invariant: `address(this).balance == totalBalances + vault`.
  - `settleEpoch(pA, pB, pC, pubSignals)` (settler or owner) runs these checks in order: the signals are field elements, `oldRoot == stateRoot`, and `lastSettledTime < newTime <= block.timestamp`.
    It then calls `StateVerifier.verifyProof`, updates the root, time, epoch and fees, and emits `StateSettled(epoch, oldRoot, newRoot, fee, newTime)`.
    Replaying a proof fails on the root check.
  - Checks-effects-interactions: `withdraw` is the only function that sends ETH, and it does so last, under `nonReentrant`.
- `contracts/verifiers/StateVerifier.sol` is the snarkjs-exported Groth16 verifier (renamed), regenerated by `scripts/gen_zk_fixture.py` along with `test/contracts/fixtures/`.
- Tests are in `test/contracts/CirculatoryEngine.t.sol` (Foundry).
  They cover deposit, decay epochs, the surplus cap, withdraw, redistribute, access control, pause, UUPS upgrade, a reentrancy attacker and the epoch checks.
  A real-proof suite covers verify, StateSettled, replay, tampered fee and tampered proof.
  Invariant fuzzing checks conservation and the lazy/stored balance identity.

## Layer 5: Interface and Delivery — Partial

Implemented in Phase 1:

- `src/tcl/cli.py` (Typer, console scripts `tcl` and `tcl-cli`): `ledger init|deposit|balance|apply-decay|root|rollback|proof|verify-proof`, `worktree list|create|remove|health`, `task create|list|status`, `daemon start|health`, `zk compile|prove|verify|settle`, `chain deploy|settle`. See `docs/API.md`.
  All output is JSON.
- `src/tcl/utils/logger.py` (`src/tcl/logs.py` re-exports it): JSON-lines structured logging under the `tcl.*` logger namespace.
- `src/tcl/config.py`: pydantic-settings `Settings`. Precedence: kwargs > `TCL_*` env > `.env` > `config.yaml` (or `$TCL_CONFIG`) > defaults. The CLI currently reads `TCL_DB`, `TCL_TREE_DEPTH`, and `TCL_LOG_LEVEL` directly; routing it fully through `Settings` is planned.
- `Containerfile`: rootless image (UID 65534) that runs the full test suite.
- `.github/workflows/ci.yml` has these jobs: pytest on Python 3.11 to 3.13; lint (ruff, solhint, circomspect); zk (circom 2.1.9 and snarkjs 0.7.6, Groth16 tests with `TCL_REQUIRE_ZK=1` so they fail closed); contracts (`forge build` and `forge test`); and the podman rootless container.
- `provenance.json`: SHA-256 hashes of key artifacts, the base commit, and test results.

Planned:

- Release packaging and signed provenance.
