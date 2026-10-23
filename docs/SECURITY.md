# TCL Security Model

Status: the research and pre-production stage, with no external audit.
Don't deploy to a network that holds real value.

## Threat model

| Asset | Threat | Mitigation | Evidence |
|---|---|---|---|
| Off-chain ledger state | Crash or partial write | SQLite WAL transactions; leaf-to-root updates are atomic; a root journal allows rollback | `tests/test_crash_recovery.py` (SIGKILL) |
| Off-chain state transitions | Forged decay or balance | Groth16 proof of `StateTransition(depth)` | `tests/test_zk_prover.py` |
| On-chain funds | Reentrancy | `nonReentrant` on every mutating entry point; CEI; `withdraw` is the only ETH transfer and comes last | `test_ReentrancyBlocked` |
| On-chain funds | Accounting drift | Invariant `balance == totalBalances + vault` | `invariant_Conservation`, `invariant_LazyConservation` |
| Settlement | Proof replay or fork | `pubSignals[2]` must equal the current `stateRoot`; `newTime` must be strictly increasing and not in the future | `test_ReplayRejected`, `test_SettleEpochChecks` |
| Settlement | Non-canonical public inputs | Every signal must be below the BN254 field (checked in the engine and in the verifier) | `test_SettleEpochChecks` |
| Admin | Unauthorized upgrade, pause or redistribution | `OwnableUpgradeable`; `_authorizeUpgrade` is `onlyOwner`; the implementation constructor calls `_disableInitializers` | `test_AccessControl`, `test_InitializeOnce` |
| Agents | Runaway tasks | Subprocess timeouts and rlimits (Phase 2) | `tests/test_orchestrator*` (PR #3) |

## Circuit constraint guarantees

- All signals are assigned with `<==` or constrained with `===`.
  The only `<--` hints are `DivConst.q` and `DivConst.r`, bound by `num === q*den + r` and the range checks `0 <= r <= den-1` and `q < 2^64` or `2^128`.
  Bounds: `balance < 2^128`, `dt <= 604800`, and every product stays below 2^200, far below the field, so modular wrap is impossible.
- Path bits are boolean (`bit*(bit-1) === 0`).
  Old and new leaves share the same path, so the proof attests to a single-leaf update.
- Taylor-term non-negativity and `fee <= surplus` are enforced with `Num2Bits` range checks.
- CI fails closed: the `zk` job sets `TCL_REQUIRE_ZK=1`, so a missing toolchain fails instead of skipping.
  The `lint` job runs circomspect and fails on any finding other than CS0005, and `tests/test_circuit_hygiene.py` proves those CS0005 hints are bound.
- Tamper tests: a wrong balance, time, account, root or sibling, a non-boolean path bit, `dt` above 7 days or below 0, any changed public signal, or a corrupted proof point each fail at witness generation or at verification.

## Known limitations

1. **Trusted setup:** the ptau and zkey are DEV ONLY (local, single party). Use a public Perpetual Powers of Tau file and an MPC phase 2 before production.
2. **Vault leaf:** `Ledger.settle` writes the account leaf and then the vault leaf. The proof covers only the account leaf, so the vault leaf update isn't proven yet. The next step is a two-leaf or batched circuit.
3. **Multi-step decay:** gaps longer than 7 days need several proofs, one per Taylor step. `build_transition_input` refuses longer gaps.
4. **On-chain and off-chain decay differ:** the contract's per-second linear decay on deposits isn't the same function as the off-chain Taylor rule. The two are separate ledgers, linked only through `settleEpoch`.
5. **Settler trust:** `settleEpoch` is permissioned (settler or owner). Proofs bind the state, but the settler controls liveness.
6. **Owner key:** a single `Ownable` owner. Use a multisig or timelock in production.

7. **Consecutive epochs:** because of item 2, the on-chain `stateRoot` after `settleEpoch` is the intermediate (account-only) root, not the ledger's final root; a second epoch from the same ledger is rejected with `StaleRoot` (fail-closed, but not yet usable). Fixed by the two-leaf circuit.

## Delivery-layer controls (Phase 4)

- **Dev key:** `chain_private_key` defaults to anvil's public account #0 key so local runs work out of the box. It is only safe against local dev chains; set `TCL_CHAIN_PRIVATE_KEY` from a secret store for anything else, and never put it in `config.yaml`.
- **HTTP /health** is read-only (GET only, no task submission) and binds to `127.0.0.1` by default; the container binds `0.0.0.0` but compose publishes it on `127.0.0.1:8787` only. Task submission stays on the `0600` Unix socket.
- **Replay:** re-sending a settled proof reverts with `StaleRoot` (tested in `test/integration/test_cli_zk_chain.py`); a tampered public signal fails `tcl zk verify`.
- **Verifier/zkey binding:** `tcl chain deploy` exports the verifier from the same zkey that `tcl zk prove` uses, and checks the deployed `stateRoot` equals the ledger root before writing `deployment.json`.
- **Container:** non-root UID 10001; the podman CI job runs it rootless with `--userns=keep-id`.

## Reporting a vulnerability

Open a private GitHub Security Advisory on `onnxscibroccoli/TCL-orc` (Security → Report a vulnerability).
Please don't open public issues for security bugs.
