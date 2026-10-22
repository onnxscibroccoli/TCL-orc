# Physical Mint and Escrow Specification

The executable prototype locks real ledger balances against Poseidon commitments of a private secret, blinding scalar, value, and deployment context.
Redemption requires a Groth16 proof of knowledge of the corresponding secret and blinding scalar, bound to the recipient, value, and context.
The commitment is permanently marked spent after successful redemption so that the same note cannot be redeemed twice.
Nullifiers provide an additional replay check and are bound to the chosen redemption recipient.
The configured controller escrow is exempt from demurrage while the note remains locked.
Depositor, denomination, recipient, commitment, and redemption linkage remain public.
The implementation proves secret knowledge rather than physical unclonability and does not yet provide anonymous minting, shielded payments, or offline double-spend prevention.
The complete executable statement, trust assumptions, and remaining physical enrollment requirements are specified in `05_protocol.md`.
