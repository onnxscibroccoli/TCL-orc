# Empirical client protocol iteration

The wallet SDK uses the existing Groth16 statement and frozen ledger.
It adds no consensus, shielded payments, enrollment, custody, or recovery mechanism.
Production deployment remains blocked by independent ceremony, hardware, custody, audit, and legal evidence.

## Authoritative protocol boundaries

The public signals are commitment, nullifier, value, recipient, and context, in that order.
Context binds the chain ID and controller address through the existing ABI hash.
The nullifier is recipient-bound; permanent commitment consumption also prevents replay to another recipient.
Proof bytes are not replay identifiers.
The integration test rerandomizes a valid Groth16 proof and verifies that settlement still rejects a spent statement.

The 5,000,000 AETH threshold is a per-account demurrage exemption, not a global supply cap.
The client mirrors the existing integer exponentiation with scale 10^27 and the existing second factor.
No Taylor approximation, arbitrary 55-wei correction, issuance recycling, or circuit migration is introduced.
Thirteen existing symbolic ledger obligations do not certify complete circuit constraints or compiled bytecode.
MPC setup contributions are not threshold signing or distributed consensus.

## Client execution

Run `npm run zk:setup`, then `npm run test:client` inside the validation image.
The build writes browser bundles and digest-pinned proof assets to `build/client`.
Serve that directory over HTTPS for an operator-reviewed deployment.
The read-only page requires an injected EIP-1193 wallet and an independently authenticated `deployment.json`.
That policy contains decimal `chainId`, `controller`, `verifier`, their lowercase `controllerCodeHash` and `verifierCodeHash`, and `ledgerCodeHash`.
Its `assets` object uses the generated `assets.json` entries, each containing same-origin URL, exact byte count, and SHA-256.
Development setup output is explicitly local-test-only and cannot satisfy ceremony release gates.

The SDK transfers a 64-byte secret/blinding buffer to a disposable browser worker.
It verifies asset bytes and hashes, locally verifies the proof, checks canonical signals, and rechecks deployment context before submission.
Worker termination and byte-buffer clearing reduce lifetime but do not guarantee JavaScript heap erasure or protect against a compromised browser origin.
A trusted asset manifest and deployment policy must be distributed independently of an untrusted RPC endpoint.
Transactions remain public, and this implementation makes no shielded-payment privacy claim.

Submission reserves its proof before any asynchronous RPC call.
Concurrent submissions and proof generation during submission are rejected.
An RPC rejection, timeout, or context change consumes the local authorization and requires external transaction reconciliation.
There is deliberately no automatic resend or API that silently clears uncertain state.
A returned transaction hash is submission evidence, not finality.
Operators must inspect receipts and canonical-chain state before creating a new client after uncertainty.

The interface separates committed stored balance from an unsettled display estimate.
Snapshots use one block number and recheck its hash; chain/account events invalidate the display immediately.
Local interpolation becomes stale after 60 seconds.
The private ledger rounding remainder causes up to one wei of uncertainty at a given timestamp; prediction error from future block timestamps is separate.
No less-than-100-wei future-time accuracy guarantee is made.

## Measured acceptance and limitations

Rootless validation executes the full suite offline in a read-only, capability-dropped OCI runtime.
A second fresh container runs actual Chromium proof generation with a two-CPU quota and 2 GiB memory limit.
The browser test asserts a cold worker deadline below 4,500 milliseconds and records the measured result, browser version, cgroup limits, and memory peak.
A failed latency target fails validation rather than being described as production-qualified.
Browsers expose no reliable hard per-worker 256 MiB cap here, so that target remains uncertified.
Playwright's browser sandbox settings are CI settings inside the outer rootless boundary, not an endorsed production browser security policy.

Fault checks cover artifact corruption, worker timeout, context invalidation, concurrent submission, invalid proof rejection through the real EVM, and repeat submission blocking.
Immutable image export, command records, runtime inspection, source hashes, and digest manifests accompany the existing replay evidence.
New code is not empirically accepted until those rootless jobs pass for its exact commit.
P2P partition recovery, 64-node stress, 100 TPS, physical PUF reliability, and mainnet readiness are not measured by this client harness.

The default RPC deadline is 10 seconds and the default submission gas ceiling is 5,000,000.
A verified estimate receives a 20 percent margin and must remain under the policy ceiling, which cannot exceed 30,000,000.
The synthetic Ganache browser fixture allows 60 seconds per RPC because real pairing-based gas estimation measured over 14 seconds in a direct reproduction.
This fixture override does not alter the 4,500-millisecond worker proof benchmark or claim a production RPC latency guarantee.
