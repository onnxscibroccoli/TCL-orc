# Production Deployment Architecture

## Deployment goal and invariant boundary

This release adds enforceable production deployment controls around the executable research ledger.
It preserves the five public Groth16 inputs `[commitment, nullifier, value, recipient, context]`, permanent spent commitments, fixed supply, the 5,000,000 AETH threshold, and nominal compounding rate of 0.0005 per day.
EVM transactions remain the sole authority for real balances and escrow releases.
Off-chain executors cannot write ledger balances and never receive a transaction signing key by default.
A production architecture is not evidence that an independent ceremony, hardware validation, legal assessment, or security audit has occurred.

## Release and ceremony trust

An externally anchored policy pins independent contribution identities and separate release-approval identities.
Every recorded phase-one and circuit-specific phase-two artifact has a signed predecessor-linked receipt and undergoes native snarkjs verification.
Release manifests bind the exact circuit, dependency lock, source, R1CS, WASM, proving key, verification key, and verifier artifacts.
The operator verifies durable archival copies and independent replica readback before release acceptance.
A manifest signature cannot prove independent control of participants or destruction of toxic waste.
Production ceremonies require independently operated participant environments, participant-controlled entropy, public transcript publication, a precommitted future randomness beacon, and at least one honest erasing contributor in each required phase.
No local development key or single-operator ceremony is eligible for production release.

## Settlement isolation and governance

The chain serializes state transitions and rolls back proof rejection, balance insufficiency, and external-call failure atomically.
Transaction submitters use chain-specific nonces, bounded replacement policies, confirmation/finality thresholds, and reorg-aware event indexing rather than interpreting an RPC timeout as failed execution.
The production controller pins release provenance and verifier runtime code hash and separates stopping new deposits from redemption availability.
A guardian can stop deposits or redemptions independently and immediately; either resumption requires delayed governance.
A redemption halt freezes escrow without permitting a privileged withdrawal.
Governance is a pinned deployed timelock controlled by an independently configured multisignature contract.
Deployment records publish the exact chain identifier, compiler build, constructor arguments, artifact hashes, governor, guardian, and immutable controller addresses.
The controller constructor checks the exact approved ProtocolTimelock runtime code hash, rejecting forwarding contracts that merely report a delay.
The deployment factory configures the ledger and controller in one transaction and nominates delayed governance.
The timelock accepts ledger governance through its normal delayed execution path before activation.
The temporary factory governor has no callable path to redistribute, replace the controller, or transfer governance again.
A guardian cannot withdraw escrow or replace the verifier.
A proof-bound recipient remains unchanged even if another sender front-runs a redemption.

## Isolated execution and signing separation

Production jobs use a distinct signed-plan executor rather than local Git worktrees.
A pinned OCI image digest executes a verified source snapshot as a non-root user with a read-only filesystem, no network, dropped capabilities, no privilege escalation, and explicit resource limits under a rootless container daemon.
Only approved file-backed secrets may be injected, and secret-bearing output is not retained in task logs.
Provenance binds the source, plan, image, policy, and execution result.
Durable state uses compare-and-set task claims and refuses automatic replay after an uncertain interruption.
A separate signing service must independently verify deployment policy, approved calldata, chain, nonce, and release identity before authorizing an on-chain transaction.
The task runtime is not that signing service and never gains blanket wallet authority.

## Physical PUF enrollment and fuzzy extraction

Enrollment must begin in a measured physical environment with qualified readers, authenticated operators, calibrated environmental conditions, and a documented substrate threat model.
The issuer records a signed device-independent enrollment attestation containing a random opaque note identifier, denomination, substrate batch, reader identity, challenge-domain version, helper-data hash, extractor-suite version, revocation epoch, and provenance manifest digest.
Public records must not contain the raw PUF response or extracted redemption secret.
Repeated enrollment, temperature and aging sweeps, false acceptance, false rejection, residual min-entropy, and malicious-reader resistance determine admission thresholds before production.
For extraction, the chosen secret length and statistical distance must satisfy a reviewed conditional-min-entropy budget after helper-data leakage.
Hash-to-field conversion must use a reviewed uniform-sampling or rejection-sampling procedure into the nonzero BN254 scalar field.
The extractor uses a reviewed secure sketch plus error-correcting reconstruction and a seeded strong extractor with explicit conditional min-entropy accounting.
Helper data is authenticated and versioned because helper-data manipulation can invalidate the entropy or reconstruction guarantees.
A generic hash of a noisy response is not a fuzzy extractor.
The enrollment service must domain-separate the reconstructed secret from any reader authentication key and deliver note blinding material through an authenticated confidential channel.
Issuer signing keys belong in an independently administered HSM or equivalent signing service with rotation, revocation, dual control, and auditable access.
The implemented on-chain admission requires a reviewed issuer signature over the Poseidon commitment, value, chain, controller, expiry, depositor, and a one-use enrollment identifier without changing the redemption proof statement.
An enrollment identifier must bind the issuer's canonical off-chain enrollment attestation, including authenticated helper-data and extractor-suite hashes.
This release implements the issuer-signature adapter and specifies the physical boundary rather than implementing a new, unaudited error-correcting cryptosystem or claiming real substrate enrollment.
A stolen reconstructed secret still allows a competing valid redemption, so physical anti-copy assurance requires hardware and operational controls in addition to the proof.

## Shielded payment and identity extension

Shielded payments require a separate audited pool and circuit rather than relabeling transparent escrow as anonymous.
The proposed pool uses append-only note commitments, an authenticated Merkle root history, membership proofs, domain-separated global spend nullifiers, encrypted output notes, and public value conservation across joins and exits.
Deposit commitments and withdrawal recipients remain linkable through denomination and timing unless the privacy protocol explicitly addresses those channels.
A shielded transition must bind chain, pool, asset, accepted root, input nullifiers, output commitments, public deposit or withdrawal amount, relayer fee, and withdrawal recipient.
The shielded pool must never receive the physical-controller demurrage exemption without a reviewed economic specification for hidden surplus.
Per-person immunity needs a separate privacy-preserving uniqueness credential with issuer or federation trust, revocation, anti-transfer policy, and an explicit recovery model.
The unresolved composition problem is proving aggregate per-person surplus across transparent accounts, shielded notes, and physical holdings without exposing identity or enabling exclusion.
Until that model is reviewed, the deployed ledger threshold remains per address and production promotion must acknowledge the resulting Sybil and exemption risks.
No identity claim is inferred from a wallet address or PUF possession.

## Continuous auditing and promotion

Release CI compiles and fingerprints production contracts, runs symbolic state-machine invariants with counterexample tests, exercises EVM concurrency and rollback, and reruns real Groth16 proof tampering tests.
The symbolic model proves only its stated transition system and does not replace refinement analysis or an audit of deployed bytecode.
Isolated-container execution, independent MPC participants, durable replica retention, key custody, physical qualification, and jurisdiction-specific review require separate acceptance evidence.
Release promotion fails closed when required evidence, hashes, signatures, or approvals are missing.
Public read-only monitoring checks event continuity, finality, escrow solvency, vault conservation, guardian actions, release identity, and artifact replica availability.
Any unexpected controller code hash or escrow shortfall stops new deposits and pages the incident authority without automatically changing the proof verifier.
