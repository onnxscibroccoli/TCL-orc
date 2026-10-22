# Production ceremony and provenance

This repository has **not run a production multiparty ceremony**. Its local
`zk:setup` artifacts are development material. A production release must instead
satisfy the external policy and authenticated workflow below. Never promote a
locally generated development zkey by renaming it.

`production/ceremony.py` verifies externally produced snarkjs 0.7.6 transcripts.
It does not generate participant secrets, collect entropy, manufacture
participants, or prove erasure. Security requires at least one independent honest
participant to generate private randomness and erase its secret contribution in
**each** of phase 1 and phase 2. Signatures, identity counts, public beacons, and
successful transcript verification cannot establish that assumption.

## Trust and prerequisites

Install the pinned Node dependencies with `npm ci` and the Python dependencies in
`requirements-production.txt`. Participants independently review the preserved
`circuits/puf_verify.circom`, its includes, compiler, source revision, and build.
The Groth16 relation must retain exactly five public inputs in its established
order. A complete `node_modules` tree is pinned because snarkjs imports code from
its dependencies; pinning only a launcher is insufficient.

Copy the reviewed source into a clean, separate staging directory, excluding
`node_modules`, generated artifacts, receipts, policy, and manifest. Build from
that source and independently compare R1CS/WASM outputs. For a tracked source snapshot, for example, run `mkdir reviewed-source` followed
by `git archive HEAD | tar -x -C reviewed-source`. Set the manifest's `source`
path to that directory and `circuit` to its `circuits/puf_verify.circom`; the
circuit must reside inside the pinned source tree.

The ceremony gate recompiles the pinned circuit with the pinned circom2 0.2.22
toolchain, requires identical R1CS/WASM hashes, and checks mathematical consistency between R1CS, ptau, and zkey, five public
inputs, and exact exported verification key and Solidity verifier. Independent review must still establish that the pinned source expresses the
intended relation and that the selected toolchain is trustworthy.

Distribute the policy through an authenticated out-of-band channel. Merely
fetching a policy beside an untrusted manifest does not authenticate its public
keys. Use a protected runner and reviewed Node binary; the gate does not defend
against an attacker controlling the host while verification executes.

Start from `production/ceremony-policy.example.json`. It is deliberately invalid:
there are no participant or release keys and no actual approved hashes. Never
commit real private keys or private entropy. Generate Ed25519 keys on each
participant's own signing device, and place only their 32-byte public keys as
64-character hex strings in the policy. Use different keys for every identity
and for release approval. The gate rejects duplicate public keys across roles.

## Policy schema

The policy is JSON with these required fields:

| Field | Meaning |
| --- | --- |
| `version` | Integer `1` |
| `contributors` | Object mapping independently authenticated IDs to Ed25519 public-key hex |
| `release_approvers` | Separate ID to Ed25519 public-key hex map |
| `phase_quorum` | Minimum distinct **private** contribution signers in each phase, at least 2; beacon signers do not count |
| `release_quorum` | Minimum distinct release signatures, at least 2 |
| `pinned_artifacts` | Expected artifact SHA256 values; at least `source`, `circuit`, `lock`, `toolchain` |
| `beacons` | Objects `ptau` and `zkey`, each with `source`, `value`, `iterations`, `name` |

Each beacon's `source` specifies a previously agreed future public event,
observation time, retrieval method, and value derivation. Preserve that prior
commitment independently. The final policy includes the observed public hex
`value` of at least 32 bytes, an integer iteration exponent from 10 through 63,
and the exact UTF-8 contribution `name` (maximum 64 bytes). Choose the exponent
for the publicly reviewed ceremony protocol; the example's 10 is a runnable
illustration, not a recommendation for a particular production threat model.

The gate reproduces the deterministic beacon output using the pinned value and
name. It does not query a public randomness service or establish that an event
was unpredictable or observed honestly. Independent reviewers authenticate the
public observation and earlier commitment before trusting the final policy.
Receipts may be signed retrospectively after both public beacon values are
known and the final policy is frozen. No policy change is permitted after signing;
a change requires fresh receipts and release signatures.

Compute a file or directory digest with:

```sh
python3 production/ceremony.py digest /reviewed/source
python3 production/ceremony.py digest node_modules
```

File digests are ordinary SHA256. Directory digests hash sorted relative POSIX
filenames encoded as canonical JSON strings, followed by each file's binary
SHA256 digest. Empty directories and file permissions are excluded. File symlinks are allowed only when their resolved target stays within the
pinned tree; their target bytes are hashed and archival copies dereference them.
Directory symlinks, dangling links, and escaping links are rejected. This supports
normal npm `.bin` links while binding the complete installed dependency bytes. Use the gate's digest command for
these directory pins; `sha256sum` on a directory is not equivalent.

Policy IDs and quorums are governance trust decisions; use actual independently
controlled parties and require more than one private contributor in each phase.
The gate requires at least two private contributors in each phase and at least
two release approvers. Identity authentication and actual independence still
require governance review.

## Executable participant workflow

Run the pinned local CLI explicitly. The following shell variable contains a
public executable path, never entropy:

```sh
SNARKJS=node_modules/snarkjs/build/cli.cjs
node "$SNARKJS" powersoftau new bn128 14 phase1_0000.ptau
node "$SNARKJS" powersoftau contribute phase1_0000.ptau phase1_0001.ptau -n="Alice phase1"
node "$SNARKJS" powersoftau verify phase1_0001.ptau
```

The contributor command prompts interactively for entropy on the participant's
own computer. Never pass entropy via `-e`, shell arguments, shared orchestration,
logs, or environment variables. Use freshly generated private randomness per
participant and phase, then erase it according to independently reviewed device
procedures. Select the phase-1 power from the actual reviewed circuit's size;
14 above is only a command example. Alice transfers the output and signed
receipt to Bob, who independently verifies it, contributes to that exact file,
and returns the next output and receipt. Continue to the configured quorum.
Record every file, including genesis, each contribution, and beacon outputs.

After the precommitted phase-1 public event has occurred, obtain and independently
verify its value. Here `PUBLIC_BEACON_HEX` is public data:

```sh
node "$SNARKJS" powersoftau beacon phase1_last.ptau phase1_beacon.ptau "$PUBLIC_BEACON_HEX" 10 -n="TCL phase1 public beacon"
node "$SNARKJS" powersoftau verify phase1_beacon.ptau
node "$SNARKJS" powersoftau prepare phase2 phase1_beacon.ptau phase1_final.ptau
node "$SNARKJS" powersoftau verify phase1_final.ptau
node "$SNARKJS" groth16 setup puf_verify.r1cs phase1_final.ptau phase2_0000.zkey
node "$SNARKJS" zkey contribute phase2_0000.zkey phase2_0001.zkey -n="Alice phase2"
node "$SNARKJS" zkey verify puf_verify.r1cs phase1_final.ptau phase2_0001.zkey
```

Repeat phase-2 private contributions on independent participant devices, then
apply the separately precommitted phase-2 public beacon:

```sh
node "$SNARKJS" zkey beacon phase2_last.zkey final.zkey "$PUBLIC_PHASE2_BEACON_HEX" 10 -n="TCL phase2 public beacon"
node "$SNARKJS" zkey verify puf_verify.r1cs phase1_final.ptau final.zkey
node "$SNARKJS" zkey export verificationkey final.zkey verification_key.json
node "$SNARKJS" zkey export solidityverifier final.zkey Groth16Verifier.sol
```

A designated authenticated contributor signs the deterministic beacon receipt.
That identity must be distinct from every private contributor in the same phase.
A public beacon never substitutes for a private contribution or honest erasure.

## Manifest and signed receipts

All manifest artifact paths are relative to the manifest's directory, or absolute
paths. Every artifact entry is `{"path":"...","sha256":"64 hex digits"}`.
The exact required artifact names are:

```text
source circuit r1cs wasm lock toolchain ptau proving_key verification_key verifier
```

`source` is the reviewed source staging directory; `circuit` is the preserved
Circom file; `lock` is `package-lock.json`; `toolchain` is the complete pinned
`node_modules` directory; `ptau` is the prepared phase-1 final file;
`proving_key` is the phase-2 final beacon output. The remaining names identify
the corresponding build and export files.

The manifest shape is:

```json
{
  "version": 1,
  "policy_sha256": "SHA256_OF_CANONICAL_POLICY_JSON",
  "artifacts": {"source": {"path": "source", "sha256": "..."}},
  "phases": {
    "ptau": {"initial": {"path": "phase1_0000.ptau", "sha256": "..."}, "receipts": []},
    "zkey": {"initial": {"path": "phase2_0000.zkey", "sha256": "..."}, "receipts": []}
  },
  "approvals": []
}
```

The abbreviated artifact map above must contain all ten names in a real manifest.
Each receipt is `{"payload": PAYLOAD, "signature": "ED25519_SIGNATURE_HEX"}`.
A private contribution's payload is:

```json
{
  "phase": "ptau",
  "contributor": "alice",
  "kind": "contribution",
  "policy_sha256": "SHA256_OF_CANONICAL_POLICY_JSON",
  "r1cs_sha256": "REVIEWED_R1CS_SHA256",
  "predecessor_sha256": "PREVIOUS_FILE_SHA256",
  "path": "phase1_0001.ptau",
  "output_sha256": "THIS_FILE_SHA256"
}
```

Use `phase: "zkey"` in phase 2. A final beacon receipt uses `kind: "beacon"` and
adds `beacon` equal to the entire corresponding policy beacon object. Receipt
order is transcript order. Each output must mathematically verify and append
exactly one contribution to its predecessor's full transcript metadata.
Distinct valid files from a different ceremony branch are rejected. The gate
reproduces both zero-contribution genesis files, phase-1 preparation, both
beacons, and verifier exports, so hashes alone do not stand in for transcript
mathematics or linkage to the reviewed R1CS.

Canonical JSON uses sorted keys, no insignificant whitespace, ASCII escaping,
and UTF-8. Compute canonical policy SHA256 locally:

```sh
python3 -c 'import hashlib,json; p=json.load(open("policy.json")); print(hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest())'
```

Create the exact bytes for a receipt signature:

```sh
python3 production/ceremony.py signing-message --domain receipt receipt-payload.json > receipt-message.bin
```

The bytes are `TCL-CEREMONY-v1:receipt`, newline, then canonical payload JSON.
Each participant signs these bytes using its independently held Ed25519 key;
encode the 64-byte signature as 128 hex digits. The gate intentionally has no
private-key signing command. For OpenSSL Ed25519 PEM signing keys, for example:

```sh
openssl pkeyutl -sign -rawin -inkey /private/alice.pem -in receipt-message.bin -out receipt-signature.bin
python3 -c 'print(open("receipt-signature.bin","rb").read().hex())'
```

A release approver signs the entire final manifest **excluding** `approvals`,
using domain `release`. Save that object as `release-payload.json`:

```sh
python3 production/ceremony.py signing-message --domain release release-payload.json > release-message.bin
```

Append approvals as `{"approver":"reviewer-id","signature":"..."}`. Duplicate
IDs, duplicate public keys, duplicate JSON object keys, insufficient quorums,
wrong policies, invalid signatures, wrong branches, missing artifacts, unsupported
snarkjs versions, and substituted hashes fail closed. Release signatures cover
every receipt, all artifact hashes and paths, and the trusted policy digest.

## Verify, archive, and independent readback

```sh
python3 production/ceremony.py verify \
  --policy /trusted/policy.json \
  --manifest /release/manifest.json \
  --archive /archive/sha256 \
  --replica /independent-mounted-readback/sha256
```

Success prints the canonical release payload SHA256 and exits zero. A rejection
prints a reason and exits nonzero. Use the separate `audit` subcommand with the same policy and manifest flags
for read-only verification. Production `verify` requires both archive flags. No proof submission,
deployment, or production activation is authorized by this command.

Archival names are content addresses. Writers hold a cross-process archive lock,
copy into temporary staging objects, verify and fsync file data and directory
entries, then atomically publish without overwriting an existing address. File
publication uses an exclusive hard link; directory publication requires Linux
`renameat2(RENAME_NOREPLACE)`. Interrupted staging copies never become final
objects and are removed on a subsequent retry. Filesystem and mounted storage
services must honor fsync and atomic publication semantics. Existing objects are
never overwritten or silently repaired;
all existing and newly copied objects are read back and hash verified, including
initial and intermediate transcripts, source and dependency trees, canonical
policy, and canonical manifest. Both archive roots must be separate and may
not contain one another. Run the replica path against a separately operated
storage service or independently mounted copy; local path separation does not
prove independent custody, durability, physical media, or access controls.

Files are set read-only as a local guard. For durable immutable archival, use
externally administered WORM/object-lock storage, retention policy, access
controls, periodic readback, and copies held by independent parties. A privileged
host administrator can override filesystem permissions. A hash readback verifies
bytes and is never a substitute for the snarkjs transcript checks performed by
the gate. Keep the original authenticated policy distribution and public beacon
commitment evidence alongside the archived release.

Tests use ephemeral generated signing keys and tiny synthetic local transcripts.
They do not constitute a production ceremony or evidence of participant erasure.
