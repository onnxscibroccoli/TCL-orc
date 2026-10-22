# Offline authenticated quicknet beacon

`production/drand.py` verifies a saved quicknet response with `py_ecc==8.0.0`.
Verification performs no network requests. The frozen Circom statement and the
snarkjs MPC construction are unchanged. This adapter verifies beacon material;
its tests are not evidence that a production ceremony occurred.

The external authenticated ceremony policy must pin the complete `chain_info`
object and its `chain_info_sha256` (SHA-256 of sorted compact UTF-8 JSON), as in
`production/drand-chain.example.json`. The pinned object includes chain hash,
public key, scheme, genesis time, period, group hash, and beacon identifier.
This digest is an application policy pin of the serialized chain metadata; it
is deliberately **not** a reimplementation of drand's chain hash algorithm.
Changing metadata and recomputing its digest is authorized only by replacing
the trusted external policy, never by accepting metadata from the response.

Before the final contribution, record `make_commitment(policy, round,
committed_at)` in the authenticated append-only ceremony log. It binds the
round, chain hash, complete metadata digest, and commitment timestamp. Then
record the contribution completion timestamp. `verify_beacon(policy,
commitment, response, completed_at)` requires commitment time strictly before
completion and the selected round's scheduled time strictly greater than
`completed_at + 120`. Scheduled time is `genesis_time + (round-1)*period`.
A round exactly 120 seconds later is rejected. Signed external logs establish
who attested these times; neither this verifier nor BLS proves clock honesty,
prepublication, or that the coordinator waited. The ceremony integration must
authenticate both records and enforce their ordering before using this result.

Only `bls-unchained-g1-rfc9380` is accepted: 96-byte G2 public key and 48-byte
G1 signature. The signed message is SHA-256 of the uint64 big-endian round.
Hash-to-G1 uses the official RFC9380 DST
`BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_NUL_`. Point decoding, hash-to-curve,
subgroup multiplication, and pairings are delegated to py_ecc. Infinity,
non-subgroup, malformed/noncanonical hex, signature mismatch, and a mismatch
between SHA-256(signature) and the response randomness are rejected. Verified
output contains the canonical randomness hex suitable as snarkjs beacon input;
ceremony transcript hashing and final dual-hash binding are separate ceremony
responsibilities.

Offline CLI:

```sh
python production/drand.py --policy production/drand-chain.example.json \
  --commitment commitment.json --response response.json \
  --completed-at 1692803200
```

The CLI emits canonical JSON only after verification. Its timestamp argument
is not an authentication mechanism. The round-one regression vector uses
historical timestamps exclusively to verify interoperability, and must never
be presented as a future production commitment.

## Primary interoperability references

- [Official scheme implementation](https://github.com/drand/drand/blob/master/crypto/schemes.go), `NewPedersenBLSUnchainedG1` and `RandomnessFromSignature`: round encoding, message hash, DST, group selection, and signature hash.
- [Official quicknet chain info](https://api.drand.sh/52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971/info), retrieved 2026-10-21; all fields are pinned in the example policy.
- [Official round-one response](https://api.drand.sh/52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971/public/1), embedded in `tests/test_drand.py`, verified against the pinned key.
- [Official quicknet announcement](https://docs.drand.love/blog/2023/10/16/quicknet-is-live), describing correction of the earlier noncompliant G1 scheme.
- [py_ecc hash-to-curve implementation](https://github.com/ethereum/py_ecc/blob/v8.0.0/py_ecc/bls/hash_to_curve.py) and [point compression](https://github.com/ethereum/py_ecc/blob/v8.0.0/py_ecc/bls/point_compression.py).

Run `python -m unittest discover -s tests -p test_drand.py` for the real network
vector and negative mutations. Successful verification authenticates the
beacon under the policy key and BLS assumptions; it does not prove the network
threshold remains uncompromised or that external timestamp attestations are true.

## Strict mainnet ceremony profile

`production/ceremony_profile.py` wraps the native snarkjs ceremony verifier.
It requires ten distinct private contributor keys in each phase, SHA-256 and
BLAKE2b-512 on every transcript output and artifact, the frozen circuit/ledger
source digests, independent archive readback and authenticated quicknet beacons.
Phase two must complete after phase one's beacon time and use a later round.
Two distinct timeline witnesses must be separate from ceremony/release keys.

An externally pinned profile has `version: 1`,
`profile: "tcl-mainnet-ceremony-v1"`, `minimum_contributors: 10`,
`timeline_quorum: 2`, `timeline_keys` mapping identifiers to Ed25519 hex keys,
`ceremony_policy_sha256`, `drand_policy_sha256`, and `beacon_commitments`
containing `ptau` and `zkey` commitment objects described above.
The manifest adds `blake2b512` to artifact and initial-file specifications;
signed receipt payloads add `output_blake2b512`.

The timeline envelope has `payload` and `approvals`. Its payload has
`domain: "TCL-ceremony-timeline-v1"`, `manifest_sha256`, and `phases`.
Each phase has `commitment`, `completed_at`, `final_private_output_sha256`
and the original drand `response`. Witnesses sign
`b"TCL-CEREMONY-TIMELINE-v1\n" + canonical(payload)`; approvals contain
`key_id` and hex `signature`. All JSON pins use sorted compact UTF-8 JSON.

```sh
python -m production.ceremony_profile \
  --profile /trusted/mainnet-profile.json --profile-sha256 EXTERNAL_SHA256 \
  --policy /trusted/ceremony-policy.json --drand-policy /trusted/drand.json \
  --manifest /release/manifest.json --timeline /release/timeline.json \
  --archive /durable/archive --replica /independent/replica
```

There is no audit-only bypass in this wrapper. The generic verifier's lower
research quorum does not satisfy this mainnet profile. Archive the profile,
timeline and beacon policy alongside the native verifier's artifacts.
The profile report is additional input to release authorities, not automatic
approval by `release_gate.py`, which continues to verify their signed evidence.
Witness signatures authenticate assertions; external append-only publication
and honest clocks are still required. Software cannot establish organizational
independence or that private toxic waste was erased.
