# Empirical Resilience — Iteration 2

The fixed-point compatibility helper delegates to the contract-exact integer reference.
The Taylor polynomial and arbitrary truncation correction were removed because they diverged from the deployed ledger mechanics.
Use the remainder-aware settlement API for repeated calculations.
The integer nullifier set is a local demonstration, not persistent or atomic contract storage.
Actual Groth16 proving, rerandomized replay rejection, governance, and ledger tests execute through the rootless validation pipeline.
See the client protocol document for current operational acceptance boundaries.
