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

   It reports, and never blocks: whether `.env.example` already exists (then the pipeline's template
   goes to `.env.pipeline.example` — **an existing `.env.example` is never overwritten, whoever
   wrote it**, so its provenance is never asked and there is no third case), whether a live `.env`
   or `.no-mistakes.yaml` is already there, and whether `cosmic-ray.toml` is missing — **named here
   rather than at step 8, because two later steps require it and neither can create it**, and the
   report carries the config to write rather than a path to it. Report every line of it to the user;
   the steps below act on it.

1. **Artifact tree.** Create `docs/features/`; if a feature id was given, also
   `docs/features/<id>/` and `docs/features/<id>/phases/`.

2. **gitignore.** Ensure `.gitignore` contains `**/.pytest_cache/`, `session.sqlite` and
   `.gate-session.sqlite` (cosmic-ray sessions), `.gate-cosmic-ray.toml` (the generated diff-scoped
   config), `.gate-tmp.txt`, `.avenger-auto` (the `/avenger-run --auto` permission sentinel),
   `.lavish/` (scratch HTML review surfaces for the plan stop and the retrospective triage),
   `.avenger-gate-cache/` (the body each gate last judged, so a re-gate can be scoped to the diff —
   rebuildable, and a lost entry costs one full re-gate, the safe direction),
   `.avenger-metrics.log` (diagnostics from the fail-open metrics path — the measurements themselves
   live in firstmate's record, never here), **`.env`** (it holds a live API key), and
   **`.env-assemble.*.tmp`** (step 2a's assembler writes the merged config to a temp file beside the
   destination and renames it over, so a crash cannot truncate a live `.env` — that temp file holds
   the fully merged result, credentials included, and an interruption nothing can catch leaves it
   behind. `.env` does not match it, so without this pattern a `git add -A` commits it). None of these
   may ever be committed.

2a. **Configuration.** Copy `${CLAUDE_PLUGIN_ROOT}/docs/templates/env.example` to the
   **target step 0 named**, and to no other path — `Survey.template_target` is the single authority
   on which one that is, and a second copy of that rule written here is one that can disagree with
   it. Copy it with a flag that **cannot** clobber, so "never overwrite either file" is carried by
   the command rather than by this sentence:

   ```bash
   cp -n "${CLAUDE_PLUGIN_ROOT}/docs/templates/env.example" <the target step 0 named>
   ```

   `-n` keeps an earlier init's copy. **Never overwrite a live `.env`** either; that half is already
   mechanical, since `assemble` refuses an existing output without `--force`.

   Then assemble the run's `.env`, **never with `cat`**. **Step 0 printed the exact command for this
   repository — run the one it printed**, rather than a path written here. It names the example file
   that actually exists: with a live `.env` and no `.env.example` of its own the template lands at
   `.env.example`, and a fixed `.env.pipeline.example` in this document reads a file that is not
   there. `Survey.template_target` is the one place that decides which example is the pipeline's.

   Both shapes, so you can recognise what step 0 printed — **the last source to declare a key wins,
   and that is what decides the order**:

   ```bash
   # no .env yet — every example the project has, unedited copies of the shipped template first
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/env_assemble.py" assemble \
     --out .env <every example still identical to the template> <every example someone edited>

   # a live .env ALREADY exists — merge in place, naming it as its OWN LAST SOURCE so every value
   # the operator already chose beats the template's default, while missing pipeline keys are added
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/env_assemble.py" assemble \
     --out .env <the target step 0 named> .env --force
   ```

   **The more operator-owned file goes later, and CONTENT is what decides which that is.** An
   example still byte-identical to `docs/templates/env.example` carries nobody's decision: it is
   named first and supplies only what nothing else declares. One that has been edited is
   operator-owned and is named after it. Both halves are load-bearing, and each was wrong on its own
   once. Named last, a repository whose `.env.example` an earlier init wrote and the team then
   filled in and committed got `.env.pipeline.example` as its target, `cp -n` wrote the SHIPPED
   template there unmodified, and last-wins handed the assembled `.env` the shipped `GATE_MODEL`,
   `GATE_PROVIDER` and `MUTATION_POLICY` instead of the team's choices. Named first
   unconditionally, an operator's already filled-in `.env.pipeline.example` lost every key it shares
   with `.env.example` — and since both derive from the same template, that is all of them. Either
   way the values parse, survive the read-back and carry no `REPLACE_ME`, so nothing reports them.

   **Content and not presence, because the `cp -n` above is what changes presence.** A presence rule
   printed one order when step 0 ran before the copy and the reverse when anything asked again after
   it — a single authority giving one repository two answers. Content is stable across the copy, so
   step 0 prints the same command before it, after it, and on a second `/pipeline-init` of an
   already-initialised repository. This decides only which source wins a key; **never-overwrite is
   untouched and never asks who wrote a file.**

   **The merge form names the pipeline's example and the live file, and no other example.** That is
   not an omission: last-wins protects every key the live `.env` already declares, but a key it does
   NOT declare would arrive carrying the project example's value, and an example's values are dummies
   and defaults by construction — `your_api_key_here`, a `DRY_RUN=false` default — written into a live
   file where the app had been falling back to its own in-code default, with nothing reporting it. A
   live `.env` is already the operator's chosen configuration; the project's example says what the
   app's config should CONTAIN, which is exactly what the create form needs and exactly what a merge
   must not import.

   **The one example it names is the file carrying the operator's PIPELINE configuration** — an
   edited `.env.pipeline.example` when there is one, otherwise whichever file holds the shipped
   template (a greenfield init writes it to `.env.example`, and there that file is the pipeline's
   own), otherwise the target step 2a is about to create. The two branches ask different questions
   of the same disk: the create form needs the file that HOLDS the template, since it supplies only
   what nothing else declares; the merge needs the file carrying the operator's DECISIONS, since it
   is the only example named. Answering both with the first question dropped them — a pristine
   `.env.example` beside a filled-in `.env.pipeline.example` made the merge name the pristine one,
   and the live `.env` came back with the shipped `GATE_MODEL` and `MUTATION_POLICY` instead of the
   chosen ones, parsing cleanly, surviving the read-back and carrying no `REPLACE_ME`.

   **Never name the live `.env` first**: the template declares `OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME`
   and default `GATE_MODEL`, `GATE_PROVIDER`, `AUTHOR_FAMILY` and `MUTATION_POLICY` uncommented, so a
   template read after the live file replaces the operator's real key and silently reverts their
   config to those defaults.

   `cat` joins BYTES. A first file with no trailing newline fuses its last key onto the first line of
   the second, and the parser reads the merged line as a different value: measured on
   grid-bot-platform, `DRY_RUN=true` became `DRY_RUN=false` on every worktree assembled that way,
   silently, for the life of the task — found only during live-fire checks, on an account that
   happened to be a demo. `env_assemble.py` joins with an explicit separator **and reads the result
   back**: every key each source declared must parse out of `.env` holding the value that source
   declared, or nothing is written and the step stops naming the key. It refuses an existing `.env`
   unless `--force`, so a live one is never replaced by accident — the merge form above is the only
   sanctioned use of that flag, and its read-back is what proves the live file's own keys survived.

   Tell the user what they must fill in: `OPENROUTER_API_KEY`, and `GATE_PROVIDER=openrouter`
   (without it `gate_runner.py` defaults to the `opencode` CLI and the key is ignored). Warn that
   `GATE_MODEL` (the spec gate's observe pass) and `GATE_TRIAGE_MODEL` (its triage pass) must not
   share `AUTHOR_FAMILY` — a same-family gate exits 2, fail-closed. **A value left holding
   `REPLACE_ME` stops the run, it does not become the config**: `gate_runner.py` refuses before any
   provider call, naming the key. The refusal is scoped to what the run reads — every key the
   project's own `.env` declares, plus the pipeline's own variables (`GATE_*`, `OPENROUTER_*`,
   `AUTHOR_FAMILY`, `MUTATION_*`, `SPEC_*`) from the real environment, so an unrelated tool's
   scaffold variable can never wedge a gate. `OPENROUTER_API_KEY` is in scope under **both**
   providers: `opencode` inherits it from the environment and is documented as authenticating that
   way. `env_assemble.py check` asks that same question, through the same function, on demand.
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
