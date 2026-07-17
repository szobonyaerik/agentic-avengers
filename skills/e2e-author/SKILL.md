---
name: e2e-author
description: Use after the final phase of a feature is green — write the small set of end-to-end tests that prove the feature's goal holds through the whole assembled system, traced to the overview rather than to spec ids.
---

# e2e-author

Author the **feature-level end-to-end suite**, once, after the last phase of a feature is green. Every
phase has proven its own slice at its own seam; nothing has yet proven the slices add up to the thing
the feature was for. That is the only job here.

> Runs **once per feature**, not per phase. Not selected by `work_kind` — every feature gets this
> stage regardless of greenfield/migration/refactor.

## Keep it small — this is a hard rule
Target **1-3 tests. More than 5 is a defect**, not thoroughness. E2E tests are the slowest, flakiest,
and worst-localizing tests in the repo: when one goes red it names a broken *feature*, not a broken
line. They earn their keep only by being few and by covering the path that actually matters. If you
want a sixth, the behavior you have in mind almost certainly belongs in a phase's integration suite —
route it to the Test-Author's phase work instead.

Do not enumerate edge cases here. Do not re-test what a phase already covers. Error paths belong at
the phase level unless the *feature's stated goal* is about that error path.

## Inputs
- `docs/features/<feature>/overview.md` — the feature goal and the user-visible outcome. This is the
  source you trace to.
- `docs/features/<feature>/plan.md` — the phase list, to see what the assembled system now spans.
- Each phase's `handover.md` — what was actually delivered.

## Procedure

1. **State the feature's goal in one sentence**, quoted from `overview.md`. If you cannot find a
   user-visible outcome to quote, stop and route back — an e2e with no goal to prove is theatre.

2. **Pick the critical path(s).** Ask: if exactly one flow had to work for this feature to be worth
   shipping, which is it? That is your first test. Add at most two more, each justified by a distinct
   goal in the overview — not a distinct branch of the same flow.

3. **Write them against the outermost boundary**, with the real system assembled: the HTTP API, the
   CLI, the UI — whatever the actual caller touches. No mocks of anything internal. Mock only what you
   cannot run: third-party APIs, LLMs, payment providers.
   - Files go in `tests/e2e/<feature>/` (e.g. `tests/e2e/clickup-sync/test_task_roundtrip.py`).
   - First docstring line traces to the **goal**, not a spec id:
     `"""e2e: <feature> | goal — a task created in ClickUp appears in the dashboard."""`

4. **Confirm they pass.** Unlike phase tests, these are written *after* implementation, so they must
   be **green** on first run. A red e2e here means either the feature genuinely does not work end to
   end — a real finding, route it back with the failure — or your test is wrong. Do not ship it red.

5. **Record them** in `docs/features/<feature>/e2e-mapping.md` and state the suite is frozen: e2e
   tests live under `tests/`, so the frozen contract applies exactly as it does to phase tests.

## Where e2e sits in the pipeline
- **Excluded from the per-edit verifier hook.** `scripts/hook_verifier.sh` scopes to the active phase
  and passes `--ignore=tests/e2e`; these never run on a code edit.
- **Excluded from the mutation gate.** Mutation is diff-scoped per phase and judges phase suites. E2E
  tests are not there to kill mutants and must never be written to farm them.
- **They run at feature close** (and in CI), which is the moment they have something to say.

## e2e-mapping.md format

```markdown
---
feature: <feature>
stage: e2e-author
model: <model>
created: <date>
links: [overview.md]
---
| test | goal (from overview.md) | boundary |
|---|---|---|
| test_task_roundtrip | a task created in ClickUp appears in the dashboard within 60s | HTTP API + real Postgres |
| test_invalid_signature_never_persists | forged deliveries cannot reach the dashboard | HTTP API |
```

## Done when
- 1-3 (max 5) tests exist under `tests/e2e/<feature>/`, each tracing to a quoted goal in `overview.md`.
- Each drives the outermost real boundary, with nothing internal mocked.
- All are green, and `pytest -q tests/e2e/<feature>/` proves it.
- `e2e-mapping.md` exists; the suite is declared frozen.
