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
- `spec.md`, `fidelity-report.md`, `scoped/review-<slice>.md`

Phase-level → `docs/features/<feature>/phases/<n>-<slug>/`:
- `test-mapping.md`, `implementation-report.md`, `test-execution-report.md`, `handover.md`

Frontmatter on every artifact: `feature`, `phase` (omit for feature-level), `stage`, `model`,
`verdict` (gates only), `created`, `links`.

## 2. Tests are a frozen contract
- Only the **Test-Author** writes or edits files under `tests/`.
- The **Implementer never edits tests** — it changes the implementation to satisfy them.
- If a test is genuinely wrong, route back to the Test-Author; never reshape a test to make code pass.
- Every test traces to a spec id (recorded in `test-mapping.md`). No spec id → the test should not exist.

## 3. Phases run in dependency / risk order
The Implementation Planner orders phases by risk and dependency; build one phase at a time, fully
through the build-and-verify loop, before starting the next.

## 4. Fresh model ≠ author
- Gates run on a **different (cross-family) model** than the agent that produced the work.
- The Test-Author (Opus) is a different model than the Implementer (Sonnet).
- This decorrelation is the point — do not run a gate on the same model that authored the work.

## 5. Gates fail closed
- A gate that cannot reach a verdict (missing key, provider down, no JSON) **stops**, it does not pass.
- On any non-GO verdict, stop and surface the report and `route_back` target; resume from there.

## 6. Test adequacy = mutation score, not coverage
Add tests until surviving mutants are killed, then stop. A test that kills no mutants and is not the
sole cover for a behavior is a deletion candidate. Coverage percentage is not the target.