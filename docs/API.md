# TCL API Reference (stub)

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
