# Plan-Build-Verify pipeline (GitHub Copilot)

This repository runs the plan-build-verify pipeline. Agents live in `.github/agents/`,
skills in `.github/skills/`. Gates run at commit time via pre-commit (`scripts/gate_ci.sh`).

## Conventions (always apply)
1. **Artifacts** under `docs/features/<feature>/` (feature-level: `spec.md`, `fidelity-report.md`,
   `scoped/review-*.md`) and `docs/features/<feature>/phases/<n>-<slug>/` (`test-mapping.md`,
   `implementation-report.md`, `test-execution-report.md`, `handover.md`). YAML frontmatter on each.
2. **Tests are a frozen contract.** Only the Test-Author writes files under `tests/`. The implementer
   never edits a test; a wrong test routes back to the Test-Author.
3. **Phases run in dependency/risk order**, one at a time, fully through build-and-verify.
4. **Fresh model ≠ author** — gates run on a different (cross-family) model than the author; the
   Test-Author model differs from the implementer's.
5. **Gates fail closed** — a gate that cannot reach a verdict stops; it never passes.
6. **Mutation score, not coverage**, is the test-adequacy target and the stop signal.

## Running it
Plan once per feature, then loop per phase. Invoke agents with `@name`:
```
@task-analyst "<feature brief>"
@solution-architect
@implementation-planner
@spec-writer                      # commit spec.md -> fidelity gate runs
@spec-isolation-review slice=<slice>   # once per slice
# per phase, in dependency order:
@test-author      <phase>         # locked RED tests
@backend-architect <phase>        # or @frontend-developer; commit code -> tests run
@handover         <phase>         # PR -> mutation runs
```
```
