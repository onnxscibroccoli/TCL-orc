# Offline production release evidence gate

The release gate validates a concrete deployment proposal against an independently
provisioned policy and external Ed25519 approvals. It prints only an approved
SHA-256 deployment digest. It does not create keys, sign approvals, deploy contracts,
or broadcast transactions. This repository contains no actual external approvals,
independent audit acceptance, custody certification, or legal compliance finding.

## Interface

```sh
python3 -m production.release_gate --config deployment.json --policy policy.json \
  --approvals approvals.json --root /absolute/path/to/artifact-bundle
python3 -m unittest discover -s tests -p 'test_release_gate.py'
```

The Python API is `validate(config, policy, envelope, root, now=None)`. Failure raises
an exception and the CLI exits nonzero. Success returns the digest of the canonical
approval payload. Canonical JSON uses sorted keys, compact separators, UTF-8,
unescaped Unicode and no NaN. All signatures cover these exact bytes.

## External policy and deployment proposal

Policy is a trust root that must be pinned and provisioned through an external
release process. Allowing a proposer to replace both policy and proposal would
invalidate the approval boundary. Keep the expected policy digest in independently
controlled release infrastructure; the proposal's policy digest alone does not
establish trust.

Policy fields are:

| Field | Meaning |
| --- | --- |
| `version` | Integer `1` |
| `chain_id` | Intended positive chain ID |
| `circuit_version` | Exact statement version |
| `quorum` | At least two distinct release public keys |
| `release_keys` | Key ID to raw 32-byte Ed25519 public key encoded as hex |
| `attestation_keys` | Separate accepted executor attestation public keys |
| `attestation_quorum` | Positive threshold, defaults to one |
| `max_validity_seconds` | Maximum approval lifetime |
| `required_evidence` | All mandatory kinds below, plus any policy additions |
| `artifact_pins` | Exact SHA-256 pins for statement, manifest, audit and executor provenance |
| `deployment_pins` | Exact intended deployment parameters below |
| `source_pins` | Exact source path to SHA-256 mapping from compiler audit |

The mandatory `deployment` object contains nonzero Ethereum addresses `governance`,
`guardian`, `initial_holder`, `verifier`, `enrollment_authority`, and `factory`;
nonzero 32-byte hexadecimal values `verifier_code_hash`, `release_manifest_hash`,
and `governance_code_hash`; positive base-unit `supply` below $2^{128}$;
positive `finality_confirmations`; and nonnegative `max_reorg_depth` smaller than
that confirmation threshold.
The entire deployment object must equal externally provisioned `deployment_pins`.
The controller's release-manifest hash is the canonical ceremony release digest
returned by `production/ceremony.py verify`, rather than a hash of an approval
signature's representation.

The proposal contains `policy_sha256` (canonical policy digest), `chain_id`,
`purpose: "production-release"`, integer `issued_at` and `expires_at` Unix seconds,
`circuit_version` and `evidence`. Include all intended constructor, governance,
network and deployment parameters as additional proposal fields: every field is
covered by the release signatures. Freshness uses the release runner's clock.

Each evidence entry is `{ "path": "relative/file", "sha256": "hex digest" }`.
The gate resolves paths under the artifact root, rejects escapes, reads actual
nonempty bytes, and verifies their digests. Any evidence containing the marker
`local-test-ceremony` is rejected. Source and dependency files are also read and
verified against the compiler report. No evidence-presence Boolean can substitute
for artifact bytes.

Mandatory evidence kinds are:

- `circuit_statement`: JSON with exact `version` and a nonempty full `statement`.
- `release_manifest`: the exact externally approved release manifest bytes.
- `contract_audit`: the compiler audit report, pinned as an entire artifact,
  including compiler identity, compiler input digest, source hashes and bytecode.
- `executor_provenance` and `executor_attestation`: exact provenance and its signed
  chain-bound attestation.
- `governance_custody`, `guardian_custody`, and `issuer_custody`: custody evidence
  accepted by the release authorities.
- `independent_security_audit` and `source_model_refinement_audit`: independent
  reviews covering implementation security and the Solidity-to-symbolic-model gap.
- `mpc_report`, `mpc_archive`, and `mpc_replica`: accepted ceremony report and actual
  archived/replicated artifact bytes.

- `physical_enrollment_qualification`: independent physical measurements and extractor qualification for the admitted note substrate.
- `privacy_scope_review`: reviewed privacy claims, public metadata exposure, and any shielded-pool integration boundary.
- `jurisdiction_review`: a release-specific review by qualified counsel for intended deployment locations and activities.
- `incident_recovery_drill`: demonstrated custody recovery, incident halt, and chain reorganization procedures.
- `rootless_runtime_acceptance`: actual container isolation evidence from the deployment-class runtime.

The contract report format is generated by `scripts/audit_contracts.cjs`. The gate
checks its source pins, reads those sources and resolved dependency bytes, and
recomputes creation/runtime-template bytecode digests. External policy pins the
whole report, thereby binding exact compiler identity and settings digest. This
is compiler provenance, not proof of deployed runtime equivalence. Constructor
immutable substitutions and chain deployment verification remain separate duties.

## Attestation and approval envelopes

Executor attestation JSON contains `payload` and `signatures`. The exact payload is:

```json
{
  "domain": "TCL-executor-attestation-v1",
  "chain_id": 1,
  "executor_provenance_sha256": "the exact provenance artifact digest"
}
```

Each signature is `{ "key_id": "authority-id", "signature": "Ed25519 hex" }`.
The separate policy attestation key set verifies that envelope.

Release approvals use the same signature entry format. Their exact `payload` is
`{"domain":"TCL-production-release-v1","deployment": <entire proposal>}`.
At least the policy quorum of distinct public keys must sign this exact object.
Repeated key IDs or hexadecimal/whitespace aliases of the same decoded 32-byte
public key do not count twice. Executor attestation public keys must be disjoint
from release authority public keys, comparing decoded bytes. Any changed
proposal field, artifact digest, policy digest, chain or validity time invalidates
approval. The successful output is the SHA-256 digest of that payload.

The MPC report is JSON with `purpose: "production-ceremony"`, `status: "accepted"`
and `archive`/`replica` objects. Each object requires `status: "verified"` and
`sha256` matching the actual corresponding evidence bytes. These statuses are
claims made by external release authorities: signatures bind them to the exact
bundle. The gate does not independently rerun an MPC ceremony or verify remote
replication. Likewise opaque custody and independent audit files have semantics
accepted by the policy authorities, not inferred by this program.

The source-to-model refinement audit is mandatory precisely because UNSAT results
for the accounting abstraction do not establish Solidity or EVM correctness. A
signed synthetic test fixture demonstrates gate behavior only; it provides no
production approval. Missing evidence blocks promotion until external authorities
supply and approve the concrete release bundle.
