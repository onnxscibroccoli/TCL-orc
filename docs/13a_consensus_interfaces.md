# 13a — Consensus Interface Contract (v0, DESIGN-ONLY)

This file pins the interfaces that the localnet, architect-prompt and node work build against. Changing anything
here requires a PR that touches this file. All integers are unsigned and big-endian in hashing; amounts are integer
base units.

## Encoding and hashing
- **Canonical encoding (v0):** JSON with sorted keys, no whitespace (`separators=(",",":")`), UTF-8. Ints are
  encoded as JSON numbers, field elements as decimal strings. A TLV/CBOR encoding may replace this in v1 (D5).
- **Hashes:**
  - `H(x)` = SHA-256 over the canonical encoding. It's used for block, tx and message ids.
  - State commitments use Poseidon (BN254, `tcl.crypto.poseidon`).
- **Domain separation:** every signed payload is wrapped as `{"chain_id": str, "domain": str, "body": ...}`.
  The domain is one of `tx`, `prevote`, `precommit` or `proposal`.

## Transaction
```
Tx = { "chain_id": str, "module": str, "method": str, "sender": str|null,
       "nonce": int, "args": object, "proof": object|null, "sig": hex|null,
       "pow_stamp": int }
```
- `sender` and `sig` are Ed25519. `sender` = hex of the Poseidon address (doc 13 §2).
- Anonymous txs (vote, enroll, note deposit) set `sender=null` and `sig=null`. They must carry `proof` (Groth16)
  plus a `nullifier` in `args`.
- No fee field (owner decision: no protocol transaction fees).
- `tx_id = H(Tx without sig)`.

## Block
```
Header = { "chain_id", "height": int, "time": int (unix s), "prev_hash": hex,
           "tx_root": hex,            # binary SHA-256 Merkle over tx_ids, in block order
           "state_root": dec,         # Poseidon accounts-tree root after applying txs
           "aux_roots": {"notes","nullifiers","personhood","governance"},  # dec Poseidon roots
           "validators_hash": hex, "next_validators_hash": hex,
           "proposer": hex, "app_version": str, "module_code_hash": hex }
Block  = { "header": Header, "txs": [Tx], "last_commit": Commit }
Commit = { "height", "round", "block_hash", "sigs": [{"validator": hex, "sig": hex}] }
```
- `block_hash = H(header)`.
- A commit is valid iff it holds more than 2/3 of the validator count, with **equal weight** per validator.
- Genesis is `height=0` with `prev_hash = "00"*32`.
- Time rules:
  - `time > parent.time`.
  - `time` is within ±`MAX_SKEW` (default 30 s) of the validators' local clocks.
  - Localnet may override time via `tcl_warp` (dev only).

## Module API (native state machine)
```python
class Module(Protocol):
    name: str
    def validate_stateless(self, tx: Tx) -> None: ...          # raise Reject
    def apply(self, state: StateView, tx: Tx, ctx: BlockCtx) -> list[Event]: ...  # raise Reject => tx has no effect
    def begin_block(self, state, ctx) -> list[Event]: ...      # e.g. guardian expiry, conviction accrual
    def end_block(self, state, ctx) -> list[Event]: ...        # e.g. validator-set update (personhood module only)
BlockCtx = {height, time, proposer, chain_id, rand: int}       # rand = H(last_commit) for sortition
```
- **Determinism:** no floats, no wall clock, no I/O, sorted iteration, fixed work budget per tx.
- **Atomicity:** a rejected tx reverts fully and is recorded with its reject code. Blocks are never partially
  applied.
- **v0 modules:** `circulatory`, `notes`, `governance`, `bounty`, `personhood`, `guardian`, `validators`.

## Validator set rules
- **Eligible:** a person enrolled in `personhood` at tier ≥ 2 who has opted in and posted the flat bond
  `VALIDATOR_BOND` (the same for everyone).
- **Seat cap:** one seat per personhood nullifier. Keys don't count, so a person can't hold extra seats through
  extra keys.
- **Selection:** every `EPOCH_BLOCKS`, `end_block` seats `N_VALIDATORS` (default 100) eligible persons, sampled
  without replacement by `rand` (a VRF/commit hash).
  - Term is `TERM_EPOCHS` (default 4).
  - Re-selection is barred for `COOLDOWN_EPOCHS`.
- **Weight:** every validator weighs 1. Stake or balance never changes weight.
- **Slashing:** a double-sign forfeits the bond plus a permanent seat ban for that personhood nullifier. Evidence is
  submitted via `bounty.report`.
- **Fault model:** safe while Byzantine seats are < N/3, live after GST.
- **Dev/localnet:** a fixed set of deterministic dev keys. This is **not capture-resistant**.

## RPC (own spec; JSON-RPC 2.0 over HTTP and Unix socket)
Method names are prefixed with `tcl_`:
- **Chain:** `tcl_chainId`, `tcl_status`, `tcl_getBlock(height|hash)`, `tcl_getTx(tx_id)`
- **Txs:** `tcl_sendTx(Tx)` returns `tx_id`
- **State:** `tcl_getAccount(addr)`, `tcl_getProof(addr)` (Poseidon Merkle proof), `tcl_getNote(id)`,
  `tcl_isNullifierSpent(n)`, `tcl_getValidators(height)`, `tcl_getParams`, `tcl_getProposal(id)`
- **Verifier:** `tcl_verifyProof(vk_id, proof, public)`, which is local only and doesn't change state
- **Dev only:** these are enabled only with `--dev`, and refused otherwise (fail-closed): `tcl_mine(n)`,
  `tcl_warp(seconds)`, `tcl_snapshot`, `tcl_revert(id)`, `tcl_faucet(addr, amount)`,
  `tcl_injectFault(spec)`

## Reject codes (stable)
`BAD_SIG`, `BAD_NONCE`, `BAD_PROOF`, `NULLIFIER_SPENT`, `INSUFFICIENT_BALANCE`, `TIER_LIMIT`, `PAUSED`,
`UNKNOWN_MODULE`, `UNKNOWN_METHOD`, `WORK_BUDGET`, `UNAUTHORIZED`, `BAD_ARGS`.
