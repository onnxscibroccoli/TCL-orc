# 12 — Physical notes MVP (implementation of docs/11 §4, §6, §7)

> **In plain words.** This page says which parts of the physical-cash design (doc 11) are actually built. An *MVP* (minimum viable product) is a first, bare-bones version. You can mint notes worth 5, 10, 20, 50, 100, 200 or 500 TCL by paying digital TCL. Each note can be deposited back, then *re-armed* with a fresh one-time code and used again. Big deposits are unlocked gradually. **Important limits:** there is no real chip (it is simulated), nothing checks that a physical note really exists, limits are per address rather than per person, and the proof setup is development-only. Terms are in the [Glossary](GLOSSARY.md).
>
> **Example (real limits from the code).** Minting a 500 TCL note: you pay 500 TCL. The cost refund can be at most 20% (100 TCL) and the reward at most 1% (5 TCL), so the most you can get back is 105 TCL. If this epoch's budget or the pool is used up, you get 0 back: a straight 500-for-500 swap. The total amount of TCL never changes.

Status: **MVP, dev-only trusted setup, not audited, not production.**

## What is implemented

| Spec | Where | Notes |
| --- | --- | --- |
| §4.2 permanent id | `circuits/note/note_deposit.circom`, `src/tcl/zk/note.py` | `id = H(10, H(secret), pufHash, H(d, serial))` — `(d, serial)` packed into one hash because the reference Poseidon is limited to 4 inputs |
| §4.4 nullifier | same | `nf = H(12, id, k, ctx)`, `ctx = chainid << 160 \| controller` |
| §4.4/4.5 LIVE/DORMANT, re-arm at k+1 | `contracts/physical/NoteController.sol` | deposit at era k → DORMANT (era stays k, nf_k spent); `rearm` → era k+1 LIVE |
| §7.1–7.4 burn-to-mint | `NoteController.mint/rearm/quote` | $`C = \min(\mathrm{attested},\ \mathrm{costCap}_d)`$, $`\mathrm{costCap}_d \le 0.2\,V`$ (hard bound), $`R = \rho V`$, $`\rho \le 0.01`$; per-epoch budget; over budget or empty pool $`\Rightarrow C = R = 0`$ (pure 1:1) |
| denominations | `NoteController` | whitelist {5,10,20,50,100,200,500}, hard `MAX_DENOM = 500` |
| §6.1–6.2 re-entry unlock | `NoteController._reentry/release` | $`U = \Theta + E \cdot \min\!\left(1,\ \Delta t / W_r\right)`$ over rolling window $`W_a`$ |
| §6.4 accelerated idle decay | `src/tcl/ledger/notes.py` (ledger side only) | velocity-weighted $`\lambda_h`$, $`\kappa \in [0, 20]`$, 1-day steps |

Conservation (tested by fuzz + invariant): $`\mathrm{balance}(\mathrm{controller}) = P + \mathrm{pool} + \mathrm{lockedTotal}`$, total supply unchanged (no issuance).

## What is simulated / not real

- **No hardware.** The secure element is simulated by a field-element `noteSecret` known to the prover; `pufHash` is an opaque number. There is no SE signature, no PUF read, no offline challenge-response (§4.3), and no SE-side DORMANT latch. Anyone who knows the secret can deposit.
- **Mint does not attest hardware.** `mint(id, …)` accepts any id; nothing proves a physical note exists. `attestedCost` is trusted up to the cap; the median/market discovery of $`\tilde{c}_d`$ (§7.2) is not implemented.
- **No Merkle membership.** LIVE/DORMANT state is an on-chain mapping instead of a leaf in the Poseidon tree.
- **Per-address aggregation** stands in for per-person pseudonyms $`\nu`$ (§6.5); Sybil splitting across addresses is NOT prevented until the personhood layer exists. No personhood floor $`\theta_{\min}`$.
- **Dev trusted setup.** `contracts/verifiers/NoteDepositVerifier.sol` and fixtures come from a single-party dev zkey (`scripts/gen_note_fixtures.py`). Production needs a ceremony.
- Admin roles (PARAMS/PAUSER/UPGRADER) are held by the deployer; handover to governance/timelock is out of scope here.

## Damaged notes: off-chain only

**Damaged or worn notes are handled off-chain only** (owner decision L13, 2026-10-23). They are retired directly into off-chain transactions, with no on-chain damaged-note retirement, replacement or refund path. The production-cost credit C and mint reward R in §7 are unchanged. `NoteController` has no retirement or replacement function, by design.

## Fail-closed behaviour

Verifier revert/false ⇒ `InvalidProof`; zero addresses rejected at init; implementation initializers disabled; bounds enforced on every parameter; Python helpers raise on out-of-range inputs and `prove_deposit` self-verifies.
