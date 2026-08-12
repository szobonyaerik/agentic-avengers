---
description: The HUMAN spec-review sign-off for one spec — a grill-me interrogation against the checklist. The machine gate already ran on write; this sets review_status to approved.
allowed-tools: Bash, Read, Grep, Glob, Edit
argument-hint: "<path to a spec.md> [--auto]"
---

Run the **human half** of the quality wall against the spec at `$ARGUMENTS` (a
`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`).

**The machine gate already ran.** `scripts/hook_spec_gate.sh` fired on the spec's write: it counted
the requirement cap, ran the subprocess check, observed the spec on one model, triaged those
observations against the closed blocking set on another, and stamped `spec_gate: approved | blocked`.
This command does **not** re-run it and does not run a second rubric — that is precisely the shape
that was removed. Two model gates over one document at one moment once passed a spec on one and
failed it on the other, on byte-identical text.

What is left here is the thing a model cannot do: **interrogate a human** about whether this spec is
the right thing to build.

Determine the mode: `$ARGUMENTS` containing `--auto`, or `SPEC_REVIEW_MODE=auto` in the environment,
means **unattended**. Strip `--auto` to get the spec path.

## Unattended mode (`--auto` / `SPEC_REVIEW_MODE=auto`)

There is no human to grill, and the machine gate is deliberately the whole wall — it already
stamped `review_status: approved` itself when it approved the spec. So:

1. Read the spec's `spec_gate`:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_state.py" status "<spec path>"
   ```
2. `approved` → confirm `review_status: approved` is present and say the spec may go to the
   implementer. **Do not run a model.** If `review_status` is somehow still `pending`, `Edit` that one
   field.
3. `blocked` → stop and route back to `avenger-spec-writer`, reproducing the gate's report:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" report "<spec path>" gate
   ```
4. `pending` → the gate has not run. Do **not** approve. Re-write the spec so the hook fires, or say
   the hook did not fire — an ungated spec reaching the implementer is the failure this wall exists
   to prevent.

## HITL mode (default)

Load the `grill-me` and `spec-review-checklist` skills, then:

1. **Read context first — and only what the read path gives you.** Read the spec (whole; `work_kind`
   and `criticality` are in *its* frontmatter, so no second document is opened for them), its
   `spec-notes.md` sidecar if one exists (the gate's known-open notes — context for your questions,
   never a list of things to demand), the `## Contracts and Decisions` header of its `overview.md`,
   and the **immediately prior phase's contract card** (`handover.md`, capped at 6 KB) — not every
   prior phase's, and not the card's archive. Explore the referenced code paths. Do not ask what
   these answer.
   This scope is not a suggestion and it is not local to this command: it is
   `skills/pipeline-conventions` § *The document read path*, which owns the table, the reasons and
   the measured costs. This stage fires **once per spec**, so everything it opens is multiplied by
   the spec count.
2. **Check the machine gate did not block.** If `spec_gate` is `blocked`, stop — route back to the
   Spec Writer before a human review is worth doing. If it is `pending`, the gate never ran; say so
   rather than standing in for it.
3. **Check whether this is a re-gate.** If the spec is already `review_status: approved` **and**
   `status: in-progress`/`done`, it has been built: review **its changes only**. Get them with
   ```bash
   diff -u <(python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" previous <spec> gate) \
          <(python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/spec_gate_cache.py" body <spec>)
   ```
   Settled text is out of bounds — see "Re-gating a spec that is already implemented" in
   `spec-review-checklist` for what still warrants a full pass. If the first command exits 1 nothing
   was kept, so review the whole spec.
4. **Grill the reviewer, one question at a time.** Walk the `spec-review-checklist` bar (plus the
   migration/refactor items when `work_kind` calls for them). One question at a time, each with a
   recommendation; resolve upstream items first; chase vague answers to a concrete behavior.
5. **Decide.** Clean pass → set `review_status: approved` (that field only). Unresolved items → leave
   `pending`, list the gaps, route back to `avenger-spec-writer`.

**Ask for a split, never for more prose.** If the spec is genuinely doing too much, the remedy is
sibling specs under the same phase — `scripts/requirement_cap.py` caps it at 12 requirements and
says the same thing mechanically. A spec that answers a review with more text is the ratchet this
pipeline measured: 25k characters to 51k across four rejected rounds.

Write nothing except the single `review_status` frontmatter change. Do not edit requirements, tests,
or code — a spec that needs content changes is the Spec Writer's to fix.

> A spec reaches the implementer only when `review_status: approved` AND `spec_gate: approved`.
