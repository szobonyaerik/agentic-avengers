---
description: Spec-review gate for one spec — HITL grill-me (default) or automated cross-family AI review (--auto / SPEC_REVIEW_MODE=auto). On a pass, flips review_status to approved.
allowed-tools: Bash, Read, Grep, Glob, Edit
argument-hint: "<path to a spec.md> [--auto]"
---

Run the **second stage of the composed quality wall** against the spec at `$ARGUMENTS` (a
`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`). It follows the automated
Fidelity Gate. Two modes:

- **HITL** (default) — a human is interrogated one question at a time.
- **Automated** — an unattended cross-family AI reviewer runs the same checklist. Selected by the
  `--auto` flag in `$ARGUMENTS`, or by default when `SPEC_REVIEW_MODE=auto` is set in the environment.

Determine the mode: automated if `$ARGUMENTS` contains `--auto` **or** `SPEC_REVIEW_MODE=auto`;
otherwise HITL. Strip `--auto` from `$ARGUMENTS` to get the spec path.

## Automated mode (`--auto` / SPEC_REVIEW_MODE=auto)
A real second opinion, not a rubber stamp — it fails closed and routes NO-GO back.

1. Run the cross-family checklist gate (decorrelated from the anthropic build family, and from
   fidelity's DeepSeek):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/gate_runner.py" \
     --rubric "${CLAUDE_PLUGIN_ROOT:-.}/prompts/spec-review-rubric.md" \
     --model google/gemini-2.5-pro --author-family "${AUTHOR_FAMILY:-anthropic}" \
     --print-verdict --target "<spec path>"
   ```
2. Read the printed verdict token:
   - **GO** or **REVIEW** → `Edit` the spec frontmatter `review_status: approved` (that one field only).
     State the spec may now go to the implementer.
   - **NO-GO** → leave `review_status: pending`, print the report (stderr) and `route_back:
     avenger-spec-writer`.
   - **non-zero exit / no token** → fail closed: do NOT approve; surface the error.

## HITL mode (default)
Load the `grill-me` and `spec-review-checklist` skills, then:

1. **Read context first.** Read the spec, its `overview.md`, its `task-analysis.md` (for `work_kind`),
   and any prior phase `handover.md`. Explore the referenced code paths. Do not ask what these answer.
2. **Check the Fidelity Gate didn't hard-block.** If the spec's `fidelity_verdict` is `NO-GO`, stop —
   route back to the Spec Writer before a review is worth doing.
3. **Grill the reviewer, one question at a time.** Walk the `spec-review-checklist` bar (plus the
   migration/refactor items when `work_kind` calls for them). One question at a time, each with a
   recommendation; resolve upstream items first; chase vague answers to a concrete behavior.
4. **Decide.** Clean pass → set `review_status: approved` (that field only). Unresolved items → leave
   `pending`, list the gaps, route back to `avenger-spec-writer`.

Write nothing except the single `review_status` frontmatter change. Do not edit requirements, tests, or
code — a spec that needs content changes is the Spec Writer's to fix.

> A spec reaches the implementer only when `review_status: approved` AND `fidelity_verdict != NO-GO`.
