# Plan-Build-Verify pipeline (opencode)

This repository runs the plan-build-verify pipeline. Agents live in `.opencode/agents/`, skills in
`.opencode/skills/` (the same `SKILL.md` files Claude Code uses). **Gates fire mid-session** via the
native plugin `.opencode/plugin/pipeline-gates.ts`, and the git floor (pre-commit + CI in
`scripts/gate_ci.sh`) backstops them — both call `gate_runner.py` on a fresh cross-family model.

## Conventions (always apply)
1. **Artifacts** under `docs/features/<feature>/` (feature-level: `task-analysis.md`, `overview.md`,
   `plan.md`, `fidelity-report.md`, `scoped/review-*.md`), `docs/features/<feature>/phases/<n>-<slug>/`
   (`test-mapping.md`, `implementation-report.md`, `test-execution-report.md`, `handover.md`), and
   spec-level `.../phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`. YAML frontmatter on each.
2. **Multi-spec phases + IDs.** A phase is a verifiable slice holding one or more numbered specs
   `<n>.<k>`; requirement ids `R<n>.<k>.<m>`. The Verifier runs **once per phase**, after every spec is green.
3. **Composed quality wall (per spec).** Automated Fidelity Gate on spec write (sets `fidelity_verdict`;
   NO-GO routes back) **and** human grill-me via `@spec-review` (sets `review_status: approved`). A spec
   reaches the Test-Author only when `fidelity_verdict != NO-GO` and `review_status: approved`.
4. **Tests are a frozen contract.** Only the Test-Author writes files under `tests/`; a wrong test
   routes back. Three modes by `work_kind`: greenfield · migration · refactor/brownfield. Plus
   **e2e-author**, run once per feature after the last phase is green (not selected by `work_kind`).
4a. **Integration by default.** Every test drives its requirement through a **seam** (the public entry
   point a caller uses), with real collaborators; mock only trust/cost boundaries. `test-mapping.md`
   carries `level`: `integration` (default) · `e2e` · `narrow` — **`narrow` needs a written
   justification**. Requirements with no integration surface get no dedicated test. Migration is exempt
   (parity outranks the default).
4b. **Feature-level e2e**: 1-3 tests (5 max) in `tests/e2e/<feature>/`, tracing to the goal in
   `overview.md` — the one exception to "no spec id → no test". Recorded in `e2e-mapping.md`. Excluded
   from mutation and from the per-edit verifier hook.
5. **Phases run in dependency/risk order**, one at a time, fully through build-and-verify.
6. **Fresh model ≠ author** — gates run on a cross-family model (family ≠ author).
7. **Gates fail closed** — a gate that cannot reach a verdict (incl. same-family) stops; it never passes.
   Break-glass `GATE_BYPASS="reason"` is logged to `gate-overrides.log`, shown, and recorded in `handover.md`.
8. **Mutation score, not coverage.** cosmic-ray, once per phase, **diff-scoped** via `cr-filter-git`.
   The verdict is **deterministic** (`scripts/mutation_score.py`, not a model): score `>=
   MUTATION_MIN_SCORE` (default **0.85**) → GO with no model call; below → survivors are named as
   missing cases and the phase routes back to the Test-Author. Not 100% on purpose. Baseline-guarded:
   a failing suite would otherwise score 1.0, since a mutant counts as killed whenever tests fail.

## Running it
Plan once per feature, then loop per phase. Invoke agents with `@name`:
```
@avenger-task-analyst "<feature brief>"   # sets work_kind: greenfield|migration|refactor
@avenger-solution-architect
@avenger-implementation-planner           # phases, each with candidate specs <n>.<k>
@avenger-spec-writer                      # writes specs/<n>.<k>-<subslug>/spec.md -> fidelity gate runs
/spec-review <spec>               # HITL grill-me; add --auto (or SPEC_REVIEW_MODE=auto) for automated review -> flips review_status: approved
# per phase, in dependency order, per spec:
@avenger-test-author      <spec>          # locked RED tests (mode by work_kind)
@avenger-backend-architect <spec>         # or @avenger-frontend-developer; commit code -> tests run
# once all specs in the phase are green:
@avenger-handover         <phase>         # per-phase verifier + cosmic-ray mutation run
# once the FINAL phase of the feature is green:
@avenger-test-author      --e2e <feature> # 1-3 feature-level e2e tests -> tests/e2e/<feature>/
```

## Models / provider
Build agents use OpenRouter model ids set in `scripts/sync_opencode.py` (`MODEL_MAP`). Authenticate
once with OpenRouter (`opencode auth login`) or export `OPENROUTER_API_KEY`. The gate models
(DeepSeek/Gemini) are called by `gate_runner.py` via `OPENROUTER_API_KEY`.

## Regenerate after editing canonical files
The canonical agents live in `agents/` and skills in `skills/`. After changing either:
```
python3 scripts/sync_opencode.py
```
This re-transpiles `.opencode/agents/` and ensures `.opencode/skills/` is linked. Do not edit
`.opencode/agents/` by hand — it is generated.

**`.opencode/plugin/pipeline-gates.ts` is the exception:** it is NOT generated. It reimplements the
gate routing (fidelity, verifier, mutation) natively for opencode, so any change to
`scripts/hook_*.sh` — thresholds, scoping, fail-closed rules — must be mirrored there by hand. If the
two drift, the runtimes enforce different rules and only one of them is the one you tested.

## Environment
| var | default | effect |
|---|---|---|
| `MUTATION_MIN_SCORE` | `0.85` | mutation score required to pass the per-phase gate |
| `MUTATION_BASE` | merge-base with default branch | diff base for scoping mutants |
| `PHASE` | most recent phase dir | which phase's tests the verifier hook runs |
| `GATE_MODEL` | per-gate defaults | routes every gate to one model |
| `GATE_BYPASS` | unset | break-glass: logged, visible, never silent |
