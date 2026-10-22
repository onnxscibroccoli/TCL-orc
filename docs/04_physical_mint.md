# Physical Mint and Escrow Specification

Physical notes are locked by submitting a Pedersen commitment of the note value and a blinding factor.
Redemption requires a zero-knowledge proof $\pi_{\text{puf}}$ that the presenter controls the corresponding Physical Unclonable Function response.
The commitment is nullified upon successful redemption so that the same note cannot be redeemed twice.
All operations preserve the anonymity of the depositing and redeeming parties.
