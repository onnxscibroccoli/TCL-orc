# TCL API Reference

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
