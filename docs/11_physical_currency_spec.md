# 11 — Physical Currency, Minting, Mining and Governance Specification

> **In plain words.** This is the full design for TCL cash. Notes pass hand to hand with no internet, like bills. They never shrink while they are paper. Anyone may make (*mint*) notes by turning digital TCL into them, and earns a small reward for it. When someone deposits a very large amount of notes at once, the excess above 5,000,000 TCL is unlocked slowly, and if it then sits unused it decays faster. That penalty goes to the shared vault. The design also covers voting rules where each real person gets one vote, not one vote per coin, so the rich cannot buy control. Most of this page is a **design, not built code**; see doc 12 for the small part that is built. Terms are in the [Glossary](GLOSSARY.md).
>
> **What this means for you:** if you use notes like normal cash, nothing here charges you. Only huge, idle piles brought back online are slowed down and charged.

> [!WARNING]
> **DRAFT — MOSTLY NOT IMPLEMENTED.** This document is a design specification under discussion. Only a small testnet MVP of it exists in `main` (mint, deposit, re-arm and gradual unlock; see [`12_physical_notes_mvp.md`](12_physical_notes_mvp.md)). Everything else described here is not built. Formulas, parameters and default values are proposals and will change after owner review of the research-driven decisions (see [§12](#12-decisions-owner-confirmed-2026-10-23)). Where this draft conflicts with code, the code is authoritative for what currently runs.

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

Appendix A. [Rejected alternatives and why](#appendix-a-rejected-alternatives-and-why)

---

## 1. Scope and design goals

**In plain words.** This section lists the goals the owner set. Every rule in this document must meet them. Some words used here:

- *Bearer cash* means whoever holds the note owns it, like a dollar bill.
- *Fiat* is ordinary government money.
- *Denominations* are the fixed values printed on notes, like a $5 or $20 bill.
- *Re-entry* means putting a note back into a digital account.
- *Minting* means making a new note. *Burning* means destroying digital money so it can't be spent.
- *Mining* here means earning rewards for doing useful computer work for the network. *Proof-of-work* is a kind of mining where computers race to solve puzzles; it is optional and was later dropped.
- *Plutocratic capture* means rich people taking control. *Governance* is how rules get changed.

The owner-confirmed requirements this draft must meet:

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

**In plain words.** This section lists the symbols used in the formulas and where their values come from in the code.

All amounts are whole numbers in tiny units. One TCL token is $`S = 10^{18}`$ *base units* (a 1 with 18 zeros). This is like counting cents instead of dollars, only much finer. It is called *fixed point*. The value lives in `SCALE` in `src/tcl/ledger/demurrage_fixed.py` and `RATE_SCALE` in `contracts/CirculatoryEngine.sol`.

Terms in the table below: the *threshold* is the protected amount that never decays. The *surplus* is the part above it. The *vault* is the shared pot where decay goes. The *state root* is a short code that acts as the fingerprint of the whole account book (built with a *Merkle tree* using the *Poseidon* hash over the *BN254* number field). A *nullifier* is a one-time code; once spent, it can never be used again. A *hash* is a data blender that turns any input into a fixed-size code. A *domain tag* is a fixed first number fed into the hash, so codes made for different purposes can never be confused.

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

Today's digital decay works like this. Take a balance $`b`$. Let $`\Delta t`$ seconds pass. If the balance is above the threshold, only the surplus shrinks, smoothly over time. If it is at or below the threshold, nothing happens:

```math
b' = T + (b - T)\,e^{-\lambda \Delta t} \quad \text{for } b \gt T, \qquad b' = b \quad \text{for } b \le T,
```

The amount lost, the fee $`f = b - b'`$, goes to the vault $`\mathcal{V}`$.

New symbols this draft adds. *Supply* means the total amount in a form: $`P`$ is all money in physical notes, $`D`$ is all digital money (including the vault), and $`I`$ is the *incentive pool*, a shared fund that pays rewards. An *era* is how many times a note has come back online.

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

**In plain words.** An earlier attempt at physical notes exists in this project. This section says what we keep from it and what must change.

The earlier work is on a branch (a side copy of the code) that was never merged: `origin/feat/executable-protocol-v02-20261021`. It contains `circuits/puf_verify.circom` (`PufVerify`), `contracts/PhysicalMintController.sol`, `contracts/production/ProductionMintController.sol`, `ledger/nullifiers.py` and `docs/04_physical_mint.md`. (Note: `PhysicalMintController.sol` has since been deleted from `main`.)

It used a *commitment*: a sealed code that locks in a note's details without showing them, like a sealed envelope. It also used a nullifier. Its formulas are:

```math
\mathsf{cm} = H(1,\ \mathsf{secret},\ \mathsf{blinding},\ \mathsf{value},\ \mathsf{context}), \qquad
\mathsf{nf} = H(2,\ \mathsf{secret},\ \mathsf{cm},\ \mathsf{context},\ \mathsf{recipient}),
```

The *public signals* (values everyone sees) are $`(\mathsf{cm}, \mathsf{nf}, \mathsf{value}, \mathsf{recipient}, \mathsf{context})`$. *Range checks* make sure `value` fits in 128 bits and `recipient` fits in 160 bits (the size of an account address).

What we keep:

- Poseidon commitments with domain tags.
- Nullifiers that stop *replay* (cashing the same thing twice).
- *Recipient binding*: the proof names who gets the money. That stops *front-running*, where someone copies your pending redemption and sends it first to steal the money.

What must change:

1. **It was single-use.** A note was locked once and cashed once. After that it was dead forever. That breaks R1, which says notes are reusable.
2. **It proved knowledge, not that the note was real.** The circuit's own comment says it "proves knowledge of an enrolled, reconstructed secret, not physical unclonability". In other words, anyone who learns the `secret` number can act as the note. A copy would work just as well as the real thing.
3. **It let people dodge demurrage.** The branch admits notes were a way to escape the holding fee. §6 replaces this with a clear rule for re-entry.

---

## 4. Reusable bearer notes: identity, lifecycle and nullifiers

### 4.1 Physical construction (proposed)

**In plain words.** Each note would have a tiny chip and a special pattern that can't be copied.

Each note carries two things:

1. A *secure element* (SE). This is a small tamper-resistant chip, like the one in a bank card. It holds a secret *signing key* $`sk_{\mathsf{id}}`$ that can never be read out (*non-exportable*). A signing key lets the chip produce a *signature*: a code that proves a message came from this chip. The chip also has a *monotonic counter*, a number that can only go up.
2. A *PUF* (physical unclonable function). This is a random physical pattern, like scattered specks in the paper (*optical*) or a coating over the chip. Each one is unique and can't be copied. Reading it gives a response $`\rho`$. The PUF ties the chip to the paper it sits in (the *substrate*). If someone tampers with the paper, $`\rho`$ changes.

The final hardware choice still needs research (§13).

### 4.2 Persistent identity

```math
\mathsf{id} = H\big(10,\ pk_{\mathsf{id}},\ H(\rho),\ d,\ \mathsf{serial}\big)
```

**In plain words.** Each note gets a permanent ID, like a serial number. It is made by hashing the chip's public key ($`pk_{\mathsf{id}}`$, the shareable partner of its secret key), the hashed PUF response, the denomination $`d`$ and a serial number.

The $`\mathsf{id}`$ never changes while the note exists, no matter how many people hold it. The ledger keeps one entry (*leaf*) per note:

```math
\mathsf{leaf}_{\mathsf{id}} = H\big(11,\ \mathsf{id},\ k,\ \sigma,\ t_{\mathrm{mint}},\ g\big), \qquad \sigma \in \{\textsf{LIVE},\ \textsf{DORMANT}\},
```

Here:

- $`k`$ is the era counter.
- $`\sigma`$ is the note's state. LIVE means it can be spent as cash. DORMANT means it has been deposited and is switched off.
- $`t_{\mathrm{mint}}`$ is when this era started.
- $`g \in \{0,1\}`$ is 1 for *genesis* notes, those handed out in the first distribution, and 0 otherwise.

### 4.3 Offline hand-to-hand transfer

**Whoever holds the note owns it**, just like cash. Handing it over is not recorded anywhere and needs no internet. The person receiving it can check it is real, offline, in three steps:

1. **Challenge and answer.** The receiver's phone sends the note a random one-time number, a *nonce* $`c`$. The chip signs that number together with its era and state: $`\mathrm{Sig}_{sk_{\mathsf{id}}}(c \,\|\, k \,\|\, \sigma)`$. A fresh nonce each time means an old recorded answer can't be reused.
2. **Check it's on the list.** The phone checks the chip's public key $`pk_{\mathsf{id}}`$ against a saved list of LIVE note IDs, updated now and then when online. Or the chip shows a *Merkle inclusion proof*: a short proof that its leaf is in a recent account-book fingerprint $`\mathcal{R}`$.
3. **Check the pattern.** The phone camera checks the PUF pattern.

No one's identity is recorded, so holders stay anonymous (R1).

### 4.4 Re-entry (deposit) and the nullifier rule

> **In plain words.** When you deposit a note, its chip switches it off (DORMANT) and the system marks that note's current one-time code as spent. The money lands in your account. The note itself is not destroyed: it can be switched back on later with a new code (a new *era*). So each era can be cashed in exactly once.

Step by step:

1. The holder deposits the note into account address $`a`$.
2. The chip signs a one-time permission to cash it (*redemption authorization*).
3. In the same instant (*atomically*), the chip switches itself to DORMANT and raises its counter. So a deposited note visibly stops working as cash.
4. The system computes the note's one-time code for this era, the *re-entry nullifier*. Here $`\mathsf{ctx}`$ (*context*) ties the code to this chain and this contract:

```math
\mathsf{nf}_{\mathsf{id},k} = H\big(12,\ \mathsf{id},\ k,\ \mathsf{ctx}\big).
```

5. A zero-knowledge proof shows three things:
   - The note's leaf $`\mathsf{leaf}_{\mathsf{id}}`$ is in the account book $`\mathcal{R}`$, marked LIVE at era $`k`$.
   - The permission is a valid chip signature over $`(\mathsf{id}, k, a, \mathsf{ctx})`$: this note, this era, this account, this chain.
   - The code $`\mathsf{nf}_{\mathsf{id},k}`$ is not already on the spent list: $`\mathsf{nf}_{\mathsf{id},k} \notin \mathcal{N}`$.
6. The records are updated. The code goes on the spent list. The leaf moves to era $`k+1`$, DORMANT. The account $`a`$ gains the face value $`V_d`$:

```math
\mathcal{N} \leftarrow \mathcal{N} \cup \{\mathsf{nf}_{\mathsf{id},k}\}, \quad
\mathsf{leaf}_{\mathsf{id}} \leftarrow H(11,\ \mathsf{id},\ k{+}1,\ \textsf{DORMANT},\ t_{\mathrm{mint}},\ g), \quad
b_a \leftarrow b_a + V_d .
```

**Each deposit gets its own one-time code, not each note.** Each era $`k`$ has exactly one nullifier. So each era can be cashed once. But the note itself is never destroyed and can come back for another era.

### 4.5 Recommended design: re-arm the same note (fresh era), not a new commitment

**In plain words.** After a note is deposited, what happens to the physical note? We looked at two choices:

| | Option A: re-arm the same note | Option B: retire and issue a fresh commitment |
| --- | --- | --- |
| Physical object | Same note goes back into circulation | Note shredded or retired; new note minted |
| Ledger | Same $`\mathsf{id}`$, new era $`k{+}1`$, fresh leaf | New $`\mathsf{id}'`$ and leaf |
| Cost | Only the burn of $`V_d`$ (§7) | Full production cost again |
| Linkability | Era counter links the eras of one note | No link between old and new note |

(*Linkability* means whether someone could tell two eras belong to the same note.)

**We recommend Option A: re-arm the same note.** Step by step:

1. Someone wants to put a DORMANT note back into use. They do a mint event (§7).
2. That burns $`V_d`$ of their digital money and sets the leaf to $`\mathsf{leaf}_{\mathsf{id}} \leftarrow H(11, \mathsf{id}, k{+}1, \textsf{LIVE}, t_{\mathrm{now}}, 0)`$: new era, LIVE, new start time.
3. The chip only accepts the switch back on if it gets a receipt for era $`k{+}1`$, signed by the ledger or backed by a proof.

Why Option A:

- It reuses sturdy hardware. That is the whole point of reusable cash.
- The era counter makes it impossible to replay an old era.
- Only the person who re-armed it and the person who deposited it could be linked to it. The people who held it in between never can.

Option B is still used for worn or damaged notes.

### 4.6 Research integration (Part A)

**In plain words.** The owner supplied an outside research report (Gemini deep research, 2026-10-23, master report, Part A). This section sums up its hardware findings and says where we agree or disagree. Claims marked † are not yet confirmed by original sources.

**Hardware survey, as summed up from the report.** (*AES-128 CMAC* is a check code made with a shared secret key. *Asymmetric* chips use a key pair: a secret key that signs and a public key anyone can check with. *CBDC* means central bank digital currency. *Decapping* means opening a chip's case to probe it. A *fuzzy extractor* turns a slightly noisy reading into the same exact key every time.)

- Optical PUFs: random light-scattering particles in the substrate, read with a phone flash.
- Coating PUFs: a particle matrix over the chip that is destroyed by decapping.
- SRAM PUFs: power-up state of the chip's memory; need a fuzzy extractor.
- RF PUFs: antenna impedance signatures.
- NXP NTAG 424 DNA: AES-128 CMAC "SUN" messages with a tap counter, about USD 0.20–0.35.
- Asymmetric secure elements (NXP SE050, Infineon SLE78): about USD 1.10–1.80.
- Prior art: transferable e-cash (Chaum; Brands; Okamoto–Ohta; Baldimtsi et al.) and hardware-enforced offline CBDC pilots (ECB digital euro offline, BIS Project Polaris, e-CNY hardware wallets).

The report suggests three things. A commitment $`C_{\mathrm{note}} = H(\mathsf{NoteID}, S_{\mathrm{secret}})`$. A deposit nullifier $`N_{\mathrm{dep}} = H(S_{\mathrm{secret}}, \mathsf{DepositNonce})`$. And re-arming by writing a new secret $`S'`$ into the chip.

**Assessment:**

1. **Mistake: the report's nullifier allows cashing a note twice.** The deposit nonce $`\mathsf{DepositNonce}`$ can be any number the depositor picks. So one note could produce endless different codes, and each would look unspent. A nullifier must always come out the same for the same note and era (*deterministic*). So this spec keeps $`\mathsf{nf}_{\mathsf{id},k} = H(12, \mathsf{id}, k, \mathsf{ctx})`$ (§4.4). We do adopt the report's idea of changing the secret ($`S \to S'`$) as an extra option, *on top of* the era counter. Then the leaf becomes $`H(11, \mathsf{id}, k, \sigma, t_{\mathrm{mint}}, g, H(S_k))`$.
2. **Mistake: the cheap NTAG 424 DNA chip can't be checked offline by strangers.** Its check codes (SUN/CMAC) are *symmetric*: the same key makes and checks them. So a phone could only check offline if it held the key. But anyone with the key could make fake notes. **This spec requires an asymmetric chip** (SE050/SLE78 class) for offline checks. NTAG-class chips are fine only for online checks.
3. **The re-arm secret can't be sent by the contract.** Everything a public blockchain contract (on the *EVM*, Ethereum's contract engine) sends is visible to all. So it can't send a secret "encrypted re-arming payload." The new secret must be made *on the chip* (or on the minter's device). Only its hash $`H(S')`$ is published.
4. **Still unconfirmed (†):** that the SE050 supports BN254 math; that optical PUFs hold "$`\gt 10^6`$ bits/mm²" of randomness (*entropy*); and the figures for what cloning a note costs.

---

## 5. No demurrage while physical

**In plain words.** A note never shrinks while it is paper. A 100 TCL note is worth 100 TCL whether you hold it a day or ten years.

While the note is LIVE ($`\sigma = \textsf{LIVE}`$), it keeps its face value $`V_d`$ the whole time:

```math
\mathrm{value}_{\mathsf{id}}(t) = V_d \qquad \forall\, t \in [t_{\mathrm{mint}},\ t_{\mathrm{reentry}}].
```

There is no fee for holding notes offline. There is no fee based on how long you held a note. The anti-hoarding rules only look at what you do *after* you deposit (§6).

**What this means for you:** cash in your drawer keeps its full value.

### 5.1 Research integration (Part B): rejected holding-duration penalty

**In plain words.** History has tried money that loses value while you hold it. The report suggested a penalty based on how long you held a note. We rejected it.

Part B of the report looks at past experiments:

- Silvio Gesell's *Freigeld* ("free money"), a 1916 idea for money that loses value over time.
- The town of Wörgl, Austria (1932–33). It used *stamp scrip*: local notes that needed a paid stamp each month, costing 1% a month, to stay valid.
- The Wära experiments in Germany, and US stamp scrip in the 1930s.

Reported Wörgl results were about 20 times faster spending (*velocity*), 35% more back taxes paid, and 25% less unemployment. These are † and need original sources. One clear lesson: buying and sticking on stamps by hand was a hassle. That supports §5: no decay at all on paper notes.

The report then suggests a penalty at deposit, based on how long the note was held. Here $`\tau_{\mathrm{hold}}`$ is the holding time and $`\tau_{\mathrm{grace}}`$ is a free period:

```math
\Phi(V, \tau_{\mathrm{hold}}) = V\left[1 - e^{-\lambda \max(0,\ \tau_{\mathrm{hold}} - \tau_{\mathrm{grace}})}\right], \quad \lambda = 0.00045\ \mathrm{day}^{-1},\ \tau_{\mathrm{grace}} = 90\ \mathrm{d}.
```

**Rejected.** Two reasons:

1. It is really demurrage on paper notes with a new name. That breaks R2. The owner was clear: holding cash is free, and only what you do *after* depositing can be charged.
2. It applies to every note, with no threshold. So it would hit ordinary people with small amounts.

We keep §6 instead.

**Checking the report's math.** The report says 0.05% a day ($`r_d = 0.0005`$) is "≈16.78% a year." That is slightly off. The right figure is $`1 - (1 - 0.0005)^{365} \approx 16.69\%`$. With smooth (*continuous*) decay it is $`1 - e^{-0.1825} \approx 16.68\%`$. Apart from that, its digital decay formulas match the code's rule: only the surplus above $`T`$ decays.

---

## 6. Anti-hoarding at re-entry (hybrid design)

> **In plain words.** If one person brings a very large amount back online within 30 days, only the part above 5,000,000 TCL is affected. That excess unlocks a little each day over 30 days, so nobody can dump a huge pile on the market at once. If the person then passes the money on, they pay only the normal rate. If they let it sit, it decays faster, up to 10 times the normal rate. Every penalty goes to the shared vault.
>
> **Example (default parameters).** You deposit notes worth 8,000,000 TCL in one month. The excess is 8,000,000 − 5,000,000 = 3,000,000 TCL. After 10 days, 5,000,000 + 3,000,000 × 10/30 = 6,000,000 TCL is unlocked. After 30 days it is all unlocked. If you spend none of the excess, after the 7-day grace period the extra decay ramps up: 45 days past grace, the rate is 1 + 9 × 45/90 = 5.5 times normal; after 90 days past grace it is 10 times normal. If you pass it all on, it stays at 1 times normal.
>
> **What this means for you:** deposits up to 5,000,000 TCL a month are never touched by these rules.

### 6.1 Re-entry bucket

All deposits by one **person** are added up, not by account address (see §6.5). They are added over a *rolling window* $`W_a`$: the last 30 days by default, always counting back from today. Call that total $`A`$. Only the part above the threshold, the *excess* $`E`$, is affected:

```math
E = \max(0,\ A - \Theta), \qquad \Theta = 5\,000\,000 \cdot S \ \text{(configurable)}.
```

If $`A \le \Theta`$, nothing in this section applies.

Otherwise the excess goes into a personal *bucket* $`B(t)`$. At the start time $`t_0`$, the bucket holds $`E`$, so $`B(t_0) = E`$. When the person sends money out, it comes out of the bucket first:

```math
B \leftarrow \max(0,\ B - \mathrm{out}).
```

### 6.2 Rate-limited release (anti-dumping)

The excess unlocks in equal daily steps over a *release window* $`W_r`$ (30 days by default). $`U(t)`$ is how much is unlocked at time $`t`$:

```math
U(t) = \Theta + E \cdot \min\!\left(1,\ \frac{t - t_0}{W_r}\right).
```

Locked money can't be sent yet. But it still counts as part of the depositor's balance. This rule stops a huge stockpile from flooding the market all at once (*dumping*).

### 6.3 Outbound velocity

*Velocity* $`v`$ is the share of the unlocked excess the person has already passed on:

```math
v(t) = \frac{E - B(t)}{\max\!\big(S,\ \min(E,\ U(t) - \Theta)\big)} \in [0, 1].
```

$`v = 1`$ means everything unlocked so far has been spent or passed on. $`v = 0`$ means it all sat still (*parked*). The $`\max(S, \dots)`$ part just avoids dividing by zero.

### 6.4 Accelerated, velocity-weighted decay

The bucket decays faster the longer it sits idle and the less it moves. Three settings control this:

- A *grace window* $`g`$ (7 days by default). No extra decay during it.
- The *idle time* $`\tau = t - t_0`$, time since the deposit.
- A *ramp* $`H`$ (90 days by default). After the grace window, the extra decay grows evenly over this many days.

The bucket's decay rate is:

```math
\lambda_h(t) = \lambda \left[ 1 + \kappa \cdot \min\!\left(1,\ \frac{\max(0,\ \tau - g)}{H}\right) \cdot \big(1 - v(t)\big) \right],
```

```math
\frac{dB}{dt} = -\lambda_h(t)\, B(t), \qquad \text{penalty } \pi = \int_{t_0}^{t} \lambda_h(s) B(s)\, ds \ \longrightarrow\ \mathcal{V}.
```

The total penalty $`\pi`$ goes to the vault. The multiplier $`\kappa`$ is 9 by default and may be set from 0 to 20. With $`\kappa = 9`$, fully parked excess decays at up to $`10\lambda`$, ten times the normal rate. Its properties:

- Below threshold ($`A \le \Theta`$), and during the grace window, $`\lambda_h`$ never applies.
- Fully circulated excess ($`v = 1`$) decays at only the normal rate $`\lambda`$.
- The penalty grows with the amount ($`B`$), with idle time (ramp) and with low velocity ($`1 - v`$).
- All penalties go to the redistribution vault $`\mathcal{V}`$ (R3).

The bucket decay runs alongside the normal decay above $`T`$. To avoid charging the same money twice, the balance is split in two: the bucket $`B`$ and the rest $`r`$. Each part decays under its own rule:

```math
b = B + r, \qquad r' = T + (r - T)\,e^{-\lambda \Delta t}\ \text{if } r \gt T \ (\text{else } r' = r), \qquad B' = B\,e^{-\int \lambda_h\, dt}, \qquad b' = B' + r'.
```

How the code computes it: $`\lambda_h S`$ is worked out for each step. It is fed into the same 5th-order Taylor shortcut used for normal decay. (A *Taylor series* adds up a few simple terms to closely match a curve.) Normal steps are at most $`\Delta t \le 604\,800`$ s, 7 days (`MAX_STEP_SECONDS`). The shortcut is only accurate when $`x = \lambda_h \Delta t`$ is small. At $`\kappa = 20`$ and 7 days, $`x \approx 21 \times 0.35 \approx 7.4`$. That is too big. So whenever $`\kappa \gt 1`$, steps are cut to at most $`\Delta t \le 86\,400`$ s (1 day).

### 6.5 Sybil splitting

A *Sybil attack* means pretending to be many people. Someone could split a big deposit across many accounts, so each stays under $`\Theta`$. Here is how that is blocked:

1. **Totals are per person.** Any deposit above a small *floor* $`\theta_{\min}`$ (for example $`10\,000 \cdot S`$, i.e. 10,000 TCL) must include an anonymous *proof of personhood*. That proof shows you are one real person without saying who. It produces a *pseudonym* (a stand-in name) for this 30-day window: $`\nu = H(13,\ \mathsf{personSecret},\ \lfloor t / W_a \rfloor)`$. All your deposits are added up under $`\nu`$. The pseudonym changes each window, so no one can link your windows together or to your real identity.
2. **Small deposits need no proof.** Everyday deposits stay quick and easy. To slip a full threshold past the rule with small deposits, a cheater would need $`\Theta / \theta_{\min} = 500`$ separate people (5,000,000 ÷ 10,000 = 500). Faking 500 people is a cost the personhood layer must make high (§10).
3. **Clues from the notes.** If many notes made by the same minter in the same era are all deposited together, they can be flagged as one group. This is an optional rule of thumb (*heuristic*).

**Today:** the personhood layer is a **mock**, so splitting across accounts is not yet prevented.

### 6.6 Research integration (Q1–Q3)

**In plain words.** How the outside report's answers to questions Q1–Q3 were used.

- **Q1: how should the extra decay combine with normal decay?** The report suggests multiplying two decay factors: $`B_s(t+1) = B_s(t)(1 - \delta_b)(1 - \delta_a \mathbb{1}[t_{\mathrm{idle}} \gt 180\,\mathrm{d}])`$, with $`\delta_a = 0.001`$. It argues that adding rates could make balances go negative. That reasoning is wrong. Adding rates inside *exponential* decay, $`e^{-(\lambda + \lambda_a)\Delta t}`$, can never go below zero. And multiplying is almost the same as adding for small rates: $`(1-\delta_b)(1-\delta_a) = 1 - \delta_b - \delta_a + \delta_b\delta_a`$. So this spec keeps §6.4, which is exponential and can't go negative. We adopt the report's good point that faster decay applies **only to the surplus**. Q1 is settled as "the bucket and the rest decay separately" (the split $`b = B + r`$ in §6.4).
- **Q2: what counts as passing money on?** Adopted: money sent out only counts toward $`v`$ if it was held at least $`\Delta t_{\min} = 12`$ hours, and only if it goes to someone outside the sender's last $`k`$ trading partners (*counterparties*). This stops two friends passing the same money back and forth to fake velocity. The report's wording assumed a coin-by-coin model (*UTXO*). TCL uses accounts instead. So the proof shows the recipient is not in a sealed list of the sender's last $`k`$ partners. Only the sender knows that list.
- **Q3: how big is the floor?** Adopted: $`\theta_{\min} = 10\,000`$ TCL (in units of $`S`$).

---

## 7. Decentralized minting

> **In plain words.** Anyone may make notes. You pay the note's face value in digital TCL. You get back a refund for what it cost to make the note, plus a small reward, both paid from a shared pool that is filled by decay fees. So making notes honestly earns a little money, but no new money is created.
>
> **Example (this spec's bounds).** A 100 TCL note, real cost 3 TCL, reward rate 1%: you pay 100 and get back 3 + 1 = 4, so you burn 96 net. This spec caps the cost refund at 5% of face value (5 TCL here). **Note:** the built MVP allows a cap of up to 20%; see "Unclear points" in the PR. If the epoch's budget is used up, you get 0 back (a straight 1:1 swap).

### 7.1 Mint event

Anyone can make (*mint*) a note. Step by step, to mint or re-arm a note of denomination $`d`$:

1. The minter $`m`$ burns $`V_d`$ of digital money, the note's face value.
2. The minter gets back two amounts from the incentive pool $`I`$: a production-cost credit $`C_d`$ and a reward $`R_d`$, so $`C_d + R_d`$ in total.
3. Physical supply $`P`$ goes up by $`V_d`$. The pool goes down by what it paid out.

```math
b_m \leftarrow b_m - V_d + C_d + R_d, \qquad P \leftarrow P + V_d, \qquad I \leftarrow I - (C_d + R_d).
```

So the minter's net cost is $`V_d - C_d - R_d`$. If the reported cost is honest, the minter ends up ahead by exactly the reward $`R_d`$ after paying for the note's materials.

### 7.2 Production-cost credit $`C_d`$ (attested, capped, market-discovered)

**In plain words.** Minters are paid back for what the note cost to make. They can't just name any price, though. The refund is the smallest of three numbers: what they claim, a hard cap, and slightly above what other minters typically claim.

Each mint includes a stated (*attested*) cost $`\hat c`$. A chip factory (*fab*) can sign a batch of chips, but the system does not rely on that signature. The credit is:

```math
C_d = \min\big(\hat c,\ c^{\max}_d,\ \tilde c_d\big), \qquad
\tilde c_d = \mathrm{median}\big(\hat c_{(1)}, \dots, \hat c_{(K)}\big)\cdot(1 + \epsilon),
```

Here:

- $`\tilde c_d`$ is the *median* (middle value) of the last $`K`$ stated costs for this denomination, plus a 5% margin. Defaults: $`K = 256`$, $`\epsilon = 0.05`$. "Rolling" means it always uses the most recent $`K`$.
- $`c^{\max}_d`$ is a hard cap per denomination. See the recommended rule below.

Why this works: minters compete. Claiming too much can't push your refund above the median. Honest minters with low costs keep the difference as extra profit.

Re-arming an existing note (Option A) gets $`C_d = 0`$, because no new hardware is made.

> [!NOTE]
> **Decided by Ian's criteria (2026-10-23), implemented in [PR #20](https://github.com/onnxscibroccoli/TCL-orc/pull/20) (draft).** The criteria: capture-resistant, fixed supply, minting profitable, pool not drainable by over-claiming, and small everyday notes must actually get made. All seven options compared are in [Appendix A.4](#a4-cost-credit-cap-5-vs-20).

**The rule, step by step.**

1. **New note.** The cost credit is the smallest of four numbers: what you claim, a per-note override cap, a percentage of face value, and a fixed amount.
2. **Small-note bonus.** New 5, 10 and 20 notes get a small flat bonus on top of the normal reward. A chip costs the same in a 5 note as in a 500 note, so this makes small notes worth making.
3. **Re-arming.** Putting an old note back into use gets no cost credit, because no new hardware is made. It gets the normal reward only if the note's last era lasted at least 7 days. That stops people from farming rewards by depositing and re-arming over and over.
4. **Per-mint ceiling.** Credit plus reward can never exceed 60% of face value.
5. **Reserved budget.** Half of each epoch's budget is kept for small notes, so big mints can't use it all up.
6. **Fail closed.** If governance's settings can't be read, minting becomes a plain 1-for-1 swap.

```math
\begin{aligned}
C_d &= \min\bigl(\hat c,\ \gamma_c \cdot V_d,\ c_{\mathrm{abs}}\bigr) \quad (\text{new note}), \qquad C_d = 0 \quad (\text{re-arm}),\\
R_d &= \rho \cdot V_d + b_{\mathrm{small}} \cdot \mathbb{1}[V_d \le 20 \text{ and new note}], \qquad C_d + R_d \le 0.6\, V_d .
\end{aligned}
```

where $`\gamma_c`$ is the percentage cap (default 0.40, hard limit 0.50), $`c_{\mathrm{abs}}`$ is the fixed cap (default 3 TCL, hard limit 10), $`\rho`$ is the reward rate (default 0.002, hard limit 0.01), $`b_{\mathrm{small}}`$ is the small-note bonus (default 0.25 TCL, hard limit 1) and $`\mathbb{1}[\cdot]`$ is 1 when the condition holds and 0 otherwise. All of these live in `ParameterRegistry`. Governance can move them only within those limits, one step per cycle.

**Defaults for every note value, with an attested cost above the cap (checked by `test_DefaultScheduleAllDenominations`).**

| Note | Cost credit | Reward | Total | % of face | Honest minter's profit if real cost is 2 TCL |
| --- | --- | --- | --- | --- | --- |
| 5 | 2 | 0.01 + 0.25 = 0.26 | 2.26 | 45.2% | 0.26 |
| 10 | 3 | 0.02 + 0.25 = 0.27 | 3.27 | 32.7% | 0.27 |
| 20 | 3 | 0.04 + 0.25 = 0.29 | 3.29 | 16.45% | 0.29 |
| 50 | 3 | 0.10 | 3.10 | 6.2% | 0.10 |
| 100 | 3 | 0.20 | 3.20 | 3.2% | 0.20 |
| 200 | 3 | 0.40 | 3.40 | 1.7% | 0.40 |
| 500 | 3 | 1.00 | 4.00 | 0.8% | 1.00 |

An honest minter's profit is the reward, whenever the real cost is at or under the cap (2 TCL for a 5 note, 3 TCL for the others). The extra a cheater can grab by over-claiming is at most cap minus real cost: 0 for a 5 note and 1 TCL for the others at a real cost of 2. The 2 TCL cost is an estimate (USD 1.6–2.3 at volume, §7.6), and TCL has no dollar price yet. **Not built yet:** the median rule above is not on-chain, and minting still doesn't check that a real note exists. So the epoch budget is what actually limits how much fake mints could take.

**Example.** Minting a 100 TCL note. You claim a cost of 4 TCL. Recent minters' median is 3 TCL, so $`\tilde c_d = 3 \times 1.05 = 3.15`$. The cap at 5% is 5 TCL. Your credit is the smallest: 3.15 TCL.

### 7.3 Mint reward $`R_d`$ (adjustable, bounded, pool-funded)

```math
R_d = \min\!\Big(\rho \cdot V_d,\ \frac{\alpha\, \mathcal{E}_e}{\max(1, n_e)}\Big), \qquad \rho \in [0,\ \rho_{\max}],\ \rho_{\max} = 0.01,
```

**In plain words.** The reward is a small percentage of the face value, at most 1%. But if many people mint in the same period and the money set aside runs low, everyone's reward shrinks to share it fairly.

In the formula:

- $`\mathcal{E}_e`$ is the *emission*: the amount the pool pays out in epoch $`e`$ (§9). An *epoch* is one payout period.
- $`\alpha`$ is the share of that emission set aside for minters.
- $`n_e`$ is the number of mints in the epoch.

When the pool is thin, each mint's reward shrinks on its own. Governance can also steer the reward rate $`\rho`$ toward a target share of money held as paper, $`p^* = P/(P+D)`$. If there is too little paper money, $`\rho`$ goes up a little; if too much, it goes down. $`\eta_\rho`$ sets how fast it adjusts, and *clip* keeps it between 0 and the maximum:

```math
\rho_{e+1} = \mathrm{clip}\Big(\rho_e + \eta_\rho\,(p^* - p_e),\ 0,\ \rho_{\max}\Big).
```

### 7.4 Anti-over-minting

**In plain words.** These rules stop people from minting too much to milk the rewards.

- **A budget per epoch.** The total of all credits and rewards in an epoch can't exceed $`\sum_{\text{mints in } e} (C_d + R_d) \le \alpha\,\mathcal{E}_e + \text{cost reserve}`$. Mints past the budget still work, but get $`C_d = R_d = 0`$. They are a plain 1-for-1 swap, so they create no free money.
- **Minting can't create money.** Every LIVE note is backed one-for-one by burned digital money. The rule $`P = \sum_{\textsf{LIVE}} V_d`$ always holds: physical supply equals the face values of all LIVE notes.
- **A top denomination.** The largest note value $`V_{\max}`$ is kept low enough that copying one note costs more than the copy can earn (§11).

### 7.5 Counterfeit economics

**In plain words.** Could a crook profit by copying a note? Only if copying is cheap and the copies get spent many times before anyone notices. The rule: make copying cost more than a copy could ever earn.

Let $`K_{\mathrm{clone}}`$ be the cost of pulling the secret key $`sk_{\mathsf{id}}`$ out of a chip and copying the PUF pattern $`\rho`$. A copied note can be handed over offline $`n`$ times before the first copy is deposited. After that, era $`k`$ is marked spent, and every copy shows as DORMANT the next time phones update their lists. The crook's profit is:

```math
\Pi = (n - 1)\,V_d - K_{\mathrm{clone}} - c_{\mathrm{dist}}(n).
```

Design target: $`K_{\mathrm{clone}} \gt n_{\max} V_{\max}`$. Here $`n_{\max}`$ is a realistic number of offline hand-offs before lists update, for example 20. So $`V_{\max} \lt K_{\mathrm{clone}} / n_{\max}`$. The actual numbers wait on hardware research.

**Example (illustrative).** If cloning one note costs 20,000 TCL and a copy can be spent at most 20 times, the top note must be worth less than 20,000 ÷ 20 = 1,000 TCL. The chosen top value, 500 TCL, would pass.

### 7.6 Research integration (Part C Model 1, Part D, Q6)

**In plain words.** The outside report suggested its own minting formulas. We kept some ideas and fixed some mistakes.

The report's mint model. (*CPI* is the consumer price index, a measure of inflation published by governments. An *oracle* is an outside data feed brought onto the blockchain.)

```math
C(t) = \min\!\Big(C_{\mathrm{cap}},\ C_{\mathrm{base}}\tfrac{I_{\mathrm{CPI}}(t)}{I_{\mathrm{CPI}}(0)}\Big), \qquad R = V \mu_{\max}\Big(1 - \tfrac{S_{\mathrm{phys}}}{S_{\mathrm{target}}}\Big),
```

with $`C_{\mathrm{base}} = 1.50`$, $`C_{\mathrm{cap}} = 5.00`$, $`\mu_{\max} = 0.01`$ and $`S_{\mathrm{target}} = 20\%`$ of supply.

**Assessment:**

- **Mistake:** the reward $`R`$ turns negative when paper supply goes above its target ($`S_{\mathrm{phys}} \gt S_{\mathrm{target}}`$). A negative reward makes no sense. Fix: use $`\max(0, \cdot)`$, so it stops at zero.
- **Conflict:** the CPI feed comes from one central source. That clashes with the goal of no central control. It is rejected. §7.2's median of minters' own costs is used instead.
- **Adopted:** the report's idea of shrinking the reward as paper supply nears its target. It replaces the §7.3 controller. Here $`M`$ is total supply:

```math
R_d = \min\!\Big(V_d\,\rho_{\max}\max\!\big(0,\ 1 - \tfrac{P}{p^{*}M}\big),\ \tfrac{\alpha\mathcal{E}_e}{\max(1,n_e)}\Big), \quad \rho_{\max} = 0.01,\ p^{*} = 0.2 \ (\text{proposed}).
```

- **Cost cap:** set $`c^{\max}_d = \min(\gamma_c V_d,\ 5\ \text{TCL})`$. Superseded by the decided rule in §7.2 (percentage and fixed cap, small-note bonus, re-arm with no cost credit).

**Part D (hardware).** The proposed note is made of plastic film (*BOPP polymer*, like some modern banknotes). It has shiny color-shifting patches (*OVD*), inks that glow under ultraviolet (UV) and infrared (IR) light, an optical PUF layer, and a built-in chip that talks to phones by tap (*NFC SE*). Its silver antenna lines break if someone peels the layers apart (*delamination*). The report says a note lasts 8–10 years † and gives cost per note:

| Volume | 10k | 1M | 100M |
| --- | --- | --- | --- |
| Total $`C`$ | USD 5.00 | USD 0.75 | USD 0.23 |

The parts add up correctly. **But there's a mismatch:** the table's chip price (USD 0.11–1.25) is for the cheap NTAG-class chip. The report's own comparison prices the asymmetric SE050 class at USD 1.10–1.80. §4.6 requires the asymmetric kind. So a realistic cost at 1 million notes is about USD 1.6–2.3 each (estimate †).

**Q6 (which note values).** The report proposes 5, 10, 20, 50, 100 and 500, with a top value $`V_{\max} = 500`$ TCL. It cites a cloning cost of USD 50k–150k "per design iteration".

**Mistake:** that is a one-time setup cost for the whole design. A crook pays it once and then spreads it over every copy they make. What matters is the cost of *each extra* copy (the *marginal* cost). The right test from §7.5 is $`\Pi = nN_cV - K_{\mathrm{setup}} - N_c K_{\mathrm{marginal}}`$, where $`N_c`$ is the number of copies. This spec adopts the denominations for now, until the per-copy cost is known, with $`V_{\max} = 500`$ as the top. The report also mixes TCL and US dollars without saying how they convert. (The built MVP also allows 200; see doc 12.)

---

## 8. Cryptographic mining

**In plain words.** Here, "mining" means getting paid for useful work that anyone can check. Mostly, that work is making the proofs that keep the account book honest. It does not mean burning electricity on puzzles; that option was dropped (§8.1).

Roles: a *prover* makes proofs. A *settler* sends them to the blockchain. A *relayer* collects offline deposits and passes them on. A *bond* is a deposit you lock up and lose if you misbehave (*slashing*).

| Role | Work | Verified by |
| --- | --- | --- |
| Prover | Groth16 proofs for settlement and re-entry | on-chain verifier (`StateVerifier`) |
| Settler | submitting `settleEpoch`, batching re-entries | `StateSettled` event, `StaleRoot` checks |
| Re-entry/mint relayer | aggregating offline deposits and receipts | proof inclusion |
| PoW (optional, default off) | hash puzzle for permissionless leader selection or rate limiting | difficulty target |

Each prover is paid in proportion to the work their proofs took. The work $`w_j`$ of proof $`j`$ is measured by its number of equations (*constraint count*). Payment happens only if the blockchain accepts the proof:

```math
r_j = \beta\,\mathcal{E}_e \cdot \frac{w_j}{\sum_{i \in e} w_i}.
```

**Example.** The prover share this epoch is 450 TCL. Alice's proofs took 3,000 equations and Bob's took 1,000. Alice gets 450 × 3,000/4,000 = 337.5 TCL. Bob gets 112.5 TCL.

Settlers share $`\gamma\,\mathcal{E}_e`$, split per accepted settlement. Optional proof-of-work (PoW) would get $`\delta\,\mathcal{E}_e`$, but §8.1 sets this to 0.

A *cartel* is a group that teams up to grab all the work. Three rules stop that:

1. **Random turns.** Each epoch's proving turn goes to a random prover who has posted a bond. Each has an equal chance. The randomness comes from the previous root, $`H(14, \mathcal{R}_{e-1}, e)`$, so no one can pick it. If the chosen prover doesn't deliver within a time limit $`t_{\mathrm{fb}}`$, anyone may step in.
2. **A cap per prover.** No prover may take more than a share $`s_{\max}`$ of rewards (for example 20%). Anything above goes back to the pool.
3. **Penalties.** A prover who misses an assigned turn loses their bond $`\beta_{\mathrm{bond}}`$.

### 8.1 Research integration (Part C, Q7, Q8)

- **Q7.** Adopted: drop proof-of-work completely and set $`\delta = 0`$ for good. It uses energy without doing useful work.
- **Cartels.** Adopted: a *reverse-Dutch auction* for batch rewards. The reward starts low and rises step by step until someone takes the job: $`r(t) = r_0(1 + \kappa_r \lfloor (t - t_0)/N_{\mathrm{blocks}} \rfloor)`$, up to a cap $`r_{\max}`$. This works alongside the random turns in §8.
- **Q8.** Adopted as a future plan: combine many proofs into one (*recursive aggregation*, using Nova folding or Plonky2), then wrap them in one final Groth16 proof. The report says checking it would cost about 220k *gas* (the blockchain's fee for work). That is believable for this kind of proof with few public values: about 181k base plus about 6k per value. It should be checked on Anvil. Until then, the current settlement circuit stays as it is.

---

## 9. Shared incentive pool, emission and conservation

> **In plain words.** Decay fees and penalties go into the vault. Up to half of that can be moved into an *incentive pool* that pays minters, provers and settlers. Each epoch, a slice of the pool is paid out and split between those jobs. Rewards come only from fees, so no new money is ever printed.
>
> **Example (illustrative values within the bounds).** Fees this epoch: 1,000 TCL. With the pool share at 0.5, 500 TCL go to the pool and 500 stay in the vault. If the pool holds 10,000 TCL and the payout rate is 0.10, this epoch pays 1,000 TCL; with the default minting share of 0.35, minters share 350 TCL.

### 9.1 Funding

The vault $`\mathcal{V}`$ collects decay fees $`F_e`$ and anti-hoarding penalties $`\Pi_e`$. A share $`\phi`$ of that goes to the incentive pool. Governance sets it within $`\phi \in [0, 0.5]`$. The rest is paid back to people as today:

```math
I_{e+1} = I_e + \phi\,(F_e + \Pi_e) - \mathcal{E}_e^{\mathrm{paid}}, \qquad \mathcal{V}_{e+1} = \mathcal{V}_e + (1 - \phi)(F_e + \Pi_e) - \mathrm{redist}_e.
```

### 9.2 Emission and split

```math
\mathcal{E}_e = \eta\, I_e, \quad \eta \in [0.01, 0.25]; \qquad \alpha + \beta + \gamma + \delta = 1,
```

Each epoch, the pool pays out a fraction $`\eta`$ of what it holds. Governance may set it from 1% to 25%. **The default is 5%** ($`\eta = 0.05`$, the same as `EMISSION_RATE` in `ParameterRegistry` on main and the §10.3 table). The range is the fence; 5% is where it starts. That payout is split four ways: $`\alpha`$ to minters, $`\beta`$ to provers, $`\gamma`$ to settlers and $`\delta`$ to proof-of-work. The defaults are $`(\alpha, \beta, \gamma, \delta) = (0.35,\ 0.45,\ 0.20,\ 0)`$. Anything not paid out stays in the pool $`I`$.

**No new money is printed (*no inflation*).** Rewards come only from fees. So the pool can never go below zero.

### 9.3 Conservation

**In plain words.** Money is only moved, never made or destroyed. Digital, paper and pool always add up to the same total.

Total supply is $`M = D + P + I`$ (digital including the vault, plus paper, plus pool). No action changes it:

```math
\Delta M = \underbrace{(-V_d + C_d + R_d)}_{\Delta D} + \underbrace{V_d}_{\Delta P} + \underbrace{(-C_d - R_d)}_{\Delta I} = 0 \quad \text{(mint)},
```

```math
\Delta M = V_d - V_d = 0 \ \text{(re-entry)}, \qquad \Delta M = 0 \ \text{(demurrage, penalties, rewards: transfers among } D, \mathcal{V}, I).
```

And paper money always equals the face values of all LIVE notes:

```math
P = \sum_{\mathsf{id}\,:\,\sigma = \textsf{LIVE}} V_{d(\mathsf{id})}.
```

Lost notes stay counted in $`P`$ forever. In practice they are gone, like lost cash. They are never taken back or reissued (R1).

### 9.4 Research integration (Part C Model 2, Q9, Q11)

**In plain words.** What the outside report said about the pool, and what we took from it.

The report pays out $`E_{\mathrm{em}} = E_{\mathrm{pool}}(1 - e^{-\gamma})`$ each epoch, with $`\gamma = 0.05`$. That is about 4.88% of the pool per epoch. It is the same idea as §9.2's default of $`\eta = 0.05`$: a fixed share of whatever is in the pool. The report's version just writes the share as $`1 - e^{-\gamma}`$. We use the simpler form, where $`\eta`$ is the share itself. It splits the payout 20% to minters and 80% to provers, adjusted as conditions change:

```math
\alpha(L) = \max\!\big(0.05,\ 0.20 - \eta_L \max(0,\ L - L_{\mathrm{target}})\big), \quad L_{\mathrm{target}} = 60\ \mathrm{s},\ \eta_L = 0.005\ \mathrm{s}^{-1}.
```

**Assessment:**

- **Mismatch inside the report:** Part C writes the formula as $`\alpha = 0.20 - \theta (T_{\mathrm{avg}} - T_{\mathrm{target}})/T_{\mathrm{target}}`$. That has no lower limit, so it could go below 0 or above 1. We adopt the Q9 version, which has a lower limit.
- **Mistake:** the report fills the vault with "50% of EVM transaction base fees." But on the main blockchain (*L1*), those fees are burned or paid to the computers that run the chain (*validators*). They never reach TCL. And TCL charges no transaction fee. So this spec fills the vault only from decay and re-entry penalties, unless the owner adds a fee later.
- **Adopted:** minters get $`\alpha`$. Provers and settlers share the rest, $`1 - \alpha`$, split 70:30 ($`\beta:\gamma = 0.7:0.3`$). Proof-of-work gets $`\delta = 0`$.
- **Q11, UBI.** *UBI* (universal basic income) is an equal payment to every verified person. The report suggests: keep decay at about 17% a year ($`r_d = 0.0005`$/day) above 5 million, and after paying minters and provers, give 80% of the remaining decay equally to every verified person. That **prints no new money**, so it works with a fixed supply. (An earlier formula based on *PPP*, purchasing power parity, would have printed money and stays rejected.) It is proposed as $`1 - \phi = 0.8`$ of what flows into the vault, paid per personhood credential per epoch. **Warning:** paying per person makes faking people very profitable. So the personhood system (§10.5) must be secure first. Today it is a mock.

---

## 10. Governance and anti-capture

### 10.1 Current state

**In plain words.** *Governance* is how the rules get changed. Right now one key controls everything. This section explains how control moves to everyone, and how the design stops the rich or powerful from taking it.

Today, `CirculatoryEngine` uses `OwnableUpgradeable` and `UUPSUpgradeable`. That means one owner can swap in new code (*upgrade*). The `settler`, who records proofs, is the owner by default. Whoever gets that one key controls the system. This *single point of capture* must be removed.

### 10.2 Voting

- **Who votes:** every human gets one anonymous *personhood credential*, a digital pass that proves you are one unique person without saying who. Each vote uses a one-time code per proposal, $`H(15, \mathsf{personSecret}, \mathsf{proposalId})`$. So each person votes once per proposal. The vote can't be linked to who you are or how much money you have.
- **How votes count:** one person, one vote, built up over time by conviction (§10.6), with the plural discount for tight-knit blocs. Money gives **no** voting power at all. People show how strongly they care by *how long* they keep their support, not by buying extra votes. This is what `ConvictionVoting` on main does. (An earlier draft used quadratic voting; see [Appendix A.1](#a1-quadratic-voting-vs-one-person-one-vote-conviction) for why it was dropped.)
- **Stopping bribes:** ballots are encrypted in the style of *MACI* (Minimal Anti-Collusion Infrastructure). Voters can change their key and vote again. So no voter can prove to a briber how they voted, and a briber can never be sure they got what they paid for.
- **Quorum and passing:** a *quorum* is the minimum turnout for a vote to count. Here it is $`q \ge 5\%`$ of registered people. A proposal passes with more than 50%, $`\gt 50\%`$ (or $`\gt 66\%`$ for *constitutional* changes, the most basic rules).

### 10.3 Bounded parameters and timelocks

**In plain words.** Even if bad actors win a vote, they can only nudge settings a little at a time, inside fixed limits. And every change waits two weeks or more, so people can see it coming and leave.

Even a majority that has been captured can only set each value $`x`$ inside a range written into the code. And it can only move it by a limited step each governance cycle:

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
| $`\gamma_c`$ | 0.40 | $`[0, 0.50]`$ | 0.05 |
| $`c_{\mathrm{abs}}`$ | 3 TCL | $`[0, 10]`$ TCL | 1 TCL |
| $`b_{\mathrm{small}}`$ | 0.25 TCL | $`[0, 1]`$ TCL | 0.25 TCL |
| small-note budget reserve | 50% | $`[0, 80\%]`$ | 10 points |
| minimum era age before a re-arm reward | 7 days | $`[1, 90]`$ days | 50% |
| $`\phi`$ | 0.2 | $`[0, 0.5]`$ | 0.05 |
| $`\eta`$ | 0.05 | $`[0.01, 0.25]`$ | 0.02 |

Every change waits out a *timelock* of $`\ge 14`$ days (30 for upgrades) before it takes effect. During that time anyone can pull out.

**Example.** The daily rate $`r_d`$ is 0.0005. One cycle can move it by at most 25% of its value: to between 0.000375 and 0.000625. Even after many cycles it can never leave the range 0.0001 to 0.002.

### 10.4 Key removal roadmap

**In plain words.** The plan to take away the single owner key, in four stages.

1. **Stage 0 (today):** the owner is one ordinary account controlled by one key (an *EOA*, externally owned account).
2. **Stage 1:** the owner becomes a *multisig* (several people must approve) plus a timelock. Anyone with a valid proof can be the settler (*permissionless*).
3. **Stage 2:** the owner becomes the governance timelock contract. Upgrades need a vote of the people.
4. **Stage 3:** ownership is given up for good (`renounceOwnership`). Upgrades are switched off forever by disabling `_authorizeUpgrade`, so the core can never change (*immutable*). Only the limited settings in §10.3 can still be voted on.

### 10.5 Identity layer: anonymous proof-of-personhood

**In plain words.** How can the system know each account belongs to one real, unique person, without learning who they are? This is *proof of personhood* (PoP). This section compares existing systems and picks a design. **None of it is built yet; personhood is a mock today.**

Source: Gemini deep research (supplied by the owner, 2026-10-23). The formulas in §10.5–§10.7 are quoted from that report. This draft cites only the report. Several of the report's own sources are weak (for example Patreon links). Claims marked † still need original sources.

Terms in the table: an *Orb* is World's iris-scanning device. *Semaphore* is a zero-knowledge tool for anonymous one-time signals. A *pseudonym party* is a meetup where everyone attends at the same time, so no one can be in two places. A *social graph* is the web of who knows whom. A *long con* is a fake identity built up patiently over months. *Kleros* is a jury system for disputes. A *deepfake* is a fake video made by AI.

| System | Mechanism | Main weakness (per report) |
| --- | --- | --- |
| Worldcoin / World ID | Orb iris scan plus Semaphore ZK | Biometric; hardware supply chain; regional bans† |
| Encointer | Simultaneous in-person pseudonym parties | Liveness and attendance friction |
| BrightID / Aura | Social-graph ranking (SybilRank) | Slow, long-con infiltration |
| Proof of Humanity | Video, vouching, Kleros disputes | No privacy; deepfakes† |
| Human Passport | Stamp aggregation | Wealth bias; stamps can be bought |

The report proposes combining three checks. A credential would need all of: an iris scan proven with zero knowledge, an Encointer-style meetup, and a high enough place in the social graph, $`C_{\mathrm{graph}}(i) \gt \theta`$. (*Centrality* measures how well-connected someone is to trusted people.)

**Iris scans are *biometrics* (body measurements). They clash with the owner's goal of anonymity and no identity checks.** So this draft **recommends a combination that doesn't need biometrics**:

1. **Meetup:** Encointer-style pseudonym parties.
2. **Graph:** BrightID-style centrality above $`\theta`$.
3. **ZK and MACI:** Semaphore-style one-time codes $`H(15, \mathsf{personSecret}, \cdot)`$ and MACI key changes.

Biometrics are an optional extra clue, never required. A person is valid if they passed a meetup *and* are well connected:

```math
\mathrm{valid}_i = \mathbb{1}[\text{ceremony}_i] \wedge \mathbb{1}[C_{\mathrm{graph}}(i) \gt \theta] \quad (\text{optional biometric may lower } \theta \text{, never replace either term}).
```

The report says the cost of making $`k`$ fake identities grows like $`k^2`$, written $`C_{\mathrm{sybil}}(k) = \mathcal{O}(k^2)`$. **The report just states this; it doesn't prove it.** It needs independent checking†.

The report's hardware advice applies to the optional biometric path and to the chips in notes (§4.1): use sensors from several makers; have devices prove remotely what software they run (*remote attestation*); and let governance cancel a manufacturer's master keys if they are compromised.

### 10.6 Plural conviction voting

**In plain words.** In *conviction voting*, your support for a proposal builds up slowly the longer you keep it there, like a dimmer switch turning up. A sudden burst of votes counts for little. *Plural* means that groups who always vote together count somewhat less each, so a bloc can't dominate. This is the voting rule (§10.2, decision 5 in §12). It is built in `contracts/governance/ConvictionVoting.sol`. Each person's support is their weight $`W_i`$ (1 if no plural hook is plugged in). It grows using the normalized formula below and fades with the half-life from `ParameterRegistry` (7 days, settable from 3 to 30). **Not yet built:** a real plural-weight module (only the `IPluralWeight` hook and a test mock exist) and a real personhood check (mock).

The report's formula. $`Y`$ is the built-up support, $`S`$ is what a person stakes, $`\alpha`$ is how much support carries over each step, and $`\tau_{1/2}`$ is the *half-life*, the time for old support to fade by half:

```math
Y_{i,p}(t) = \alpha\,Y_{i,p}(t-1) + W_i\, S_{i,p}(t), \qquad \alpha = 2^{-1/\tau_{1/2}},
```

```math
\sigma(i,j) = \frac{|N(i) \cap N(j)|}{|N(i) \cup N(j)|}, \qquad W_i = \frac{1}{\sqrt{1 + \sum_{j \in V_p,\, j \ne i} \sigma(i,j)}},
```

Here $`\sigma(i,j)`$ measures how much two voters' circles of contacts overlap, from 0 (none) to 1 (identical). $`W_i`$ is voter $`i`$'s weight. The more their circle overlaps with other supporters, the lower their weight. If a bribed group all share the same contacts, each one's weight falls toward:

```math
\lim_{\Sigma_p \to \mathbf{1}} W_i = \frac{1}{\sqrt{|V_{\mathrm{bribe}}|}}.
```

**Checking the report: the flash-loan claim.** A *flash loan* is a huge loan borrowed and repaid within one block (one tick of the blockchain clock). The report claims that support added for just one block counts for very little: $`Y_{\mathrm{flash}} = S_{\mathrm{flash}}(1 - 2^{-1/\tau_{1/2}}) \approx S_{\mathrm{flash}} \ln 2 / \tau_{1/2}`$, about $`10^{-6}`$ of it at $`\tau_{1/2} = 10^6`$ blocks. **That does not follow from the report's own formula.** Start from zero, $`Y(t-1) = 0`$. One block of $`Y(t) = \alpha Y(t-1) + W S`$ already gives $`Y = W S`$: the full amount. The claim is only true for a *normalized* version, where new support is scaled down by $`(1-\alpha)`$. **This spec adopts the normalized version:**

```math
Y_{i,p}(t) = \alpha\,Y_{i,p}(t-1) + (1 - \alpha)\, W_i\, S_{i,p}(t) \ \Rightarrow\ Y_{\mathrm{flash}} = W_i S (1-\alpha), \quad Y_{ss} = W_i S.
```

In TCL, $`S_{i,p}`$ is voice credits staked by a **person**, not money. So borrowed money already gives no votes. The normalized form still stops sudden swings.

**Example.** With a 7-day half-life, $`\alpha \approx 0.9057`$ per day. Someone stakes full support on day 1. After one day their support counts $`1 - 0.9057 \approx 9.4\%`$. It keeps rising toward 100% over the following weeks.

How much support a funding request needs: a request for $`R_p`$ from the shared fund (*treasury*) $`T_{\mathrm{tr}}`$ passes once total support reaches the threshold below. (The report wrote $`T`$; it is renamed here so it isn't confused with the decay threshold $`T`$.) Bigger requests need much more support:

```math
\sum_{i \in V_p} Y_{i,p}(t) \ge Y_p^*(R_p) = \frac{\beta_1\, T_{\mathrm{tr}}}{\left(1 - \beta_2 \frac{R_p}{T_{\mathrm{tr}}}\right)^2}, \qquad \beta_2 \in (0,1).
```

*Futarchy* ("vote on values, bet on beliefs") lets people vote on goals and then uses betting markets to predict which policy reaches them. It is put off to Phase 3, with a citizen veto.

### 10.7 UBI and demurrage (optional governance-controlled extension)

**In plain words.** The report modeled paying everyone a basic income $`u`$ (UBI) while balances decay. Its model assumes new money is printed. TCL never prints money, so its main formula is not used. UBI stays optional and would be paid only from decay fees.

The report's model of a balance with UBI $`u`$:

```math
\frac{dB_i}{dt} = -\lambda B_i + u(t), \qquad B_i(t) = B_i(0)e^{-\lambda t} + \frac{u}{\lambda}\left(1 - e^{-\lambda t}\right), \qquad B_{ss} = \frac{u}{\lambda},
```

and a rule (based on *PPP*, purchasing power parity) for how much money to print. $`\mu`$ is the growth rate, $`\pi^{*e}`$ the expected inflation target, $`N`$ the number of people and $`M`$ the total supply:

```math
\mu(t) = (1 + \pi^{*e})(1 + \dot{T}^e) - 1, \qquad \frac{dM}{dt} = N u - \lambda M = \mu M \ \Rightarrow\ u = \frac{(\lambda + \mu) M}{N}.
```

**Checking the report.** These formulas assume three things: new money is printed all the time (*issuance*); decay destroys money ($`-\lambda M`$); and decay applies to the whole balance. TCL differs on all three:

- Supply is fixed: fees move to $`\mathcal{V}`$ and are not destroyed, so $`\Delta M = 0`$ (§9.3).
- Decay applies only to the surplus above $`T`$.
- There is no issuance.

So the PPP rule **clashes with TCL's fixed supply** and is not adopted. UBI is only an **optional extra that governance could switch on**. If switched on, it would be paid per personhood credential from the vault's payback share, without printing money: $`N u \le (1-\phi)(F_e + \Pi_e)/\Delta t_e`$. In words: all UBI paid in a period can't exceed the fees that came in. The built constants ($`r_d = 0.0005`$, $`T`$, $`\lambda`$) stay the same. The report's suggested decay path (about 1% a year, then 5–10% a year) is still an open question.

**Example (illustrative).** 1,000 TCL of fees come in this epoch, and the payback share is 0.8. That leaves 800 TCL. With 400 verified people, each gets 2 TCL.

### 10.8 Rollout (from research, adapted)

**In plain words.** The order in which this gets built.

1. **Phase 1:** combine personhood credentials; a multisig plus timelock as a safety net (Stage 1 of §10.4).
2. **Phase 2:** personhood without biometrics, plural conviction voting, and governance becomes the owner (Stage 2).
3. **Phase 3:** the core becomes unchangeable (Stage 3), with futarchy and a citizen veto. Any price-stability goal is met **without printing money**: governance may nudge only the limited dials in §10.3 (decay rate, pool share, payout rate), within their fences and timelocks. The money-printing PPP controller is rejected; see [Appendix A.2](#a2-ppp-money-printing-vs-fixed-supply).

### 10.9 Tiered personhood and Sybil unprofitability (owner-confirmed design)

> **In plain words.** To prove you are a real person you need 3 other verified people to vouch for you. To move bigger amounts you need more vouches (for example 6, 12, 24). Each vouch locks up a deposit (a *bond*). If someone you vouched for turns out to be fake, you lose your bond. If they stay honest, you earn a small reward. The bond is set high enough that making fake people always loses money. **This layer is not built; personhood is a mock today.**
>
> **Example (illustrative numbers; the real values are still open).** Say a tier-1 person may move 1,000 TCL per window (L1), a fake survives 3 windows before being caught, making one costs nothing (worst case), and a fake is caught half the time. Then each vouch bond must be more than (1 × 1,000 × 3 − 0) ÷ (0.5 × 3) = 2,000 TCL.

**Tiers.** A *tier* is a level. A tier-$`n`$ credential needs $`v_n`$ vouches. Tier 1 needs $`v_1 = 3`$, and each tier needs more than the one before ($`v_{n+1} \gt v_n`$), for example $`v = (3, 6, 12, 24)`$. Vouches only count fully when the vouchers don't all know the same people (*graph-independent*). Each voucher's weight shrinks if their contacts overlap with the other vouchers', using the plural discount from §10.6. The weights must add up to at least $`v_n`$:

```math
\mathrm{eff}_n(i) = \sum_{j \in \mathrm{vouchers}(i)} W_j^{(i)} \ge v_n, \qquad W_j^{(i)} = \frac{1}{\sqrt{1 + \sum_{l \in \mathrm{vouchers}(i),\, l \ne j} \sigma(j,l)}} .
```

Each tier allows a limit $`L_n`$ on how much you can send and deposit per window. It is tracked under your pseudonym $`\nu`$ for that window $`W_a`$. Higher tiers get higher limits:

```math
\sum_{\text{tx} \in W_a} \mathrm{amt}(\text{tx}) \le L_n, \qquad L_1 \lt L_2 \lt \cdots .
```

The base tier limit, $`L_1`$, covers everyday small payments.

**Spotting funneling.** *Funneling* is when many small fake accounts all send their money to one person, to sneak a big amount past the limits. Here is how it is spotted. Take a recipient $`r`$ (a pseudonym or a group). Let $`f_{i \to r}`$ be what sender $`i`$ sent to $`r`$ this window. Let $`\mathcal{L}`$ be all tier-1 senders. Two numbers are computed: $`c_r`$, the share of tier-1 senders' money that went to $`r`$, and $`m_r`$, how many tier-1 senders sent to $`r`$ at all:

```math
c_r = \frac{\sum_{i \in \mathcal{L}} f_{i \to r}}{\sum_{i \in \mathcal{L}} \mathrm{out}_i}, \qquad m_r = \big|\{ i \in \mathcal{L} : f_{i \to r} \gt 0 \}\big| .
```

$`r`$ is flagged when both are high: many senders ($`m_r \ge m^*`$), and they send most of their money to $`r`$ ($`c_r \ge c^*`$). Flagged money then counts as if $`r`$ had received it directly. It counts against $`r`$'s own tier limit $`L_{n(r)}`$, the decay threshold $`T`$ (§2) and the re-entry threshold $`\Theta`$ (§6):

```math
A_r \leftarrow A_r + \sum_{i \in \mathcal{L}} f_{i \to r} \cdot \mathbb{1}[\text{flagged}] .
```

So splitting money across fake people and funneling it back gains nothing. The decay and re-entry rules still apply to the full amount.

**Vouch bonds and honesty rewards.** Each vouch locks up a bond $`B_v`$. If the person you vouched for is proven fake (by funneling evidence, failing a meetup, or losing a dispute), every voucher for them loses $`B_v`$. That bond goes to the vault. If the person stays in good standing, each voucher earns $`r_v`$ per window from the incentive pool, up to the pool's share for vouch rewards.

**Damage is limited.** In one window, $`k`$ fake tier-1 identities can move at most:

```math
D_{\max} = k \cdot L_1 .
```

That is the most that three vouchers working together can get out per window. And funneling detection counts it against the recipient anyway.

**When faking people never pays.** Define:

- $`q \in [0,1]`$: the share of $`L_1`$ a cheater can actually grab per window (from UBI, dodging the threshold, and so on).
- $`w_d`$: how many windows a fake usually lasts before being caught.
- $`c_{\mathrm{id}}`$: the cost of making one fake (attending a meetup, worming into the social graph).
- $`p_s`$: the chance the vouchers' bonds get taken (*slashed*).

Each fake identity uses $`v_1 = 3`$ vouches. The expected gain is

```math
\mathbb{E}[G(k)] = k \Big( q\, L_1\, w_d - c_{\mathrm{id}} - p_s\, v_1 B_v \Big) \lt 0
\iff
B_v \gt \frac{q\, L_1\, w_d - c_{\mathrm{id}}}{p_s\, v_1} .
```

In words: the expected gain $`\mathbb{E}[G(k)]`$ is negative when the bond is big enough. The gain grows in step with $`k`$ (it is *linear*): $`\mathbb{E}[G]`$ is linear in $`k`$. So if one fake loses money, a thousand fakes lose a thousand times as much. No size of attack pays.

How to pick the numbers: first set $`L_1`$ from what everyday payments need. Then set $`B_v`$ above the limit, using worst-case values (for example $`q = 1`$, $`c_{\mathrm{id}} = 0`$). Honest vouching must still pay off, so also require:

```math
r_v \cdot \mathbb{E}[\text{good-standing windows}] \gt (1 - p_{\mathrm{honest\ ok}})\, B_v ,
```

That means an honest voucher's expected reward is bigger than their expected loss from being fooled. Higher tiers raise the cost of a fake to $`v_n B_v`$, while what a fake can grab, $`L_n`$, grows more slowly. For that, the limit per vouch, $`L_n / v_n`$, must never go up from one tier to the next.

### 10.10 Incentivized reporting (fraud bounties) (owner-requested)

> **In plain words.** Anyone who reports cheating, with proof the system can check, gets a reward paid from the cheater's forfeited bond. The reward is set so that reporting a bribe pays better than taking it. A false report costs the reporter their own bond. Reporting yourself never pays, because you lose more than you get.
>
> **Example (illustrative numbers).** The cheater's bond is 2,000 TCL, the reward share is 0.5 and the pool top-up is 100 TCL. The reward is min(0.5 × 2,000 + 100, 2,000) = 1,100 TCL. If a report is paid 90% of the time, a briber would have to offer more than 0.9 × 1,100 = 990 TCL to *every* person who holds the evidence. If the cheater reports themselves: 1,100 − 2,000 = −900 TCL, a loss.

**What can be reported.** A report must include evidence the blockchain can check by itself, with no need to trust anyone:

| Offence | Evidence |
| --- | --- |
| Bribe offer | Bribe message signed by the briber's key, binding a proposal and a vote |
| Double deposit | Two valid deposit authorizations for the same $`(\mathsf{id}, k)`$, or conflicting nullifiers |
| Fake-identity vouch | A vouch for an identity later proven Sybil (§10.9) |
| Invalid proof | A proof submission that fails verification, or a proof of a false statement |

**Bonds.** The cheater has a bond at stake, $`b_o`$: a vouch bond $`B_v`$, a prover bond or a minter bond. The reporter also posts a bond $`b_r`$. If the report turns out false, the reporter loses it to the vault. A confirmed report pays:

```math
R = \min\!\big(\sigma\, b_o + \tau,\ b_o\big), \qquad \sigma \in (0, 1),\ \tau \le \tau_{\max} \ (\text{capped pool top-up}),
```

This comes from the cheater's forfeited bond. Here $`\sigma`$ is the share of the bond paid out and $`\tau`$ is a small, capped top-up from the pool. Whatever is left, $`b_o - R`$, goes to the vault.

**Should I report or take the bribe?** Let $`p`$ be the chance a report is confirmed and paid. Let $`\mathcal{B}`$ be a bribe offered to stay quiet. Reporting is the better deal exactly when:

```math
p\,R - (1-p)\,b_r^{\mathrm{false}} \gt \mathcal{B},
```

For a true report with valid evidence, $`p`$ is close to 1 ($`p \to 1`$) and nothing is lost ($`b_r^{\mathrm{false}} = 0`$). So it simplifies to $`p R \gt \mathcal{B}`$: the expected reward beats the bribe. A false report is expected to lose the reporter's bond: $`-b_r \lt 0`$.

**Reporting yourself doesn't pay.** A cheater who reports themselves, directly or through a fake account (*sock puppet*), loses $`b_o`$ and gets back $`R \le b_o`$. The cheat is also undone. The net result is:

```math
\Pi_{\mathrm{self}} = R - b_o \le 0 .
```

**Why cheating stays a bad bet.** Say a cheat gains $`G`$. It has $`m`$ helpers, or people holding the evidence. Each reports on their own with chance $`p_a`$. The chance at least one report succeeds is $`\pi = 1 - (1 - p\,p_a)^m`$. The cheater's expected payoff is:

```math
\mathbb{E}[\Pi_o] = (1 - \pi)\,G - \pi\,b_o \lt 0 \iff b_o \gt \frac{(1-\pi)}{\pi}\,G .
```

The first helper to report keeps their own bond (*leniency*). They give up their share $`g_a`$ of the loot. Compare reporting with staying quiet:

```math
U_{\mathrm{report}} = p\,R + b_a \quad\text{vs}\quad U_{\mathrm{silent}} = (1-\pi_{-a})\,g_a - \pi_{-a}\,b_a .
```

If the reward is at least the helper's share ($`R \ge g_a`$), and the helper's bond $`b_a`$ is at stake, reporting always beats staying quiet. To stop that, the briber must pay $`\mathcal{B} \gt p R`$ to *every* helper. That pushes the cost of the cheat above $`m p R`$. A cheater and reporter teaming up can't profit either: together they net $`R - b_o \le 0`$.

**Link to tiered personhood (§10.9).** A vouch bond $`B_v`$ is one kind of cheater bond $`b_o`$. Reports about vouches for fakes raise the chance of slashing, $`p_s`$, in §10.9. A higher $`p_s`$ means a smaller bond is enough: $`B_v \gt (qL_1w_d - c_{\mathrm{id}})/(p_s v_1)`$.

**Limit.** A bribe made entirely in person, with no signed evidence, can't be caught by bounties. MACI handles that case (§10.2). It is *receipt-free*: a voter can always change keys and vote again. So a briber can never check that the voter did what they were paid for.

---

## 11. Threat model

**In plain words.** This table lists ways someone might attack TCL cash, how the design blocks each one, and what risk is left over (*residual risk*). A *mempool* is the waiting room where blockchain transactions sit before they are recorded. A *kiosk* is a public machine that helps people use their cards.

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

**In plain words.** These are the owner's final choices. Where they differ from the research suggestions in §12.1, these win.

**1. Starting handout (*genesis*).** 70% of the fixed supply is given free and equally to verified people (an *airdrop*). The other 30% is sold at auction. The money raised pays note makers and the costs of getting started (*bootstrapping*). With $`N_0`$ verified people at the start and total supply $`M`$:

```math
a_{\mathrm{person}} = \frac{0.7\,M}{N_0}, \qquad M_{\mathrm{auction}} = 0.3\,M .
```

**Example (illustrative).** If $`M`$ is 10 billion TCL and 1 million people are verified, each person gets 0.7 × 10,000,000,000 ÷ 1,000,000 = 7,000 TCL. The auction sells 3 billion TCL.

No special start-up exemption is needed, because §6 has no penalty for holding.

**2. No transaction fees.** Sending TCL costs nothing in protocol fees. The incentive pool is filled only by decay and re-entry penalties:

```math
\Delta I_e = \phi\,(F_e + \Pi_e).
```

**3. Optional basic income per person, switched on only by vote.** UBI is paid from decay fees, and only after governance decides personhood proofs are secure. $`\mathbb{1}[G_{\mathrm{UBI}}]`$ is 1 if the switch is on and 0 if off:

```math
u_e = \mathbb{1}[G_{\mathrm{UBI}}]\cdot\frac{(1-\phi)(F_e + \Pi_e)}{N_e}.
```

Turning it on, $`G_{\mathrm{UBI}}`$, is a proposal. It must pass §10.6 voting with more than 66% (a *supermajority*) and wait out the 30-day timelock. It must also come with a published review of how well §10.5 resists fake people. The same process can turn it off.

**4. Top note value is 500.** The series is 5, 10, 20, 50, 100 and 500. (The built MVP also allows 200; see doc 12.) *Still to check (not a blocker):* that copying one asymmetric-chip note costs more than one copy can earn, $`K_{\mathrm{marginal}} \gt n_{\max} \cdot 500`$ (§7.5–§7.6). With 20 hand-offs, that means a copy must cost more than 10,000 TCL to make.

**5. Governance.** Conviction voting uses a 7-day half-life: old support fades by half each week. That gives $`\alpha = 0.9057`$ per day ($`t_{1/2} = \ln 0.5/\ln\alpha \approx 6.998`$ days). It uses the normalized formula, so support builds gradually:

```math
Y_{i,p}(t) = \alpha\,Y_{i,p}(t-1) + (1-\alpha)\,W_i\,S_{i,p}(t).
```

Voting is one anonymous person, one vote, using zero-knowledge personhood. Biometrics are optional.

**6. Personhood (tiered).** Step by step:

- The base tier needs 3 vouches from existing trusted users. It allows up to $`L_1`$ per window, for small payments.
- Higher tiers need more vouches, from people who don't all know each other, with the plural discount. They allow higher limits $`L_n`$.
- Funneling of small accounts' money toward one person is detected and counted under that person.
- Vouches need a bond. Dishonest vouchers lose it. Honest ones earn small rewards.
- City kiosks only pass messages along (*relay*) for cards that users build themselves. They never see the user's secret.
- **Damage is limited:** $`k`$ fakes vouched for by colluders can move at most $`k \cdot L_1`$ per window. $`B_v`$ is set so that making fakes loses money on average.
- Everyone re-confirms (*re-attests*) every 6 months.

See §10.9. **Not built yet; personhood is a mock today.**

### 12.1 Research assessment (Gemini recommendations, for the record)

**In plain words.** For the record, this table lists what the outside research recommended on twelve questions, and our view of each. Source for every entry: Gemini deep research (supplied by the owner, 2026-10-23, master), Part E. Key: **A** = agree, **M** = agree with changes, **R** = reject. Where this table and §12 differ, §12 wins.

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

**Checking the conviction math.** $`t_{1/2} = \ln 0.5 / \ln 0.9057 \approx 6.998`$ days. That is correct. With the normalized formula, after one day your support reaches $`1 - \alpha \approx 9.4\%`$ of its full level.

**Checking the fake-identity math (why quadratic voting was dropped, A.1).** Under quadratic voting, splitting $`V`$ credits across $`k`$ fake identities gives $`k\sqrt{V/k} = \sqrt{kV}`$ votes. That is correct, and it shows splitting pays. For example, 100 credits in one identity buy 10 votes, but split over 4 identities they buy 4 × 5 = 20 votes. That is why personhood must be strong. The report's phrase "resistance $`\mathcal{O}(1/k)`$" is loose wording. Its claim $`\mathrm{Cost}_{\mathrm{Sybil}}(k) = \gamma k^2 + \delta k`$ is still stated without proof.

## 13. Open questions (still unresolved)

**In plain words.** Questions that still need answers before this design is final.

1. **Cost of faking people (Q12).** The tiered design (§10.9) handles it in principle. But the actual numbers are not chosen yet: $`L_n`$, $`v_n`$, $`B_v`$, $`r_v`$, $`c^*`$, $`m^*`$, and estimates of how often fakes get caught.
2. **Cost of copying a note.** Check that copying one asymmetric-chip note costs more than the 500 top value allows (Q6).
3. **Cost-credit cap: decided** (§7.2, PR #20, draft). Still open: the real dollar cost of a note, and a TCL market price, to confirm the 3 TCL default. Also on-chain hardware attestation, which would let the epoch budget be raised safely.

## 14. Research received

**In plain words.** The outside research reports used to write this document, and which sections they fed into.

| Topic | Feeds sections | Status |
| --- | --- | --- |
| Reusable physical bearer notes | §4.6, §11 | **Received** (master, Part A) |
| Demurrage history | §5.1 | **Received** (master, Part B) |
| Mint and mine incentives | §7.6, §8.1, §9.4 | **Received** (master, Part C) |
| Hardware and manufacturing | §4.6, §7.6 | **Received** (master, Part D) |
| Personhood and governance | §10.5–§10.8, §12 | **Received**: Gemini deep research (user-supplied, 2026-10-23) and master Part E |

**How good are the sources?** Several sources the report cites are second-hand or weak. We don't rely on them. They include accio.com, chainscorelabs.com, coinlive.com, mikkoikola.com, okx.com, binance.com and forklog.com. Claims that rest on them are marked †. Better original sources are in the report's own list, such as NXP AN12196, the Baldimtsi et al. paper on transferable e-cash, the Plonky2 blog and the CNPD ruling. Details about legal bans in Q12 are left out, by the owner's choice (R7).


---

## Appendix A. Rejected alternatives and why

**In plain words.** Earlier drafts and the research suggested some ideas that clash with TCL's goals. They are kept here, with the reasons, so nothing is quietly lost. The goals they were judged against: no rich person or group can take control; the supply is fixed (no printing); no transaction fees; ordinary people govern; the owner's locked decisions (§12); and honest minting must pay.

### A.1 Quadratic voting vs one-person-one-vote conviction

| | Quadratic voting (old §10.2) | One person, one vote with conviction and plural discount (adopted) |
| --- | --- | --- |
| Can money buy votes? | No | No |
| Shows how strongly you care | Yes, by spending credits ($`n`$ votes cost $`n^2`$) | Yes, by keeping support longer |
| Payoff from fake identities | Grows: $`k`$ fakes turn $`V`$ credits into $`\sqrt{kV}`$ votes | Each fake is just one more vote, also discounted if it shares contacts with the others |
| Matches decision 5 (§12) | No | Yes |
| Built on main | No | Yes (`ConvictionVoting`) |

**Why.** Quadratic voting makes splitting pay. 100 credits held by one person buy 10 votes. The same 100 credits split across 4 fakes buy 4 × 5 = 20. Under conviction voting, 4 fakes give at most 4 votes, the same as 4 real people, and each counts less if they share contacts. Personhood is still a mock, so this is the safer choice. The good part of quadratic voting, showing how much you care, is kept, but through time instead of credits.

### A.2 PPP money-printing vs fixed supply

| | PPP controller (old Phase 3) | Non-issuing dials (adopted) |
| --- | --- | --- |
| Prints new money | Yes: $`u = (\lambda + \mu)M/N`$ | No |
| Needs a price feed (oracle) | Yes, a central source | No |
| Fixed supply kept | No | Yes |
| Who can capture it | Whoever controls the feed | Governance only, inside the §10.3 fences |

**Why.** Printing money breaks the fixed supply (§9.3), and a price feed is a single point of control, which §7.6 already rejects for CPI. What stays: UBI paid only from fees (decision 3), and governance moving the limited dials slowly.

### A.3 Pool payout rate: default vs range

This was not a real conflict. §9.2 gave only the allowed range (1%–25%). §9.4 and §10.3 gave the default (5%). They are now stated together in §9.2. The code agrees: `EMISSION_RATE` defaults to 0.05 with bounds [0.01, 0.25] and moves at most 0.02 per cycle. The report's $`1 - e^{-0.05} \approx 4.88\%`$ describes almost the same payout, written another way.

### A.4 Cost-credit cap: 5% vs 20%

**Decided by Ian's criteria (2026-10-23).** Scores run from 1 (bad) to 5 (good). For complexity, 5 means simplest.

| Option | Small notes get made | Pool safe from over-claiming | Hard to capture or collude | Simple | Minting pays | Governable |
| --- | --- | --- | --- | --- | --- | --- |
| Flat 5% | 1: credit 0.25 on a 5 note | 4: at most 25 per mint | 3 | 5 | 2: small notes lose money | 3 |
| Flat 20% | 3: credit 1 on a 5 note, below the ~2 cost | 1: up to 100 per 500 note | 2 | 5 | 3 | 3 |
| Lower of 20% or 5 TCL | 3 | 4: at most 5 | 3 | 4 | 3: small notes marginal | 3 |
| Fixed cap per note value, set by governance | 4 | 4 | 2: seven settings to lobby | 3 | 4 | 4 |
| Median of claimed costs with fixed and % caps | 3 | 5 | 2: a cartel can shift the median; costly to run on-chain | 1 | 4 | 3 |
| Cost-plus with a small-note bonus | 5 | 3: bonus needs a budget limit | 3 | 3 | 5 | 4 |
| Re-arm first (no new-hardware credit) | 2: needs notes to exist already | 5 | 4 | 4 | 2 | 3 |
| **Chosen: combined rule (§7.2)** | **5** | **4** | **4** | **3** | **5** | **5** |

**Why the combined rule wins.**

- **Small notes get made.** A flat bonus on 5, 10 and 20 notes, plus a 40% percentage cap, covers an estimated 2 TCL chip even in a 5 note. Half of each epoch's budget is reserved for small notes, so big mints can't use it all.
- **The pool stays safe.**
  - The fixed cap holds over-claiming on big notes to at most 3 TCL, compared with 100 under a flat 20%.
  - Credit can never exceed 60% of face value.
  - Re-arms get no cost credit, and their reward waits 7 days, so rewards can't be farmed.
  - The epoch budget caps everything.
- **Capture and collusion are hard.** Each value can move only within its limits and by one step per cycle (§10.3), and only by vote. The contract's hard limits can't be voted past.
- **Re-arming comes first.** Old notes go back into use at no hardware cost. New hardware is paid for only where it's needed.
- **Fixed supply holds.** Every credit comes from the fee-funded pool, and minting is still backed 1-for-1. Tests check that total supply never changes (fuzz and invariant).

**What we gave up:** some simplicity, since there are five settings instead of one. And the median rule stays off-chain until it can be built cheaply.

**Superseded options, kept for the record.** The flat 5% cap, the flat 20% cap, and "the lower of 20% or 5 TCL" (the previous recommendation) are replaced by the rule above.
