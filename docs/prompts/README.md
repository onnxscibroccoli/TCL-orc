# Prompts

Reusable prompts for engineering agents working on TCL.

| File | Purpose |
|---|---|
| `sovereign_architect_prompt.md` | The master prompt for implementing TCL as a sovereign, non-EVM chain. It covers native modules, personhood-seat BFT, in-node Groth16, and a fully offline bundled localnet. It also includes the locked owner decisions, the open decisions (D1, D3, D4, D5), phases P0–P7 with acceptance tests, the port map, the definition of done, the reporting format and anti-hallucination rules. |

How to use these prompts:
- Paste everything below the `---` in the prompt into the agent.
- The prompt depends on `docs/13_sovereign_chain_plan.md`, `docs/13a_consensus_interfaces.md` (PR #13) and `docs/14_tcl_localnet_plan.md` (`feat/localnet`). Read those first.
- When an owner decision changes, update the prompt's L/D tables in the same PR.
