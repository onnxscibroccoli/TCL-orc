# Production Architecture Implementation Plan

> **For agentic workers:** Use the authorized parallel implementation workflow with independent review before publishing.

**Goal:** Add verifiable production deployment controls while preserving the existing proof statement and ledger economics.

**Architecture:** Independent ceremony, isolated execution, and symbolic verification controls feed an immutable deployment manifest.
A production controller and atomic factory connect that manifest to the on-chain ledger under delayed governance.

**Tech Stack:** Solidity 0.8.30, Circom 2, snarkjs, Python 3.10+, Ed25519, rootless OCI execution, Z3, and GitHub Actions.

**Spec:** `docs/06_production_architecture.md`.

## Global constraints

- Preserve five public Groth16 inputs and the current Poseidon statement.
- Preserve fixed supply, threshold 5,000,000 AETH, and daily rate 0.0005.
- Keep local test ceremony parameters ineligible for production.
- Never commit signing keys, raw PUF data, or secret-bearing runtime logs.
- Do not claim external ceremonies, container acceptance, or hardware audits without evidence.

## Review focus

- A substituted release policy must not authorize its own keys.
- Concurrent redemption attempts must release one escrow value only.
- A crashed container must not replay a financial side effect automatically.
- Tampered replicas must invalidate release verification.
- Symbolic proofs must state their abstraction and detect deliberately broken transitions.

## Integrated implementation

- [x] Ceremony pipeline: signed predecessor-linked contribution receipts, native transcript verification, approval quorum, content-addressed archives, replica readback, tampering regression tests.
- [x] Production execution: signed plans, digest-pinned images and source, rootless containment, file-only secret injection, durable CAS state and timeout cleanup, rejection and provenance tests.
- [x] Ledger deployment: pinned verifier provenance, emergency deposit stop, delayed resumption, atomic factory configuration, concurrent redemption and full rollback tests.
- [x] Formal audit: symbolic transition proofs with negative mutation tests, actual-source compilation manifest, continuous CI gates.
- [x] Privacy integration design: physical enrollment attestation and fuzzy-extractor requirements, separate shielded-pool proof statement, identity/economic composition boundary.
- [ ] Integration: full regression suite, independently reviewed findings, source/blob verification, GitHub PR update and CI execution.
