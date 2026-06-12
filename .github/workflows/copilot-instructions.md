# Plan-Build-Verify pipeline (GitHub Copilot)

This repository uses the plan-build-verify pipeline. Custom agents live in `.github/agents/` — chain
them with the handoff buttons: **Task Analyst → Solution Architect → Implementation Planner → Spec
Writer → Test-Author → Implementer → Handover**. Skills are in `.github/skills/`. **Gates are
enforced by CI and the pre-commit hook** (`scripts/gate_ci.sh`), not in-session — a commit or PR runs
the fidelity, test, and mutation gates on a fresh cross-family model.

## Conventions (always apply)
1. **Artifacts** under `docs/features/<feature>/` (feature-level: `spec.md`, `fidelity-report.md`,
   `scoped/review-*.md`) and `docs/features/<feature>/phases/<n>-<slug>/` (`test-mapping.md`,
   `implementation-report.md`, `test-execution-report.md`, `handover.md`), each with YAML frontmatter.
2. **Tests are a frozen contract.** Only the Test-Author writes files under `tests/`. Never edit a
   test to make code pass — fix the implementation, or route the issue back to the Test-Author.
3. **Phases run in dependency/risk order**, one at a time, fully through build-and-verify.
4. **Fresh model ≠ author** — gates run on a different (cross-family) model than the author.
5. **Gates fail closed** — a gate that cannot reach a verdict stops; it never passes.
6. **Mutation score, not coverage**, is the test-adequacy target and the stop signal.

## Working in this repo
- Build one phase at a time; do not start the next until the current one is green.
- Expect a commit or PR to be **rejected by the gate workflow** if the spec is incomplete, tests
  fail, or mutants survive. Read the gate report and fix at the stage it names (Spec Writer,
  Implementer, or Test-Author) — don't work around the gate.
- These agents and skills are generated from the canonical `agents/` and `skills/` via
  `scripts/sync_copilot.py`; edit the canonical sources, not the `.github/` copies.
