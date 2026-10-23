# 11 — Physical Currency, Minting, Mining and Governance Specification

> [!WARNING]
> **DRAFT — NOT IMPLEMENTED.** This document is a design specification under discussion. Nothing described here exists in `main`. Formulas, parameters and default values are proposals and will change after owner review of the research-driven decisions (see [§12](#12-decisions-owner-confirmed-2026-10-23)). Where this draft conflicts with code, the code is authoritative for what currently runs.

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
12. [Decisions (owner-confirmed, 2026-10-23)](#12-decisions-owner-confirmed-2026-10-23)
13. [Open questions (still unresolved)](#13-open-questions-still-unresolved)
14. [Research received](#14-research-received)

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

### 4.6 Research integration (Part A)

Source: Gemini deep research (user-supplied, 2026-10-23, master), Part A. Claims marked † need primary sources.

**Hardware survey, as summarized from the report:**

- Optical PUFs: random light-scattering particles in the substrate, read with a phone flash.
- Coating PUFs: a particle matrix over the chip that is destroyed by decapping.
- SRAM PUFs: power-up state of the chip's memory; need a fuzzy extractor.
- RF PUFs: antenna impedance signatures.
- NXP NTAG 424 DNA: AES-128 CMAC "SUN" messages with a tap counter, about USD 0.20–0.35.
- Asymmetric secure elements (NXP SE050, Infineon SLE78): about USD 1.10–1.80.
- Prior art: transferable e-cash (Chaum; Brands; Okamoto–Ohta; Baldimtsi et al.) and hardware-enforced offline CBDC pilots (ECB digital euro offline, BIS Project Polaris, e-CNY hardware wallets).

The report proposes a commitment $`C_{\mathrm{note}} = H(\mathsf{NoteID}, S_{\mathrm{secret}})`$, a deposit nullifier $`N_{\mathrm{dep}} = H(S_{\mathrm{secret}}, \mathsf{DepositNonce})`$, and re-arming by writing a new secret $`S'`$ to the SE.

**Assessment:**

1. **Error: the nullifier as given allows double deposits.** With a free $`\mathsf{DepositNonce}`$, one commitment yields unlimited distinct nullifiers. A nullifier must be a deterministic function of the commitment and era. This spec keeps $`\mathsf{nf}_{\mathsf{id},k} = H(12, \mathsf{id}, k, \mathsf{ctx})`$ (§4.4). The report's secret rotation $`S \to S'`$ is adopted as an option, *in addition* to the era counter: the leaf becomes $`H(11, \mathsf{id}, k, \sigma, t_{\mathrm{mint}}, g, H(S_k))`$.
2. **Error: NTAG 424 DNA cannot support offline verification by strangers.** SUN/CMAC is symmetric, so a phone can verify offline only if it holds the AES key, and anyone with the key can forge a tag. **This spec requires an asymmetric SE** (SE050/SLE78 class) for offline checks. NTAG-class chips are acceptable only for online checks.
3. **Re-arm payload.** A public EVM contract cannot send a confidential "encrypted re-arming payload". The re-arm secret must be generated *on the SE* (or by the minter's device), and only $`H(S')`$ is published.
4. **Claims marked †:** BN254 support on the SE050; optical PUF entropy "$`\gt 10^6`$ bits/mm²"; clone-cost figures.

---

## 5. No demurrage while physical

While $`\sigma = \textsf{LIVE}`$, a note carries face value $`V_d`$ unchanged:

```math
\mathrm{value}_{\mathsf{id}}(t) = V_d \qquad \forall\, t \in [t_{\mathrm{mint}},\ t_{\mathrm{reentry}}].
```

There is no charge for holding notes off network and no charge based on how long a note was held. Anti-hoarding acts only on what happens *after* re-entry (§6).

### 5.1 Research integration (Part B): rejected holding-duration penalty

Part B surveys Gesell's *Freigeld*, Wörgl (1932–33, 1%/month stamp scrip), the Wära experiments and 1930s US stamp scrip. Reported Wörgl outcomes (about 20× velocity, back taxes up 35%, unemployment down 25%) are † and need primary sources. The lesson that manual stamping added friction supports §5's choice: no decay at all on physical notes.

The report then proposes a re-entry penalty based on how long the note was held:

```math
\Phi(V, \tau_{\mathrm{hold}}) = V\left[1 - e^{-\lambda \max(0,\ \tau_{\mathrm{hold}} - \tau_{\mathrm{grace}})}\right], \quad \lambda = 0.00045\ \mathrm{day}^{-1},\ \tau_{\mathrm{grace}} = 90\ \mathrm{d}.
```

**Rejected.** It is demurrage while physical under another name, which conflicts with R2 and with the owner's clarification that holding cash is free and only the *behaviour after re-entry* is charged. It also applies per note, with no threshold, so it hits small everyday users. §6 is kept.

**Numeric check.** The report gives $`r_d = 0.0005`$/day as "≈16.78%/yr". The correct figure is $`1 - (1 - 0.0005)^{365} \approx 16.69\%`$ (continuous: $`1 - e^{-0.1825} \approx 16.68\%`$). Its digital decay equations otherwise match the code's surplus-above-$`T`$ rule.

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

### 6.6 Research integration (Q1–Q3)

- **Q1.** The report proposes multiplicative decay, $`B_s(t+1) = B_s(t)(1 - \delta_b)(1 - \delta_a \mathbb{1}[t_{\mathrm{idle}} \gt 180\,\mathrm{d}])`$, with $`\delta_a = 0.001`$. Its rationale is wrong: additive *exponential* decay $`e^{-(\lambda + \lambda_a)\Delta t}`$ can never go negative, and $`(1-\delta_b)(1-\delta_a) = 1 - \delta_b - \delta_a + \delta_b\delta_a`$ is additive to first order. This spec keeps §6.4 (exponential, so it can't go negative), adopts the report's point that acceleration applies **only to surplus**, and resolves Q1 as "separate decay for the bucket and the rest" (§6.4 split $`b = B + r`$).
- **Q2.** Adopted: an outflow counts towards $`v`$ only after a minimum hold $`\Delta t_{\min} = 12`$ h, and only if sent to a counterparty other than the sender's last $`k`$ counterparties. The report's "distinct nullifier path" wording assumes a UTXO model. TCL uses accounts, so the circuit instead proves the recipient is outside a committed set of the sender's last $`k`$ counterparties, which is private to the sender.
- **Q3.** Adopted: $`\theta_{\min} = 10\,000`$ TCL (in units of $`S`$).

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

### 7.6 Research integration (Part C Model 1, Part D, Q6)

The report's mint model:

```math
C(t) = \min\!\Big(C_{\mathrm{cap}},\ C_{\mathrm{base}}\tfrac{I_{\mathrm{CPI}}(t)}{I_{\mathrm{CPI}}(0)}\Big), \qquad R = V \mu_{\max}\Big(1 - \tfrac{S_{\mathrm{phys}}}{S_{\mathrm{target}}}\Big),
```

with $`C_{\mathrm{base}} = 1.50`$, $`C_{\mathrm{cap}} = 5.00`$, $`\mu_{\max} = 0.01`$ and $`S_{\mathrm{target}} = 20\%`$ of supply.

**Assessment:**

- **Error:** $`R`$ goes negative when $`S_{\mathrm{phys}} \gt S_{\mathrm{target}}`$. Use $`\max(0, \cdot)`$.
- **Conflict:** the CPI oracle is a centralized price feed, which conflicts with sovereignty and decentralization. It is rejected in favour of §7.2's market-discovered median.
- **Adopted:** the supply-target shape replaces the §7.3 controller:

```math
R_d = \min\!\Big(V_d\,\rho_{\max}\max\!\big(0,\ 1 - \tfrac{P}{p^{*}M}\big),\ \tfrac{\alpha\mathcal{E}_e}{\max(1,n_e)}\Big), \quad \rho_{\max} = 0.01,\ p^{*} = 0.2 \ (\text{proposed}).
```

- **Cost cap:** set $`c^{\max}_d = \min(\gamma_c V_d,\ 5\ \text{TCL})`$.

**Part D (hardware).** The proposed note is a BOPP polymer substrate with OVD, UV and IR inks, an optical PUF layer, an embedded NFC SE, and delamination-sensitive silver antenna tracks. The report gives circulation life as 8–10 years † and lists per-unit cost:

| Volume | 10k | 1M | 100M |
| --- | --- | --- | --- |
| Total $`C`$ | USD 5.00 | USD 0.75 | USD 0.23 |

The component sums check out. **Inconsistency:** the table's SE line (USD 0.11–1.25) is NTAG-class, while the report's own matrix prices the asymmetric SE050 class at USD 1.10–1.80. Since §4.6 requires an asymmetric SE, realistic $`C`$ at 1M units is about USD 1.6–2.3 (estimate †).

**Q6 (denominations).** The report proposes 5, 10, 20, 50, 100 and 500, with $`V_{\max} = 500`$ TCL, citing a clone cost of USD 50k–150k "per design iteration".

**Error:** a per-*design* setup cost is amortized over every clone made, so the right test from §7.5 is on the *marginal* clone cost, $`\Pi = nN_cV - K_{\mathrm{setup}} - N_c K_{\mathrm{marginal}}`$. This spec adopts the denomination set provisionally, pending a marginal-cost estimate, with $`V_{\max} = 500`$ as an upper bound. It also mixes TCL and USD units without an exchange rate.

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

### 8.1 Research integration (Part C, Q7, Q8)

- **Q7.** Adopted: drop optional PoW and set $`\delta = 0`$ permanently (energy without useful work).
- **Cartels.** Adopted: a reverse-Dutch auction for batch rewards, $`r(t) = r_0(1 + \kappa_r \lfloor (t - t_0)/N_{\mathrm{blocks}} \rfloor)`$ capped at $`r_{\max}`$. It complements the §8 random assignment.
- **Q8.** Adopted as a roadmap item: off-chain recursive aggregation (Nova folding or Plonky2) wrapped in one outer Groth16 proof. The report's figure of about 220k gas is plausible for BN254 Groth16 with few public inputs (about 181k base plus about 6k per input; verify on Anvil). The existing settlement circuit stays as-is until then.

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

### 9.4 Research integration (Part C Model 2, Q9, Q11)

The report's emission rule is $`E_{\mathrm{em}} = E_{\mathrm{pool}}(1 - e^{-\gamma})`$ with $`\gamma = 0.05`$, or about 4.88% per epoch, consistent with §9.2's $`\eta = 0.05`$. It splits emission 20% to minters and 80% to provers, adjusted dynamically:

```math
\alpha(L) = \max\!\big(0.05,\ 0.20 - \eta_L \max(0,\ L - L_{\mathrm{target}})\big), \quad L_{\mathrm{target}} = 60\ \mathrm{s},\ \eta_L = 0.005\ \mathrm{s}^{-1}.
```

**Assessment:**

- **Inconsistency:** Part C states this controller as $`\alpha = 0.20 - \theta (T_{\mathrm{avg}} - T_{\mathrm{target}})/T_{\mathrm{target}}`$, which has no floor and can go negative or above 1. The Q9 form with a floor is adopted.
- **Error:** the report funds the vault with "50% of EVM transaction base fees", but L1 base fees are burned or paid to validators, not to the protocol, and TCL charges no protocol fee. This spec funds the vault from demurrage and re-entry penalties only, unless a protocol fee is added later (owner decision).
- **Adopted:** the minter/prover split is $`\alpha`$ for minters and $`1 - \alpha`$ shared by provers and settlers ($`\beta:\gamma = 0.7:0.3`$ within it), with $`\delta = 0`$.
- **Q11, UBI.** The report recommends keeping about 17%/yr ($`r_d = 0.0005`$/day) above 5M and distributing 80% of net demurrage, after minter and prover rewards, equally per verified person. That **needs no issuance**, so it is compatible with fixed supply, unlike the earlier PPP formula, which stays rejected. It is proposed as $`1 - \phi = 0.8`$ of the vault inflow, distributed per personhood credential per epoch. Caveat: per-person UBI makes personhood the most valuable attack target, so §10.5 must be in place first.

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

Source: Gemini deep research (user-supplied, 2026-10-23). Equations in §10.5–§10.7 are quoted from that report. This draft cites the report only; several of the report's own references are low quality (for example Patreon links). Claims marked † still need primary sources.

| System | Mechanism | Main weakness (per report) |
| --- | --- | --- |
| Worldcoin / World ID | Orb iris scan plus Semaphore ZK | Biometric; hardware supply chain; regional bans† |
| Encointer | Simultaneous in-person pseudonym parties | Liveness and attendance friction |
| BrightID / Aura | Social-graph ranking (SybilRank) | Slow, long-con infiltration |
| Proof of Humanity | Video, vouching, Kleros disputes | No privacy; deepfakes† |
| Human Passport | Stamp aggregation | Wealth bias; stamps can be bought |

The report proposes a fusion in which a credential requires all three of: a ZK iris attestation, an Encointer-style ceremony, and graph centrality $`C_{\mathrm{graph}}(i) \gt \theta`$. **Iris biometrics conflict with the owner's anonymity and no-identity goal**, so this draft **recommends a non-biometric-first fusion**:

1. **Ceremony:** Encointer-style pseudonym parties.
2. **Graph:** BrightID-style centrality above $`\theta`$.
3. **ZK and MACI:** Semaphore-style nullifiers $`H(15, \mathsf{personSecret}, \cdot)`$ and MACI key rotation.

Biometrics are an optional extra signal, never a requirement:

```math
\mathrm{valid}_i = \mathbb{1}[\text{ceremony}_i] \wedge \mathbb{1}[C_{\mathrm{graph}}(i) \gt \theta] \quad (\text{optional biometric may lower } \theta \text{, never replace either term}).
```

The report states Sybil cost $`C_{\mathrm{sybil}}(k) = \mathcal{O}(k^2)`$ in the number of fake identities $`k`$. **This is an assertion in the report, not a derivation**, and needs independent analysis†. The report's hardware advice (multi-vendor sensors, remote attestation, governance revocation of manufacturer root keys) applies to the optional biometric path and to note secure elements (§4.1).

### 10.6 Plural conviction voting

This is the Phase 2 target, replacing plain QV from §10.2. The report's update is

```math
Y_{i,p}(t) = \alpha\,Y_{i,p}(t-1) + W_i\, S_{i,p}(t), \qquad \alpha = 2^{-1/\tau_{1/2}},
```

```math
\sigma(i,j) = \frac{|N(i) \cap N(j)|}{|N(i) \cup N(j)|}, \qquad W_i = \frac{1}{\sqrt{1 + \sum_{j \in V_p,\, j \ne i} \sigma(i,j)}},
```

with the bribe-cluster limit

```math
\lim_{\Sigma_p \to \mathbf{1}} W_i = \frac{1}{\sqrt{|V_{\mathrm{bribe}}|}}.
```

**Critical review: flash-loan bound.** The report claims a single-block injection yields $`Y_{\mathrm{flash}} = S_{\mathrm{flash}}(1 - 2^{-1/\tau_{1/2}}) \approx S_{\mathrm{flash}} \ln 2 / \tau_{1/2}`$, which is about $`10^{-6}`$ at $`\tau_{1/2} = 10^6`$ blocks. That bound **does not follow from the stated update**: with $`Y(t) = \alpha Y(t-1) + W S`$ and $`Y(t-1) = 0`$, one block already gives $`Y = W S`$, the full stake. The bound holds only for the normalized update, **which this spec adopts**:

```math
Y_{i,p}(t) = \alpha\,Y_{i,p}(t-1) + (1 - \alpha)\, W_i\, S_{i,p}(t) \ \Rightarrow\ Y_{\mathrm{flash}} = W_i S (1-\alpha), \quad Y_{ss} = W_i S.
```

In TCL, $`S_{i,p}`$ is the voice credit staked by a **person**, not tokens, so flash loans already have no direct effect. The normalized form still limits sudden swings. Execution threshold for a request $`R_p`$ against treasury $`T_{\mathrm{tr}}`$ (written $`T`$ in the report; renamed here to avoid a clash with the demurrage threshold $`T`$):

```math
\sum_{i \in V_p} Y_{i,p}(t) \ge Y_p^*(R_p) = \frac{\beta_1\, T_{\mathrm{tr}}}{\left(1 - \beta_2 \frac{R_p}{T_{\mathrm{tr}}}\right)^2}, \qquad \beta_2 \in (0,1).
```

Futarchy ("vote on values, bet on beliefs"), with a citizen veto, is deferred to Phase 3.

### 10.7 UBI and demurrage (optional governance-controlled extension)

The report models balances with UBI $`u`$:

```math
\frac{dB_i}{dt} = -\lambda B_i + u(t), \qquad B_i(t) = B_i(0)e^{-\lambda t} + \frac{u}{\lambda}\left(1 - e^{-\lambda t}\right), \qquad B_{ss} = \frac{u}{\lambda},
```

and a PPP controller

```math
\mu(t) = (1 + \pi^{*e})(1 + \dot{T}^e) - 1, \qquad \frac{dM}{dt} = N u - \lambda M = \mu M \ \Rightarrow\ u = \frac{(\lambda + \mu) M}{N}.
```

**Critical review.** These formulas assume continuous new issuance, and that demurrage is destroyed ($`-\lambda M`$) and applies to the whole balance. TCL differs on all three:

- Supply is fixed: fees move to $`\mathcal{V}`$ and are not destroyed, so $`\Delta M = 0`$ (§9.3).
- Decay applies only to the surplus above $`T`$.
- There is no issuance.

So the PPP rule **conflicts with the current fixed-supply design** and is not adopted. UBI is presented only as an **optional extension that governance could enable**. If enabled, it would be paid from the redistribution share of $`\mathcal{V}`$ without issuance, $`N u \le (1-\phi)(F_e + \Pi_e)/\Delta t_e`$, per personhood credential. Implemented constants ($`r_d = 0.0005`$, $`T`$, $`\lambda`$) are unchanged. The report's suggested demurrage path (about 1%/yr, then 5–10%/yr) is an open question.

### 10.8 Rollout (from research, adapted)

1. **Phase 1:** credential aggregation, multisig plus timelock backstop (Stage 1 of §10.4).
2. **Phase 2:** non-biometric PoP fusion, plural conviction voting, and governance as owner (Stage 2).
3. **Phase 3:** immutable core (Stage 3), optional PPP controller and futarchy with citizen veto.

### 10.9 Tiered personhood and Sybil unprofitability (owner-confirmed design)

**Tiers.** A personhood credential at tier $`n`$ needs $`v_n`$ vouches, with $`v_1 = 3`$ and $`v_{n+1} \gt v_n`$, for example $`v = (3, 6, 12, 24)`$. Vouches only count when the vouchers are graph-independent. Each voucher's weight is the plural-discount weight from §10.6:

```math
\mathrm{eff}_n(i) = \sum_{j \in \mathrm{vouchers}(i)} W_j^{(i)} \ge v_n, \qquad W_j^{(i)} = \frac{1}{\sqrt{1 + \sum_{l \in \mathrm{vouchers}(i),\, l \ne j} \sigma(j,l)}} .
```

Each tier unlocks a per-window limit on transfers and deposits, under the person's pseudonym $`\nu`$ for window $`W_a`$:

```math
\sum_{\text{tx} \in W_a} \mathrm{amt}(\text{tx}) \le L_n, \qquad L_1 \lt L_2 \lt \cdots .
```

The base tier, $`L_1`$, covers everyday small transactions.

**Funneling detection.** For a recipient pseudonym or cluster $`r`$ in window $`W_a`$, let $`f_{i \to r}`$ be the flow from sender $`i`$, and let $`\mathcal{L}`$ be the set of tier-1 senders. The share of tier-1 senders' outflow that goes to $`r`$ is

```math
c_r = \frac{\sum_{i \in \mathcal{L}} f_{i \to r}}{\sum_{i \in \mathcal{L}} \mathrm{out}_i}, \qquad m_r = \big|\{ i \in \mathcal{L} : f_{i \to r} \gt 0 \}\big| .
```

$`r`$ is flagged when $`m_r \ge m^*`$ and the flow from those senders is concentrated on it ($`c_r \ge c^*`$, and the senders send most of their outflow to $`r`$). Flagged flows count toward $`r`$'s own tier limit and its hoarding aggregate. They are summed under $`r`$'s pseudonym for $`L_{n(r)}`$, for $`T`$ in §2 and for $`\Theta`$ in §6:

```math
A_r \leftarrow A_r + \sum_{i \in \mathcal{L}} f_{i \to r} \cdot \mathbb{1}[\text{flagged}] .
```

Splitting funds across fake identities and funneling them back therefore gains nothing against the demurrage threshold or the re-entry rules.

**Vouch bonds and honesty rewards.** Each vouch locks a bond $`B_v`$. If the vouchee is proven Sybil, through funneling evidence, ceremony failure or a dispute, every voucher of that identity loses $`B_v`$. That bond goes to the vault. A voucher whose vouchee stays in good standing earns $`r_v`$ per window from the incentive pool, capped by the pool share for vouch rewards.

**Bounded damage.** In a single window, $`k`$ fake tier-1 identities can move at most

```math
D_{\max} = k \cdot L_1 .
```

That is all three colluding vouchers can extract per window, and funneling detection makes it count against the recipient anyway.

**Unprofitability condition.** Write $`q \in [0,1]`$ for the fraction of $`L_1`$ an attacker can actually capture per window (UBI, threshold evasion or the like), $`w_d`$ for the expected number of windows before detection, $`c_{\mathrm{id}}`$ for the cost of creating one identity (ceremony attendance plus graph infiltration), and $`p_s`$ for the probability of being slashed. Each fake identity uses $`v_1 = 3`$ vouches. The expected gain is

```math
\mathbb{E}[G(k)] = k \Big( q\, L_1\, w_d - c_{\mathrm{id}} - p_s\, v_1 B_v \Big) \lt 0
\iff
B_v \gt \frac{q\, L_1\, w_d - c_{\mathrm{id}}}{p_s\, v_1} .
```

Because $`\mathbb{E}[G]`$ is linear in $`k`$, the same inequality makes every scale of attack unprofitable. Parameter choice: fix $`L_1`$ from everyday payment needs, then set $`B_v`$ above the bound using conservative values (for example $`q = 1`$, $`c_{\mathrm{id}} = 0`$). Honest vouching must still pay, so require

```math
r_v \cdot \mathbb{E}[\text{good-standing windows}] \gt (1 - p_{\mathrm{honest\ ok}})\, B_v ,
```

meaning an honest voucher's expected reward exceeds their expected loss from being deceived. Higher tiers raise the attack cost to $`v_n B_v`$ per identity, while the extractable value $`L_n`$ grows more slowly. This requires $`L_n / v_n`$ to be non-increasing in $`n`$.

### 10.10 Incentivized reporting (fraud bounties) (owner-requested)

**Reportable offences.** A report must carry evidence that can be verified on chain, so the contract or a circuit can check it without trusting anyone:

| Offence | Evidence |
| --- | --- |
| Bribe offer | Bribe message signed by the briber's key, binding a proposal and a vote |
| Double deposit | Two valid deposit authorizations for the same $`(\mathsf{id}, k)`$, or conflicting nullifiers |
| Fake-identity vouch | A vouch for an identity later proven Sybil (§10.9) |
| Invalid proof | A proof submission that fails verification, or a proof of a false statement |

**Bonds.** The offender has a bond at stake, $`b_o`$ (a vouch bond $`B_v`$, prover bond or minter bond). The reporter posts a bond $`b_r`$, which is forfeited to the vault if the report proves false. A verified report pays

```math
R = \min\!\big(\sigma\, b_o + \tau,\ b_o\big), \qquad \sigma \in (0, 1),\ \tau \le \tau_{\max} \ (\text{capped pool top-up}),
```

from the offender's slashed bond. Any remainder $`b_o - R`$ goes to the vault.

**Reporter's choice.** Let $`p`$ be the probability that a report is verified and paid, and $`\mathcal{B}`$ a bribe offered for silence. The reporter defects from the scheme, and reports, exactly when

```math
p\,R - (1-p)\,b_r^{\mathrm{false}} \gt \mathcal{B},
```

where $`b_r^{\mathrm{false}} = 0`$ for a truthful report backed by valid evidence, since $`p \to 1`$. For truthful reports this reduces to $`p R \gt \mathcal{B}`$. False reports have expected value $`-b_r \lt 0`$.

**Self-reporting is closed.** An offender who reports themselves, or through a sock-puppet, loses $`b_o`$ and receives $`R \le b_o`$, and the exploit is reversed on proof. The net is

```math
\Pi_{\mathrm{self}} = R - b_o \le 0 .
```

**Equilibrium.** An exploit with gain $`G`$ has $`m`$ accomplices, or holders of the evidence, each of whom reports independently with probability $`p_a`$. The chance of detection is $`\pi = 1 - (1 - p\,p_a)^m`$, and the offender's expected payoff is

```math
\mathbb{E}[\Pi_o] = (1 - \pi)\,G - \pi\,b_o \lt 0 \iff b_o \gt \frac{(1-\pi)}{\pi}\,G .
```

The first accomplice to report keeps their own bond (leniency), giving up their share $`g_a`$ of the exploit:

```math
U_{\mathrm{report}} = p\,R + b_a \quad\text{vs}\quad U_{\mathrm{silent}} = (1-\pi_{-a})\,g_a - \pi_{-a}\,b_a .
```

With $`R \ge g_a`$, and $`b_a`$ at stake, reporting dominates staying silent. The briber can only outbid this by paying $`\mathcal{B} \gt p R`$ to *every* accomplice, which raises the cost of the exploit above $`m p R`$. Collusion between the offender and the reporter cannot profit either: their joint net is $`R - b_o \le 0`$.

**Link to tiered personhood (§10.9).** A vouch bond $`B_v`$ is an offender bond $`b_o`$. Reporting a vouch for a proven-fake identity raises the slashing probability $`p_s`$ in §10.9's unprofitability bound, which lowers the bond needed, $`B_v \gt (qL_1w_d - c_{\mathrm{id}})/(p_s v_1)`$.

**Limitation.** Bribes made entirely off chain, leaving no signed evidence, cannot be caught by bounties. They are handled by MACI receipt-freeness (§10.2): a voter can always rotate keys and re-vote, so a briber can never verify compliance.

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
| Symmetric-key forgery | Extract the NTAG AES key from a verifier app | Asymmetric SE only for offline checks (§4.6) | None if enforced |
| Nonce-based multi-deposit | Free nonce in $`N_{\mathrm{dep}}`$ | Deterministic era nullifier (§4.6) | None |
| Kiosk custody | A kiosk sees the user's identity secret | Card-side proving; kiosk only relays (Q10) | Kiosk metadata |

---

## 12. Decisions (owner-confirmed, 2026-10-23)

These decisions were locked by the owner and supersede the corresponding rows in §12.1.

**1. Genesis distribution.** 70% of the fixed supply is airdropped equally to verified persons. The other 30% is sold by auction, and the proceeds fund physical-note manufacturers and initial bootstrapping. With $`N_0`$ verified persons at genesis and fixed supply $`M`$:

```math
a_{\mathrm{person}} = \frac{0.7\,M}{N_0}, \qquad M_{\mathrm{auction}} = 0.3\,M .
```

No genesis exemption flag is needed, because §6 has no holding penalty.

**2. No protocol transaction fees.** The incentive pool is funded only by demurrage and re-entry penalties:

```math
\Delta I_e = \phi\,(F_e + \Pi_e).
```

**3. Optional per-person UBI, behind a governance gate.** UBI is paid from the demurrage pool only after governance judges personhood proofs secure:

```math
u_e = \mathbb{1}[G_{\mathrm{UBI}}]\cdot\frac{(1-\phi)(F_e + \Pi_e)}{N_e}.
```

$`G_{\mathrm{UBI}}`$ is a proposal that must pass §10.6 plural conviction voting with the constitutional supermajority (more than 66%) and the 30-day timelock. It must also be backed by a published Sybil-resistance assessment of §10.5. It can be reversed by the same process.

**4. Maximum denomination of 500 face value.** The series is 5, 10, 20, 50, 100 and 500. *Verification task (not a blocker):* confirm that the per-copy clone cost of an asymmetric-SE note exceeds the value one clone can earn, $`K_{\mathrm{marginal}} \gt n_{\max} \cdot 500`$ (§7.5–§7.6).

**5. Governance.** Conviction voting uses a 7-day half-life, $`\alpha = 0.9057`$ per day ($`t_{1/2} = \ln 0.5/\ln\alpha \approx 6.998`$ d), with the normalized update so that conviction builds gradually:

```math
Y_{i,p}(t) = \alpha\,Y_{i,p}(t-1) + (1-\alpha)\,W_i\,S_{i,p}(t).
```

Voting is one anonymous person, one vote via ZK personhood. Biometrics are optional.

**6. Personhood (tiered).** The base tier needs 3 vouches from existing trusted users and carries a per-window limit $`L_1`$ for small transactions. Higher tiers need more graph-independent, plural-discounted vouches and unlock higher limits $`L_n`$. Funneling of low-tier flows toward one recipient is detected and aggregated under that recipient. Vouches are bonded: dishonest vouchers are slashed, and honest ones earn small rewards. City kiosks act only as relays for cards the user builds; they never see the user's secret. **Bounded damage:** $`k`$ colluding-vouched identities can move at most $`k \cdot L_1`$ per window, and $`B_v`$ is set so that Sybil creation has negative expected value. Re-attestation every 6 months still applies. See §10.9.

### 12.1 Research assessment (Gemini recommendations, for the record)

Source for every "Gemini rec" entry: Gemini deep research (user-supplied, 2026-10-23, master), Part E. Assessment key: **A** = agree, **M** = modify, **R** = reject.

| # | Question | Gemini recommendation | Assessment | Proposed decision | Owner input? |
| --- | --- | --- | --- | --- | --- |
| 1 | Fast decay vs normal decay | Multiplicative surplus decay, idle above 180 d, $`\delta_a = 0.001`$ | **M**: the "negative balance" rationale is wrong (§6.6); idle-only ignores velocity | Keep §6.4 velocity-weighted exponential decay on a separate bucket, surplus only | No |
| 2 | Velocity and wash transfers | 12 h hold delay plus distinct-path ZK check | **M**: UTXO wording does not fit accounts | 12 h $`\Delta t_{\min}`$ plus a private "not in last $`k`$ counterparties" proof | No |
| 3 | Personhood floor | 10,000 TCL | **A** | $`\theta_{\min} = 10^4 S`$ | No |
| 4 | Genesis distribution | 70% PoP airdrop and 30% auction; no penalty exemption | **M**: the exemption is moot because §6 has no holding penalty; the auction needs a counter-asset and price-setter | 70/30 split as proposal; no exemption flag needed ($`g`$ kept for audit only) | **Yes** (split, auction) |
| 5 | Offline staleness | $`T_{\max} = 30`$ d or 1,000 taps | **A**: bounds clone chains | Wallet warns past $`T_{\max}`$; SE refuses past 1,000 taps without sync | No |
| 6 | Denominations | 5–500, $`V_{\max} = 500`$ | **M**: clone-cost argument uses a per-design cost, not the marginal cost (§7.6); mixes TCL and USD | Adopt the set provisionally; confirm $`V_{\max}`$ after a marginal-cost estimate | Yes (confirm) |
| 7 | Proof-of-work | Drop it | **A** | $`\delta = 0`$ permanently | No |
| 8 | Proof batching | Nova or Plonky2 aggregation in a Groth16 wrapper, about 220k gas | **A**: plausible gas, verify on Anvil | Roadmap; current circuit unchanged | No |
| 9 | Emission and split | 100% demurrage plus 50% tx fees; 20/80 with queue-latency controller | **M**: TCL has no protocol tx fee; Part C controller is unbounded | Fund vault from demurrage and penalties; bounded $`\alpha(L)`$ from §9.4 | **Yes** (add a protocol fee?) |
| 10 | Personhood cadence and access | Re-attest every 6 months; $`k \ge 3`$ peer vouches; municipal kiosks | **M**: 3 colluders can vouch endless Sybils; a kiosk "processing the ZK proof on their behalf" sees the secret; "municipal" implies state custody | 6-month re-attestation; $`k \ge 3`$ **and** ceremony; community-run kiosks that relay only (proof generated on the user's card) | Yes |
| 11 | UBI and demurrage path | Keep about 17%/yr above 5M; 80% of net demurrage as equal UBI | **A** with caveat: no issuance, fits fixed supply; "16.78%" is actually 16.69%; UBI raises Sybil stakes | Optional, governance-enabled UBI with $`1 - \phi = 0.8`$; constants unchanged | **Yes** (adopt UBI?) |
| 12 | Conviction, Sybil, biometrics | $`\alpha = 0.9057`$/day ($`t_{1/2} = 7`$ d, checked: 6.998 d); token-weighted $`D(t)`$; $`\mathcal{O}(k^2)`$ only with MACI plus pairwise; "hardware required due to biometric bans" | **M/R**: unnormalized update again (§10.6); token weight conflicts with one person, one vote; threshold form differs from the earlier report (not squared); $`\mathcal{O}(k^2)`$ still asserted, not derived; biometrics conflict with anonymity, and the ban rationale is regulatory content (R7) | Normalized update, person credits only, squared threshold; biometrics optional (§10.5); half-life 7 d proposed | Yes (half-life) |

**Conviction check.** $`t_{1/2} = \ln 0.5 / \ln 0.9057 \approx 6.998`$ days, which is correct. With the normalized update, one day of support reaches $`1 - \alpha \approx 9.4\%`$ of steady state.

**Sybil check.** Splitting $`V`$ credits across $`k`$ identities under quadratic voting gives $`k\sqrt{V/k} = \sqrt{kV}`$, which is correct and shows splitting pays. The report's "resistance $`\mathcal{O}(1/k)`$" is loose wording. The claim $`\mathrm{Cost}_{\mathrm{Sybil}}(k) = \gamma k^2 + \delta k`$ is still asserted without derivation.

## 13. Open questions (still unresolved)

1. Sybil-attack cost (Q12): addressed by the tiered design (§10.9); formal parameterization pending, covering $`L_n`$, $`v_n`$, $`B_v`$, $`r_v`$, $`c^*`$, $`m^*`$ and detection-rate estimates.
2. Verification of the per-copy clone cost of an asymmetric-SE note against the 500 face-value cap (Q6).

## 14. Research received

| Topic | Feeds sections | Status |
| --- | --- | --- |
| Reusable physical bearer notes | §4.6, §11 | **Received** (master, Part A) |
| Demurrage history | §5.1 | **Received** (master, Part B) |
| Mint and mine incentives | §7.6, §8.1, §9.4 | **Received** (master, Part C) |
| Hardware and manufacturing | §4.6, §7.6 | **Received** (master, Part D) |
| Personhood and governance | §10.5–§10.8, §12 | **Received**: Gemini deep research (user-supplied, 2026-10-23) and master Part E |

**Source quality.** Several works cited in the master report are secondary or low quality and are not relied on here, including accio.com, chainscorelabs.com, coinlive.com, mikkoikola.com, okx.com, binance.com and forklog.com. Claims resting on them are marked †. Better primary candidates appear in the report's own list, such as NXP AN12196, the Baldimtsi et al. transferable e-cash paper, the Plonky2 blog and the CNPD deliberation. The regulatory-ban details in Q12 are not reproduced (R7).
