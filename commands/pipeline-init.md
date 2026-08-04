---
description: Scaffold the plan-build-verify pipeline into this repository (dirs, gitignore, conventions, codemap, prereq check).
allowed-tools: Bash, Read, Write, Edit
argument-hint: "[feature-id] [--runtime claude|opencode|all]"
---

Set up the plan-build-verify pipeline in the current repository. Parse `$ARGUMENTS` for an optional
feature id and an optional `--runtime` (default: `claude`). Do each step, report a short summary, and
write no production code.

1. **Artifact tree.** Create `docs/features/`; if a feature id was given, also
   `docs/features/<id>/` and `docs/features/<id>/phases/`.

2. **gitignore.** Ensure `.gitignore` contains `**/.pytest_cache/`, `session.sqlite` and
   `.gate-session.sqlite` (cosmic-ray sessions), `.gate-cosmic-ray.toml` (the generated diff-scoped
   config), `.gate-tmp.txt`, `.avenger-auto` (the `/avenger-run --auto` permission sentinel),
   `.lavish/` (scratch HTML review surfaces for the plan stop and the retrospective triage), and
   **`.env`** (it holds a live API key). None of these may ever be committed.

2a. **Configuration.** Copy `${CLAUDE_PLUGIN_ROOT}/docs/templates/env.example` to `.env.example`
   in the project, then to `.env` **only if one does not already exist** — check first, never
   overwrite a live `.env`. Tell the user what they must fill in: `OPENROUTER_API_KEY`, and
   `GATE_PROVIDER=openrouter` (without it `gate_runner.py` defaults to the `opencode` CLI and the
   key is ignored). Warn that `GATE_MODEL` and `VERIFIER_GATE_MODEL` must not share
   `AUTHOR_FAMILY` — a same-family gate exits 2, fail-closed. Every gate loads this file via
   `scripts/load_env.sh`; the real environment always wins over it.

2b. **Ship gate config.** Copy `${CLAUDE_PLUGIN_ROOT}/docs/templates/no-mistakes.example.yaml` to
   `.no-mistakes.yaml` in the project **only if one does not already exist** — check first, never
   overwrite. Tell the user to replace the placeholder `lint` and `test` commands with this project's
   real ones, and that `test` must include the feature-level e2e suite, since the ship gate
   (`/avenger-run` §4a) is the only stage that runs it. Without this file the run builds every phase
   and then fails at feature close, so `/avenger-run` checks for it in preflight.

3. **Conventions in context.** Read the `pipeline-conventions` skill and make sure the rules are
   present for the chosen runtime(s): `CLAUDE.md` (Claude Code) and/or `AGENTS.md` (opencode). Create
   or append the section if missing.

4. **Runtime files.**
   - `claude` (default): nothing to vendor — the installed plugin already provides agents, skills,
     commands, and the in-session hooks.
   - `opencode` | `all`: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync_opencode.py"` to generate
     `.opencode/agents` + link skills. To vendor the git floor + opencode surface into a *separate*
     target repo with update detection, use `scripts/install.sh <target>` (see its `--check`/`--prune`).

5. **codemap.** Offer to generate the codebase map:
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/codemap.py" . --lang <python|java|c> --output codebase`
   → `codebase/MOC.md` (the Solution Architect and implementers read it).

6. **Prereq check.** Report the status of `python3`, `pytest`, `cosmic-ray` (incl. `cr-filter-git` on
   PATH — the mutation gate diff-scopes with it), `jq`, `tree-sitter` (for codemap), a
   cross-family provider (`OPENROUTER_API_KEY` set, or `opencode` on PATH), **`no-mistakes`** (the
   feature-close ship gate, `/avenger-run` §4a — needed by interactive and `--auto` runs alike) and
   **`lavish-axi`** (the plan-approval stop `/avenger-run` §3 and the retrospective triage §4b;
   interactive runs only). List anything missing with its fix (`pip install cosmic-ray tree-sitter
   tree-sitter-python`, `brew install jq`). `no-mistakes` and `lavish-axi` have **no fallback** —
   `/avenger-run` stops in preflight when they are absent rather than degrading a stop into a plain
   markdown read.

7. **Code-path note.** If source is not under `src/`, remind the user to update the path glob in
   `hook_verifier.sh`, `gate_ci.sh`, `.opencode/plugin/pipeline-gates.ts`, and `module-path` in
   `cosmic-ray.toml`.

8. **Mutation baseline sanity check.** The gate requires a green suite before it will score anything
   (a mutant counts as killed whenever the test command fails, so a broken suite scores a perfect
   1.0). If a suite already exists, run `cosmic-ray baseline cosmic-ray.toml` once and report the
   result. Mention the tunables: `MUTATION_MIN_SCORE` (default `0.85`) and `MUTATION_BASE` (default:
   merge-base with the default branch).

9. **Summary.** Print what changed and the first commands:
   `@avenger-task-analyst "<feature brief>"` → `@avenger-solution-architect` →
   `@avenger-implementation-planner` → `@avenger-spec-writer` → `/spec-review <spec>` →
   per phase: `@avenger-backend-architect` (writes tests + code, test-first) → `@avenger-handover` →
   once, after the last phase: the implementer in `e2e-author` mode.
   For `opencode`/`all`, also print: `pip install pre-commit && pre-commit install`, and add
   `OPENROUTER_API_KEY` as a CI secret.
