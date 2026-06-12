# Plan-Build-Verify pipeline (opencode)

This repository runs the plan-build-verify pipeline. Agents live in `.opencode/agents/`, skills in
`.opencode/skills/` (the same `SKILL.md` files used by Claude Code and Copilot). **Gates are not
in-session here** — they are enforced by the git floor (pre-commit + CI in `scripts/gate_ci.sh`),
which calls `gate_runner.py` on a fresh cross-family model. Commit to trigger them.

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
