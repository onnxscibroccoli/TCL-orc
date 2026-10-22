# Executable Protocol Specification

## Scope and trust boundary

The implemented protocol is a fixed-supply transparent ledger with demurrage, a redistribution vault, backed note escrow, and Groth16 proofs of secret knowledge.
It is a research implementation rather than a claim of production currency readiness.
Physical enrollment, fuzzy extraction, shielded payments, productive-work attestations, and decentralized governance are separate unimplemented protocols.
No private key, production ceremony parameter, live deployment, or personal device state is required for the tests.

## Conservation and authority

The constructor issues a fixed supply $S$ to the initial holder and permits no subsequent issuance or burning.
Supply is bounded by $0 < S < 2^{128}$ base units to bound intermediate arithmetic.
For ledger balances $B_a$ and the vault $V$, every successful transition preserves

$$
S = V + \sum_a B_a.
$$

Controller escrow is already included in the balance sum and must not be added a second time.
The sum of locked note values $L$ satisfies $L \leq B_{\mathrm{controller}}$.
Unsolicited transfers may make the inequality strict and cannot be reclaimed through an undocumented sweep path.
The governor chooses the exempt controller once and authorizes redistribution from the vault.
Governor replacement requires proposal by the current governor and acceptance by the proposed governor.
These authorities are explicit trust assumptions and should use reviewed governance custody before deployment.

## Chain-time demurrage

The immunity threshold remains $T = 5{,}000{,}000$ AETH and the nominal decay rate remains $\delta = 0.0005$ per day.
Balances settle before outgoing transfers, incoming transfers, and redistribution credits.
Any caller may request settlement, but elapsed time always comes from the chain timestamp and the stored account checkpoint.
The implementation replaces the skeleton's interval-linear approximation with per-second compounded decay so that settlement timing cannot materially change the economic result.
For $Q = 10^{27}$, define

$$
f = \left\lfloor Q \exp\left(-\frac{\delta}{86400}\right) \right\rfloor
  = 999999994212962979707861764.
$$

The integer factor for $s$ elapsed seconds is calculated by fixed-point exponentiation by squaring.
Ignoring rounding dust, the surplus evolves as

$$
B_a' = \min(B_a,T) + \max(B_a-T,0)\left(\frac{f}{Q}\right)^s.
$$

The fee is the balance reduction and is credited to the vault rather than burned.
Fractional fee remainders are retained across above-threshold settlements so repeated short intervals cannot simply erase fractional charges.
Remainders reset when a balance is at or below the threshold or when its account is the exempt controller.
The configured controller balance incurs no demurrage while backing offline notes.
This exemption makes physical conversion a demurrage escape route by design and requires economic review before any deployment.
The threshold applies per address rather than per human because no identity registry is implemented.
Stored balances expose the most recently settled amount rather than a continuously updated wallet display.
The test suite compares daily settlement against exponential decay and hourly settlement within 100 base units for the tested initial balance.

## Escrow state transitions

A lock requires a fresh nonzero field commitment, a positive 128-bit value, sufficient approved balance after settlement, and the configured controller.
The ledger transfers that value into controller escrow atomically with creation of a permanent note record.
A redeemed commitment remains recorded forever and cannot be locked again.
A redemption requires an unspent locked note, a fresh nonzero field nullifier, an external nonzero recipient, and an accepted proof.
The controller verifies before marking the commitment spent and debiting its liabilities.
It transfers the recorded value to the recipient in the same transaction.
A rejected proof or transfer failure rolls the entire transaction back.
Reentrancy protection surrounds both lock and redemption.
Relayers may submit proofs because the recipient is bound by the proof rather than selected from the submitting address.
There is no privileged withdrawal, verifier replacement, or mint-controller replacement path.
An unavailable or faulty verifier can therefore strand escrow and must be reviewed before funds are deposited.

## Proof statement and cross-contract binding

The proof uses the BN254 scalar field and Poseidon from the pinned circomlib dependency.
The private witness contains a reconstructed note secret $k$ and a blinding scalar $r$.
Both witness scalars must be sampled securely for real use; the tests deliberately use public fixtures.
The domain context is

$$
d = \operatorname{uint256}(\operatorname{keccak256}(\operatorname{abi.encode}(\mathrm{chainId},\mathrm{controller}))) \bmod p.
$$

A commitment and recipient-bound nullifier satisfy

$$
C = \operatorname{Poseidon}(1,k,r,v,d),
\qquad
N = \operatorname{Poseidon}(2,k,C,d,a).
$$

The value $v$ is constrained to 128 bits and must be nonzero.
The recipient $a$ is constrained to 160 bits and must be nonzero.
The secret must be nonzero.
The public-signal order is exactly `[commitment, nullifier, value, recipient, context]`.
The circuit has no public output signals, avoiding an implicit output before those inputs.
The controller supplies the stored note value, transaction recipient, and immutable context rather than accepting an arbitrary public-input array.
Because the nullifier is recipient-bound, a different recipient produces a different nullifier; the permanent spent commitment is the primary note-wide replay barrier.
The generated verifier and controller share a five-signal Groth16 interface.
Proof bytes encode the two-element A point, two-by-two B point in snarkjs Solidity order, and two-element C point.
Proofs from another controller or chain context are invalid for the current escrow.
A chain fork retaining the same chain identifier and controller state is outside this replay separation guarantee.

## Privacy and physical authenticity

Poseidon commitments replace the earlier Pedersen sketch for the executable implementation.
The system reveals depositor, recipient, denomination, commitment, and redemption linkage on-chain.
It proves knowledge of the committed secret without exposing that secret in the public statement.
It does not implement shielded balances or anonymous mint and redemption.
A copied or stolen reconstructed secret enables a competing valid redemption.
The first successful redemption wins, and offline possession alone cannot detect that race.
No circuit can establish a physical substrate's unclonability merely by hashing a witness supplied by software.
An actual physical-cash deployment needs enrollment rules, measured PUF entropy and stability, a fuzzy extractor, issuer attestation, and a threat model for malicious readers.
The zero-demurrage physical policy and the per-person immunity objective also need an explicit Sybil-resistance design.

## Verification and artifacts

`npm run check` builds fresh local-only ceremony parameters and runs the complete test suite.
The setup verifies the contributed proving key against the R1CS and phase-two parameters before exporting the verifier.
A manifest hashes the circuit, dependency lockfile, R1CS, WASM, proving key, verification key, and Solidity verifier.
The real-proof test deploys that generated verifier to a local EVM and redeems a backed note.
It rejects changed witness secrets, blinding, values, recipients, context, commitments, nullifiers, public signals, and corrupted proof coordinates.
Contract tests additionally cover authority checks, conservation, failed-proof rollback, duplicate commitments, spent-note replay, demurrage exemption, and time-derived settlement.
Non-Git workspaces cannot reuse prior task state because their source identity is unavailable.
Runtime tests cover task dependencies, durable deduplication, definition changes, source changes, bounded retries, timeouts, path escape, and preservation of dirty worktrees.
CI must fail when a required verification, document rendering, or artifact upload fails.
Generated private test inputs are excluded from uploaded artifacts.
Production requires a separately reviewed multi-party ceremony and independently audited protocol, contracts, circuits, governance, and physical enrollment.
