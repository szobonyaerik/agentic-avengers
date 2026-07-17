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
- `task-analysis.md`, `overview.md`, `plan.md`, `fidelity-report.md`, `scoped/review-<slice>.md`,
  `e2e-mapping.md` (written once, at feature close — see §4b)

Phase-level → `docs/features/<feature>/phases/<n>-<slug>/`:
- `test-mapping.md` (table: test → spec id → type → **level** → justification [→ source] [→ partition]),
  `implementation-report.md`, `test-execution-report.md`, `handover.md`

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
**The principle: whoever changed the code may not write or change the test that judges it.** Everything
below follows from that, and it is the reason the suite is worth anything — a test its own author's
code has to pass proves only that the author agreed with themselves.

- Only the **Test-Author** writes or edits files under `tests/`, and the Test-Author never writes
  production code.
- The **Implementer never touches `tests/`** — not to fix, relax, skip, `xfail`, or delete. It changes
  the implementation to satisfy them. This binds every implementing agent (backend, frontend,
  bug-hunter), for every reason, including "the test is obviously wrong".
- The **bug-hunter** fixes code, so it may not write its own regression test: reproduce outside
  `tests/`, fix, then route the reproduction to the Test-Author to write and lock.
- The **Breaker** is the one non-Test-Author that may land a file under `tests/` — and only because it
  **never edits production code**, so it cannot grade its own work. It writes a failing counterexample
  and routes it to the Test-Author, who traces, owns, and locks it.
- If a test is genuinely wrong, route back to the Test-Author; never reshape a test to make code pass.
- Every phase test traces to a spec id `R<n>.<k>.<m>` (recorded in `test-mapping.md`). No spec id →
  the test should not exist. **The one exception is the feature-level e2e suite** (§4b), which traces
  to the feature goal in `overview.md` and is recorded in `e2e-mapping.md`.
- Three test-author modes, set by the task's `work_kind`: **greenfield** (paired RED tests),
  **migration** (port existing tests unchanged; prove parity), **refactor/brownfield** (characterize-
  and-freeze *preserve* behavior, fresh RED for *change* behavior).

## 4a. Tests are integration-level by default
- Every test drives its requirement through a **seam** — the public entry point a caller actually uses
  (HTTP handler, service method, CLI) — with real collaborators wired up. Mock only what crosses a
  trust or cost boundary (third-party API, LLM, vault), never what lives inside the seam.
- `test-mapping.md` carries a `level` column: `integration` (default) · `e2e` (feature-level only) ·
  `narrow`. A **`narrow` test requires a written justification** in the mapping — permitted only when
  a requirement has no reachable seam. "Easier to write" is not a justification.
- Rationale: tests bound to internal structure are the ones rewritten on every refactor. Tests bound
  to a seam survive it — which is what makes a frozen contract affordable.
- Requirements with no integration surface of their own (pure helpers, parsers, mappers) get **no
  dedicated test**; they are covered transitively through the seam that uses them. If that leaves a
  real blind spot, the mutation gate (§8) is what surfaces it — not a preemptive narrow test.
- **Migration mode is exempt**: parity outranks the default, so ported tests keep their original level.

## 4b. Feature-level e2e (`skills/e2e-author`)
- Written **once per feature, after the final phase is green** — never per phase.
- **1-3 tests, 5 is a hard ceiling.** They prove the assembled system delivers the feature's goal;
  they are not where edge cases live.
- Live in `tests/e2e/<feature>/`, trace to the goal quoted from `overview.md`, recorded in
  `e2e-mapping.md`. Frozen like every other test.
- **Excluded from the per-edit verifier hook and from the mutation gate.** They must never be written
  to farm mutants.

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
Mutation runs on **cosmic-ray**, once per phase, and is **diff-scoped in every mode**: the gate adds a
`[cosmic-ray.filters.git-filter]` section naming the diff base and runs `cr-filter-git`, so a phase is
judged only on the lines it changed — not on the whole package. Coverage percentage is not the target.

- **Mutation score = 1 − (survivors / mutants actually tested).** Skipped (out-of-diff) and incompetent
  mutants are excluded from the denominator.
- **The verdict is deterministic and computed by `scripts/mutation_score.py`, not by a model.** Score
  `>= MUTATION_MIN_SCORE` (env, default **0.85**) → GO, and no model is called at all. Below it → the
  survivors go to the gate model, which names the missing case, and the phase routes back to the
  Test-Author.
- **The threshold is not 100%.** Chasing zero survivors is what turns the Test-Author into a
  mutant-farming loop and multiplies narrow tests. Add the cases the survivors name, clear the bar,
  stop. A test that kills no mutants and is not the sole cover for a behavior is a deletion candidate.
- **Fails closed** (§7) when it cannot score honestly: no mutants generated, nothing actually tested,
  or — critically — a **failing baseline**. A mutant counts as killed whenever the test command fails,
  so a broken suite would otherwise score a perfect 1.0. The gate runs `cosmic-ray baseline` first and
  refuses to score until the unmutated suite is green.
- Tuning: `MUTATION_MIN_SCORE` (default `0.85`), `MUTATION_BASE` (default: merge-base with the default
  branch).