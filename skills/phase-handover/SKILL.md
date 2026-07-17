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
3. Determine the next phase from the phase plan. If this was the last phase, set `next: e2e` (see
   below), and only after the e2e suite is green does the feature reach `ship`.

## If this was the LAST phase of the feature
The feature is not done yet. Every phase has proven its own slice at its own seam; nothing has yet
proven the slices add up to the feature's goal. Before the feature ships:
1. Set `next: e2e` in this handover.
2. Hand to the **Test-Author in `e2e-author` mode** — it writes the 1-3 feature-level e2e tests that
   prove `overview.md`'s goal holds through the assembled system, into `tests/e2e/<feature>/` +
   `docs/features/<feature>/e2e-mapping.md`.
3. E2E tests are written **after** implementation, so they must be green on the first run. A red one
   is a real finding: the feature does not work end to end. Route it back rather than shipping.
4. Once they are green, the feature is `ship`.

Note the mutation gate does not cover e2e (it is diff-scoped per phase, and e2e tests must never be
written to farm mutants), and the per-edit verifier hook skips `tests/e2e/` — they run at feature
close and in CI (`gate_ci.sh --full`).

## Output format

```markdown
---
feature: <feature>
phase: <phase>
stage: handover
model: <model>
created: <date>
status: green
next: <next-phase-slug | e2e | ship>
mutation_score: <score> (threshold <MUTATION_MIN_SCORE>)
---
## Phase <phase> — handover

<5 lines max: what this phase delivered, the key decision(s) made, and anything the next phase
depends on. No narration — just what a teammate picking up the next phase needs to know.>

### Artifacts
- specs:                 docs/features/<feature>/phases/<phase>/specs/   (one per <n>.<k>)
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
mutation_score: 0.93 (threshold 0.85)
---
## Phase 1-webhook — handover

Implemented the signed ClickUp webhook receiver with idempotent persistence keyed on delivery_id.
Valid deliveries return 200 and store exactly one row; replays are no-ops; forged signatures 401.
Mutation gate cleared at 0.93 after adding the no-double-insert negative case.
Persisted schema: tasks(task_id, delivery_id, raw, received_at).
Phase 2 reads these rows; delivery_id is the dedup key it must not re-create.

### Artifacts
- specs:                 docs/features/clickup-intake/phases/1-webhook/specs/   (1.1-verify, 1.2-persist)
- tests:                 tests/1-webhook/
- test-mapping:          docs/features/clickup-intake/phases/1-webhook/test-mapping.md
- implementation-report: docs/features/clickup-intake/phases/1-webhook/implementation-report.md
- test-execution-report: docs/features/clickup-intake/phases/1-webhook/test-execution-report.md

### Next phase
2-analysis — needs from this phase: the persisted task row and delivery_id.
```

## Done when
`handover.md` exists with `status: green`, a ≤5-line summary, all artifact links, the phase's
`mutation_score`, and a `next` value (a phase slug, `e2e` if this was the last phase, or `ship`).

If any gate was overridden with `GATE_BYPASS`, that is recorded here too — never silently. Name the
gate and the reason, exactly as it appears in `gate-overrides.log`.