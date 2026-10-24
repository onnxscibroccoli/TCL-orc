# 13 — Sovereign Chain Plan (TCL without Ethereum)

Status: **DESIGN-ONLY**. This PR adds no node code. The localnet plan (doc 14), the architect prompt and the
`src/tcl/node/` skeleton are owned by the `docs/localnet` and `docs/architect-prompt` branches. Interfaces are in
`docs/13a_consensus_interfaces.md`. There are no
benchmarks here, and any performance number below is marked as a target, not a measurement.

## 0. Goal and non-goals
- **Goal:** TCL runs as its own chain: a native state machine, its own consensus, p2p networking, storage and
  in-node Groth16 verification. It doesn't use the EVM, Solidity, Ethereum RPC or keys, or any Ethereum network.
- **Non-goals for v1:** smart-contract programmability (modules are native and changed only by
  governance-approved upgrades), bridges to other chains, light clients for browsers.

## 1. Native state machine (replaces contracts)
Every module is a pure, deterministic function `apply(state, tx, block_ctx) -> (state', events) | Reject`.
- **Determinism rules:**
  - Integer math only, using the existing fixed-point demurrage in `tcl.ledger.demurrage_fixed`. No floats.
  - Time comes only from `block_ctx.time`, the proposer timestamp checked by consensus as described in §3.
    Wall-clock time is never read.
  - Map iteration happens in sorted key order.
  - No I/O.
  - Out-of-gas is replaced by a fixed per-tx **work budget** in metered steps (§4).

| Module | Source being ported | Key state | Txs |
|---|---|---|---|
| `circulatory` | `contracts/CirculatoryEngine.sol`, `tcl.ledger.service` | accounts tree, vault (leaf 0), root history | transfer, settle, redistribute, submit_state_proof |
| `notes` | `contracts/physical/NoteController.sol`, `tcl.ledger.notes` | note id → (denom, era, state), nullifier set, incentive pool, hoarding buckets | mint, rearm, deposit(proof), release |
| `governance` | `GovernanceTimelock`, `ConvictionVoting`, `ParameterRegistry`, `UbiGate` | proposals, conviction (7-day default half-life, bounded 3–30 days), parameters, timelock queue | propose, vote(zk), execute, cancel |
| `bounty` | `BountyModule`, `DoubleDepositVerifier`, `InvalidProofVerifier`, `OffenderId` | bonds, reports, challenge windows | report, challenge, withdraw_bond |
| `personhood` | `IPersonhood`, `TieredPersonhoodLimits` | membership root (anonymous), vouch graph commitments, tiers | vouch, enroll(zk), tier_upgrade |
| `guardian` | emergency guardian | expiry ≤ 180 days | pause (pause only, never unpause or move funds) |

- **Invariants carried over as property tests:**
  - Total supply is conserved by every module except governance-approved mint (genesis/UBI) and burn.
  - A nullifier is spent at most once.
  - The guardian can never unpause.
  - Constitutional changes need more than 66% (the threshold from PR #9).
- **Upgrades:** the module code hash is pinned in state, and a constitutional proposal schedules
  `upgrade(code_hash, height)` (a coordinated binary upgrade). Nodes running the wrong hash halt at that height, so
  the failure is fail-closed.

## 2. Accounts and signatures (no secp256k1)
- **Signatures:** **Ed25519** (RFC 8032). It's deterministic, avoids nonce-reuse failures, has no Ethereum
  coupling, and is widely implemented. Address = `Poseidon(pk_hi, pk_lo)` truncated to the field, so circuits can
  reference it.
- **Nonces:** each account has a sequence number for replay protection, and every signed payload carries a
  chain-id domain separator.
- **Anonymous actions:** votes, personhood and note deposits carry Groth16 proofs plus nullifiers instead of
  signatures. Identity is never tied to a key.
- **Implementation:** the stdlib has no Ed25519. Option A is an in-house RFC 8032 implementation tested against the
  RFC test vectors (slow in pure Python, fine for localnet). Option B is a vendored `cryptography` wheel with a
  pinned hash. **Decision needed (D3).**

## 3. Consensus
### Requirements
- One-person-one-vote and resistance to capture by the rich: block production must **not** be weighted by stake.
- Fast deterministic finality, so a note deposit can be settled once and never reorged.
- Tolerates offline regions, and avoids energy-intensive proof-of-work, which the owner dropped.

### Options evaluated
| Option | Safety / liveness | Finality | Capture risk | Verdict |
|---|---|---|---|---|
| Nakamoto PoW | probabilistic; >50% hashpower | probabilistic | hashpower is buyable | rejected (owner dropped PoW; reorgs break note settlement) |
| PoS (stake-weighted BFT) | BFT | deterministic | plutocratic by construction | rejected |
| Tendermint-style BFT (equal-weight validators) | safe if f < n/3 Byzantine under partial synchrony; live after GST | 1 block | depends on validator selection | **recommended core** |
| HotStuff / HotStuff-2 | same fault model; linear-message view change | ~3 phases (2 in HotStuff-2) | same | good alternative for large n |
| DAG BFT (Narwhal/Bullshark) | same fault model; high throughput | deterministic | same | too complex for v1 |
| Federated (Stellar SCP) | safety depends on quorum-slice config | deterministic | social capture via tier-1 orgs | rejected as primary |

### Recommendation
- **Protocol:** Tendermint-style BFT (propose, prevote, precommit, with locking) and **equal weight per validator**.
- **Validator set:** chosen by **sortition from the personhood registry**. Each epoch, a verifiable random draw
  (a VRF over the previous block's commit) seats `n` validators from enrolled persons who opted in.
  - Each person holds at most one seat.
  - Seats are term-limited, a few epochs at most.
  - A small bond is posted in TCL and is slashable for equivocation. It is flat, the same for everyone, and doesn't
    scale influence.
- **Fault model:**
  - **Safety:** holds while fewer than 1/3 of the seated validators are Byzantine (n ≥ 3f+1), under partial
    synchrony.
  - **Liveness:** holds after the global stabilization time (GST).
  - **Sybil assumption:** sortition reduces Sybil risk to the soundness of personhood. If an attacker controls a
    fraction `p` of enrolled persons, the chance of exceeding `n/3` seats is a binomial tail. For example, with
    n=100 and p=0.2 it's about 0.074% per epoch, from `sum_{k>=34} C(100,k) 0.2^k 0.8^(100-k)` (verified with
    Python `math.comb`). It is not 0, and it compounds over epochs.
  - Personhood is therefore the root security assumption. It's **design-only** until a real personhood verifier
    exists, and the mock is not secure.
- **Why not HotStuff now:** Tendermint's message complexity (O(n²)) is fine for n ≈ 100 and it is simpler to
  implement and audit. The consensus interface is kept pluggable so HotStuff-2 can replace it if n grows.
- **Bootstrap:** until personhood is secure, testnets use a fixed dev validator set. This is explicitly
  **NOT capture-resistant**.

## 4. Networking, mempool, blocks, finality
- **P2P:**
  - In-house, over TCP with a Noise-XX-style handshake using X25519 and ChaCha20-Poly1305, or a reviewed
    vendored implementation (D4).
  - Length-prefixed canonical messages.
  - Gossip for txs and votes, request/response for blocks and state sync.
  - Peer scoring, and static seeds per network.
- **Mempool:**
  - Tx validation is fully stateless: signatures, proof format, size.
  - The nonce check runs against the tip.
  - Ordering is FIFO per sender plus round-robin across senders. There's no fee-priority ordering because there
    are no protocol fees.
  - Spam control is a per-account rate limit tied to personhood tier, plus a small proof-of-work stamp on each tx
    (anti-spam only, not consensus). The rate is set per account rather than by fee bidding.
- **Encoding:** canonical deterministic encoding (sorted-key CBOR subset or a hand-rolled TLV; D5). The PoC
  uses sorted-key JSON plus SHA-256 for simplicity.
- **Block:**
  - Header: `{chain_id, height, time, prev_hash, tx_root, state_root (Poseidon), validators_hash, proposer}`.
  - Body: txs.
  - Commit: more than 2/3 of precommit signatures over the header hash.
- **Finality:** a block is final when its commit exists. There are no reorgs. A halt (loss of liveness) is
  preferred over a fork (fail-closed).
- **Time:** the proposer's time must be greater than the parent's time and within a skew bound of the validators'
  local clocks. Demurrage uses block time.

## 5. Storage
- **Reused:**
  - `tcl.storage.db` (SQLite WAL).
  - `PoseidonMerkleTree` with root history and rollback, and the vault at leaf 0.
  - The state root in the header is the Poseidon root, so the existing circuits keep proving against the same
    tree.
- **Additions:**
  - Block store and per-height state journal.
  - **Snapshots** every K blocks: account rows, note table and nullifiers, plus a manifest hash.
  - **State sync:** download a snapshot, then verify both its Merkle root against a committed header and the
    manifest hash, then replay the later blocks.
  - Pruning of old journals beyond the snapshot horizon.
- **Note:** the nullifier set and note table need their own committed roots (sparse Merkle trees) for light proofs.
  That's design-only.

## 6. Native Groth16 verification
- The verifier runs in-node. Verifying a proof is constant-size: 3 pairings, which can be batched as a
  multi-pairing.
- Verifying keys live in state under a `vk_id`, and only governance can change them.
- **Curve:**

| | BN254 (current) | BLS12-381 |
|---|---|---|
| Compatibility | existing circuits, Poseidon constants and proving keys all use the BN254 scalar field | requires new Poseidon parameters for its field, a new tree hash (state root changes), and re-running the trusted setup |
| Security estimate | lowered by exTNFS attacks, commonly estimated at ~100 bits | commonly estimated at ~117–120 bits |
| Tooling | circom/snarkjs native | supported by circom (`-p bls12381`) and snarkjs |

- **Recommendation:**
  - Stay on **BN254 for localnet and testnet**: it keeps all existing tests and vectors.
  - Decide before mainnet whether to migrate to BLS12-381 (D1).
  - Either way, a **public multi-party trusted setup** is required before any value-bearing network. The current
    keys are dev-only and forgeable by whoever ran the setup.
- **Implementation:** pairing arithmetic in-house in pure Python is feasible but slow (correctness first, tested
  against snarkjs-generated vectors). A native-code version comes later. Implementation is **design-only**.

## 7. Keep / port / drop
| Item | Fate |
|---|---|
| `src/tcl/ledger`, `storage`, `crypto/poseidon.py`, demurrage, notes, nullifiers | **keep** (core of the state machine) |
| `src/tcl/orchestrator`, `worktree`, daemon, CLI | **keep**. The daemon becomes the node host, and the CLI gains `tcl node`/`tcl localnet` |
| `circuits/**` (Circom) | **keep** as the circuit source. Proving is client-side; verification is native |
| `contracts/**/*.sol`, `contracts/verifiers` | **port** logic to native modules, then **drop** from the runtime. Keep as a historical reference until parity tests pass |
| `src/tcl/chain/client.py` (EVM client), `foundry.toml`, `lib/forge-std`, `lib/openzeppelin-*`, `script/` forge scripts, slither config, solhint | **drop** after porting |
| Groth16 verifier generated by snarkjs for Solidity | **drop**; replaced by the native verifier |

## 8. Migration phases and acceptance tests
| Phase | Deliverable | Acceptance |
|---|---|---|
| P0 | PoC single-node producer (`src/tcl/node`) | deterministic replay, same block hashes; test passes with sockets blocked (owned by `docs/localnet`) |
| P1 | native `circulatory` + `notes` modules, Ed25519 txs | parity tests: same inputs produce the same balances and roots as the Solidity Foundry tests (vectors exported once); supply invariant fuzz |
| P2 | native Groth16 verifier | accepts all snarkjs valid vectors, rejects mutated proofs and public inputs |
| P3 | governance, bounty, personhood, guardian modules | port of the 89 governance tests; guardian-cannot-unpause; 66% constitutional threshold |
| P4 | BFT consensus + p2p, `tcl localnet` with N validators | safety under f < n/3 equivocating nodes in fault-injection; liveness after partitions heal; no fork across 10⁴ randomized runs (target) |
| P5 | snapshots/state sync, upgrade mechanism | a new node syncs from a snapshot and matches the root; a wrong-binary node halts at the upgrade height |
| P6 | delete EVM code paths | `rg -i 'solidity|evm|foundry|anvil|ethers|web3'` in runtime code returns nothing; offline CI is green |
| P7 | public MPC ceremony, external audit, public testnet | audit report; ceremony transcript published |

## 9. Risks
- **Personhood soundness:** this is the root of both consensus and voting. Personhood is a mock today.
- **Writing consensus and crypto in-house:** there's a high risk of subtle bugs. Mitigations: specs first,
  test-vector-driven development, model checking of consensus (e.g. a TLA+ spec), and an external audit.
- **Performance of pure-Python pairing and Ed25519:** fine for localnet, possibly not for a public network.
- **BN254 security margin:** see §6.
- **Liveness under global partitions:** BFT halts by design and won't fork. Offline note circulation keeps working.
- **Legal:** the owner treats the project as sovereign. This is noted, and no research was done per owner
  instruction.
