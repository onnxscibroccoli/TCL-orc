# TCL-orc — Thresholded Circulatory Ledger

Worktree orchestration framework for the Thresholded Circulatory Ledger (TCL): multi-agent parallel development, an off-chain Poseidon-Merkle demurrage ledger, Groth16 state-transition proofs, and on-chain settlement.

> **Status:** research / pre-production. No external audit. Do not deploy to a network holding real value. See [`docs/SECURITY.md`](docs/SECURITY.md).

## Contents

- [Overview](#overview)
- [Quickstart](#quickstart)
- [Architecture summary](#architecture-summary)
- [White paper: the mathematics of TCL](#white-paper-the-mathematics-of-tcl)
  - [1. Notation and units](#1-notation-and-units)
  - [2. Demurrage: the per-day linear rule](#2-demurrage-the-per-day-linear-rule)
  - [3. Demurrage: the fixed-point Taylor rule](#3-demurrage-the-fixed-point-taylor-rule)
  - [4. Vault, fee flow and redistribution](#4-vault-fee-flow-and-redistribution)
  - [5. Conservation invariants](#5-conservation-invariants)
  - [6. Nullifiers and rollback](#6-nullifiers-and-rollback)
  - [7. Poseidon over BN254](#7-poseidon-over-bn254)
  - [8. The Poseidon Merkle state tree](#8-the-poseidon-merkle-state-tree)
  - [9. The state-transition statement](#9-the-state-transition-statement)
  - [10. Circuit constraints and range checks](#10-circuit-constraints-and-range-checks)
  - [11. On-chain lazy O(1) decay](#11-on-chain-lazy-o1-decay)
  - [12. Epoch settlement and StaleRoot](#12-epoch-settlement-and-staleroot)
  - [13. Off-chain vs on-chain: what differs, what is proven](#13-off-chain-vs-on-chain-what-differs-what-is-proven)
- [Physical currency (MVP, testnet only)](#physical-currency-mvp-testnet-only)
- [Documentation](#documentation)
- [License](#license)

## Overview

TCL is a currency design in which balances above a personal immunity threshold $T$ slowly decay (*demurrage*). The decayed amount is not burned: it moves into a vault and can be redistributed. This repository implements:

| Layer | What it does | Main code |
|---|---|---|
| Control plane | Task DAG, state machine, sibling git worktrees, Conflict Guard, daemon | `src/tcl/orchestrator/`, `src/tcl/worktree/`, `src/tcl/daemon.py` |
| Off-chain ledger | Lazy demurrage, vault, nullifiers, Poseidon Merkle tree, SQLite WAL storage | `src/tcl/ledger/`, `src/tcl/storage/db.py` |
| ZK proving | Circom `StateTransition(depth)` circuit, Groth16 prover | `circuits/`, `src/tcl/zk/` |
| Settlement | `CirculatoryEngine` (UUPS, pausable, reentrancy-guarded) + Groth16 `StateVerifier` | `contracts/` |
| Delivery | `tcl` / `tcl-cli` CLI, Containerfile, docker-compose, CI | `src/tcl/cli.py`, `Containerfile`, `.github/workflows/ci.yml` |

## Quickstart

Full prerequisites (git, Python 3.11+, Docker/Podman, Foundry, Circom, snarkjs) and walkthroughs are in [`docs/SETUP.md`](docs/SETUP.md). The short version:

```bash
pip install -r requirements-test.txt && pip install -e .   # deps + CLI
pytest -q                         # unit + integration tests (ZK tests skip without circom)
docker compose up --build -d --wait   # anvil chain + daemon
curl -fsS http://127.0.0.1:8787/health
```

CLI reference: [`docs/API.md`](docs/API.md).

## Architecture summary

```text
 tcl-cli ──► daemon (Unix socket, /health) ──► task DAG engine ──► sibling worktrees
                                                     │                    │
                                                     │              Conflict Guard
                                                     ▼
                         Ledger (SQLite WAL) ──► Poseidon Merkle root
                                                     │
                                                     ▼
                       circom StateTransition ──► Groth16 proof
                                                     │
                                                     ▼
                     CirculatoryEngine.settleEpoch ──► StateSettled event
```

Tasks move through `PENDING → ALLOCATED → EXECUTING → VERIFYING → MERGED | FAILED`. Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## White paper: the mathematics of TCL

Every formula below is transcribed from the code that runs, with the source file named in each section. Where the prose specs in `docs/0*_*.md` disagree with the code, **the code is authoritative** and this section follows it.

### 1. Notation and units

| Symbol | Meaning | Value in code |
|---|---|---|
| $B$ | account balance, integer base units (wei-scale) | — |
| $S$ | fixed-point scale | $`S = 10^{18}`$ |
| $T$ | immunity threshold | $`T = 5\,000\,000 \cdot 10^{18}`$ |
| $r$ | nominal daily demurrage rate | $`r = 0.0005 = 5/10\,000`$ per day |
| $\Lambda$ | scaled per-second decay constant | $`\Lambda = 5\,788\,484\,761`$ (i.e. $`\lambda \approx 5.788 \times 10^{-9}\ \mathrm{s}^{-1}`$) |
| $\Delta_{\max}$ | maximum Taylor step | $`604\,800`$ s (7 days) |
| $V$ | vault balance | — |
| $p$ | BN254 scalar field modulus | see §7 |

All arithmetic is on non-negative integers; $`\lfloor\cdot\rfloor`$ denotes floor division (Python `//`, Solidity `/`, circom `\`).

The *surplus* of a balance is

```math
\sigma(B) = \max(0,\ B - T).
```

Balances at or below $T$ never decay.

### 2. Demurrage: the per-day linear rule

*Source: `src/tcl/ledger/demurrage.py` (`apply_demurrage`), used by `Ledger(mode="linear")`.*

For an elapsed whole number of days $d$:

```math
\mathrm{fee}_{\mathrm{lin}}(B, d) =
\begin{cases}
0 & \text{if } B \le T \text{ or } d \le 0,\\
\min\!\left(\sigma(B),\ \left\lfloor \dfrac{\sigma(B)\cdot 5 \cdot d}{10\,000} \right\rfloor\right) & \text{otherwise,}
\end{cases}
```

```math
B' = B - \mathrm{fee}_{\mathrm{lin}}(B, d).
```

In linear mode `Ledger.settle` consumes only whole days, $`d = \lfloor \Delta t / 86\,400 \rfloor`$, and advances `last_update` by $`86\,400\,d`$ so fractional days are carried forward rather than lost (`src/tcl/ledger/service.py`).

### 3. Demurrage: the fixed-point Taylor rule

*Source: `src/tcl/ledger/demurrage_fixed.py` (`apply_demurrage_fixed`), the default `Ledger(mode="fixed")`; mirrored for one step by `DemurrageStep` in `circuits/lib/fixedpoint.circom`.*

The intended continuous law is exponential decay of the surplus:

```math
\sigma(t + \Delta t) = \sigma(t)\, e^{-\lambda \Delta t},
\qquad
\lambda = \frac{-\ln(1 - r)}{86\,400}\ \mathrm{s}^{-1}.
```

With $r = 0.0005$ this gives $`\lambda S \approx 5\,788\,484\,279`$; the code pins $`\Lambda = 5\,788\,484\,761`$ (a relative difference of about $`8 \times 10^{-8}`$).

**One step.** For a step of $`\delta \le \Delta_{\max}`$ seconds, let $x = \Lambda\,\delta$ (so $x/S = \lambda\delta$). The fifth-order Maclaurin terms are computed with truncating division:

```math
t_0 = S,\qquad t_1 = x,\qquad t_k = \left\lfloor \frac{t_{k-1}\, x}{k\, S} \right\rfloor \quad (k = 2,3,4,5)
```

```math
m_0 = t_0 - t_1 + t_2 - t_3 + t_4 - t_5 \;\approx\; S\, e^{-\lambda \delta}
```

A fixed truncation correction is applied to steps of at least one day:

```math
m =
\begin{cases}
\max(0,\ m_0 - 55) & \text{if } \delta \ge 86\,400,\\
\max(0,\ m_0) & \text{otherwise.}
\end{cases}
```

Then

```math
\mathrm{fee} = \sigma(B) - \left\lfloor \frac{\sigma(B)\, m}{S} \right\rfloor,
\qquad
B' = B - \mathrm{fee}.
```

**Multiple steps.** A gap $\Delta t$ is split into $`\lceil \Delta t / \Delta_{\max} \rceil`$ steps of at most 7 days; each step is applied to the balance left by the previous one and the fees are summed. The circuit proves **exactly one** step, so gaps over 7 days need several proofs (`build_transition_input` refuses longer gaps).

**Bounds used by the circuit.** For $`\delta \le 604\,800`$: $`x \lt  2^{52}`$, and $m_0 \gt  0$, so $m$ is a 64-bit non-negative integer and $`0 \le \mathrm{fee} \le \sigma(B)`$.

### 4. Vault, fee flow and redistribution

*Source: `src/tcl/ledger/service.py` (`Ledger.settle`, `Ledger.redistribute`, `Ledger.deposit`), `src/tcl/ledger/demurrage.py` (`redistribute`).*

Decay is *lazy*: it is applied only when an account is touched. Settling account $a$ at time $t_{\mathrm{now}}$ with stored $(B_a, t_a)$:

```math
\Delta t = t_{\mathrm{now}} - t_a \ \ (\text{must be} \ge 0),\qquad
(B_a', \mathrm{fee}) = \mathrm{decay}(B_a, \Delta t),
```

```math
B_a \leftarrow B_a',\qquad t_a \leftarrow t_{\mathrm{now}},\qquad
V \leftarrow V + \mathrm{fee}\ \ \text{(vault written only if } \mathrm{fee} \neq 0\text{)}.
```

The vault is the reserved account `__vault__`, stored at Merkle leaf index $0$.

**Deposit** of $u \gt  0$: settle first, then $`B_a \leftarrow B_a' + u`$.

**Redistribution** of $u$ to account $a$:

```math
0 \le u \le V \quad\Longrightarrow\quad V \leftarrow V - u,\qquad B_a \leftarrow B_a' + u,
```

otherwise the call is rejected. The code exposes single-recipient transfers only; the work-weighted allocation $`\mathrm{Alloc}_r = V \cdot W_r / \sum_j W_j`$ described in `docs/03_orchestration_redistribution.md` is **not implemented**.

### 5. Conservation invariants

**Off-chain** (`src/tcl/ledger/service.py`). Settlement and redistribution only move value between accounts and the vault, so with $D$ the total of all deposits:

```math
V + \sum_a B_a = D.
```

Deposits are the only operation that changes the left-hand side.

**On-chain** (`contracts/CirculatoryEngine.sol`, Foundry `invariant_Conservation`, `invariant_LazyConservation`):

```math
\texttt{address(this).balance} = \texttt{totalBalances} + \texttt{vault}.
```

### 6. Nullifiers and rollback

*Source: `src/tcl/ledger/nullifiers.py`, `src/tcl/ledger/service.py` (`spend_nullifier`, `rollback`).*

A nullifier is a field element $n \in \mathbb{F}_p$. The spent set $\mathcal{N}$ only grows:

```math
\mathrm{spend}(n):\quad n \notin \mathcal{N} \;\Rightarrow\; \mathcal{N} \leftarrow \mathcal{N} \cup \{n\},\qquad n \in \mathcal{N} \;\Rightarrow\; \text{reject}.
```

Uniqueness is by field element, not by proof bytes. Every ledger write appends $`(\mathrm{seq}, \mathrm{root}, a, \mathrm{prev})`$ to a journal. `rollback(R)` undoes entries newer than the latest one whose root equals $R$ and checks the resulting root equals $R$. **Nullifiers are never un-spent by a rollback**, which keeps rollback fail-closed against double spends.

### 7. Poseidon over BN254

*Source: `src/tcl/crypto/poseidon.py` (port of circomlibjs `poseidon_reference.js`; constants in `src/tcl/crypto/poseidon_bn254_t2_t5.json`; known-answer tests in `tests/test_poseidon.py`).*

The field is the BN254 scalar field:

```math
p = 21888242871839275222246405745257275088548364400416034343698204186575808495617.
```

For $n \in \{1,2,3,4\}$ inputs the state width is $t = n + 1$. The permutation uses $R_F = 8$ full rounds and $R_P$ partial rounds:

| inputs $n$ | width $t$ | $R_P$ |
|---|---|---|
| 1 | 2 | 56 |
| 2 | 3 | 57 |
| 3 | 4 | 56 |
| 4 | 5 | 60 |

Starting from $`s = (0, x_1, \dots, x_n)`$, each round $r$ applies add-round-constants, the S-box, and an MDS mix:

```math
s_i \leftarrow s_i + C_{rt+i} \pmod p
```

```math
\text{S-box: } s_i \leftarrow s_i^{5} \bmod p \ \text{ for all } i \text{ (full rounds)},\qquad s_0 \leftarrow s_0^{5} \bmod p \ \text{ only (partial rounds)}
```

```math
s_i \leftarrow \sum_{j=0}^{t-1} M_{ij}\, s_j \pmod p
```

The first $R_F/2 = 4$ and last 4 rounds are full; the $R_P$ rounds in between are partial. The output is $`\mathrm{Poseidon}(x_1,\dots,x_n) = s_0`$. The exponent $5$ is valid because $`\gcd(5, p-1) = 1`$.

Account identifiers map labels into the field with SHA-256:

```math
\mathrm{id}(a) = \mathrm{SHA256}(a) \bmod p.
```

### 8. The Poseidon Merkle state tree

*Source: `src/tcl/ledger/poseidon_tree.py`; circuit mirror `circuits/lib/merkle.circom`.*

A binary tree of fixed depth $h$ (`tree_depth`, default 20 in `config.yaml`; the shipped circuit uses `zk_depth` $=4$; allowed $1 \le h \le 64$) holds $2^h$ leaves.

**Leaf encoding.**

```math
\mathrm{leaf}(a) = \mathrm{Poseidon}\big(\mathrm{id}(a),\ B_a,\ t_a\big),\qquad 0 \le B_a,\, t_a \lt  p.
```

An unused leaf is $0$.

**Node encoding and empty subtrees.**

```math
\mathrm{node} = \mathrm{Poseidon}(\mathrm{left}, \mathrm{right}),\qquad
Z_0 = 0,\quad Z_{k+1} = \mathrm{Poseidon}(Z_k, Z_k).
```

Only non-empty nodes are stored; a missing node at level $k$ equals $Z_k$, and the empty-tree root is $Z_h$.

**Inclusion path.** For leaf index $i$ with bits $`b_\ell = \lfloor i / 2^\ell \rfloor \bmod 2`$ and siblings $`s_0, \dots, s_{h-1}`$ (bottom-up):

```math
H_0 = \mathrm{leaf},\qquad
H_{\ell+1} =
\begin{cases}
\mathrm{Poseidon}(H_\ell,\ s_\ell) & b_\ell = 0,\\
\mathrm{Poseidon}(s_\ell,\ H_\ell) & b_\ell = 1,
\end{cases}
\qquad \mathrm{root} = H_h.
```

In the circuit the swap is branch-free with boolean $b$:

```math
b(b-1) = 0,\qquad \mathrm{left} = H + b\,(s - H),\qquad \mathrm{right} = H + s - \mathrm{left}.
```

Each leaf update recomputes the whole leaf-to-root path inside one SQLite transaction, so a crash cannot leave a half-updated path.

### 9. The state-transition statement

*Source: `circuits/puf_verify.circom` (`StateTransition(depth)`), main component `circuits/main/state_transition_d4.circom`, inputs built by `src/tcl/zk/prover.py`.*

The circuit proves one complete `Ledger.settle(account, newTime)`, as two sequential single-leaf updates:

```math
\mathrm{oldRoot} \xrightarrow{\ \text{account leaf}\ } \mathrm{midRoot} \xrightarrow{\ \text{vault leaf (index 0)}\ } \mathrm{newRoot}
```

**Public signals** (snarkjs order): $`(\mathrm{newRoot},\ \mathrm{fee},\ \mathrm{oldRoot},\ \mathrm{newTime})`$.
**Private witness:** $`\mathrm{id}, B, t_{\mathrm{old}}`$, the account path, $`e_V \in \{0,1\}`$ (vault exists), $`V, t_V`$, and the vault path.

The relation is:

```math
\Delta t = \mathrm{newTime} - t_{\mathrm{old}},\qquad (\mathrm{fee}, B') = \mathrm{DemurrageStep}(B, \Delta t)
```

```math
\mathrm{MerkleRoot}\big(\mathrm{Poseidon}(\mathrm{id}, B, t_{\mathrm{old}}),\ \mathrm{path}\big) = \mathrm{oldRoot}
```

```math
\mathrm{midRoot} = \mathrm{MerkleRoot}\big(\mathrm{Poseidon}(\mathrm{id}, B', \mathrm{newTime}),\ \mathrm{path}\big)
```

```math
\mathrm{id} \neq \mathrm{id}(\texttt{\_\_vault\_\_}),\qquad \mathrm{index}(\mathrm{path}) \neq 0
```

```math
L_V^{\mathrm{old}} = e_V \cdot \mathrm{Poseidon}(\mathrm{id}_V, V, t_V),\qquad (1 - e_V)\,V = 0,\qquad (1 - e_V)\,t_V = 0
```

```math
\mathrm{MerkleRoot}\big(L_V^{\mathrm{old}},\ \mathrm{vaultPath},\ \text{bits}=0\big) = \mathrm{midRoot}
```

```math
L_V^{\mathrm{new}} =
\begin{cases}
L_V^{\mathrm{old}} & \mathrm{fee} = 0,\\
\mathrm{Poseidon}(\mathrm{id}_V,\ V + \mathrm{fee},\ \mathrm{newTime}) & \mathrm{fee} \neq 0,
\end{cases}
\qquad
\mathrm{newRoot} = \mathrm{MerkleRoot}\big(L_V^{\mathrm{new}},\ \mathrm{vaultPath},\ \text{bits}=0\big).
```

Here $`\mathrm{id}_V = \mathrm{id}(\texttt{\_\_vault\_\_})`$ is hard-coded in the circuit as `VAULT_ID()` and pinned by `tests/test_circuit_hygiene.py`. The select is computed as $`L_V^{\mathrm{new}} = N + z\,(L_V^{\mathrm{old}} - N)`$ with $`N = \mathrm{Poseidon}(\mathrm{id}_V, V+\mathrm{fee}, \mathrm{newTime})`$ and $`z = \mathrm{IsZero}(\mathrm{fee})`$. The two updates share the vault path, and because the account cannot sit at index 0 they never alias.

### 10. Circuit constraints and range checks

*Source: `circuits/lib/fixedpoint.circom`, `circuits/puf_verify.circom`; guarantees summarized in `docs/SECURITY.md`.*

**Integer division by a constant** (`DivConst(den, qBits)`, the only `<--` hints in the repo). The prover supplies $q, r$; the circuit enforces

```math
\mathrm{num} = q \cdot \mathrm{den} + r,\qquad 0 \le r \lt  2^{64},\qquad 0 \le \mathrm{den} - 1 - r \lt  2^{64},\qquad 0 \le q \lt  2^{q_{\mathrm{bits}}},
```

with $`0 \lt  \mathrm{den} \lt  2^{64}`$ and $`q_{\mathrm{bits}} \in \{64, 128\}`$. Together these force $`q = \lfloor \mathrm{num}/\mathrm{den} \rfloor`$ over the integers.

**Range checks** (`Num2Bits(k)` proves $0 \le v \lt  2^k$):

| Value | Constraint |
|---|---|
| balance $B$ | $`B \lt  2^{128}`$ |
| $\Delta t$ | $`\Delta t \lt  2^{20}`$ and $`\Delta t \le 604\,800`$ (which also rejects negative $\Delta t$) |
| $t_{\mathrm{old}}$ | $`t_{\mathrm{old}} \lt  2^{64}`$ |
| Taylor quotients $`t_2..t_5`$ | $`\lt  2^{64}`$ |
| $m_0$ and $m$ | $`0 \le m_0, m \lt  2^{64}`$ (no field wrap) |
| $`\lfloor \sigma m / S \rfloor`$ | $`\lt  2^{128}`$ |
| fee | $`0 \le \mathrm{fee} \lt  2^{128}`$, i.e. decayed amount $\le \sigma$ |
| vault before / after | $`V \lt  2^{128}`$, $`V + \mathrm{fee} \lt  2^{128}`$ |

**Threshold comparison** (`GtConst128(T)`): for $`B \lt  2^{128}`$, decompose $`B + 2^{128} - T - 1`$ into 129 bits; bit 128 equals $[B \gt  T]$, so

```math
\sigma = [B \gt  T]\cdot (B - T).
```

**No modular wrap.** Every product stays below $`2^{200} \ll p \approx 2^{254}`$ (e.g. $`\sigma\, m \lt  2^{192}`$, $`x^2 \lt  2^{104}`$), so field arithmetic equals integer arithmetic.

**Size.** At depth 4 the circuit has about 4,100 constraints (reported in the Phase 3 PR before the vault update was added).

### 11. On-chain lazy O(1) decay

*Source: `contracts/CirculatoryEngine.sol` (`_fee`, `_settle`, `balanceOf`).*

The contract stores per account $`(B, t_{\mathrm{last}})`$ as `(uint128, uint64)` plus configurable `threshold` $\theta$ and `ratePerSecond` $\rho$ (scaled by $`10^{18}`$, at most $`10^{18}`$). The fee for elapsed $`\Delta t = \texttt{block.timestamp} - t_{\mathrm{last}}`$ is **linear per second**:

```math
\mathrm{fee}_{\mathrm{chain}}(B, \Delta t) =
\begin{cases}
0 & B \le \theta \text{ or } \Delta t = 0,\\
\min\!\left(B - \theta,\ \left\lfloor \dfrac{(B - \theta)\,\rho\,\Delta t}{10^{18}} \right\rfloor\right) & \text{otherwise.}
\end{cases}
```

It is evaluated in constant time when the account is touched (`settle`, `deposit`, `withdraw`, `redistribute`), never by iterating over accounts. On settlement $`\texttt{totalBalances} \mathrel{-}= \mathrm{fee}`$ and $`\texttt{vault} \mathrel{+}= \mathrm{fee}`$. The view `balanceOf` returns $`B - \mathrm{fee}_{\mathrm{chain}}`$ without writing state.

Note that $\theta$ and $\rho$ are deployment parameters, not the off-chain constants $T$ and $\Lambda$. `tcl chain deploy` (`src/tcl/chain/client.py`) defaults both to $0$.

### 12. Epoch settlement and StaleRoot

*Source: `contracts/CirculatoryEngine.sol` (`settleEpoch`).*

With public signals $`\pi = (\pi_0,\pi_1,\pi_2,\pi_3) = (\mathrm{newRoot}, \mathrm{fee}, \mathrm{oldRoot}, \mathrm{newTime})`$ and stored `stateRoot` $R$ and `lastSettledTime` $\tau$, the call succeeds only if all of the following hold, checked in this order:

```math
\pi_i \lt  p \ \ (i = 0..3)
```

```math
\pi_2 = R \qquad (\text{otherwise revert } \texttt{StaleRoot}(R, \pi_2))
```

```math
\tau \lt  \pi_3 \le \texttt{block.timestamp} \qquad (\text{otherwise } \texttt{InvalidEpochTime})
```

```math
\mathrm{Groth16.Verify}(\mathrm{vk},\ \pi,\ (A,B,C)) = \mathrm{true} \qquad (\text{otherwise } \texttt{InvalidProof})
```

The effects are

```math
R \leftarrow \pi_0,\qquad \tau \leftarrow \pi_3,\qquad \texttt{offchainFeesSettled} \mathrel{+}= \pi_1,\qquad \texttt{epoch} \mathrel{+}= 1,
```

and it emits `StateSettled(epoch, oldRoot, newRoot, fee, newTime)`. Because $R$ becomes $\pi_0$, the next proof must start from exactly this root. Replays, out-of-order proofs and proofs built on a forked state all fail with `StaleRoot`. Only the settler or owner may call it.

### 13. Off-chain vs on-chain: what differs, what is proven

| Aspect | Off-chain ledger | On-chain `CirculatoryEngine` | Proven by ZK? |
|---|---|---|---|
| Decay law | Fixed-point Taylor $e^{-\lambda\Delta t}$, 7-day steps (or per-day linear mode) | Linear per second, $`(B-\theta)\rho\Delta t / 10^{18}`$ | Off-chain Taylor step **yes** (one step, $\le$ 7 days) |
| Threshold / rate | $`T = 5\cdot 10^{24}`$, $`\Lambda = 5\,788\,484\,761`$ (constants) | `threshold`, `ratePerSecond` (deployment params) | Constants are hard-coded in the circuit |
| Account + vault update in `settle` | Yes | — | **Yes** (both leaves) |
| `deposit`, `redistribute` | Yes | Yes (separate on-chain accounting) | **No.** An unproven write between settlements makes the next `settleEpoch` revert `StaleRoot` |
| Nullifiers | Spent set, survives rollback | None in `CirculatoryEngine` | No |
| Link between the two | — | `stateRoot` / `offchainFeesSettled` updated by `settleEpoch` | Only through the root chain |

The two decay functions are **different functions**; the off-chain ledger and on-chain balances are separate ledgers linked only by settlement. Other open limits: the trusted setup is a development-only single-party ceremony, the settler controls liveness, and the owner is a single key. See `docs/SECURITY.md` → *Known limitations*.

---

## Physical currency (MVP, testnet only)

**Damaged or worn notes are handled off-chain only** (owner decision L13, 2026-10-23). They are retired directly into off-chain transactions, with no on-chain damaged-note retirement, replacement or refund path. The production-cost credit C and mint reward R in §7 are unchanged.

> **Experimental.** A physical-notes MVP is on `main`; it is not audited and uses a dev-only trusted setup.

What exists today:

- `contracts/physical/NoteController.sol` mints and redeems reusable notes. Each note keeps a permanent id and gets a fresh one-time spend code each time it re-enters the chain, so an old era's proof is rejected.
- `circuits/note/note_deposit.circom` proves note ownership for a deposit; `contracts/verifiers/NoteDepositVerifier.sol` checks it on-chain.
- Minting burns the face value, refunds an attested production cost (capped at 20%) and pays a reward of up to 1% from an incentive pool; when the pool is empty it falls back to a 1:1 swap. Denominations run from 5 to 500.
- The earlier `PhysicalMintController.sol` skeleton has been removed.
- The file name `circuits/puf_verify.circom` is historical. It holds the ledger `StateTransition` circuit and has nothing to do with PUFs.

Full design (circulation, minting incentives, governance): [`docs/11_physical_currency_spec.md`](docs/11_physical_currency_spec.md) (draft; only the MVP above is implemented).

Known gaps: chip hardware is simulated, minting does not verify that a physical note exists, the hoarding threshold is per address rather than per person, and admin roles are not yet handed to governance.

## Documentation

| Document | Contents |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layer topology and data models |
| [`docs/SETUP.md`](docs/SETUP.md) | Developer onboarding |
| [`docs/API.md`](docs/API.md) | CLI, JSON payloads, contract ABI |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, circuit guarantees, limitations |
| [`docs/11_physical_currency_spec.md`](docs/11_physical_currency_spec.md) | Physical currency specification (draft) |
| `docs/00_whitepaper.md`, `docs/03_*`–`06_*` | Earlier research prose (not authoritative where it differs from code) |

## License

See [`LICENSE`](LICENSE).
