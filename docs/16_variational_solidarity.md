# 16: Variational solidarity and consensus admission (experimental)

**Status: opt-in mathematical prototype; not active on any network.**
This design preserves the existing Tendermint-style BFT protocol: >2/3
**equal-weight** validator signatures, seeded personhood sortition,
flat bonds, and one-person-one-vote conviction governance. Stake and capital
never scale the consensus vote. Neither the mock personhood provider nor
the in-process localnet demonstrates Sybil resistance in production.

## What the optimization actually controls

A Lagrangian can rank or reject *protocol transitions*; it cannot cause
an outside company or person to adopt TCL. Participation is voluntary.
The feasible objective is open infrastructure that is attractive through
interoperability, lower fees/overhead, transparent governance, portable
proofs, and governance protections. External legal enforcement,
compulsory routing or monopolization are explicitly **not** consensus powers.

Let the evidence-derived state field at block height h be

- `p_h`: distinct **verified** personhood nullifiers participating in a
  governance-changing transition; `P_h`: eligible verified persons;
- `c_h`: largest beneficial-owner share of circulating balances (PPM);
- `m_h`: beneficial-owner HHI, that is `sum_i (balance_i / supply)^2` (PPM);
- `w_h`: independently metered verification workload units;
- `g_h` and `v_h`: indicators for governance and value-changing transitions.

All shares are integers in [0,10^6]. **Keys and wallet addresses are
not persons or beneficial owners.** In particular, present mock identity
bindings cannot provide these metrics securely. An audited proof provider
must derive them from committed state, not block-proposer declarations.

A spatial/temporal network action is conceptually

```text
S = sum_h L_h;
L_h =  g_h * alpha * (p_h / P_h)
     - beta  * [max(0,c_h - c_max)]^2
     - gamma * [max(0,m_h - m_max)]^2
     - delta * (w_h / w_max)
     + lambda_h * (supply_in - supply_out)
     - sum_j mu_j * max(0, constraint_violation_j).
```

Units and coefficients are normalized; the code evaluates an integer,
fixed-point local density using PPM and never evaluates floating point
inside consensus. The Euler–Lagrange view,
`d/dh (partial L/partial dot q) - partial L/partial q = 0`, is a
**design heuristic for candidate policy schedules**, not a replacement
for BFT or an assertion that a blockchain obeys physical least action.
For discrete state, evaluate the action over candidate transitions and
enforce feasibility as a hard gate. Governance may tune coefficients only
through versioned, timelocked, constitutionally authorized changes.

Current prototype's hard constraints:

1. For governance-changing transitions, `p_h / P_h >= 0.10` by default.
   A new vote still must mature under existing conviction-voting rules.
2. When `c_h > 0.20`, it cannot *increase* from `c_(h-1)`. Likewise, when
   `m_h > 0.15`, it cannot increase. This is a **repair corridor**, not
   retroactive confiscation or censorship of all transactions.
3. `w_h <= 50,000` verified work units; malformed/missing signals reject.
4. Empty and ordinary blocks do not require governance turnout.
5. BFT safety assumptions and signature quorum remain unchanged.

Thresholds are **illustrative**, not approved economic policy. HHI and
largest-share values are only meaningful with audited beneficial
ownership, privacy-preserving aggregation and an agreed supply
denominator; present code has no such adapter.

## Implemented integration

`src/tcl/consensus/variational_policy.py` defines immutable
`PolicyConfig`, `TransitionMetrics`, a fixed-point density, and
`PolicyDecision` with exact violation codes. `TendermintEngine`
accepts an optional `transition_policy`. If enabled, its existing
`_valid_block` path checks both the application's standard block
validation, a chain-bound policy commitment, and
`app.transition_metrics(block, height)`. The application must independently
verify that `app.policy_commitment(height)` comes from an authorized genesis
or coordinated upgrade, and that it matches
`LagrangianPolicy.commitment(app.chain_id)`. The canonical SHA-256 hash uses
versioned, sorted JSON parameters and a domain separator. It binds parameters
to the chain but **does not prove or commit to the implementation code hash**.
A genuine source-code or build-artifact commitment still requires a separate
protocol-level upgrade mechanism.

The metrics callback must independently reconstruct values from previously
committed state and independently verifiable transition evidence. Missing or
malformed commitments and metrics fail closed. The decision is cached by block
hash per height. Mock application callbacks in tests verify software logic,
not the authenticity of real personhood or beneficial-ownership proofs.

**Default is disabled.** No app currently provides the required audited
beneficial-owner and proof-of-personhood metrics. Enabling different
parameters on different validators would break deterministic block
validity and can halt liveness. It MUST NOT be enabled on a shared
network before a genesis/upgrade commitment for configuration and
implementation code hash exists and all validators share the same
admissible transition semantics. Never read local time, mempool
preferences, remote APIs, or a noncanonical float in that path.

## Overhead-free solidarity: practical interpretation

Use cryptographic evidence for enrollments, nullifiers, votes, commit
certificates, and provenance; automated quality gates make participation
less bureaucratic. Local Git worktrees can independently validate
proposed changes, generate reproducible witnesses, and submit review
artifacts. GitHub Actions is a development orchestrator, **not** chain
consensus. Recursive validation must have bounded depth, bounded work
and independent test vectors, or a malicious witness can exhaust
resources. Eliminating *all* governance, identity and dispute-resolution
overhead is not realistic: privacy review, Sybil resistance, appeals and
independent audits require accountable human processes.

## Rollout checklist

1. Independently audit personhood and beneficial-owner aggregation,
   including attack models for Sybil attacks, splits/merges, exchanges,
   privacy leakage, and false positives.
2. Specify canonical proofs, soundness, state commitment, supply
   denominator, aggregation arithmetic, and metered work units.
3. Commit the policy commitment plus separately verified implementation
   code hash to genesis or an approved coordinated upgrade. Implement an
   authenticated, replayable `policy_commitment(height)` application method.
   Pin protocol tests and activate only at an approved block height.
4. Extend localnet/production host with evidence-derived
   `transition_metrics(block, height)`; independently reconstruct all
   metrics during block replay. Never accept metrics from a tx alone.
5. Run deterministic cross-implementation vectors, partitions,
   Byzantine-safety and liveness tests, and economic simulations.
6. Add public policy score/violation audit logs with no raw personal
   identifiers, independent review, clear appeals, and emergency halt.
7. Evaluate real transaction throughput, validation latency,
   inclusiveness and concentration over time; do not claim launch
   readiness until those measurements and external audits exist.

## Test

```bash
PYTHONPATH=src pytest -q tests/test_variational_policy.py tests/test_consensus.py
```
