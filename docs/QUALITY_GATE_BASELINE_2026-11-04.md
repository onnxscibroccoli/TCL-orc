# Documentation quality-gate diagnosis — 2026-11-04

**Lifecycle:** In Development; fail-closed remediation.  
**Evidence source:** Docs-as-Code Quality Gates run `40054917162`, source
`36b5647e3f25826821537c6831d8241aea3bad6b`.  
**Production/security context:** This is documentation-tooling work, not a
review of TCL's consensus, economics or zero-knowledge cryptography.

## What failed

The most recent baseline had these job outcomes:

| Job | Baseline result | Reason |
| --- | --- | --- |
| Formal Math and KaTeX Parsing | PASS | Script executed successfully. |
| Markdown Structure and Accessibility | FAIL | 316 reported Markdown defects in 17 documentation files. |
| Prose Style and Terminology | FAIL before execution | `errata-ai/action-vale@v2` no longer resolves. |
| Institutional PDF generation | SKIPPED | Required preceding jobs failed. |
| Static Pages preview | SKIPPED | PDF job was skipped. |

Most markdown warnings are concentrated in
`docs/13_sovereign_chain_plan.md`,
`docs/prompts/sovereign_architect_prompt.md`, `docs/API.md`, and
`docs/14_tcl_localnet_plan.md`. Warnings include missing heading/list/fence
spacing, empty code-language tags, invalid tables and raw HTML.

## Correction in this PR

- Replace the invalid Vale action with the currently maintained
  `vale-cli/vale-action`, pinned to a reviewed full commit SHA.
- Pin checkout, setup-node, markdownlint and artifact-upload actions.
- Use a deterministic Node major instead of the deprecated Node 20 setup.
- Remove the nondeterministic `npm ci || npm install` fallback from a
  markdownlint action that already bundles its own tool.
- Remove `|| true` from required PDF generation and reject missing PDFs.
- Remove the obsolete separate `pandoc-citeproc` apt dependency; Pandoc's
  `--citeproc` flag remains required.
- Keep Markdown and Vale failures visible. **This PR does not silence
  historical lint violations.**

## Verification and next increment

Run the Docs-as-Code workflow at the exact PR SHA to distinguish:
(1) action resolution, (2) Markdown failures, (3) Vale errors,
(4) PDF execution, and (5) missing Pages artifact.

Then remediate documentation in bounded source-preserving batches.
Review rendered mathematical notation, tables, commands and terminology
after any automatic formatting. Do not lower quality thresholds merely
to obtain green checks, and do not claim the PDF or preview was generated
until the relevant workflow actually executes and publishes an artifact.

## Rollback

Revert this documentation-toolchain PR through a reviewed commit if
the pinned tools prove incompatible. Reintroducing missing artifacts as
successful results is **not** an acceptable rollback.

**Current state:** Existing lint debt remains open; CI result at the
new candidate SHA is pending. No production contracts or financial
invariants were changed.
