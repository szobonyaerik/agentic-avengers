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

## First, in both modes — the mechanical check (no model)
```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/subprocess_check.py"
```
With no argument it scans `$SUBPROC_CHECK_PATHS`, falling back to `tests/` — set that variable in
the project's `.env` when the tests live somewhere else, because a root that does not exist scans
nothing. That case is CLEAN, but it says so on stderr; if you see the "no such test root" note, the
gate read no files and has cleared nothing.

Exit 1 lists tests that spawn a process without `@pytest.mark.subprocess("<why>")` and a
justification. Exit 2 means a file could not be read or parsed — it fails closed, and the fix is the
unreadable file it names, not a marker. **Do not approve while it is red.** This is the only stage
that can see the cost —
fidelity, cross-family review and verification all read for correctness, and a subprocess is not
incorrect. Four tests that each spawned a nested run of the whole suite once survived all of them
across five phases. Route violations to the **implementer** that owns those tests, not the
spec-writer. `scripts/hook_spec_review.sh` runs the same check on every spec write in both modes, so
normally it is already green by the time you are here.

## Automated mode (`--auto` / SPEC_REVIEW_MODE=auto)
A real second opinion, not a rubber stamp — it fails closed and routes NO-GO back.

1. Run the cross-family checklist gate (decorrelated from the anthropic build family, and from
   fidelity's DeepSeek):
   ```bash
   . "${CLAUDE_PLUGIN_ROOT:-.}/scripts/gate_runner_guard.sh"
   require_gate_runner "${CLAUDE_PLUGIN_ROOT:-.}/scripts/gate_runner.py" || exit 2
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/gate_runner.py" \
     --rubric "${CLAUDE_PLUGIN_ROOT:-.}/prompts/spec-review-rubric.md" \
     --model "${GATE_MODEL:-opencode-go/deepseek-v4-pro}" --author-family "${AUTHOR_FAMILY:-anthropic}" \
     --print-verdict --target "<spec path>"
   ```
   The guard is not decoration: a scaffold `gate_runner.py` that printed a bare `GO` having checked
   nothing was once believed, and this path is the one that runs the gate by hand rather than through
   a hook, so it is where a substituted runner would go unnoticed longest. If the gate stops, its
   stderr carries a `cause=` (`timeout`, `provider-payment-required`, `provider-unreachable`, …) and
   the provider's own words — read the cause before concluding anything about the spec.
   For a spec that is already approved **and** implemented, this is a **re-gate**: pass a bundle
   instead of the bare spec, so the reviewer gates the diff rather than re-rolling a verdict on text
   it already passed. `scripts/hook_spec_review.sh` builds exactly that bundle on every spec write
   under `SPEC_REVIEW_MODE=auto`; prefer letting the hook do it over hand-assembling one here.
2. Read the printed verdict token:
   - **GO** or **REVIEW** → `Edit` the spec frontmatter `review_status: approved` (that one field
     only), then record what was judged so the next re-gate can be scoped to the diff:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" stamp <spec> review GO
     ```
     State the spec may now go to the implementer.
   - **NO-GO** → leave `review_status: pending`, print the report (stderr) and `route_back:
     avenger-spec-writer`. Record the rejection too, against the body that earned it:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" stamp <spec> review NO-GO <report-file>
     ```
     Recording only passes is what left a refusal with no trace of which text was refused, and made
     the next frontmatter-only edit look unchanged-and-therefore-fine.
   - **non-zero exit / no token** → fail closed: do NOT approve; surface the error **including its
     `cause=`**. A `provider-payment-required` or a `timeout` says nothing at all about the spec.

## HITL mode (default)
Load the `grill-me` and `spec-review-checklist` skills, then:

1. **Read context first — and only what the read path gives you.** Read the spec (whole; `work_kind`
   and `criticality` are in *its* frontmatter, so no second document is opened for them), the
   `## Contracts and Decisions` header of its `overview.md`, and the **immediately prior phase's
   contract card** (`handover.md`, capped at 6 KB) — not every prior phase's, and not the card's
   archive. Explore the referenced code paths. Do not ask what these answer.
   This scope is not a suggestion and it is not local to this command: it is
   `skills/pipeline-conventions` § *The document read path*, which owns the table, the reasons and
   the measured costs. This stage fires **once per spec**, so everything it opens is multiplied by
   the spec count — which is why this is the stage the read path is strictest about.
2. **Check the Fidelity Gate didn't hard-block.** If the spec's `fidelity_verdict` is `NO-GO`, stop —
   route back to the Spec Writer before a review is worth doing.
3. **Check whether this is a re-gate.** If the spec is already `review_status: approved` **and**
   `status: in-progress`/`done`, it has been built: review **its changes only**. Get them with
   ```bash
   diff -u <(python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" previous <spec> review) \
          <(python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" body <spec>)
   ```
   Settled text is out of bounds — see "Re-gating a spec that is already implemented" in
   `spec-review-checklist` for what still warrants a full pass. If the first command exits 1 nothing
   was kept, so gate the whole spec.
4. **Grill the reviewer, one question at a time.** Walk the `spec-review-checklist` bar (plus the
   migration/refactor items when `work_kind` calls for them). One question at a time, each with a
   recommendation; resolve upstream items first; chase vague answers to a concrete behavior.
5. **Decide.** Clean pass → set `review_status: approved` (that field only). Unresolved items → leave
   `pending`, list the gaps, route back to `avenger-spec-writer`.

Write nothing except the single `review_status` frontmatter change. Do not edit requirements, tests, or
code — a spec that needs content changes is the Spec Writer's to fix.

> A spec reaches the implementer only when `review_status: approved` AND `fidelity_verdict != NO-GO`.
