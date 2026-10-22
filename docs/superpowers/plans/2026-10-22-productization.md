# Durable operator foundation implementation plan

> Agentic workers: use executing-plans for inline implementation.

**Goal:** Establish a durable operator storage/CLI boundary without changing settlement authority.

**Architecture:** SQLite WAL stores atomic state batches and a replayable SHA-256 event chain.
Existing EVM balances and Groth16 statements remain authoritative.
A Poseidon state-proof protocol requires a separate circuit, ceremony, verifier, and migration.

**Tech stack:** Python standard library, SQLite WAL, existing Node/Circom/EVM toolchain.

**Spec:** User architecture A–J, reconciled with docs/14_client_protocol.md.

## Global constraints

Preserve frozen circuit and ledger bytes.
Production execution uses the existing signed rootless executor; native worktrees are development conveniences.
No automatic merge, chain rollback, mainnet transaction, or upgradeable proxy is introduced.
Every implementation change must run in the existing rootless acceptance pipeline.

## Review focus

Concurrent writers must reject stale expected heads.
SIGKILL after commit must preserve all 1,000 committed mutations.
Invalid batches must leave neither partial state nor partial history.
Modified journal/state must fail replay verification.
A journal hash needs an independently retained head to detect wholesale history replacement.

## Durable foundation

- [x] Add failing tests for atomicity, concurrency, corruption and SIGKILL recovery.
- [x] Implement src/tcl/storage/db.py StateStore with FULL synchronous WAL transactions.
- [x] Expose JSON CLI health, verify, state apply and snapshot commands.
- [x] Add persistent canonical-field nullifier indexing through the same store.
- [x] Run unit tests, document exact interfaces and include source hashes in provenance.
- [ ] Execute the unchanged rootless acceptance pipeline on the committed source.

## Subsequent protocol migration gates

Native sibling worktree allocation needs repository-scoped locking and dirty-worktree preservation.
Conflict detection must distinguish advisory file overlap from serializable merge authorization.
Poseidon ledger state requires a canonical schema, range bounds, inclusion witnesses and durable root checkpoints.
State settlement needs a separately versioned circuit/verifier and independent ceremony; current redemption proves none of these statements.
UUPS introduces upgrade authority and storage-layout obligations and is not silently applied to immutable deployments.
Anvil/Foundry/circomspect and compose boot checks must execute before those tools become required release evidence.
A 30-second pipeline target is an acceptance test to measure, not a roadmap guarantee.
