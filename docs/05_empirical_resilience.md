# Empirical Resilience Additions

Demurrage mechanics are now covered by unit tests that run inside a rootless container.
The Containerfile uses a non-root user and executes pytest with no privileged operations.
Artifact provenance is recorded as SHA-256 hashes in provenance.json after every change that affects ledger or contract behavior.
Any Groth16 or circuit change must include a corresponding test that fails closed until the proof verifies.
Governance gates remain outside the critical path until they have the same test and container coverage.
