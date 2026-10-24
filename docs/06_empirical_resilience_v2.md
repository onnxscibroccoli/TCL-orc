# Empirical Resilience — Iteration 2

> **In plain words.** This is an early research note, kept for history. Where it disagrees with the code or with the [README white paper](../README.md#white-paper-the-mathematics-of-tcl), the code wins. Terms are in the [Glossary](GLOSSARY.md).

Fixed-point demurrage now uses a 5th-order Taylor multiplier with a 7-day step bound and a 55-wei truncation correction on daily-or-longer steps.
Nullifier uniqueness is enforced by a simple integer set; proof-byte hashes are never used for replay protection.
Both modules have unit tests that run under the existing rootless Containerfile path.
Groth16 circuit constraints and public-input canonical ordering remain documented only until a reproducible proving pipeline with failing-closed tests exists.
