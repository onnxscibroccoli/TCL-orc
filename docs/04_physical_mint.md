# Physical Mint and Escrow Specification

> **In plain words.** This is an early research note, kept for history. Where it disagrees with the code or with the [README white paper](../README.md#white-paper-the-mathematics-of-tcl), the code wins. Terms are in the [Glossary](GLOSSARY.md).

Physical notes are locked by submitting a Pedersen commitment of the note value and a blinding factor.
Redemption requires a zero-knowledge proof $\pi_{\text{puf}}$ that the presenter controls the corresponding Physical Unclonable Function response.
The commitment is nullified upon successful redemption so that the same note cannot be redeemed twice.
All operations preserve the anonymity of the depositing and redeeming parties.
