# Glossary

**In plain words:** this page explains every special word used in the TCL docs. Words are in A–Z order. No knowledge of cryptocurrency is needed.

| Word | What it means |
|---|---|
| **Anvil** | A program that runs a practice blockchain on your own computer, for testing. |
| **Attestation / vouch** | A statement by a verified person that someone else is a real, unique person. Higher personhood tiers need more vouches. |
| **Audit** | A careful security review by outside experts. TCL has **not** had one yet. |
| **Base unit** | The smallest amount the system counts. 1 TCL = 10^18 base units, like counting in tiny fractions of a cent. |
| **Blockchain** | A shared record kept by many computers at once, so no single one can secretly change it. |
| **BN254** | The specific set of numbers (a *field*) that TCL's proofs do their math in. Math in it "wraps around" at a huge prime number, like clock hours wrap at 12. |
| **Bounty** | A reward paid to someone who reports cheating, with proof. Designed to be worth more than any bribe. |
| **CI (continuous integration)** | Robots that run all the tests every time the code changes. |
| **Circom / circuit** | Circom is a language for writing proof *circuits*. A circuit is a long list of equations a proof must satisfy. |
| **CLI** | Command-line interface: a program you control by typing commands. TCL's is `tcl` (also `tcl-cli`). |
| **Conservation** | The rule that money is never created or destroyed by the system, only moved. |
| **Container** | A sealed box that runs a program with everything it needs. Docker and Podman make containers. |
| **Daemon** | A helper program that runs in the background. |
| **Demurrage** | A small fee on holding money. In TCL it only applies to the part of a balance above the threshold, and it goes to the vault, not to anyone's profit. |
| **Denomination** | The face value printed on a note: 5, 10, 20, 50, 100, 200 or 500 TCL. |
| **Deploy** | Install the contracts on a blockchain. |
| **DORMANT / LIVE** | States of a physical note. LIVE: it can be handed around and deposited. DORMANT: it was deposited and must be re-armed before use. |
| **Double spending** | Trying to spend the same money twice. Nullifiers stop it. |
| **Epoch** | One round or period of time, such as one settlement round. |
| **Era** | How many times a physical note has re-entered the digital system. Each era has its own one-time code. |
| **Groth16** | The specific kind of zero-knowledge proof TCL uses. Proofs are small and quick to check. |
| **Hash** | A data blender: input in, fixed-size code out. Same input, same code; you can't work backwards. |
| **Immunity threshold (T)** | The protected amount. Money at or below it never decays. Currently 5,000,000 TCL in the off-chain code. |
| **Invariant** | A rule that must always stay true, checked by tests. |
| **Ledger** | An account book recording who holds what. |
| **Merkle tree / root** | A way to boil the whole account book down to one short code (the *root*). Change any account and the root changes. |
| **Mint** | Make a new physical note by turning digital TCL into it. |
| **Mock** | A stand-in for a part not built yet. It lets tests run but gives no real security. Personhood is a mock today. |
| **Multisig / timelock** | A multisig needs several people to approve an action. A timelock forces a waiting period before an action happens, so people can react. |
| **Nullifier** | A one-time code, like a torn ticket stub. Once spent, it is rejected forever. |
| **Off-chain / on-chain** | Off-chain: kept by TCL's own software. On-chain: stored on a blockchain. |
| **Personhood (proof of)** | A way to show you are one real, unique human without revealing who you are. Only a mock exists today. |
| **Poseidon** | The hash TCL uses inside proofs, designed to be cheap there. |
| **Proof (zero-knowledge, ZK)** | A way to prove a statement is true without revealing private details, like proving you're over 18 without showing your birth date. |
| **PUF / secure element** | Chip features that make a physical note hard to copy. Simulated in software today. |
| **Re-entry** | Depositing a physical note back into a digital account. |
| **Redistribution** | Paying money out of the vault back to people. |
| **Rollback** | Undoing recent changes to the account book, e.g. after a crash. Never un-spends nullifiers. |
| **Settlement / settler** | Recording a proven update on the blockchain. The settler is the account allowed to do it. |
| **Smart contract** | A small program that lives on a blockchain and runs exactly as written. |
| **Surplus** | The part of a balance above the threshold. Only the surplus decays. |
| **Sybil attack** | Pretending to be many people (many fake accounts) to get around per-person limits. |
| **Testnet** | A public practice blockchain where coins have no value. |
| **Trusted setup** | A one-time step that makes the secret keys proofs need. Whoever has its leftover secret could fake proofs. TCL's was run by one developer, so it is for **testing only**; real use needs a public ceremony with many people. |
| **UBI** | Universal basic income: an equal payment to every verified person. Optional in TCL and off until personhood is secure. |
| **Vault** | The shared pot where decayed money goes before it is paid back out. |
| **Velocity** | How quickly money is passed on instead of sitting still. |
| **Worktree** | A separate working copy of the code, so several tasks can run side by side. |
