---
name: pipeline-conventions
description: Use when starting work in a repo that uses the plan-build-verify pipeline, or whenever writing a spec, tests, or an implementation phase. The rules every agent and gate must follow.
---

# Pipeline conventions

These rules govern the whole pipeline. Follow them in every stage. (A plugin's CLAUDE.md is not
loaded as context, so this skill is the canonical home for these rules; `/pipeline-init` can also
copy them into a target repo's own CLAUDE.md.)

## 1. Artifacts (in-repo, markdown + YAML frontmatter)
Feature-level → `docs/features/<feature>/`:
- `task-analysis.md`, `overview.md`, `plan.md`, `fidelity-report.md`, `scoped/review-<slice>.md`

Phase-level → `docs/features/<feature>/phases/<n>-<slug>/`:
- `test-mapping.md`, `implementation-report.md`, `test-execution-report.md`, `handover.md`

Spec-level → `docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/`:
- `spec.md` (one file per numbered spec; a phase holds one or more)

Frontmatter on every artifact: `feature`, `phase` (omit for feature-level), `stage`, `model`,
`verdict` (gates only), `created`, `links`. Specs also carry `spec`, `review_status`, `fidelity_verdict`.

## 2. Multi-spec phases + ID scheme
- A **phase** `<n>-<slug>` is an independently verifiable slice; it holds **one or more numbered specs**
  `<n>.<k>` at `.../specs/<n>.<k>-<subslug>/spec.md`.
- Requirement ids are `R<n>.<k>.<m>` (phase.spec.requirement). Every requirement is single + verifiable,
  carries an id, and has paired pass/fail acceptance criteria.
- The **Verifier runs once per phase**, after every spec in the phase is green — not per spec.

## 3. The composed quality wall (per spec)
Every spec passes both, in order, before the Test-Author touches it:
1. **Automated Fidelity Gate** (`gate_runner` + `prompts/fidelity-rubric.md`, cross-family) fires on
   spec write and sets `fidelity_verdict`; `NO-GO` routes back to the Spec Writer.
2. **Spec-review**, in one of two modes, both setting `review_status: approved` on success:
   - **HITL** (default) — `/spec-review <spec>` uses `skills/grill-me` + `skills/spec-review-checklist`
     to interrogate a human one question at a time.
   - **Automated** — `/spec-review <spec> --auto` (or `SPEC_REVIEW_MODE=auto`, hands-off in-session)
     runs `prompts/spec-review-rubric.md` as a cross-family gate (Gemini); GO/REVIEW auto-approves,
     NO-GO routes back. Fails closed like every gate — not a rubber stamp.

A spec reaches the Test-Author only when `fidelity_verdict != NO-GO` **and** `review_status: approved`.

## 4. Tests are a frozen contract
- Only the **Test-Author** writes or edits files under `tests/`.
- The **Implementer never edits tests** — it changes the implementation to satisfy them.
- If a test is genuinely wrong, route back to the Test-Author; never reshape a test to make code pass.
- Every test traces to a spec id `R<n>.<k>.<m>` (recorded in `test-mapping.md`). No spec id → the test should not exist.
- Three test-author modes, set by the task's `work_kind`: **greenfield** (paired RED tests),
  **migration** (port existing tests unchanged; prove parity), **refactor/brownfield** (characterize-
  and-freeze *preserve* behavior, fresh RED for *change* behavior; diff-scoped mutation).

## 5. Phases run in dependency / risk order
The Implementation Planner orders phases by risk and dependency; build one phase at a time, fully
through the build-and-verify loop, before starting the next.

## 6. Fresh model ≠ author
- Gates run on a **different (cross-family) model** than the agent that produced the work.
- The Test-Author (Opus) is a different model than the Implementer (Sonnet).
- This decorrelation is the point — do not run a gate on the same model that authored the work.

## 7. Gates fail closed
- A gate that cannot reach a verdict (missing key, provider down, no JSON, same-family model) **stops**, it does not pass.
- On any non-GO verdict, stop and surface the report and `route_back` target; resume from there.
- **Break-glass** (`GATE_BYPASS="reason"`) is the only override: it is logged to `gate-overrides.log`,
  shown as a visible "⚠ BYPASSED" state, and recorded in the phase `handover.md`. Never silent.

## 8. Test adequacy = mutation score, not coverage
Mutation runs on **cosmic-ray** (session-based; diff-scoped for refactor mode). Add tests until
surviving mutants are killed, then stop. Mutation score = 1 − survival rate. A test that kills no
mutants and is not the sole cover for a behavior is a deletion candidate. Coverage percentage is not the target.