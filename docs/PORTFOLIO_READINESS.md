# Portfolio reviewer readiness — TCL-orc

> **Status:** Reviewable documentation plan, not a production certification. **As of:** 2026-11-11. **Tracking:** [local issue](https://github.com/onnxscibroccoli/TCL-orc/issues/25) · [cross-repository ledger](https://github.com/onnxscibroccoli/broccoli-core/issues/102).  
> Do not interpret AI-assisted source development, successful offline tests or simulations as independently verified industry employment, compliance, or live operations.

## One-minute technical orientation

**Focus:** Deterministic consensus and zero-knowledge research implementation. Experimental Tendermint-style consensus state machine, testnet and ledger implementation, cryptographic note/circuit work, and extensive CI including offline and negative controls.

**What is demonstrable:** The listed source files and explicitly scoped CI runs can be inspected. A reviewer can evaluate the code, rerun supported unit tests, and challenge the architecture decisions. Any claimed practical capability requires the corresponding test, environment, and source revision; not all systems are integrated or generally available.

## Source and reviewer starting points

| Evidence | Path |
| --- | --- |
| Consensus implementation | [Inspect](https://github.com/onnxscibroccoli/TCL-orc/blob/main/src/tcl/consensus/engine.py) |
| Localnet tests | [Inspect](https://github.com/onnxscibroccoli/TCL-orc/blob/main/tests/test_localnet.py) |
| Security limitations | [Inspect](https://github.com/onnxscibroccoli/TCL-orc/blob/main/docs/SECURITY.md) |
| Multi-job CI | [Inspect](https://github.com/onnxscibroccoli/TCL-orc/blob/main/.github/workflows/ci.yml) |

## Executed CI references (historical, scoped)

| Check | Exact run |
| --- | --- |
| Main CI passed | [Run 40054917158](https://github.com/onnxscibroccoli/TCL-orc/actions/runs/40054917158) |
| Docs quality gate failed | [Run 40054917162](https://github.com/onnxscibroccoli/TCL-orc/actions/runs/40054917162) |
| Draft hardening CI passed | [Run 41844226605](https://github.com/onnxscibroccoli/TCL-orc/actions/runs/41844226605) |

These run links refer to the specific branch/commit shown on GitHub. They must not be silently attributed to a different current HEAD, production environment, device or approval state.

## Reproduce the relevant local checks

After cloning the repository and reviewing dependencies and safe environment requirements:

```sh
pip install -r requirements-test.txt && pip install -e . && pytest -q
```

This is a **documented command**, not a claim that this portfolio PR executed it. Some modules require dependencies or privileges, and a missing dependency must be reported rather than converted into a false PASS.

## Ordered actionable changes

1. [ ] Highlight executable consensus/crypto algorithms rather than promising secure real-money circulation.
2. [ ] Explain mocked proof of personhood, test-only trusted setup and absence of an external audit.
3. [ ] Repair the docs quality gate in the existing targeted hardening PR where possible; do not relax lint or security requirements.
4. [ ] Link deterministic localnet and negative-control tests to a reproducible walkthrough.
5. [ ] Publish a testnet demonstration only after reproducible clean-room verification.

## Maturity and verification boundary

Protocol experiment. No external security audit, independent cryptographic assurance, production monetary network or safe use with real value is established.

## Release and artifact strategy

Source bundle and test-only localnet artifacts; avoid presenting as audited currency or financial infrastructure.

## Safe continuation after interruption

1. Follow the local issue and the [master execution ledger](https://github.com/onnxscibroccoli/broccoli-core/issues/102); inspect existing PRs before creating new ones.
2. Check `git rev-parse HEAD`, repo default branch, current feature branches and fresh Actions conclusions.
3. For each checklist item, implement the smallest change on a reviewable PR; record exact SHA, test scope, successes, failed/untested checks and outstanding decisions.
4. Keep a previously failing independent-approval requirement or live-device gate unresolved until a legitimately authorized actor/environment supplies evidence.
5. Re-read this document against source after changing implementation. Do **not** mark a checklist item complete merely because it is documented.

**Human-only / outside repository assertions:** employment history, professional tenure, team-management responsibilities, third-party audit and approvals.