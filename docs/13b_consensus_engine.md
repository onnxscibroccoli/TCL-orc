# 13b — Consensus engine (code) and swap-in contract

Implements docs/13 §3 and 13a "Validator set rules" in `src/tcl/consensus/` (stdlib only).

| File | What |
|---|---|
| `ed25519.py` | Pure-Python RFC 8032 Ed25519 (tested against vector 1). **Not constant-time — dev/test only** (D2). |
| `encoding.py` | 13a canonical JSON, `H` = SHA-256, domain-separated signed payloads, tx Merkle root. |
| `types.py` | Tx/Header/Block/Commit per 13a, `Vote`, `Proposal`, `CommitCert`, `quorum(n)` (> 2/3 of the count), `verify_commit`. |
| `engine.py` | `TendermintEngine`: propose/prevote/precommit, locking (`locked_round/value`), `valid_round/value` re-proposal, round timeouts growing by `delta` per round, f+1 round skip, commit-certificate catch-up. Equal weight. |
| `validators.py` | `ValidatorRegistry`: tier ≥ 2 gate, flat bond, one seat per nullifier, seeded draw without replacement, term + cooldown, double-sign → bond to `slashed_pool` + permanent nullifier ban. |
| `evidence.py` | `DuplicateVoteEvidence` (same validator/kind/height/round, different hash, both sigs valid). |
| `personhood.py` | `PersonhoodProvider` protocol + **`MockPersonhoodProvider` (MOCK: verifies no ZK proof)**. |
| `sim.py` | Deterministic network simulator: seeded delays, held-not-lost partitions (heal = GST), equivocating Byzantine nodes. |

## Swap-in interface (for `src/tcl/localnet`)
```python
class Application(Protocol):           # the host/app side
    chain_id: str
    def validators(self, height) -> list[str]          # sorted Ed25519 pubkeys, weight 1 each
    def build_block(self, height, round, proposer) -> dict   # 13a Block
    def validate_block(self, block, height) -> bool
    def on_decide(self, block, commit) -> None         # apply + persist

class ConsensusEngine(Protocol):       # what localnet calls
    height: int
    def start(self, height) -> list[Action]
    def on_message(self, msg) -> list[Action]          # Proposal | Vote | CommitCert
    def on_timeout(self, height, round, step) -> list[Action]
# Action = Broadcast(msg) | ScheduleTimeout(height, round, step, delay_ticks) | Decide(height, block, commit)
```
The engine is pure: no I/O, no clock, no threads. The host delivers messages (any order, duplicates OK),
fires timeouts after `delay` ticks of its own clock (localnet can warp it), and broadcasts. Swap the localnet
round-robin engine by constructing `TendermintEngine(app, secret)` per validator and routing actions;
`tcl.consensus.sim.Network` is a complete reference host.

## What the tests prove (`tests/test_consensus.py`)
- No fork among honest nodes with < n/3 equivocators (n=4,f=1 over 6 seeds; n=7,f=2) and liveness continues.
- Partition with no side holding > 2/3: halts, no fork; resumes after heal. 5/2 split: majority continues, minority catches up.
- Double-sign evidence → slash + permanent ban (fresh key can't re-enter); bad/replayed evidence rejected.
- One seat per person even with 50 keys; flat bond; tier gate; selection deterministic and seed-sensitive; term/cooldown; draw uniformity.
- Property tests: random n ∈ {4,5,7}, random f ≤ ⌊(n−1)/3⌋, random delays and partitions → never fork.

## Gaps
- Personhood is a mock; sortition `rand` is caller-supplied (no VRF/commit-reveal yet; doc 13 calls for H(last_commit)).
- Registry is not yet a native `validators` module wired to `end_block`; validator-set change mid-run is not exercised in the simulator.
- No state machine / module execution (`state_root`/`aux_roots` are placeholders in the sim app); no tx mempool.
- Evidence gossip is not automatic: honest nodes record equivocation they observe; cross-node assembly is test-side.
- No WAL/crash recovery of engine state, no real transport, Ed25519 is not constant-time, simulations are small (n ≤ 7).
