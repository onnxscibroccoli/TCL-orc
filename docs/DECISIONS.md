# Owner decisions log

| ID | Decision | Status | Date (ET) | Source |
|---|---|---|---|---|
| L13 | Remove the production-cost refund (C) from contracts and native modules. Damaged notes are handled purely off-chain and retired into off-chain transactions, with no on-chain refund. Mint = burn V → note V + bounded reward R. | **DECIDED** | 2026-10-23 | Owner (Ian) |
| D1 | Proof curve: BN254 or BLS12-381 | PENDING-OWNER | | docs/13 §6 |
| D3 | Ed25519: in-house RFC 8032 or vendored `cryptography` | PENDING-OWNER | | docs/13 §2 |
| D4 | P2P crypto: in-house Noise-XX or vendored | PENDING-OWNER | | docs/13 §4 |
| D5 | Encoding: CBOR subset or TLV | PENDING-OWNER | | docs/13 §4 |

**Consequence of L13 (not a new decision):** without C, minters bear the production cost themselves. Minting is profitable only if R, or off-chain economics such as manufacturer funding from the 30% genesis auction, covers that cost. This needs to be modeled.
