---
name: phase-handover
description: Use at the end of a phase to document it.
---

# phase-handover

Close out a finished phase: write a short, durable record and point to what comes next. This
runs after the phase is green (tests pass, mutation gate cleared). Writing `handover.md` is also
what satisfies the Stop-hook artifact check, so the phase isn't considered done until it exists.

## Precondition
The phase must actually be complete: the Verifier passed and the Mutation gate returned GO
(see `test-execution-report.md`). If it isn't green, stop and report what's outstanding instead
of writing a handover.

## Inputs
- The finished phase (`<feature>`, `<phase>` slug).
- The phase plan (to find the next phase).
- The phase's artifacts: spec section, `test-mapping.md`, `implementation-report.md`,
  `test-execution-report.md`, and the feature's `scoped/` reviews.

## Procedure
1. Confirm the phase is green (precondition above).
2. Write `docs/features/<feature>/phases/<phase>/handover.md` in the format below — a 5-line
   summary, links to the artifacts, and the next phase.
3. Determine the next phase from the phase plan. If this was the last phase, set `next: ship`.

## Output format

```markdown
---
feature: <feature>
phase: <phase>
stage: handover
model: <model>
created: <date>
status: green
next: <next-phase-slug | ship>
---
## Phase <phase> — handover

<5 lines max: what this phase delivered, the key decision(s) made, and anything the next phase
depends on. No narration — just what a teammate picking up the next phase needs to know.>

### Artifacts
- spec:                  docs/features/<feature>/spec.md  (section: <phase>)
- tests:                 tests/<phase>/
- test-mapping:          docs/features/<feature>/phases/<phase>/test-mapping.md
- implementation-report: docs/features/<feature>/phases/<phase>/implementation-report.md
- test-execution-report: docs/features/<feature>/phases/<phase>/test-execution-report.md

### Next phase
<next-phase-slug> — needs from this phase: <the one or two things it depends on>.
```

(Use repo-root-relative paths so links don't break when the file moves.)

### Example (phase `1-webhook`)
```markdown
---
feature: clickup-intake
phase: 1-webhook
stage: handover
model: haiku
created: 2026-06-10
status: green
next: 2-analysis
---
## Phase 1-webhook — handover

Implemented the signed ClickUp webhook receiver with idempotent persistence keyed on delivery_id.
Valid deliveries return 200 and store exactly one row; replays are no-ops; forged signatures 401.
Mutation gate clean after adding the no-double-insert negative case.
Persisted schema: tasks(task_id, delivery_id, raw, received_at).
Phase 2 reads these rows; delivery_id is the dedup key it must not re-create.

### Artifacts
- spec:                  docs/features/clickup-intake/spec.md  (section: 1-webhook)
- tests:                 tests/1-webhook/
- test-mapping:          docs/features/clickup-intake/phases/1-webhook/test-mapping.md
- implementation-report: docs/features/clickup-intake/phases/1-webhook/implementation-report.md
- test-execution-report: docs/features/clickup-intake/phases/1-webhook/test-execution-report.md

### Next phase
2-analysis — needs from this phase: the persisted task row and delivery_id.
```

## Done when
`handover.md` exists with `status: green`, a ≤5-line summary, all artifact links, and a `next`
value (a phase slug or `ship`).