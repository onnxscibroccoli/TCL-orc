# 11 — Physical Currency, Minting, Mining and Governance Specification

> [!WARNING]
> **DRAFT — NOT IMPLEMENTED.** This document is a design specification under discussion. Nothing described here exists in `main`. Formulas, parameters and default values are proposals and will change after the pending research (see [§13](#13-pending-research-awaiting-gemini-results)) is returned. Where this draft conflicts with code, the code is authoritative for what currently runs.

## Contents

1. [Scope and design goals](#1-scope-and-design-goals)
2. [Notation and constants inherited from code](#2-notation-and-constants-inherited-from-code)
3. [Prior art in this repository](#3-prior-art-in-this-repository)
4. [Reusable bearer notes: identity, lifecycle and nullifiers](#4-reusable-bearer-notes-identity-lifecycle-and-nullifiers)
5. [No demurrage while physical](#5-no-demurrage-while-physical)
6. [Anti-hoarding at re-entry (hybrid design)](#6-anti-hoarding-at-re-entry-hybrid-design)
7. [Decentralized minting](#7-decentralized-minting)
8. [Cryptographic mining](#8-cryptographic-mining)
9. [Shared incentive pool, emission and conservation](#9-shared-incentive-pool-emission-and-conservation)
10. [Governance and anti-capture](#10-governance-and-anti-capture)
11. [Threat model](#11-threat-model)
12. [Open questions](#12-open-questions)
13. [Pending research (awaiting Gemini results)](#13-pending-research-awaiting-gemini-results)

---

## 1. Scope and design goals

User-confirmed requirements this draft must satisfy:

| # | Requirement |
| --- | --- |
| R1 | Physical notes are **reusable bearer cash**: passed hand to hand offline like fiat, fixed face denominations, anonymous holders, lost means lost. |
| R2 | **No demurrage** accrues while a note is physical. |
| R3 | **Anti-hoarding at re-entry**: no upfront fee; large re-entries above a threshold decay faster if parked idle; large re-entries are released gradually; all penalties go to the redistribution vault. |
| R4 | **Anyone can mint**. Minting burns face value minus a production-cost credit plus a small reward. |
| R5 | **Cryptographic mining**: rewards for proof generation and settlement work (optional proof-of-work), sharing one incentive pool with minting. |
| R6 | **Populace governance** resistant to plutocratic and political capture. |
| R7 | No regulatory section (explicit user decision). |

---

## 2. Notation and constants inherited from code

All amounts are fixed-point integers with $`S = 10^{18}`$ base units per token (`SCALE` in `src/tcl/ledger/demurrage_fixed.py`, `RATE_SCALE` in `contracts/CirculatoryEngine.sol`).

| Symbol | Meaning | Source / value |
| --- | --- | --- |
| $`S`$ | Fixed-point scale | $`10^{18}`$ |
| $`T`$ | Demurrage threshold (balances at or below are untouched) | $`5\,000\,000 \cdot S`$ (`T` in `demurrage_fixed.py`) |
| $`r_d`$ | Daily demurrage rate on surplus | $`0.0005`$ |
| $`\lambda`$ | Continuous per-second decay constant | $`\lambda S \approx 5\,788\,484\,761`$ (`LAMBDA_SCALED`) |
| $`\mathcal{V}`$ | Redistribution vault | `vault` in `CirculatoryEngine.sol`, Merkle leaf 0 off-chain |
| $`\mathcal{R}`$ | Poseidon (BN254) binary Merkle state root | `stateRoot`, `src/tcl/ledger` |
| $`\mathcal{N}`$ | Nullifier set (spent markers) | `src/tcl/ledger/nullifiers.py` |
| $`H(\cdot)`$ | Poseidon hash, first input a domain tag | `src/tcl/crypto/poseidon.py` |

The existing digital decay of a balance $`b`$ over $`\Delta t`$ seconds is

```math
b' = T + (b - T)\,e^{-\lambda \Delta t} \quad \text{for } b \gt T, \qquad b' = b \quad \text{for } b \le T,
```

with fee $`f = b - b'`$ credited to $`\mathcal{V}`$. New symbols introduced by this draft:

| Symbol | Meaning |
| --- | --- |
| $`d \in \mathcal{D}`$ | Denomination from a fixed set, face value $`V_d`$ |
| $`\mathsf{id}`$ | Persistent note identity |
| $`k`$ | Note era counter (increments on each re-entry) |
| $`P, D, I`$ | Physical supply, digital supply (incl. $`\mathcal{V}`$), incentive pool |
| $`\Theta`$ | Re-entry hoarding threshold (default $`\Theta = T`$) |
| $`C_d, R_d`$ | Production-cost credit and mint reward for denomination $`d`$ |

---

## 3. Prior art in this repository

The unmerged branch `origin/feat/executable-protocol-v02-20261021` contains `circuits/puf_verify.circom` (`PufVerify`), `contracts/PhysicalMintController.sol`, `contracts/production/ProductionMintController.sol`, `ledger/nullifiers.py` and `docs/04_physical_mint.md`. Its statement is

```math
\mathsf{cm} = H(1,\ \mathsf{secret},\ \mathsf{blinding},\ \mathsf{value},\ \mathsf{context}), \qquad
\mathsf{nf} = H(2,\ \mathsf{secret},\ \mathsf{cm},\ \mathsf{context},\ \mathsf{recipient}),
```

with public signals $`(\mathsf{cm}, \mathsf{nf}, \mathsf{value}, \mathsf{recipient}, \mathsf{context})`$ and range checks on `value` (128 bits) and `recipient` (160 bits).

What we keep: domain-separated Poseidon commitments, nullifier-based replay protection, and recipient binding (prevents front-running a redemption).

What must change:

1. **Single-use.** A commitment is locked once and nullified once, after which the note is permanently dead. This contradicts R1.
2. **Knowledge, not unclonability.** The circuit itself comments that it "proves knowledge of an enrolled, reconstructed secret, not physical unclonability". Anyone who learns `secret` is the note.
3. **Demurrage escape.** The branch acknowledges notes are an escape route from demurrage. §6 replaces this with an explicit re-entry rule.

---

## 4. Reusable bearer notes: identity, lifecycle and nullifiers

### 4.1 Physical construction (proposed)

Each note carries (a) a secure element (SE) holding a non-exportable signing key $`sk_{\mathsf{id}}`$ and a monotonic counter, and (b) an optical or coating PUF whose response $`\rho`$ binds the SE to the substrate (tampering with the substrate changes $`\rho`$). Final hardware choice is pending research (§13).

### 4.2 Persistent identity

```math
\mathsf{id} = H\big(10,\ pk_{\mathsf{id}},\ H(\rho),\ d,\ \mathsf{serial}\big)
```

$`\mathsf{id}`$ never changes for the physical life of the note, regardless of how many holders it passes through. The ledger holds one leaf per note:

```math
\mathsf{leaf}_{\mathsf{id}} = H\big(11,\ \mathsf{id},\ k,\ \sigma,\ t_{\mathrm{mint}},\ g\big), \qquad \sigma \in \{\textsf{LIVE},\ \textsf{DORMANT}\},
```

where $`k`$ is the era counter, $`t_{\mathrm{mint}}`$ the era's mint time and $`g \in \{0,1\}`$ marks genesis (initial-distribution) notes.

### 4.3 Offline hand-to-hand transfer

**Possession is ownership**, exactly like cash. A hand-off is not a ledger event and requires no network. A receiver verifies offline:

1. Challenge-response: receiver's phone sends nonce $`c`$; the SE returns $`\mathrm{Sig}_{sk_{\mathsf{id}}}(c \,\|\, k \,\|\, \sigma)`$.
2. The receiver checks $`pk_{\mathsf{id}}`$ against a cached, periodically synced snapshot of LIVE note ids (or a Merkle inclusion proof against a recent $`\mathcal{R}`$ that the SE itself can store and present).
3. The receiver checks the PUF response optically, with the phone camera.

No holder identity is recorded anywhere, so holders stay anonymous (R1).

### 4.4 Re-entry (deposit) and the nullifier rule

A note re-enters the digital ledger when the bearer deposits it to address $`a`$. The SE signs a one-time redemption authorization and **atomically sets its internal state to DORMANT** and increments its counter. This means a deposited note visibly stops being spendable cash. The re-entry nullifier is

```math
\mathsf{nf}_{\mathsf{id},k} = H\big(12,\ \mathsf{id},\ k,\ \mathsf{ctx}\big).
```

The re-entry proof shows: $`\mathsf{leaf}_{\mathsf{id}}`$ is in $`\mathcal{R}`$ with $`\sigma = \textsf{LIVE}`$ at era $`k`$; the authorization is a valid SE signature binding $`(\mathsf{id}, k, a, \mathsf{ctx})`$; and $`\mathsf{nf}_{\mathsf{id},k} \notin \mathcal{N}`$. State update:

```math
\mathcal{N} \leftarrow \mathcal{N} \cup \{\mathsf{nf}_{\mathsf{id},k}\}, \quad
\mathsf{leaf}_{\mathsf{id}} \leftarrow H(11,\ \mathsf{id},\ k{+}1,\ \textsf{DORMANT},\ t_{\mathrm{mint}},\ g), \quad
b_a \leftarrow b_a + V_d .
```

**Nullifiers are per re-entry event, not per note.** Each era $`k`$ yields exactly one nullifier, so an era can be redeemed once, but the note itself is never destroyed.

### 4.5 Recommended design: re-arm the same note (fresh era), not a new commitment

Two options were considered after re-entry:

| | Option A: re-arm the same note | Option B: retire and issue a fresh commitment |
| --- | --- | --- |
| Physical object | Same note goes back into circulation | Note shredded or retired; new note minted |
| Ledger | Same $`\mathsf{id}`$, new era $`k{+}1`$, fresh leaf | New $`\mathsf{id}'`$ and leaf |
| Cost | Only the burn of $`V_d`$ (§7) | Full production cost again |
| Linkability | Era counter links the eras of one note | No link between old and new note |

**Recommendation: Option A.** A DORMANT note is re-armed to LIVE by a mint event (§7), which burns $`V_d`$ digital and sets $`\mathsf{leaf}_{\mathsf{id}} \leftarrow H(11, \mathsf{id}, k{+}1, \textsf{LIVE}, t_{\mathrm{now}}, 0)`$. The SE accepts the re-arm only with a ledger-signed (or proof-carrying) receipt for era $`k{+}1`$. Reasons: it reuses durable hardware, which is the point of reusable cash; the era counter makes stale-era replays impossible; and linkability is limited to the minter and depositor, never the intermediate holders. Option B remains for worn or damaged notes.

---

## 5. No demurrage while physical

While $`\sigma = \textsf{LIVE}`$, a note carries face value $`V_d`$ unchanged:

```math
\mathrm{value}_{\mathsf{id}}(t) = V_d \qquad \forall\, t \in [t_{\mathrm{mint}},\ t_{\mathrm{reentry}}].
```

There is no charge for holding notes off network and no charge based on how long a note was held. Anti-hoarding acts only on what happens *after* re-entry (§6).

---

## 6. Anti-hoarding at re-entry (hybrid design)

### 6.1 Re-entry bucket

Re-entries are aggregated per **person** (see §6.5), not per address, over a rolling window $`W_a`$ (default 30 days). Let $`A`$ be the aggregated re-entered amount. Only the excess is affected:

```math
E = \max(0,\ A - \Theta), \qquad \Theta = 5\,000\,000 \cdot S \ \text{(configurable)}.
```

If $`A \le \Theta`$ nothing in this section applies. The excess $`E`$ is tracked in a per-person bucket $`B(t)`$ with $`B(t_0) = E`$. Outbound transfers drain the bucket first:

```math
B \leftarrow \max(0,\ B - \mathrm{out}).
```

### 6.2 Rate-limited release (anti-dumping)

The excess is unlocked linearly over a release window $`W_r`$ (default 30 days):

```math
U(t) = \Theta + E \cdot \min\!\left(1,\ \frac{t - t_0}{W_r}\right).
```

Locked funds cannot be transferred, but still count towards the depositor's balance. This rule stops a large stockpile from hitting the market in one move.

### 6.3 Outbound velocity

Velocity is the share of the unlocked excess that has moved on:

```math
v(t) = \frac{E - B(t)}{\max\!\big(S,\ \min(E,\ U(t) - \Theta)\big)} \in [0, 1].
```

Here $`v = 1`$ means everything unlocked so far has been spent or passed on, and $`v = 0`$ means it was all parked.

### 6.4 Accelerated, velocity-weighted decay

With a grace window $`g`$ (default 7 days), idle time $`\tau = t - t_0`$ and ramp horizon $`H`$ (default 90 days), the bucket decays at

```math
\lambda_h(t) = \lambda \left[ 1 + \kappa \cdot \min\!\left(1,\ \frac{\max(0,\ \tau - g)}{H}\right) \cdot \big(1 - v(t)\big) \right],
```

```math
\frac{dB}{dt} = -\lambda_h(t)\, B(t), \qquad \text{penalty } \pi = \int_{t_0}^{t} \lambda_h(s) B(s)\, ds \ \longrightarrow\ \mathcal{V}.
```

The multiplier $`\kappa`$ defaults to 9, bounded to $`[0, 20]`$, so parked excess decays at up to $`10\lambda`$. Properties:

- Below threshold ($`A \le \Theta`$), and during the grace window, $`\lambda_h`$ never applies.
- Fully circulated excess ($`v = 1`$) decays at only the normal rate $`\lambda`$.
- The penalty grows with the amount ($`B`$), with idle time (ramp) and with low velocity ($`1 - v`$).
- All penalties go to the redistribution vault $`\mathcal{V}`$ (R3).

The bucket decay runs in addition to the normal surplus decay above $`T`$. To avoid charging twice, the balance is split into the bucket $`B`$ and the rest $`r`$, and each part decays under its own rule:

```math
b = B + r, \qquad r' = T + (r - T)\,e^{-\lambda \Delta t}\ \text{if } r \gt T \ (\text{else } r' = r), \qquad B' = B\,e^{-\int \lambda_h\, dt}, \qquad b' = B' + r'.
```

In fixed point, $`\lambda_h S`$ is computed per step and fed to the existing 5th-order Taylor multiplier with $`\Delta t \le 604\,800`$ s (`MAX_STEP_SECONDS`), which keeps $`x = \lambda_h \Delta t`$ small. At $`\kappa = 20`$, $`x \approx 21 \times 0.35 \approx 7.4`$, which is too large for the Taylor rule, so the step is reduced to $`\Delta t \le 86\,400`$ s whenever $`\kappa \gt 1`$.

### 6.5 Sybil splitting

Splitting a re-entry across many addresses would dodge $`\Theta`$. Mitigations:

1. **Personhood-scoped aggregation.** Each re-entry above a small floor $`\theta_{\min}`$ (for example $`10\,000 \cdot S`$) must carry an anonymous personhood proof yielding a per-window pseudonym $`\nu = H(13,\ \mathsf{personSecret},\ \lfloor t / W_a \rfloor)`$. Then $`A`$ is summed per $`\nu`$, which is unlinkable across windows and to identity.
2. **Below the floor, no proof is needed.** Small everyday deposits stay frictionless. A Sybil would need $`\Theta / \theta_{\min} = 500`$ unrelated persons to bypass the rule, which is a personhood-layer cost (§10).
3. **Note-side signal.** Bulk re-entry of many notes minted by the same minter in the same era is flagged as one cluster (heuristic, optional).

---

## 7. Decentralized minting

### 7.1 Mint event

Anyone can mint. To mint (or re-arm) a note of denomination $`d`$, the minter $`m`$ burns $`V_d`$ digital and is credited $`C_d + R_d`$ from the incentive pool $`I`$:

```math
b_m \leftarrow b_m - V_d + C_d + R_d, \qquad P \leftarrow P + V_d, \qquad I \leftarrow I - (C_d + R_d).
```

The net burn is $`V_d - C_d - R_d`$. Minting is profitable by $`R_d`$ over real cost when the attested cost is honest.

### 7.2 Production-cost credit $`C_d`$ (attested, capped, market-discovered)

Each mint submits an attested cost $`\hat c`$. Hardware lots can be signed by a fab, but the protocol does not trust that signature. The credit is

```math
C_d = \min\big(\hat c,\ c^{\max}_d,\ \tilde c_d\big), \qquad
\tilde c_d = \mathrm{median}\big(\hat c_{(1)}, \dots, \hat c_{(K)}\big)\cdot(1 + \epsilon),
```

where $`\tilde c_d`$ is the rolling median of the last $`K`$ attested costs for $`d`$ (default $`K = 256`$, $`\epsilon = 0.05`$) and $`c^{\max}_d = \gamma_c V_d`$ is a governance-bounded hard cap ($`\gamma_c \in [0, 0.05]`$). Because minters compete, over-claiming raises nobody's credit above the median, while honest low-cost minters capture margin. Re-arming an existing note (Option A) carries $`C_d = 0`$, because no new hardware is made.

### 7.3 Mint reward $`R_d`$ (adjustable, bounded, pool-funded)

```math
R_d = \min\!\Big(\rho \cdot V_d,\ \frac{\alpha\, \mathcal{E}_e}{\max(1, n_e)}\Big), \qquad \rho \in [0,\ \rho_{\max}],\ \rho_{\max} = 0.01,
```

where $`\mathcal{E}_e`$ is the epoch emission (§9), $`\alpha`$ the minting share and $`n_e`$ the number of mints in the epoch. When the pool is thin, the per-mint reward shrinks automatically. A governance controller can target physical-supply share $`p^* = P/(P+D)`$:

```math
\rho_{e+1} = \mathrm{clip}\Big(\rho_e + \eta_\rho\,(p^* - p_e),\ 0,\ \rho_{\max}\Big).
```

### 7.4 Anti-over-minting

- **Budget.** $`\sum_{\text{mints in } e} (C_d + R_d) \le \alpha\,\mathcal{E}_e + \text{cost reserve}`$. Mints beyond the budget still succeed but receive $`C_d = R_d = 0`$ (pure 1:1 conversion), so they create no free value.
- **Over-minting is self-limiting.** Every LIVE note is backed one-for-one by burned digital: $`P = \sum_{\textsf{LIVE}} V_d`$ always holds.
- **Denomination cap.** $`V_{\max}`$ is bounded so that the cost of cloning one note exceeds what one clone can earn (§11).

### 7.5 Counterfeit economics

Let $`K_{\mathrm{clone}}`$ be the cost of extracting $`sk_{\mathsf{id}}`$ and reproducing $`\rho`$. A cloned note can be passed offline $`n`$ times before the first redemption, after which era $`k`$ is nullified and all copies are DORMANT on the next sync. Attacker profit is

```math
\Pi = (n - 1)\,V_d - K_{\mathrm{clone}} - c_{\mathrm{dist}}(n).
```

Design target: $`K_{\mathrm{clone}} \gt n_{\max} V_{\max}`$ for a realistic offline circulation depth $`n_{\max}`$ before resync (for example 20). So $`V_{\max} \lt K_{\mathrm{clone}} / n_{\max}`$; values are pending hardware research.

---

## 8. Cryptographic mining

"Mining" means being paid for useful verifiable work, mainly settlement proofs.

| Role | Work | Verified by |
| --- | --- | --- |
| Prover | Groth16 proofs for settlement and re-entry | on-chain verifier (`StateVerifier`) |
| Settler | submitting `settleEpoch`, batching re-entries | `StateSettled` event, `StaleRoot` checks |
| Re-entry/mint relayer | aggregating offline deposits and receipts | proof inclusion |
| PoW (optional, default off) | hash puzzle for permissionless leader selection or rate limiting | difficulty target |

Prover reward for proof $`j`$ in epoch $`e`$, weighted by circuit work $`w_j`$ (constraint count) and paid only on successful on-chain verification:

```math
r_j = \beta\,\mathcal{E}_e \cdot \frac{w_j}{\sum_{i \in e} w_i}.
```

Settlers get $`\gamma\,\mathcal{E}_e`$, split per accepted settlement. Optional PoW gets $`\delta\,\mathcal{E}_e`$ split by valid shares. Anti-cartel measures: each epoch's proving slot goes to a uniformly random bonded prover (randomness from the previous root, $`H(14, \mathcal{R}_{e-1}, e)`$), with permissionless fallback after a timeout $`t_{\mathrm{fb}}`$; a per-prover reward share cap $`s_{\max}`$ (for example 20%), with the excess returned to the pool; and slashing of bond $`\beta_{\mathrm{bond}}`$ for missed assigned slots.

---

## 9. Shared incentive pool, emission and conservation

### 9.1 Funding

The existing vault $`\mathcal{V}`$ receives demurrage fees $`F_e`$ and anti-hoarding penalties $`\Pi_e`$. A governance-bounded share $`\phi \in [0, 0.5]`$ goes to the incentive pool, and the rest is redistributed as today:

```math
I_{e+1} = I_e + \phi\,(F_e + \Pi_e) - \mathcal{E}_e^{\mathrm{paid}}, \qquad \mathcal{V}_{e+1} = \mathcal{V}_e + (1 - \phi)(F_e + \Pi_e) - \mathrm{redist}_e.
```

### 9.2 Emission and split

```math
\mathcal{E}_e = \eta\, I_e, \quad \eta \in [0.01, 0.25]; \qquad \alpha + \beta + \gamma + \delta = 1,
```

with defaults $`(\alpha, \beta, \gamma, \delta) = (0.35,\ 0.45,\ 0.20,\ 0)`$. Unspent emission stays in $`I`$. **There is no inflation**: rewards are funded only by fees, so the pool can never go negative.

### 9.3 Conservation

Total supply $`M = D + P + I`$, with $`D`$ including $`\mathcal{V}`$, is conserved by every operation:

```math
\Delta M = \underbrace{(-V_d + C_d + R_d)}_{\Delta D} + \underbrace{V_d}_{\Delta P} + \underbrace{(-C_d - R_d)}_{\Delta I} = 0 \quad \text{(mint)},
```

```math
\Delta M = V_d - V_d = 0 \ \text{(re-entry)}, \qquad \Delta M = 0 \ \text{(demurrage, penalties, rewards: transfers among } D, \mathcal{V}, I).
```

Physical backing invariant:

```math
P = \sum_{\mathsf{id}\,:\,\sigma = \textsf{LIVE}} V_{d(\mathsf{id})}.
```

Lost notes stay in $`P`$ forever. They are effectively burned, like lost cash, and are not reclaimed (R1).

---

## 10. Governance and anti-capture

### 10.1 Current state

`CirculatoryEngine` is `OwnableUpgradeable` + `UUPSUpgradeable`. The owner can upgrade the implementation, and `settler` defaults to the owner. This is a single point of capture and must be removed.

### 10.2 Voting

- **Electorate:** one anonymous personhood credential per human. Votes use a per-proposal nullifier $`H(15, \mathsf{personSecret}, \mathsf{proposalId})`$, which allows one vote per person with no link to identity or token holdings.
- **Quadratic voting:** each person has $`\Omega`$ voice credits per season; casting $`n`$ votes on an issue costs $`n^2`$ credits. Token balance confers **no** voting power.
- **Anti-bribery:** MACI-style encrypted ballots with key changes, so a voter cannot prove to a briber how they voted.
- **Quorum:** $`q \ge 5\%`$ of registered persons, and pass $`\gt 50\%`$ (or $`\gt 66\%`$ for constitutional changes).

### 10.3 Bounded parameters and timelocks

Even a captured majority can only move parameters within hard-coded ranges, and only by a limited step per cycle:

```math
x_{\mathrm{new}} \in [x_{\min},\ x_{\max}], \qquad |x_{\mathrm{new}} - x_{\mathrm{old}}| \le \Delta_x \text{ per governance cycle}.
```

| Parameter | Default | Bounds | Max step / cycle |
| --- | --- | --- | --- |
| $`r_d`$ | 0.0005 | $`[0.0001, 0.002]`$ | 25% relative |
| $`T`$, $`\Theta`$ | $`5\cdot10^6 S`$ | $`[10^5 S, 10^8 S]`$ | 25% |
| $`\kappa`$ | 9 | $`[0, 20]`$ | 2 |
| $`g`$, $`H`$, $`W_r`$ | 7, 90, 30 days | $`[1, 365]`$ days | 50% |
| $`\rho`$ | 0.002 | $`[0, 0.01]`$ | 0.001 |
| $`\gamma_c`$ | 0.02 | $`[0, 0.05]`$ | 0.005 |
| $`\phi`$ | 0.2 | $`[0, 0.5]`$ | 0.05 |
| $`\eta`$ | 0.05 | $`[0.01, 0.25]`$ | 0.02 |

Every change waits out a timelock of $`\ge 14`$ days (30 for upgrades), during which anyone can exit.

### 10.4 Key removal roadmap

1. **Stage 0 (today):** owner EOA.
2. **Stage 1:** owner moved to a multisig plus timelock, and `settler` made permissionless (any prover with a valid proof).
3. **Stage 2:** owner becomes the governance timelock contract; upgrades need a populace vote.
4. **Stage 3:** `renounceOwnership`, and the UUPS `_authorizeUpgrade` is disabled permanently (immutable core). Only bounded parameters remain governable.

### 10.5 Identity layer: anonymous proof-of-personhood

Source: Gemini deep research (user-supplied, 2026-10-23), summarized at the time of writing. The research compares:

| System | Mechanism | Main weakness |
| --- | --- | --- |
| Worldcoin / World ID | Orb iris scan plus Semaphore ZK | Biometric; hardware supply chain; regional bans |
| Encointer | Simultaneous in-person pseudonym parties | Liveness and attendance friction |
| BrightID / Aura | Social-graph ranking (SybilRank) | Slow, long-con infiltration |
| Proof of Humanity | Video, vouching, Kleros disputes | No privacy; deepfakes |
| Human Passport | Stamp aggregation | Wealth bias; stamps can be bought |

The research proposes fusing ZK iris, ceremony and graph signals. **Iris biometrics conflict with the owner's anonymity and no-identity goal**, so this draft **recommends a non-biometric-first fusion**:

1. **Ceremony:** Encointer-style pseudonym parties.
2. **Graph:** BrightID-style centrality above a threshold $`\theta_g`$.
3. **ZK credential and MACI:** Semaphore-style nullifiers $`H(15, \mathsf{personSecret}, \cdot)`$ and MACI key rotation.

Biometrics are optional only, as an extra stamp a person may choose, and are never required. A credential is valid when its fused score passes a threshold (reconstructed from research summary):

```math
s_i = w_c\,\mathbb{1}[\text{ceremony}] + w_g\,\mathbb{1}[\mathrm{centrality}_i \gt \theta_g] + w_b\,\mathbb{1}[\text{optional biometric}] \ge s^*, \qquad w_b \lt s^*.
```

Because $`w_b \lt s^*`$, biometrics alone can never qualify a person. According to the research, Sybil cost grows super-linearly in the number $`k`$ of independent signals an attacker must fake. The hardware recommendations (multi-vendor sensors, remote attestation, governance revocation of manufacturer root keys) apply to the optional biometric path and to the note secure elements (§4.1).

### 10.6 Plural conviction voting

This replaces plain QV in §10.2 as the Phase 2 target. All equations below are reconstructed from research summary. Support accumulates over time, so a flash loan or a single block cannot swing a vote:

```math
Y_p(t+1) = \alpha\,Y_p(t) + \sum_i W_i\,\sqrt{C_i}, \qquad \alpha = 2^{-1/\tau_{\mathrm{half}}},
```

where $`C_i`$ is the voice credit person $`i`$ stakes. $`W_i`$ is a correlation discount over the social graph, using the Jaccard similarity $`\sigma(i,j)`$ of neighborhoods, which reduces the weight of coordinated blocs ("Dark DAOs"):

```math
W_i = \frac{1}{1 + \sum_{j \ne i} \sigma(i,j)}.
```

With $`\tau_{\mathrm{half}} = 10^6`$ blocks, one block contributes only about $`1-\alpha \approx 7\times10^{-7}`$ of the steady state. A proposal requesting share $`R`$ of the pool passes when (reconstructed from research summary)

```math
Y_p \ge Y^* = \frac{\beta_1\, T_{\mathrm{sup}}}{\big(1 - \beta_2\, R / T_{\mathrm{sup}}\big)^2},
```

where $`T_{\mathrm{sup}}`$ is the effective supply. Tokens never add weight; only persons do. Futarchy ("vote on values, bet on beliefs"), with a citizen veto, is deferred to Phase 3.

### 10.7 UBI and demurrage interplay (proposal; implemented constants unchanged)

The research suggests recycling demurrage as a universal basic income $`u`$ to verified persons. For a holder subject to decay (reconstructed from research summary):

```math
\frac{dB}{dt} = u - \lambda B, \qquad B(t) = B_0 e^{-\lambda t} + \frac{u}{\lambda}\left(1 - e^{-\lambda t}\right), \qquad B_{ss} = \frac{u}{\lambda}.
```

In TCL, decay applies only to the surplus above $`T`$, so this would apply to the surplus $`B - T`$. Here $`u`$ would be paid from the redistribution share $`(1-\phi)`$ of $`\mathcal{V}`$ (§9.1), per personhood credential. The research's PPP-targeting controller (reconstructed from research summary) is

```math
MV = C^{*}T_x, \qquad \mu = \pi^{*} + \dot{T}_x - \dot{V}, \qquad N u = (\mu + \lambda) M \ \Rightarrow\ u = \frac{(\mu + \lambda) M}{N}.
```

This draft **does not change** the implemented constants $`r_d = 0.0005`$, $`T`$ or $`\lambda`$. The research's suggested rollout of about 1%/yr demurrage in Phase 1 and 5–10%/yr in Phase 2 is listed only as an open question. Any $`\mu \gt 0`$ (new issuance) would break the no-inflation rule of §9.2 and needs a populace decision.

### 10.8 Rollout (from research, adapted)

1. **Phase 1:** credential aggregation, multisig plus timelock backstop (Stage 1 of §10.4).
2. **Phase 2:** non-biometric PoP fusion, plural conviction voting, and governance as owner (Stage 2).
3. **Phase 3:** immutable core (Stage 3), optional PPP controller and futarchy with citizen veto.

---

## 11. Threat model

| Threat | Attack | Mitigation | Residual risk |
| --- | --- | --- | --- |
| PUF/SE cloning | Extract $`sk_{\mathsf{id}}`$, replicate $`\rho`$, spend copies offline | SE plus substrate-bound PUF; $`V_{\max} \lt K_{\mathrm{clone}}/n_{\max}`$; era nullifier makes only the first redemption valid; DORMANT broadcast on sync | Offline victims holding copies lose value |
| Offline double-spend | Spend the same note to several receivers | Physical possession is exclusive; a clone reduces to the row above | Same as cloning |
| Deposit and keep spending | Redeem, then keep passing the note | SE atomically goes DORMANT on redemption; receivers check the SE state | Compromised SE |
| Era replay | Re-redeem an old era | $`\mathsf{nf}_{\mathsf{id},k}`$ plus counter $`k`$ in the leaf | None known |
| Mint collusion / cost inflation | Minters attest inflated $`\hat c`$ | Median plus cap plus budget; $`C_d = 0`$ for re-arm | Cartel shifting the median within the cap |
| Over-minting | Mint to drain $`I`$ | Per-epoch budget; 1:1 backing | None to supply; only reward dilution |
| Prover cartel | Withhold proofs, extract rents | Random assignment, fallback, share cap, slashing | Liveness delay up to $`t_{\mathrm{fb}}`$ |
| Sybil (re-entry) | Split above-threshold re-entry | Personhood-scoped aggregation above $`\theta_{\min}`$ | Strength of the personhood layer |
| Sybil (governance) | Fake persons for votes | Personhood plus QV plus MACI | Personhood is an open problem |
| Dumping | Flood re-entered funds | Release window $`W_r`$ | None within bounds |
| Admin capture | Owner upgrades or changes params | §10.4 roadmap | Until Stage 3 |
| Front-running a redemption | Steal the proof in the mempool | Recipient $`a`$ bound in the SE signature and proof | None known |

---

## 12. Open questions

1. Exact anti-hoarding combination with normal surplus decay (§6.4): additive, or replace the normal rate for the bucket?
2. Should velocity count only transfers to *distinct* counterparties (resists wash transfers to one's own addresses), and how is that measured without identity?
3. Is $`\theta_{\min}`$ (the personhood-proof floor) acceptable friction, and what value?
4. How do genesis notes ($`g = 1`$) enter: what is the initial-distribution mechanism, and are they exempt from §6?
5. Offline revocation latency: how stale may a receiver's LIVE snapshot be?
6. The denomination set $`\mathcal{D}`$ and $`V_{\max}`$, pending hardware clone-cost data.
7. Should optional PoW exist at all, given it adds energy cost without useful work?
8. Can re-entry proofs be batched in the existing settlement circuit, or do they need a separate circuit and verifier?
9. Default emission split and $`\phi`$: what are the sustainable values under realistic fee volume?
10. Ceremony cadence and graph threshold $`\theta_g`$ for the non-biometric PoP fusion, and access for people without phones.
11. Adopt UBI recycling (§10.7)? Should demurrage move toward the research's ~1%/yr then 5–10%/yr path (currently $`r_d = 0.0005`$/day, about 17%/yr on surplus)?
12. Plural conviction parameters $`\tau_{\mathrm{half}}, \beta_1, \beta_2`$.

---

## 13. Pending research (awaiting Gemini results)

| Topic | Feeds sections | Status |
| --- | --- | --- |
| Reusable physical bearer notes (PUF, SE, offline e-cash, offline CBDC) | §4, §7.5, §11 | Awaiting results |
| Demurrage history on physical currency (Wörgl, Freigeld, Chiemgauer) | §5, §6 | Awaiting results |
| Mint and mine incentive models (proof markets, bonding, slashing) | §7, §8, §9 | Awaiting results |
| Hardware and manufacturing unit economics (NTAG 424 DNA, SE050, polymer, PUF) | §4.1, §7.2, §7.5 | Awaiting results |
| Anonymous proof-of-personhood and anti-capture governance | §6.5, §10.5–§10.8 | **Received**: Gemini deep research (user-supplied, 2026-10-23); incorporated |

Regulatory research is intentionally out of scope (R7).
