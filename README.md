# Thresholded Circulatory Ledger

TCL is a research implementation of a fixed-supply ledger, thresholded demurrage, physical-note escrow, and zero-knowledge redemption.
The v0.2 research core connects the ledger, Poseidon circuit, generated Groth16 verifier, and durable task runtime in executable tests.
It is not a production currency deployment.

## Run the complete protocol checks

Use Node.js 22 or newer, npm, Python 3.12 or newer, Bash, and Git on Linux for the complete suite.
The development CLI can still be used separately in Termux; production containment requires a dedicated supported Linux host.
The local EVM test dependency may use a JavaScript fallback on newer Node versions.

```bash
python3 -m pip install -r requirements-production.txt -r verification/requirements.txt -r requirements-test.txt
npm ci
npm run check
```

This compiles the circuit, creates fresh local test ceremony parameters, verifies the proving key, exports a Solidity verifier, and runs runtime, ledger, real-proof, and documentation tests.
Generated keys, witnesses, proofs, and build outputs stay under ignored `build/`.
The ceremony is for local testing only.
A reviewed multi-party ceremony and pinned verification-key manifest are required before deployment.

## Protocol structure

| Path | Responsibility |
| --- | --- |
| `contracts/CirculatoryEngine.sol` | Fixed supply, transfers, allowances, chain-time demurrage, restricted redistribution |
| `contracts/PhysicalMintController.sol` | Backed escrow, immutable verifier, spent-note and nullifier checks |
| `circuits/puf_verify.circom` | Poseidon commitment and recipient-bound secret-knowledge proof |
| `scripts/zk-setup.sh` | Local Groth16 setup and verifier export |
| `tests/zk.test.cjs` | Real witness, proof, generated-verifier deployment, and escrow redemption |
| `agents/orchestrator.py` | Trusted command DAG, dependency gates, timeouts, durable attempts |
| `agents/daemon.py` | Restartable plan polling |
| `bin/worktree-harness` | Isolated branches without forced deletion or implicit credential copying |
| `docs/05_protocol.md` | Implemented invariants, economics, public-signal order, and threat model |

## Execute the protocol task graph

```bash
python3 agents/orchestrator.py --plan agents/plan.json --state .runtime/check-v2.sqlite
python3 agents/daemon.py --plan agents/plan.json --state .runtime/check-v2.sqlite --once
bash bin/worktree-harness create research/next-protocol
bash bin/worktree-harness list
```

Task commands are trusted repository configuration and run with the caller's permissions.
The runtime does not fetch model-generated commands or claim to implement an LLM provider.
Successful tasks are skipped only when task definitions, dependency definitions, and source fingerprints match.
Non-Git workspaces may execute a fresh plan but cannot reuse task state without a reliable source identity.
After a source revision, use a new state path for the new verification run.
After an interrupted running task, reconcile its external effects before using a new state database.
Retries are opt-in because commands can have non-idempotent effects.
Dirty worktrees are preserved when removal is requested.

## Deployment and scope

Deploy the ledger with a governor, initial holder, and fixed supply of at most `uint128.max` base units.
Deploy a verifier generated from the reviewed circuit and ceremony, then deploy the controller with the ledger and verifier addresses.
The governor configures that controller exactly once using `setPhysicalMint`.
Approving and locking notes then debits real ledger balances; verified redemption returns the same amount to the proof-bound recipient.
No live network deployment or automated merge is part of the test suite.

Escrow amounts, addresses, and note commitments are public.
A secret-knowledge proof does not establish physical unclonability, enrollment authenticity, or offline double-spend resistance.
The immunity threshold is per address; per-person enforcement requires an additional identity protocol.
Governance controls redistribution and the initial controller choice.
See the protocol specification for these boundaries and the remaining research work.

## Production deployment architecture (v0.3)

Production controls preserve the existing commitment/nullifier statement and compounding ledger.
They are implemented separately from the local development daemon and test ceremony.
Use Python 3.12+ on a Linux host with durable filesystem primitives for the production tooling.
Rootless Docker with working cgroup resource controllers is required for production task execution.

```bash
python3 -m pip install -r requirements-production.txt -r verification/requirements.txt -r requirements-test.txt
npm ci
npm run check
```

The checks now include real signed ceremony transcript verification with synthetic participants, dual archive readback, symbolic invariant proofs and mutation counterexamples, production contract regression tests, and release-policy rejection tests.
Synthetic test identities never establish an independently operated production ceremony.
GitHub Actions additionally runs actual rootless-container isolation and timeout-cleanup acceptance.

| Control | Implementation |
| --- | --- |
| Independent MPC and artifact retention | `production/ceremony.py`, [ceremony procedure](docs/07_ceremony.md) |
| Signed OCI task DAG and secret-file injection | `production/executor.py`, [execution policy](docs/08_execution.md) |
| Immutable ledger/controller provenance and governance floor | `contracts/production/`, [deployment architecture](docs/06_production_architecture.md) |
| State-machine invariants and compiler artifact fingerprints | `verification/ledger_model.py`, `scripts/audit_contracts.cjs`, [verification limits](docs/09_verification.md) |
| Evidence-based release promotion | `production/release_gate.py`, [required evidence](docs/10_release_gates.md) |
| Exact unchanged redemption statement | `production/circuit-statement.json` |

The production controller starts with deposits disabled.
Its constructor pins the verifier runtime hash, release digest, incident guardian, enrollment authority, and exact approved timelock implementation.
The factory configures ledger escrow atomically and nominates the timelock as governor.
The multisignature proposer schedules governance acceptance and subsequent deposit activation through the timelock's minimum two-day delay.
The factory has no path to use its temporary governor authority for other ledger operations.
Enrollment uses EIP-712 signatures bound to depositor, note commitment/value, one-use enrollment identifier, expiry, release digest, chain, and controller.
Issuer keys may use ECDSA or an ERC-1271 signing contract; a signature is admission authorization, not evidence of physical unclonability.
Guardians can independently halt deposits or redemptions; only delayed governance can resume either.
There is no guardian withdrawal or verifier replacement path.

Before deployment, independently provision and pin the actual ceremony, execution, and release policies.
The example policy files intentionally contain invalid placeholders.
The release gate requires exact deployment parameters and artifact hashes, at least two distinct release authorities, separate executor attestation authority, independent security/refinement reviews, custody evidence, physical enrollment qualification, privacy-scope review, jurisdiction review, and an incident recovery drill.
It does not sign or broadcast a transaction.

```bash
python3 production/ceremony.py verify \
  --policy /trusted/ceremony-policy.json --manifest /release/manifest.json \
  --archive /durable/archive --replica /independent/replica
python3 -m production.release_gate \
  --config /release/deployment.json --policy /trusted/release-policy.json \
  --approvals /release/approvals.json --root /release/bundle
```

No independent production ceremony, live chain deployment, physical PUF qualification, or compliance approval has been performed by these repository tests.
Fuzzy extraction and shielded payments have explicit integration designs and acceptance requirements; neither is represented as a completed privacy system.
The formal model proves its stated abstract transitions, not automatic equivalence of deployed Solidity bytecode.

The empirical validation lane builds a dependency-complete image from the exact
committed `git archive`, records its SHA-256 and immutable image ID, and runs
`npm run check` inside a real rootless container with network disabled, a read-only
root filesystem, no capabilities, UID 65532 and bounded CPU/memory/PIDs.
The CI artifact `rootless-protocol-validation` retains the test log, compiler
report, local-test proof artifacts, package versions, hashed provenance and the
exact exported image for offline replay.
A missing rootless runtime, failed test, missing evidence or failed cleanup
fails this lane; there is no simulated acceptance fallback.

Operational extensions and their limits are documented in the
[corrected readiness roadmap](docs/11_readiness_roadmap.md),
[authenticated beacon and strict ceremony profile](docs/12_drand_beacon.md), and
[physical qualification evidence requirements](docs/13_hardware_qualification.md).
These checks do not replace actual independent ceremony participants, measured
silicon behavior, custody provisioning or signed external reviews.

The full validation image uses separately digest-pinned Node and Python 3.12
stages; a Debian system Python must not silently replace the supported runtime.
Its bounded `/tmp` is 2 GiB to accommodate verified toolchain archive and replica
copies. The initial 256 MiB allowance failed under real container validation;
that failure remained a blocked acceptance result rather than being skipped.

The browser-worker client, canonical statement checks, balance display and
constrained Chromium acceptance are described in the
[client protocol](docs/14_client_protocol.md).
The durable operator CLI starts with `bin/tcl-cli health`;
[architecture](docs/ARCHITECTURE.md), [setup](docs/SETUP.md),
[API](docs/API.md) and [security boundaries](docs/SECURITY.md) distinguish
implemented storage and settlement from later Merkle/daemon/worktree migrations.
