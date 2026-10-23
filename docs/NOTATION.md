# Notation

One symbol table for every document in this repository. The values are the code's defaults; see the cited source for the authority.
Rendering rules are in [STYLE_MATH.md](STYLE_MATH.md).

## Ledger and demurrage

| Symbol | Meaning | Value / source |
| --- | --- | --- |
| $B$, $`B_i(t)`$ | Account balance (of address $i$ at time $t$), integer base units | — |
| $S$ | Fixed-point scale | $`10^{18}`$ |
| $T$ | Immunity threshold: balances at or below it never decay | $`5\,000\,000 \cdot S`$ (`demurrage_fixed.py`) |
| $`\sigma(B)`$ | Surplus above the threshold, $`\max(0,\ B - T)`$ | README §1 |
| $r$ (spec: $`r_d`$) | Daily demurrage rate on the surplus | $`0.0005\ \mathrm{day}^{-1}`$ |
| $`\lambda`$ | Continuous per-second decay constant, $`-\ln(1-r)/86\,400`$ | $`\approx 5.788 \times 10^{-9}\ \mathrm{s}^{-1}`$ |
| $`\Lambda`$ | Pinned scaled constant used by the code, $`\approx \lambda S`$ | $`5\,788\,484\,761`$ (`LAMBDA_SCALED`) |
| $`\Delta_{\max}`$ | Longest single Taylor step | $`604\,800\ \mathrm{s}`$ (7 days) |
| $`\Delta t`$ | Elapsed time | seconds (days in doc 03) |
| $`\delta`$ | Length of one Taylor step, $`\delta \le \Delta_{\max}`$ | seconds |
| $d$ | Whole elapsed days (linear rule); also a denomination in the notes spec | — |
| $`x`$, $`t_k`$, $`m_0`$, $m$ | Taylor argument $`\Lambda\delta`$, series terms, raw and corrected multiplier | README §3 |
| $`\mathrm{fee}`$ | Amount moved from an account to the vault in one settlement | — |
| $V$ (spec: $`\mathcal{V}`$) | Vault balance (Merkle leaf 0) | `__vault__` |
| $D$ | Total digital supply (README: total deposits) | — |
| $`\mathrm{Alloc}_q`$, $`W_q`$ | Vault share and work-rate attestation of region $q$ (not implemented) | doc 03 |

## Cryptography and proofs

| Symbol | Meaning | Value / source |
| --- | --- | --- |
| $p$ | BN254 scalar field modulus ($`\approx 2^{254}`$) | README §7 |
| $`\mathbb{F}_p`$ | Field of integers mod $p$ | — |
| $`\operatorname{Poseidon}`$, $`H(\cdot)`$ | Poseidon hash (spec: first input is a domain tag) | `src/tcl/crypto/poseidon.py` |
| $`t`$, $`R_F`$, $`R_P`$ | Poseidon state width, full and partial rounds | README §5 |
| $h$ | Merkle tree depth | `tree_depth` |
| $`Z_k`$ | Root of an empty subtree of height $k$ | README §6 |
| $`b_\ell`$, $`s_\ell`$ | Path bit and sibling at level $`\ell`$ | README §6 |
| $`\mathcal{R}`$ | State root | `stateRoot` |
| $`\mathcal{N}`$, $n$ | Nullifier set and one nullifier | `nullifiers.py` |

## Physical notes (docs/11, docs/12)

| Symbol | Meaning |
| --- | --- |
| $`d \in \mathcal{D}`$, $`V_d`$ | Denomination and its face value |
| $`\mathsf{id}`$, $k$ | Permanent note identity, era counter |
| $P$, $I$ | Physical supply, incentive pool |
| $`\Theta`$ | Re-entry hoarding threshold (default $`\Theta = T`$) |
| $`C_d`$, $`R_d`$ | Production-cost credit and mint reward for denomination $d$ |
| $`\tilde{c}_d`$, $`\gamma_c`$ | Median attested cost, cost-cap fraction |
| $`\rho`$ | Mint reward fraction of face value |
| $U$, $E$, $`W_r`$, $`W_a`$ | Re-entry unlock amount, excess, release window, aggregation window |
| $`\kappa`$, $`\lambda_h`$ | Idle-decay multiplier, accelerated idle decay rate |
| $`\nu`$, $`\theta_{\min}`$ | Per-person pseudonym, personhood floor |
| $`b_o`$, $`b_r`$, $`\mathcal{B}`$, $`\tau`$ | Offender bond, reporter bond, bribe, pool top-up (bounties) |

## Known overloads (do not add more)

These letters currently mean two things in different sections. They are listed so readers are not misled; renaming them would change published text and is left to the owner.

- $`\sigma`$: surplus $`\sigma(B)`$ (README, spec §2) and bounty share $`\sigma \in (0,1)`$ (spec bounty section).
- $R$: mint reward $`R_d`$ / bounty payout $R$ (spec) and partial-round count $`R_P`$, $`R_F`$ (README).
- $p$: field modulus (README) and report-success probability (spec bounty section).
- $g$: genesis flag $`g \in \{0,1\}`$ and a duration parameter in the spec governance table.
- $d$: whole days (README §2) and denomination (spec).
- $t$: time, and Poseidon width / Taylor terms $`t_k`$ (README).
