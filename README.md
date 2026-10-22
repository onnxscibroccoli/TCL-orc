# Thresholded Circulatory Ledger

TCL is a research implementation of a fixed-supply ledger, thresholded demurrage, physical-note escrow, and zero-knowledge redemption.
Version 0.2 connects the ledger, Poseidon circuit, generated Groth16 verifier, and durable task runtime in executable tests.
It is not a production currency deployment.

## Run the complete protocol checks

Use Node.js 22 or newer, npm, Python 3.10 or newer, Bash, and Git on Linux or Termux.
The local EVM test dependency may use a JavaScript fallback on newer Node versions.

```bash
npm ci
npm run check
```

This compiles the circuit, creates fresh local test ceremony parameters, verifies the proving key, exports a Solidity verifier, and runs runtime, ledger, real-proof, and documentation tests.
Generated keys, witnesses, proofs, and build outputs stay under ignored `build/`.
The ceremony is for local testing only.
A reviewed multi-party ceremony and pinned verification-key manifest are required before deployment.

## Protocol structure

| Path | Responsibility |
| --- | --- |
| `contracts/CirculatoryEngine.sol` | Fixed supply, transfers, allowances, chain-time demurrage, restricted redistribution |
| `contracts/PhysicalMintController.sol` | Backed escrow, immutable verifier, spent-note and nullifier checks |
| `circuits/puf_verify.circom` | Poseidon commitment and recipient-bound secret-knowledge proof |
| `scripts/zk-setup.sh` | Local Groth16 setup and verifier export |
| `tests/zk.test.cjs` | Real witness, proof, generated-verifier deployment, and escrow redemption |
| `agents/orchestrator.py` | Trusted command DAG, dependency gates, timeouts, durable attempts |
| `agents/daemon.py` | Restartable plan polling |
| `bin/worktree-harness` | Isolated branches without forced deletion or implicit credential copying |
| `docs/05_protocol.md` | Implemented invariants, economics, public-signal order, and threat model |

## Execute the protocol task graph

```bash
python3 agents/orchestrator.py --plan agents/plan.json --state .runtime/check-v2.sqlite
python3 agents/daemon.py --plan agents/plan.json --state .runtime/check-v2.sqlite --once
bash bin/worktree-harness create research/next-protocol
bash bin/worktree-harness list
```

Task commands are trusted repository configuration and run with the caller's permissions.
The runtime does not fetch model-generated commands or claim to implement an LLM provider.
Successful tasks are skipped only when task definitions, dependency definitions, and source fingerprints match.
Non-Git workspaces may execute a fresh plan but cannot reuse task state without a reliable source identity.
After a source revision, use a new state path for the new verification run.
After an interrupted running task, reconcile its external effects before using a new state database.
Retries are opt-in because commands can have non-idempotent effects.
Dirty worktrees are preserved when removal is requested.

## Deployment and scope

Deploy the ledger with a governor, initial holder, and fixed supply of at most `uint128.max` base units.
Deploy a verifier generated from the reviewed circuit and ceremony, then deploy the controller with the ledger and verifier addresses.
The governor configures that controller exactly once using `setPhysicalMint`.
Approving and locking notes then debits real ledger balances; verified redemption returns the same amount to the proof-bound recipient.
No live network deployment or automated merge is part of the test suite.

Escrow amounts, addresses, and note commitments are public.
A secret-knowledge proof does not establish physical unclonability, enrollment authenticity, or offline double-spend resistance.
The immunity threshold is per address; per-person enforcement requires an additional identity protocol.
Governance controls redistribution and the initial controller choice.
See the protocol specification for these boundaries and the remaining research work.
