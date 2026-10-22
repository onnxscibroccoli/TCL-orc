# Product architecture and migration boundaries

The product uses five layers: operator control, durable state, proving, EVM settlement, and delivery.
The production execution boundary is production/executor.py: signed plans, approved image digests, command allowlists, secret files, resource limits and uncertain-task reconciliation.
Native worktrees support trusted development; a Git worktree is not a process sandbox.
Existing worktree allocation is repository-local and serially locked; sibling allocation and concurrent conflict detection remain migration work.

The operator store in src/tcl/storage/db.py uses SQLite WAL, FULL synchronous commits and BEGIN IMMEDIATE writer isolation.
Each mutation batch checks an expected journal head and commits state and history together.
Journal hashes use SHA-256 and can be replayed to verify stored state.
An independently retained head detects truncation or wholesale journal replacement.
The local nullifier index can persist through this store and separates deployment domains.
Neither the store nor index verifies proofs, releases escrow, or substitutes for EVM consensus.

The frozen circuit proves knowledge of a commitment opening and a recipient/context-bound nullifier.
It does not prove Merkle inclusion, off-chain balance privacy, demurrage state transitions, or enrollment signatures.
The on-chain controller additionally consumes commitments permanently.
The ledger already uses bounded integer exponentiation over chain timestamp deltas and transfers extracted fees to its vault.

A Merkle settlement release requires a canonical account schema, Poseidon tree domain separation, range-constrained state transition circuit, new ceremony, verifier, deposit/withdrawal semantics and migration audit.
It must use a new protocol version; replacing the current circuit under existing proving keys is invalid.
UUPS adds upgrade authorization and storage-layout risks and requires a separately reviewed deployment design.
No upgrade proxy or destructive state rollback is introduced here.

The current CLI exposes durable operator state and verification.
Typer packaging, IPC daemon services, sibling worktrees, live conflict monitoring, OpenTelemetry, compose health endpoints, Anvil/Foundry and circomspect integration remain explicit roadmap items.
Existing Node/Ganache real-proof, contract, rootless and symbolic checks remain the executable baseline.
A directory layout or tool name is not evidence that those migrations have run.
