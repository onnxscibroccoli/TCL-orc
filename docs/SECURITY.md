# Security boundaries and release gates

EVM contracts remain the balance and escrow authority.
The operator database is an auditable local projection, not a consensus system or authorization oracle.
Its SHA-256 journal is not a Poseidon Merkle tree or a Groth16 proof.
Replay verification detects inconsistency against a retained head; a same-user attacker who replaces the database and all trusted heads is outside that guarantee.
Use a trusted local directory and restrictive operating-system permissions.
Symlink checks are not protection against an adversary concurrently controlling the same directory.
FULL synchronous WAL commits depend on the filesystem honoring fsync and locking.
Tests cover process death, not physical power-loss or faulty storage hardware.

No automated state rewind can undo an external EVM transaction or arbitrary command side effect.
Uncertain production execution and wallet submissions require operator reconciliation.
Task completion does not authorize a code merge or deployment.
Read-only rootless OCI limits are validated at runtime; browser CI settings are not a production enclave claim.

Continuous checks include contract-local EVM concurrency and conservation, real Groth16 proving and replay, ledger reference comparisons, crash recovery, source checksums and rootless evidence collection.
Symbolic accounting obligations do not establish circuit completeness or EVM refinement.
Independent ceremony participation, key custody, hardware/extractor qualification, external audits and jurisdiction-specific approvals remain mandatory production gates.
Shielded payments, physical fuzzy extraction and Merkle state settlement remain unimplemented.
Submit sensitive vulnerability details through a maintainer-approved private channel; do not post witnesses or keys in public issues.
