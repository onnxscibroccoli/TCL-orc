# Implementation readiness roadmap

Reviewed 2026-10-22 against the checked-out implementation after `e986683`.
This is an evidence-driven plan, not a production approval. Preserve the frozen
`tcl-puf-groth16-v1` circuit and existing conservation/demurrage ledger. New
statements or privacy protocols require separate versions, threat models, audits,
ceremonies and migration decisions; they must not silently replace this baseline.

## Verified baseline and remaining scope

| Area | Verified repository baseline | Requested direction and release blocker |
| --- | --- | --- |
| Proof stack | Circom 2 statement; `production/ceremony.py` pins `snarkjs` 0.7.6 and `circom2` npm package 0.2.22. The circuit declares Circom 2.2.0. | No Go module or gnark implementation exists here. A gnark v0.14 initialization proposal is a separate port, not an existing integration or prerequisite to releasing this statement. |
| Statement | `circuits/puf_verify.circom` proves secret/blinding knowledge for `Poseidon(1,secret,blinding,value,context)`. Five public signals are commitment, nullifier, value, recipient, context; value is nonzero and 128 bits; recipient is nonzero and 160 bits. | It proves neither physical unclonability, entropy quality, KYC attributes nor shielded ownership. Preserve exact public-input order and deployment context. |
| Replay | Recipient-bound `Poseidon(2,secret,commitment,context,recipient)` plus permanent commitment consumption in the controller. | Do not claim nullifier bijectivity or a globally unique nullifier for each note across recipients. Commitment consumption is the primary replay barrier. |
| Accounting assurance | `verification/ledger_model.py` contains 13 one-step abstract integer obligations covering settlement, transfer, redistribution, escrow and replay. | These are not R1CS completeness, cryptographic collision proofs, full protocol verification, or deployed bytecode verification. Independent source-to-model refinement remains mandatory. |
| Enrollment | Production controller verifies EIP-712 enrollment signatures, including ERC-1271 authority support, outside the Groth16 circuit. | An authority signature authenticates an enrollment assertion; it is not a KYC circuit or proof that a physical secret cannot be copied. |
| Privacy | Depositor, amount, recipient, commitment and redemption linkage remain public. | No shielded transfer engine, association-set proof, outgoing viewing key or inter-provider Travel Rule messaging is implemented. |
| Physical system | Secret reconstruction is an assumed external enrollment boundary. | No implemented BCH/Polar fuzzy extractor, qualified reader/substrate, measured BER distribution or independent hardware qualification is established. |
| Release machinery | Ceremony verification, reproducible artifacts, signed approvals, independent archive/replica and an offline evidence gate exist. | Example policies and test signatures are not real ceremony participation, custody acceptance, independent audits or legal clearance. See `10_release_gates.md`. |

## Cryptographic corrections and acceptance rules

[Groth's original paper](https://eprint.iacr.org/2016/260) describes a proof with
two G1 points and one G2 point. A hypothetical compressed BN254 representation
can occupy 32 + 64 + 32 = 128 bytes, subject to a specified valid codec. The
current Solidity proof tuple uses eight 32-byte coordinate words, or 256 bytes
for the proof alone, excluding selector and public inputs. Neither number is a
benchmark. No reproducible 1.2 ms verifier result is established for this repository.
Benchmark the actual pinned prover/verifier, hardware, build settings, input count,
serialization, EVM gas and latency distribution before publishing performance.

Pairing equations multiply elements of the target group: for example
`e(A,B) = e(alpha,beta) * e(vk_x,gamma) * e(C,delta)`, or the corresponding
product-to-one check. A sum of target-group pairings is not the verification
relation. [EIP-197](https://eips.ethereum.org/EIPS/eip-197) specifies group
membership, coordinate encoding and product checks; it also distinguishes the
base-field modulus from the scalar-field order.

Do not invent a general requirement that setup scalars or parameters be mutually
distinct or different from one. In particular, gamma equal to one does not alone
establish an invalid Groth16 parameterization. Validate the exact supported
ceremony construction and verifier, rather than replacing it with an ad hoc
parameter-separation check. The [gnark API documentation](https://pkg.go.dev/github.com/consensys/gnark@v0.14.0/backend/groth16)
illustrates a different implementation stack; its existence does not establish
interoperability, an authenticated multi-party ceremony or a port in this tree.

## Ordered milestones and required evidence

| Milestone | Work and dependency | Completion evidence |
| --- | --- | --- |
| 1. Freeze and audit | Record source, lockfile, compiler/toolchain, circuit statement and artifact digests. Review Solidity-to-model correspondence and circuit constraints independently. | Reproducible R1CS/WASM/verifier artifacts; signed security and refinement reviews; explicit unresolved findings. Keep the current ledger and statement unchanged. |
| 2. Ceremony policy | Provision independent identities and release trust roots. Adopt the requested minimum of **10 private contributors per phase** as a release policy, with authenticated receipts and final beacon after private contributions. | Actual accepted phase transcripts, verified extensions, reproducible final artifacts, release signatures, independent archive and replica. `production/ceremony_profile.py` now enforces ten distinct private contributor keys per phase, frozen core digests and signed dual-hash receipts on top of native transcript verification. The generic verifier retains a research minimum of two; it is not the mainnet profile. Independence and secret erasure need operational evidence. |
| 3. Future beacon binding | Commit a drand network/chain hash, public key, scheme, exact future round, retrieval method and timing before output availability. Set the requested **at least 120-second future margin** as an operational policy. | Timestamped commitment, round/time calculation, cryptographically verified signature/randomness, fail-closed wrong-round/chain and unavailable-beacon tests. `production/drand.py` now verifies quicknet BLS signatures and rejects a round at or within 120 seconds after completion. The strict profile binds precommitted rounds and exact final private outputs through two separate signed timing witnesses. Clock truth and publication timing require independent operational evidence. |
| 4. Physical qualification | Select substrate and reader, define adversaries and environmental envelope, then measure independent devices and repeated reads. Design reconstruction using measured entropy and helper-data leakage. | `production/hardware.py` now checks signed artifact-bound qualification evidence and per-stratum one-sided binomial confidence bounds; it does not supply an extractor or measurements. Raw datasets and reproducible analysis of BER, inter/intra-device distances, conditional min-entropy, reconstruction failure/false acceptance, aging, temperature, humidity, wear and cloning/modeling attacks; independently accepted extractor/reader assessment. Numerical BER targets are release choices justified by these data, not universal security theorems. |
| 5. Custody and runtime | Establish governance/guardian/issuer custody, recovery, finality/reorg policy and deployment-class rootless runtime. | Accepted custody records, runtime isolation evidence and incident/recovery/reorg drill. Exact chain, addresses, bytecode and manifest pins bound by release approvals. |
| 6. Privacy design decision | Specify public metadata and desired shielded behavior separately. Evaluate association sets, note encryption and disclosure/OVK design only with a complete threat model. | Versioned design, explicit privacy limitations, new circuit/contract reviews and any required new ceremony; no claim that this baseline already supports shielded payments. |
| 7. Jurisdiction acceptance | Identify operators, activities, locations, custody and data flows. Determine applicable authorization, AML/CTF, sanctions, customer diligence, reporting and information-exchange duties. | Dated release-specific counsel assessment and tested operational controls, including counterparty messaging where required. Signed enrollment or viewing access alone is insufficient. |
| 8. Promotion | Assemble the complete evidence bundle required by `production/release_gate.py` and externally controlled policy. | All mandatory evidence bytes and pins, independent attestations and fresh release approvals accepted by the offline gate; separately verify deployed immutable values/runtime and deployment transaction. Gate success is not itself legal or physical certification. |

For milestone 3, drand defines rounds using network genesis time and period and
requires verification under the selected scheme. See the [protocol specification](https://docs.drand.love/docs/specification/)
and [deployment trust guidance](https://docs.drand.love/operator/deploy/).
The ten-contributor threshold and 120-second margin are requested operational
policies, not guarantees that all participants are honest or a cryptographic
minimum inherent to Groth16/drand. A public beacon does not replace private
contributions or erase retained setup secrets.

No particular substrate-paper latency, entropy, BER, temperature range or clone
resistance number is accepted here without the original paper, exact measurement
conditions and independent applicability assessment. Such hardware figures remain
unverified; do not reuse them as measured properties of this implementation.

## Privacy and regulatory boundaries

The [Zcash protocol specification](https://zips.z.cash/protocol/protocol.pdf)
describes viewing and note-encryption capabilities. An outgoing viewing key can
support disclosure of transaction information within its protocol scope; it does
not itself identify a legal customer, discover/verify a counterparty provider,
securely transmit required originator/beneficiary information or enforce retention.
The [Privacy Pools paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4563364)
proposes association-set proofs. A cryptographic association claim is not a
regulator's approval or proof of lawful provenance. These are design references,
not implemented features or legal safe harbors.

Regulatory assessment must distinguish the following primary sources (reviewed
2026-10-21; applicability and any later changes must be rechecked at release):

- [FATF VA/VASP guidance](https://www.fatf-gafi.org/en/publications/Fatfrecommendations/Guidance-rba-virtual-assets-2021.html) supplies risk-based international standards; domestic implementation and actual activities determine obligations.
- [EU Regulation 2023/1113](https://eur-lex.europa.eu/eli/reg/2023/1113/oj/eng), particularly Articles 14–17, addresses transfer information and related procedures. It is separate from [MiCA, Regulation 2023/1114](https://eur-lex.europa.eu/eli/reg/2023/1114/oj/eng). A generic claim of “MiCA Travel Rule compliance” conflates instruments.
- [FinCEN FIN-2019-G001](https://www.fincen.gov/system/files/2019-05/FinCEN%20CVC%20Guidance%20FINAL.pdf) evaluates convertible-virtual-currency business models under the BSA. Protocol labels and decentralization claims do not replace analysis of who accepts/transmits value and the relevant exceptions.
- [AUSTRAC's 2026 transitional guidance](https://www.austrac.gov.au/about-us/legislation/updates-legislation/amlctf-transitional-rules-2026) describes deferrals for new virtual asset services until 1 July 2026. Do not describe that date as still in the future or assume every operator has identical obligations; review current service-specific rules and transitions.
- [GDPR, Regulation 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng) requires assessment of personal data, lawful basis, minimization, rights, security, roles and transfers. Keeping identity data off chain does not automatically satisfy these duties; addresses, linkage and commitments require contextual assessment too.

Until the independent evidence and approvals above exist, the accurate status is
an executable, publicly linked physical-note escrow prototype with production
release controls under development, not a qualified physical currency, deployed
shielded system, fully verified implementation or legally cleared financial service.
