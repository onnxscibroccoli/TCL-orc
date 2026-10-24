# TCL Production Architecture

> **In plain words.** This page explains how TCL is put together. It has five layers. Each layer has one job: (1) a manager that runs work, (2) an account book, (3) a proof maker that shows the account book followed the rules, (4) blockchain programs (*smart contracts*) that check the proofs, and (5) the tools people use. Unfamiliar words are in the [Glossary](GLOSSARY.md).
>
> **What this means for you:** if a layer says Partial or Planned, do not rely on it. The proof setup is for development only. Nothing has had an outside security audit.

This page describes the layers of the Thresholded Circulatory Ledger (TCL). Each layer gets one of three labels:

- **Implemented** means built and tested.
- **Partial** means some of it is built.
- **Planned** means not built yet.

A layer is only marked Implemented when it has passing tests in `tests/` or `ledger/`. A *test* is a small program that checks the code does what it should.

## Layer 1: Control Plane and Worktree Orchestration — Implemented (Phase 2)

**In plain words.** This layer is the work manager. Developers give it *tasks*, like "run this command." It runs each task in its own copy of the code, called a *worktree*. Then it checks that two tasks did not change the same files.

**The task state machine** (`src/tcl/orchestrator/state.py`). A *state machine* is a fixed list of steps a task can be in, plus rules for moving between them.

- The normal path is `PENDING → ALLOCATED → EXECUTING → VERIFYING → MERGED`. In words: waiting, given a worktree, running, being checked, accepted.
- From any step that is not final, a task may go to `FAILED`.
- `MERGED` and `FAILED` are final (*terminal*). A task never leaves them.
- A move that breaks the rules raises an error called `IllegalTransition`. The task's state stays the same.

**The task engine** (`src/tcl/orchestrator/engine.py`). The engine runs tasks in the right order. Tasks can depend on other tasks. Together they form a *DAG* (directed acyclic graph): a set of arrows between tasks with no loops. The engine is *event-driven*: it reacts when something happens, such as a task finishing.

- **Checking the plan.** `submit()` checks the whole graph first. It rejects a task that depends on an unknown task, on itself, or on a loop. It finds loops with a standard search method called *DFS* (depth-first search). If it rejects the plan, it saves nothing.
- **Running in parallel.** Ready tasks wait in a queue. N *worker threads* (parallel helpers inside the program) take tasks from it. The queue is *thread-safe*, so two helpers never grab the same task.
- **Reacting to results.** When a task ends, the engine re-checks the tasks that depend on it. If a task fails, every task that depends on it also fails, with the reason `dependency_failed`.
- **Sandboxing.** Each command runs in a *sandbox*: a fenced-off space with limits. Step by step:
  1. It gets a new session and process group, so it can be stopped as a unit.
  2. It gets a minimal environment. Secrets on the host machine are not passed in.
  3. `resource.setrlimit` caps CPU seconds, memory (address space), open files, file size and crash dumps (*core dumps*). It can also cap the number of processes, if switched on.
- **Timeouts.** If a task runs past its wall-clock time limit, the engine kills its whole process group and marks it `FAILED`.
- **Saving state.** Task details and their state history are saved in *SQLite*, a small database stored in one file. It runs in *WAL mode* (write-ahead log: changes are written to a log first, so a crash can't corrupt the file) with `synchronous=FULL` (every change is forced onto the disk).
- **Restarting.** After a restart, any task that was in the middle of running is marked failed with `engine_restart`. It is never quietly resumed. `PENDING` tasks resume normally.
- **Worktrees.** When a `WorktreeManager` is used, each task gets its own worktree and is registered with the Conflict Guard. Checking and the merge decision happen one at a time, under a *lock* (a rule that only one thread may enter at once).

**The Conflict Guard** (`src/tcl/orchestrator/conflict_guard.py`, also available at `src/tcl/worktree/conflict_guard.py`). It stops two tasks from changing the same file.

1. For each active worktree, it lists the changed files. That is the files from `git diff --name-only <base>`, plus new untracked files from `git ls-files --others --exclude-standard`, minus `.env.local`.
2. Before a task may reach `MERGED`, the guard compares its list with every other active worktree's list.
3. If any file appears in both, it sends a `ConflictAlert`. The alert goes to the structured log, to callbacks and to the daemon's telemetry (its stream of status reports).
4. The task goes to `FAILED` with the reason `conflict: <files>`.

**The worktree manager** (`src/tcl/worktree/manager.py`). `WorktreeManager` wraps the `git worktree` command. *git* is the tool that tracks code history.

- With `layout="sibling"` (used by the engine and daemon), worktrees go in `<repo>/../worktrees/worktree-<id>`. They all share one store of history, the root `.git` object store (`$GIT_COMMON_DIR`).
- With `layout="nested"` (`.worktrees/<id>`), worktrees go inside the repo. This is still the CLI default, for compatibility.
- A registry file, `.worktrees/registry.json`, records ports, claims and task state. It is guarded by `flock`, a file lock, so two programs can't write it at once.
- `health()` checks each worktree: its folder exists, git knows about it, and its `HEAD` (current commit) can be found.

**The daemon** (`src/tcl/daemon.py`, started from `agents/daemon.py`). A *daemon* is a program that keeps running in the background.

- It listens on a *Unix socket*, a private connection point on the same computer. Only its owner can use it (mode `0600`).
- Messages use *JSON lines*: one JSON message per line. *JSON* is a simple text format of labels and values. The commands are `health`, `submit` and `status`.
- It writes JSON telemetry: task states, conflict alerts and regular worktree health reports.
- It runs the Conflict Guard on a timer.

**Other entry points.**

- `agents/orchestrator.py` is kept for compatibility. It checks that `PLAN` is a valid DAG and prints a *topological order* (an order where every task comes after the tasks it needs).
- `bin/worktree-harness` is a thin wrapper around `tcl-cli worktree ...`.

**Known limits (honest scope):**

- `MERGED` means "checked and cleared by the Conflict Guard." The engine does not yet run `git merge` into the main branch. That comes with the integration pipeline.
- The `RLIMIT_*` limits apply per process. There are no *cgroup* quotas yet (Linux limits for whole groups of processes). Files are not fenced off beyond the worktree's working folder.

## Layer 2: Off-Chain Ledger and State — Partial

**In plain words.** This layer is the account book. *Off-chain* means TCL's own software keeps it, not a blockchain. It records every balance. It applies the decay rule (*demurrage*) when an account is used. It also keeps a *Merkle tree*: a way to boil the whole book down to one short code, the *root*. Change any account and the root changes, so the root is a fingerprint of the whole book.

Built in Phase 1:

**Storage** (`src/tcl/storage/db.py` is the main copy; `src/tcl/ledger/storage.py` points to it). `LedgerStorage` is an *abstract base class*: a template that says what any storage must be able to do.

- `SQLiteStorage` stores data in SQLite.
  - It uses WAL mode, `synchronous=FULL`, and `BEGIN IMMEDIATE` transactions. A *transaction* is a group of changes that all happen or none do. `BEGIN IMMEDIATE` grabs the write lock at the start.
  - Transactions inside transactions use *savepoints* (partial undo points).
  - Any write outside a transaction is refused with `StorageError`. This is *fail-closed*: when in doubt, refuse.
  - Balances are stored as decimal text. That way very large whole numbers are never rounded.
  - Schema version 2 (the layout of the tables) adds `tree_nodes`, `leaves`, `journal`, and a `root` column on `events`. Version 1 databases are upgraded when opened.
- `InMemoryStorage` behaves the same way but keeps everything in memory. It undoes changes by restoring snapshots.
- `open_storage("memory://" | path)` picks the right storage for you.

**The Poseidon hash** (`src/tcl/crypto/poseidon.py`). A *hash* is a data blender: any input goes in, a fixed-size code comes out, and you can't work backwards. *Poseidon* is a hash that is cheap to use inside zero-knowledge proofs. It works on numbers in the *BN254 scalar field* (numbers below a huge prime, where math wraps around like clock hours).

- It is a copy (*port*) of circomlibjs `poseidon_reference.js`. circomlibjs is the standard library that goes with the proof language Circom.
- The constants for 1–4 inputs are copied exactly from circomlibjs `poseidon_constants.js` @48b3ab3 into `poseidon_bn254_t2_t5.json`.
- *Known-answer tests* check it gives the same answers as circomlib. For example, `poseidon([1,2]) = 7853200120776062878684798364095072458815029376092732009249414926327459813530`.

**The Merkle tree** (`src/tcl/ledger/poseidon_tree.py`). `PoseidonMerkleTree` is a binary Merkle tree (each node has two children) that is saved to disk (*persistent*).

- Its *depth* (number of levels) can be 1 to 64. The default is 20. The depth is saved in `meta.tree_depth`. Reopening with a different depth fails with `StorageError`.
- Each inner node is `Poseidon(left, right)`. Empty branches use pre-computed *zero hashes*: `Z0 = 0`, `Z[k+1] = Poseidon(Z[k], Z[k])`. Only non-empty nodes are stored.
- Each account's *leaf* (bottom entry) is `Poseidon(sha256(label) mod p, balance, last_update)`. That is: the account name turned into a number, its balance, and when it was last updated.
- It can `update` a leaf. It can give a `proof` that a leaf is in the tree: the sibling codes from bottom to top, plus left/right path bits (the same `pathIndices` layout Circom uses). It can `verify` such a proof. `rebuild_root` recomputes the root from scratch, for checks.

**The ledger service** (`src/tcl/ledger/service.py`). `Ledger` is the account book itself.

- Decay is *lazy*: it is only worked out when an account is used (`settle`). The fee goes to the vault, the shared pot. Each action is logged as an event.
- Every account write does two things in the same transaction. It updates the account's leaf. It adds `(seq, root, account, pre-image)` to the `journal`, where *pre-image* means the values before the change.
- Each event row stores the root right after it.
- Useful calls: `root()` (current root), `roots()` (history, oldest first), `proof(account)`, and `verify_state()` (rebuilds the root from the account rows and compares).
- `rollback(root)` undoes changes back to a past root. Step by step: it finds the most recent point with that root, undoes all newer journal entries, checks the resulting root, trims the journal, and logs a `rollback` event. Spent *nullifiers* (one-time codes) stay spent. So a rollback can't be used to spend the same money twice.
- Results match the plain math functions exactly, bit for bit. Balances plus vault always add up to the same total.

**Other files.**

- `src/tcl/ledger/demurrage.py`, `demurrage_fixed.py` and `nullifiers.py` hold the original math, moved without changes.
- `ledger/*.py` are small shims so old import paths still work.

**Crash safety** (Acceptance Test 3, `tests/test_crash_recovery.py`). The test checks the book survives a sudden crash.

1. A child process applies 1,000 changes on a depth-20 tree.
2. It is killed with SIGKILL (an instant, no-warning stop), both mid-run and after finishing.
3. After reopening, the test checks four things. `PRAGMA integrity_check` returns `ok`. The stored root equals the root rebuilt from accounts. Every root reported as saved is still in the history. The current root matches the last saved root.

Data flow for one change:

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

In words: start a transaction. Save the new balance. Give the account a tree slot if it's new. Recompute the codes from its leaf up to the root. Write the undo record. Log the event. Then save it all at once (*commit*).

Planned:

- A RocksDB backend (another database). The design allows it, but SQLite WAL is what ships.

## Layer 3: Zero-Knowledge — Implemented (Phase 3, dev setup)

**In plain words.** A *zero-knowledge proof* convinces someone that a statement is true without showing the private details. It's like proving you're over 18 without showing your birth date. Here, each proof says: "This account's decay was worked out correctly, and the fee went into the vault." It does not reveal which account or its balance.

**The circuit.** A *circuit* is the list of equations a proof must satisfy. TCL's is written in *Circom* (version 2.1), a language for proof circuits. `circuits/puf_verify.circom` defines `StateTransition(depth)`, where the tree depth is a setting (*template parameter*). The file name is historical; it has nothing to do with PUFs.

Helper pieces live in `circuits/lib/`:

- `merkle.circom` has two parts. `OrderedPair` takes a path bit that must be 0 or 1 (`bit*(bit-1) === 0` forces that) and puts two codes in the right order. `MerkleRoot(depth)` climbs to the root using circomlib's `Poseidon(2)`. Each node is `Poseidon(left, right)`, the same as in `src/tcl/ledger/poseidon_tree.py`.
- `fixedpoint.circom` has three parts. `DivConst` does division that rounds down, with checks. `GtConst128` checks whether a number is bigger than a fixed value. `DemurrageStep` is a line-by-line copy of one step of `demurrage_fixed.apply_demurrage_fixed`: the 5th-order Taylor shortcut, the −55 correction for steps of at least one day, and the threshold `T`.

**What one proof states**, step by step:

1. `Poseidon(accountId, oldBalance, oldTime)` sits at a hidden slot in the tree whose root is the public `oldRoot`. So the old account really was in the book.
2. `dt = newTime - oldTime` is between 0 and 604,800 seconds (7 days, one Taylor step). `fee` and `newBalance` follow the decay rule exactly.
3. Putting `Poseidon(accountId, newBalance, newTime)` in the same slot, with the same siblings, gives an in-between root, `midRoot`.
4. In `midRoot`, the vault leaf at reserved slot 0 is `Poseidon(VAULT_ID, vaultBalance, vaultTime)`, or empty before the first fee. Replacing it with `Poseidon(VAULT_ID, vaultBalance + fee, newTime)` gives the public `newRoot`. If `fee = 0`, the vault leaf stays the same.
   The account must not be the vault (`accountId != VAULT_ID`, slot != 0).

So one proof covers a whole `Ledger.settle`. After it, `newRoot == Ledger.root()`.

The public values (*public signals*), in the order snarkjs uses, are `[newRoot, fee, oldRoot, newTime]`. The account, its balances and its tree path stay private.

**Safety rules for the equations.**

- Every value is tied down with `<==` or `===`, which create real equations.
- The only two `<--` lines are *hints*: values the prover supplies without an equation. They are the quotient and remainder in `DivConst`.
- Both hints are then forced by `num === q*den + r` plus 64-bit and 128-bit size checks. These leave no room for the clock-like math to wrap around and fake a result.
- `tests/test_circuit_hygiene.py` checks this automatically.
- The checker circomspect 0.9.0 reports only warning CS0005, on those two lines.

**The prover** (`src/tcl/zk/prover.py`). It makes proofs, step by step:

1. Compile the circuit (into r1cs and wasm files).
2. Compute the *witness*, the full set of values for one case (`snarkjs wtns calculate`).
3. Run `groth16_setup`: a dev setup file (*ptau*) plus one local *zkey* contribution (the proving key).
4. `prove`, then `verify`.
5. `export_verifier` writes the checker contract, and `solidity_calldata` formats a proof for it.

`build_transition_input(ledger, account, now)` settles a real `Ledger` account and returns the circuit input. It checks, fail-closed, that the in-between root matches the vault path and that `newRoot` equals the ledger root after settling.

On-chain, each `fee` is added to `offchainFeesSettled`. Settlements chain one after another on one deployment.

`ensure_artifacts` saves built files in a cache. The cache key is a SHA-256 fingerprint of all circuit source files. So if the circuit changes, old keys are never reused by mistake.

**Warning:** the ptau and zkey are DEV ONLY. They come from randomness made by one person on one machine. Whoever knows that randomness could fake proofs. Real use needs a public ceremony ptau and a multi-party phase 2, where many people each add randomness.

## Layer 4: Smart Contracts — Implemented (Phase 3)

**In plain words.** A *smart contract* is a small program that lives on a blockchain and runs exactly as written. TCL's main contract holds deposits, applies its own decay rule, keeps the vault, and records proven updates from Layer 3.

**`contracts/CirculatoryEngine.sol`.** It is written in *Solidity* 0.8.24, the main language for these contracts. It uses OpenZeppelin Upgradeable v5.1.0, a widely used library of reviewed building blocks:

- `UUPSUpgradeable`: the code can be replaced by an upgrade.
- `OwnableUpgradeable`: one owner account has admin powers.
- `PausableUpgradeable`: actions can be paused in an emergency.
- `ReentrancyGuardUpgradeable`: blocks *reentrancy*, a trick where a malicious contract calls back in before the first call finishes.

It sits behind an `ERC1967Proxy`. A *proxy* is a fixed address that forwards calls to the current code, so the code can be upgraded without changing the address.

How it works:

- **Lazy O(1) decay.** `fee = min(surplus, surplus * ratePerSecond * dt / 1e18)`. It is applied when an account is used (`deposit`, `withdraw`, `settle`, `redistribute`). *O(1)* means one quick step, no matter how many accounts exist. `balanceOf` and `pendingFee` are read-only views and also take one step.
  This on-chain rule is a straight line per second. On purpose, it is different from the off-chain Taylor rule. The off-chain rule is proven with zero-knowledge proofs instead.
- **Vault.** Fees collect in `vault`. The owner calls `redistribute` to move them into balances.
  The *conservation invariant* (a rule that must always hold) is `address(this).balance == totalBalances + vault`. In words: the money the contract holds equals all balances plus the vault.
- **Recording a proof.** The settler or owner calls `settleEpoch(pA, pB, pC, pubSignals)`. It checks, in order:
  1. The public values are valid field numbers.
  2. `oldRoot == stateRoot`: the proof starts from the root on file.
  3. `lastSettledTime < newTime <= block.timestamp`: time moves forward, but not into the future.

  Then it calls `StateVerifier.verifyProof`. If that passes, it updates the root, time, epoch count and fee total, and emits (announces) `StateSettled(epoch, oldRoot, newRoot, fee, newTime)`. Sending the same proof again fails the root check.
- **Checks-effects-interactions.** This is a safety pattern: check inputs, then update records, then talk to other accounts last. `withdraw` is the only function that sends ETH (the blockchain's coin). It does so last, under `nonReentrant`.

**`contracts/verifiers/StateVerifier.sol`** is the Groth16 checker exported by snarkjs, renamed. *Groth16* is the kind of proof TCL uses. `scripts/gen_zk_fixture.py` rebuilds it, together with `test/contracts/fixtures/`.

**Tests** are in `test/contracts/CirculatoryEngine.t.sol`, using *Foundry* (a toolkit for testing contracts).

- They cover deposit, decay over time, the surplus cap, withdraw, redistribute, who may call what (*access control*), pause, UUPS upgrade, a reentrancy attacker and the epoch checks.
- A real-proof suite covers verifying, the `StateSettled` event, replay, a tampered fee and a tampered proof.
- A chained suite records 3 real proofs in a row (one with fee = 0) on one deployment. After each, it checks `stateRoot` equals the ledger root. It also checks an out-of-order proof is rejected.
- *Invariant fuzzing* runs many random actions and checks conservation and that lazy and stored balances agree.

## Layer 5: Interface and Delivery — Implemented (Phase 4)

**In plain words.** This layer is how people run TCL: a command-line tool, settings, a background helper, and packages. It also includes the robots that test every change.

- **The CLI** (`src/tcl/cli.py`). A *CLI* is a program you control by typing commands. It is built with Typer, and installed as both `tcl` and `tcl-cli`. Command groups:
  - `ledger init|deposit|balance|apply-decay|root|rollback|proof|verify-proof`
  - `worktree list|create|remove|health`
  - `task create|list|status`
  - `daemon start|health`
  - `zk compile|prove|verify|settle`
  - `chain deploy|settle`

  All output is JSON. On an error it prints `{"ok": false, "error": ...}` and exits with code 1. See `docs/API.md`.
- **Settings.** Every CLI option starts as `None` and is filled in by `tcl.config.Settings`. If a setting is given in more than one place, the first one wins in this order: command flag, then `TCL_*` environment variable, then `.env` file, then `config.yaml` (or the file in `$TCL_CONFIG`), then built-in defaults. A bad setting fails closed (exit 1). Tested in `tests/test_phase4_units.py`.
- **Logging** (`src/tcl/utils/logger.py`; `src/tcl/logs.py` points to it). Logs are JSON lines, under the name `tcl.*`.
- **The daemon's web check.** Besides the Unix socket, the daemon runs a read-only web server. `GET /health` returns 200 when healthy, 503 when degraded and 404 for anything else. Set the port with `--http-port` or `TCL_HTTP_PORT` (default 8787; 0 turns it off).
- **The proof pipeline** (`src/tcl/zk/pipeline.py`). `ensure_artifacts` builds and caches the circuit and the DEV Groth16 setup. `prove_settlement` settles the ledger, makes a proof, checks the public values against the ledger, and verifies the proof locally.
- **Chain tools** (`src/tcl/chain/`).
  - `anvil()` starts and stops a practice blockchain (*anvil*).
  - `deploy()` installs the contracts. Step by step: it builds a separate Foundry project with a checker exported from the *same* zkey. It uses `forge create` to install the checker, the main code and an ERC1967 proxy with `initialize`. Then it checks `stateRoot == genesis` (the starting root).
  - `settle_epoch()` sends `settleEpoch` with `cast send` and reads `StateSettled` from the receipt. If the call fails (*reverts*) or the event is missing, it fails closed.
- **`Containerfile`.** It builds *container images* (sealed boxes with everything a program needs) in stages:
  - `toolchain`: circom 2.1.9, snarkjs 0.7.6 on node 20, foundry v1.8.6, git.
  - `app`: runs as a non-admin user (UID 10001). It starts the daemon with HTTP /health by default. The Solidity compiler is downloaded in advance.
  - `test` (the final stage): runs the full test suite with `TCL_REQUIRE_ZK=1 TCL_REQUIRE_CHAIN=1`.
- **`docker-compose.yml`** starts `anvil` and `tcl-daemon` with health checks. `toolchain` and `deploy` are one-time jobs under the `tools` profile.
- **CI jobs** (`.github/workflows/ci.yml`). *CI* (continuous integration) means robots run the checks on every change:
  - pytest (Python 3.11–3.13)
  - lint, which checks code style (ruff, solhint, circomspect)
  - zk
  - contracts (forge)
  - rootless-container: the full suite in the `test` stage, under podman without admin rights
  - integration: Acceptance 4, saving timings
  - compose: Acceptance 1. Runs `docker compose up --build --wait`, checks `curl /health` = 200, and deploys on the compose network.
  - wheel: builds and installs the Python package on every PR (pull request, a proposed change)
  - release: on tags `v*`, builds wheel, sdist and SHA256SUMS and makes a GitHub release. It only runs if all other jobs pass.
- **`provenance.json`** records SHA-256 fingerprints of key files, the base commit, test results and end-to-end timings. *Provenance* means a record of where something came from.

## End-to-end flow (Acceptance 4)

**In plain words.** This test runs everything together, from a task to a recorded proof on a practice blockchain.

`test/integration/test_full_pipeline.py`, step by step:

1. A task is sent to the daemon over the socket. The engine gives it a sibling worktree (`../worktrees/worktree-task_<id>`). It runs the command, which writes a code change. Then it verifies, runs the Conflict Guard and marks the task MERGED.
2. The change is applied to a saved SQLite WAL ledger with a depth-4 Poseidon tree.
3. `prove_settlement` settles one account and makes a Groth16 proof of the change.
4. The contracts are installed on a throwaway anvil chain. The starting root is the ledger root before settling.
5. `settleEpoch` is sent. The test checks that `StateSettled(epoch=1, oldRoot, newRoot, fee, newTime)` matches the proof's public values. It also checks the whole run, from submit to event, takes under 30 seconds. The one-time circuit build and dev setup (about 60 s on single-core-class machines) is cached and timed separately.

**Many rounds.** `test/integration/test_multi_epoch.py` records 5 settlements in a row on one anvil deployment, including one with fee = 0. After each, it checks the on-chain `stateRoot` equals the ledger root.

Planned:

- Signed provenance (with Sigstore, a signing service).
- A batched settlement circuit that proves many accounts at once.
- Proofs for deposits and redistribution.
