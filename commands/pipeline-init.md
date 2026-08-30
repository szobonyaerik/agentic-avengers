---
description: Scaffold the plan-build-verify pipeline into this repository (dirs, gitignore, conventions, codemap, prereq check).
allowed-tools: Bash, Read, Write, Edit
argument-hint: "[feature-id] [--runtime claude|opencode|all]"
---

Set up the plan-build-verify pipeline in the current repository. Parse `$ARGUMENTS` for an optional
feature id and an optional `--runtime` (default: `claude`). Do each step, report a short summary, and
write no production code.

0. **Survey the repository first — it is not necessarily greenfield.** Every later step that writes
   a file reads this report before it writes (issue #108):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pipeline_init.py" survey
   ```

   It reports, and never blocks: whether the project already owns `.env.example` (then the pipeline's
   template goes to `.env.pipeline.example`, never over the project's file) or whether that file is
   the **pipeline's own** from an earlier init (told apart by its content, and then refreshed in
   place rather than copied a second time), whether a live `.env` or `.no-mistakes.yaml` is already
   there, and whether `cosmic-ray.toml` is missing — **named here rather than at step 8, because two
   later steps require it and neither can create it**, and the report carries the config to write
   rather than a path to it. Report every line of it to the user; the steps below act on it.

1. **Artifact tree.** Create `docs/features/`; if a feature id was given, also
   `docs/features/<id>/` and `docs/features/<id>/phases/`.

2. **gitignore.** Ensure `.gitignore` contains `**/.pytest_cache/`, `session.sqlite` and
   `.gate-session.sqlite` (cosmic-ray sessions), `.gate-cosmic-ray.toml` (the generated diff-scoped
   config), `.gate-tmp.txt`, `.avenger-auto` (the `/avenger-run --auto` permission sentinel),
   `.lavish/` (scratch HTML review surfaces for the plan stop and the retrospective triage),
   `.avenger-gate-cache/` (the body each gate last judged, so a re-gate can be scoped to the diff —
   rebuildable, and a lost entry costs one full re-gate, the safe direction),
   `.avenger-metrics.log` (diagnostics from the fail-open metrics path — the measurements themselves
   live in firstmate's record, never here), and **`.env`** (it holds a live API key). None of these
   may ever be committed.

2a. **Configuration.** Copy `${CLAUDE_PLUGIN_ROOT}/docs/templates/env.example` to the
   **target step 0 named** — `.env.example` in a repository that has none, `.env.pipeline.example`
   in one that already owns that filename. **Never overwrite either file, and never overwrite a live
   `.env`.**

   Then assemble the run's `.env` from every example the project now has, **in that order** — and
   never with `cat`:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/env_assemble.py" assemble \
     --out .env .env.example .env.pipeline.example      # drop the second path when there is only one
   ```

   `cat` joins BYTES. A first file with no trailing newline fuses its last key onto the first line of
   the second, and the parser reads the merged line as a different value: measured on
   grid-bot-platform, `DRY_RUN=true` became `DRY_RUN=false` on every worktree assembled that way,
   silently, for the life of the task — found only during live-fire checks, on an account that
   happened to be a demo. `env_assemble.py` joins with an explicit separator **and reads the result
   back**: every key each source declared must parse out of `.env` holding the value that source
   declared, or nothing is written and the step stops naming the key. It refuses an existing `.env`
   unless `--force`, so a live one is never replaced by accident.

   Tell the user what they must fill in: `OPENROUTER_API_KEY`, and `GATE_PROVIDER=openrouter`
   (without it `gate_runner.py` defaults to the `opencode` CLI and the key is ignored). Warn that
   `GATE_MODEL` (the spec gate's observe pass) and `GATE_TRIAGE_MODEL` (its triage pass) must not
   share `AUTHOR_FAMILY` — a same-family gate exits 2, fail-closed. **A value left holding
   `REPLACE_ME` stops the run, it does not become the config**: `gate_runner.py` refuses before any
   provider call, naming the key. The refusal is scoped to what the run reads — every key the
   project's own `.env` declares, plus the pipeline variables the **resolved provider** uses, so
   `OPENROUTER_API_KEY` from the environment never wedges an `opencode` run. `env_assemble.py check
   --provider <opencode|openrouter>` asks that same question, through the same function, on demand;
   with no provider named it assumes `opencode`, the runner's own default, and says so.
   Every gate loads this file via `scripts/load_env.sh`; the real environment always wins over it.

2b. **Ship gate config.** Copy `${CLAUDE_PLUGIN_ROOT}/docs/templates/no-mistakes.example.yaml` to
   `.no-mistakes.yaml` in the project **only if one does not already exist** — check first, never
   overwrite. The template ships `lint` and `test` as `REPLACE_ME` placeholders: tell the user to
   replace both with this project's real commands, and that `test` must include the feature-level
   e2e suite, since the ship gate (`/avenger-run` §4a) is the only stage that runs it. Leave the
   `REPLACE_ME` token exactly as written in any value the user has not filled in — `/avenger-run` §1
   preflight greps for that token and stops the run at the start, so a half-scaffolded config fails
   fast instead of building every phase and then executing a placeholder as a shell command.

   **Then initialise the gate repo**, because copying the config initialises nothing — the bare gate
   repo, the post-receive hook, the `no-mistakes` git remote and the DB record all come from `init`,
   and without them §4a's `axi run` returns `error: repo not initialized` after every phase is
   already built. Check first, then initialise only if needed:

   ```bash
   no-mistakes axi     # exits 1 with `error: repo not initialized` when it is not
   no-mistakes init    # only if the above says so — it is safe but not a no-op
   ```

   `no-mistakes status` prints the same sentence but **exits 0**, so branch on `axi`. If `init` needs
   input you cannot answer, leave it and report the exact command in step 6 instead of guessing.

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
   cross-family provider (`OPENROUTER_API_KEY` set, or `opencode` on PATH), and
   **`lavish-axi`** (the plan-approval stop `/avenger-run` §3 and the retrospective triage §4b;
   interactive runs only). List anything missing with its fix (`pip install cosmic-ray tree-sitter
   tree-sitter-python`, `brew install jq`).

   **Report the ship gate as three separate states, not one**, because `/avenger-run` §1 preflight
   checks all three and a binary on PATH implies neither of the other two — needed by interactive and
   `--auto` runs alike:
   - **binary + runnable pipeline agent** — `no-mistakes doctor` (a run with no configured agent
     fails before its first step, and only `doctor` says so). Fix: install it, or configure an agent.
   - **repo initialised** — `no-mistakes axi`, which exits 1 with `error: repo not initialized`.
     Fix: `no-mistakes init` (step 2b).
   - **config filled in** — `grep -nE '^[[:space:]]*(lint|test):.*REPLACE_ME' .no-mistakes.yaml`
     must find nothing. Fix: replace both values (step 2b).

   `no-mistakes` and `lavish-axi` have **no fallback** — `/avenger-run` stops in preflight when they
   are absent rather than degrading a stop into a plain markdown read.

7. **Code-path note.** If source is not under `src/`, remind the user to update the path glob in
   `hook_verifier.sh`, `gate_ci.sh`, `.opencode/plugin/pipeline-gates.ts`, and `module-path` in
   `cosmic-ray.toml`.

8. **Mutation baseline sanity check.** Needs the `cosmic-ray.toml` step 0 reported on: with none at
   the repo root there is nothing to baseline and the per-phase mutation gate records `did-not-run`.
   Say so here rather than running the command and reading its failure. The gate requires a green suite before it will score anything
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
