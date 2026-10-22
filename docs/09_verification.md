# Continuous symbolic verification and compiler provenance

The audit has two independent layers: Z3 proves a mathematical ledger abstraction,
and solc compiles the actual contracts and records reproducible provenance. Neither
layer proves that deployed EVM bytecode implements the abstraction. A passing audit
is necessary evidence, not a security certification.

## Run the checks

From the repository root:

```sh
python3 -m pip install -r verification/requirements.txt
python3 -m unittest discover -s tests -p 'test_formal_model.py'
python3 -m verification.ledger_model
node scripts/audit_contracts.cjs
```

The solver dependency is pinned to `z3-solver==4.13.4.0`. Every proof checks the
negation of an invariant. Success requires `unsat`; `sat` is a counterexample and
`unknown` or timeout is an error. Mutation tests deliberately introduce fee burning,
overcharging, commitment reuse, partial rollback, and forgotten nullifiers. Each
must produce a satisfiable counterexample. The tests therefore check that the
properties detect concrete classes of broken transitions.

## Symbolic state and induction boundary

The state contains two named holder balances, an aggregate balance for all other
holders, a vault balance, the controller's token balance, and escrow liabilities.
The controller balance may exceed liabilities because unsolicited transfers are
possible. Liabilities are not counted twice as circulating tokens. Conservation is
`holderA + holderB + other + vault + controller = supply` and solvency is
`controller >= liabilities`.

Balances are symbolic integers, not sampled examples. Supply is positive and at
most `2**128 - 1`, matching the constructor. One arbitrary commitment and nullifier
are tracked; other notes are represented by the remaining liabilities. A locked
note implies permanent existence and liability at least its positive value. This
is a finite-dimensional abstraction, not enumeration of every mapping entry.
Preservation for an arbitrary selected note supports an inductive argument only
when all concrete mapping operations refine these transitions. Initial states
must satisfy the same invariants.

The obligations cover:

- Transfer and redistribution conservation and nonnegative balances.
- Settlement conservation, a charge capped at surplus above the threshold, and
  fractional carry in `[0, SCALE)`.
- The settlement numerator fitting uint256 under the supply bound.
- A fixed-point multiplication loop invariant: operands in `[0, SCALE]` preserve
  that interval and their product fits uint256. Together with initialization and
  decreasing elapsed-time bits, this bounds exponentiation arithmetic.
- Lock and redemption conservation, solvency, and unchanged state when any modeled
  guard or external transfer fails.
- Permanent commitment existence and monotonic spent-nullifier state; successful
  redemption unlocks the note and marks its nullifier spent.

A symbolic decay factor ranges over all integers in `[0, SCALE]`. This proves the
accounting bounds for any such factor, but does not prove approximation accuracy
of the exponential or a particular schedule. The arithmetic model includes
fractional remainder and saturation; it does not model settlement timestamps,
allowances, governance, EVM gas, storage aliasing, or reentrancy call traces.
Self-transfers collapse to the identity after settlement in the concrete ledger;
the two-holder transfer obligation represents distinct addresses.

## Source-to-model refinement obligations

Review these obligations whenever the source hash changes:

| Concrete behavior | Required abstraction relation |
| --- | --- |
| Ledger `balances`, `vaultBalance`, `totalSupply` | Partition all addresses into holders, aggregate, and controller exactly once |
| `_settle` | Derive factor bounds; map numerator division, carry and saturation to symbolic settlement |
| `_decayFactor` | Initialize loop invariant, preserve it at both multiplications, decrease elapsed bit length |
| `_transfer`, `redistribute` | Compose settlement with the corresponding balance transition; inspect address alias cases |
| `lockNote` | Map all range/configuration/allowance checks and external-call success into the lock guard |
| `redeemNote` | Map recipient, nullifier, proof decoding and verifier checks into the redemption guard |
| `notes.exists`, `usedNullifiers` | Show no concrete write clears permanent existence or spent state |
| Reverts and `nonReentrant` | Establish whole-call rollback and prohibit intermediate observable reentry |

Proof validity is assumed as a Boolean oracle in this model. Circuit soundness,
public-signal binding, setup provenance and verifier correctness need their own
checks. The symbolic model is not mechanically extracted from Solidity, and these
refinement obligations remain manual review duties. Recording hashes exposes
changes; it does not discharge a refinement obligation automatically.

## Compiler audit output

`build/audit/contracts.json` records compiler identity, exact compiler-input hash,
source and resolved dependency hashes, model and requirements hashes, UNSAT
results, ABI, storage layouts,
creation bytecode and runtime bytecode templates with SHA-256 digests. Contracts
are discovered recursively, including `contracts/production`. Imports resolve from
`node_modules`. Compilation
uses optimizer runs 200 and the Shanghai EVM target. All compiler diagnostics are
printed and every compiler error fails the command. No SMTChecker is claimed or
silently skipped: this command runs actual compilation and the separate Z3 model.

The generated verifier is included when `build/zk/PufVerifier.sol` exists; run the
repository's ZK setup first when auditing the complete deployment bundle. Compiler
runtime output contains placeholders for constructor immutables. To attest a
specific deployment, record chain ID, address, transaction and constructor inputs,
then compare fetched runtime code to the template after resolving its recorded
immutable references. This script does not fetch chain code or assert that a
particular address was deployed from these sources.
