---
layout: default
type: section
title: Multi-Agent Orchestration and Automated Redistribution
last_updated: 2026-10-21
authors:
  - name: Ian Jules Cossette
    affiliation: Independent Cryptoeconomic Systems Researcher
    email: icossett97@gmail.com
    orcid: 0000-0002-1825-0097
---

# Multi-Agent Orchestration

> **In plain words.** This is an early research note, kept for history. Where it disagrees with the code or with the [README white paper](../README.md#white-paper-the-mathematics-of-tcl), the code wins. Terms are in the [Glossary](GLOSSARY.md).

The Thresholded Circulatory Ledger coordinates concurrent authoring and runtime operations through an isolated multi-agent worktree orchestrator.
Each specialized agent is assigned a dedicated Git worktree channel that prevents write conflicts while sharing a single object store.
The orchestrator maintains a directed acyclic graph of tasks whose nodes include frontmatter validation, architectural synthesis, mathematical formalization, tokenomic modeling, and quality-gate enforcement.
Agents emit only identifier-based citations and sentence-per-line Markdown so that pull-request diffs remain sentence-granular.
Cross-agent state is synchronized exclusively through shared citation keys and formally declared invariants rather than mutable global variables.

# Wealth Degradation at the Immunity Threshold

Balances exceeding the personal immunity threshold $`T = 5\,000\,000`$ AETH are subject to continuous demurrage.
The state transition for an address $i$ over interval $\Delta t$ is given by
```math
B_i(t + \Delta t) = B_i(t) + \Delta x_{\mathrm{in}} - \Delta x_{\mathrm{out}} - \max\!\left(0,\; r \cdot \bigl(B_i(t) - T\bigr) \cdot \Delta t\right)
\tag{3.1}
```
where $`B_i(t)`$ is the balance of address $i$ at time $t$, $`\Delta t`$ is the elapsed time in days, $`\Delta x_{\mathrm{in}}`$ and $`\Delta x_{\mathrm{out}}`$ are the amounts received and sent during the interval, $T$ is the immunity threshold, and $`r = 0.0005\ \mathrm{day}^{-1}`$ is the demurrage decay factor (see [NOTATION.md](NOTATION.md)).
Demurrage is applied only to unshielded or re-entered surplus; offline physical notes and shielded balances below $T$ incur zero degradation.
The extracted surplus is atomically credited to the redistribution pool rather than burned.

# Automated Asset Redistribution Mechanics

Surplus extracted by demurrage is deposited into a DemurrageVault.
A redistribution epoch triggered by the GovernanceOrchestrator or by a scheduled agent allocates the vault balance according to a work-rate parity function.
The allocation to region $q$ is proportional to verified productive activity recorded by Witness Aggregators:
```math
\mathrm{Alloc}_q = V \cdot \frac{W_q}{\sum_{j} W_j}
\tag{3.2}
```
where $`\mathrm{Alloc}_q`$ is the amount sent to region $q$, $V$ is the vault balance, $`W_q`$ is the aggregate work-rate attestation for region $q$, and the sum runs over all regions $j$.
Redistribution transactions are executed as shielded state transitions that preserve anonymity of both source and recipient.
Any residual vault balance after allocation remains available for the subsequent epoch.
