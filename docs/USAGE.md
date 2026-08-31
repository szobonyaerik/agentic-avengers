# Using the pipeline: install + your first feature (HITL and automated spec-review)

This walks you from zero to a shipped first feature with **agentic-avengers**, twice over — once with
**HITL** (human) spec-review, once with **automated** spec-review. The concrete example targets
`~/Documents/GitHub/grid-bot-platform` (Python), but any repo works.

The pipeline is: **Plan → Quality wall → (Build & verify ×phases) → Ship**. Agents are named
`avenger-*` so you can tell them apart from other agents. Gates run on a **cross-family** model and
**fail closed**. Mutation is **cosmic-ray**. The one override is **break-glass** (`GATE_BYPASS`).

---

## A. Prerequisites (once per machine)

```bash
pip install pytest cosmic-ray tree-sitter tree-sitter-python
brew install jq                                  # gate scripts use jq

# Cross-family gate provider — one of:
export OPENROUTER_API_KEY=sk-or-...               # add to your shell rc, and as a GH Actions secret for CI
#   or: opencode auth login   (opencode routes gate models via its OpenRouter credential)

export AUTHOR_FAMILY=anthropic                    # the family your build agents run on (Claude)

# Feature-close tooling — /avenger-run preflight STOPS without these, there is no fallback:
no-mistakes doctor                                # ship gate (§4a): binary + a runnable pipeline agent
no-mistakes axi                                   #   ...and the repo initialised (exits 1 with
                                                  #   `error: repo not initialized` -> run `no-mistakes init`)
lavish-axi --version                              # plan-approval stop (§3) + retrospective triage (§4b),
                                                  #   interactive runs only; --auto skips both
```

> **Initialise the gate repo, and fill in `.no-mistakes.yaml`, before your first run.** These are two
> separate preconditions and neither implies the other. `no-mistakes init` is what creates the bare
> gate repo, the post-receive hook, the `no-mistakes` remote and the DB record; copying the config
> creates none of them. And `/pipeline-init` (step B) scaffolds that config with `REPLACE_ME`
> placeholders for `commands.lint` and `commands.test`. Preflight checks the file's **content**, not
> just its existence, so an unedited scaffold stops the run — deliberately, since the alternative is
> executing a literal placeholder as a shell command at the ship gate.

> **Cross-family invariant:** gates must run on a different vendor family than the author. Build agents
> = anthropic; the spec gate's observe pass = Gemini and its triage pass = DeepSeek; verifier = Gemini. If a gate model shares
> `AUTHOR_FAMILY`, it stops (fail closed).
>
> **When there is genuinely no second family to reach, waive it explicitly:**
> `GATE_SAME_FAMILY_WAIVER="$(cat <reason-file>)"`. The gate still runs and still judges; what is
> given up is decorrelation, and every record of the call says so - the runner announces it on
> stderr, the spec gate folds a `SAME-FAMILY WAIVER` banner into the report it stamps with the
> verdict, the call's metrics `note` carries it, and one line lands in `gate-overrides.log`. A waiver
> that could not be logged does not hold. **Never waive it by editing `AUTHOR_FAMILY`**: an untrue or
> emptied author family drops the invariant silently instead of disclosing it, which turns a loud
> refusal into a quiet false assurance. An empty reason is not a waiver.

---

## B. Install into Claude Code

```text
/plugin marketplace add szobonyaerik/agentic-avengers
/plugin install plan-build-verify@erik-tools
/pipeline-init                 # scaffolds docs/features, .gitignore, conventions, prereq check
```
The plugin brings the `avenger-*` agents, the skills, `/spec-review`, and the in-session hooks
(`hooks/hooks.json`). Update later with `/plugin update`.

---

## C. Install into grid-bot-platform (opencode + git floor)

grid-bot already had an **older** pipeline in `.opencode/`. Remove the stale pieces first, then vendor:

```bash
cd ~/Documents/GitHub/grid-bot-platform
git checkout -b avengers-pipeline           # never work on the shared branch directly

# 1) remove the stale old agents (they conflict with the new avenger-* set)
git rm .opencode/agents/codebase-cartographer.agent.md \
       .opencode/agents/verifier.md \
       .opencode/agents/*.agent.md

# 2) vendor the new pipeline (dry-run first)
AV=~/Documents/GitHub/experiment/agentic-avengers
"$AV/scripts/install.sh" "$PWD" --check      # preview NEW/UPDATE/DRIFT/GONE
"$AV/scripts/install.sh" "$PWD"              # writes .avengers/manifest + the surface

# 3) refresh opencode deps (plugin bumped to 1.15.11) and the git floor
cd .opencode && npm install && cd ..         # or: bun install
pre-commit install

# 4) point cosmic-ray at the package under test, and generate the codemap
$EDITOR cosmic-ray.toml                       # module-path = "<your package dir>", test-command = "pytest -x -q"
python "$AV/scripts/codemap.py" . --lang python --output codebase   # -> codebase/MOC.md

# 4a) ignore what step 5 is about to write, BEFORE it can write it. Two rules, and this route
#     reaches neither of the other lists that carry them: install.sh vendors no .gitignore and
#     .opencode/ ships no pipeline-init command, so this block is the only one a section-C
#     operator runs. `.env` is the primary rule — step 5 CREATES it, holding a live
#     OPENROUTER_API_KEY, every single time. `.env-assemble.*.tmp` is the derived one: the write
#     is atomic (temp file beside the destination, then rename), so a crash cannot truncate a live
#     .env, but SIGKILL and power loss cannot be caught, and the leftover matches no `.env`
#     pattern. Without either rule `git add -A` commits real credentials. Both go through ONE
#     append form that terminates .gitignore first: a bare `>>` onto a file whose last line has no
#     newline FUSES the two patterns into one, destroying both — the same byte-joining defect the
#     `cat` this whole section replaces was retired for.
for pattern in '.env' '.env-assemble.*.tmp'; do
  grep -qxF "$pattern" .gitignore 2>/dev/null && continue
  if [ -s .gitignore ] && [ -n "$(tail -c1 .gitignore)" ]; then printf '\n' >> .gitignore; fi
  printf '%s\n' "$pattern" >> .gitignore
done

# 5) assemble .env. `pipeline_init.py survey` prints the exact command for THIS repository —
#    which example files exist decides both the template's target and the source list, so run
#    what it printed rather than a path copied from here. The commands below are this repo's
#    shape: it owns .env.example, so the pipeline's template lands beside it.
python3 "$AV/scripts/pipeline_init.py" survey            # <- the authority for the two paths below
cp -n "$AV/docs/templates/env.example" .env.pipeline.example   # -n: an earlier init's copy is kept

#    (a) no .env yet — build it from the two examples. An example still byte-identical to the
#        shipped template is named first and only fills in what nothing else declares; an edited
#        one is operator-owned and goes after it. Run what survey printed — it knows which is which.
python3 "$AV/scripts/env_assemble.py" assemble --out .env .env.pipeline.example .env.example

#    (b) this worktree ALREADY has a live .env (grid-bot-platform has one on every worktree) —
#        merge in place by naming it as its OWN LAST SOURCE. (a) refuses here, by design.
#        Note what (b) does NOT name: .env.example. A merge imports the pipeline's template and
#        your own file, and nothing else.
python3 "$AV/scripts/env_assemble.py" assemble --out .env .env.pipeline.example .env --force
```

Form **(b)** is not the destructive `--force` it looks like: `assemble` reads every source before it
writes anything, so the live `.env` is read first and the read-back then proves every credential it
declared survived into the result. **The order is the point** — the last source to declare a key
wins, so the live file goes LAST and every value you already chose beats the template's default for
that key, while every pipeline key you do not have yet is still added. Name it first instead and the
template's `OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME` and its default `GATE_MODEL`, `GATE_PROVIDER`,
`AUTHOR_FAMILY` and `MUTATION_POLICY` overwrite yours.

**The more operator-owned file goes later, and CONTENT is what decides which that is** — that is the
principle both forms obey. An example still byte-identical to `docs/templates/env.example` carries
nobody's decision: it is named first and supplies only what nothing else declares. One that has been
edited is operator-owned and goes after it. Content and not presence, because the `cp -n` above is
what changes presence — a presence rule printed one order before that copy and the reverse after it,
so the survey gave one repository two answers. Content is stable across the copy. It decides only
which source wins a key; never-overwrite is untouched and never asks who wrote a file.

Both halves are load-bearing. Named last, form (a) did the opposite: if an earlier init wrote your `.env.example` and
your team then filled it in and committed it, the target is `.env.pipeline.example`, `cp -n` writes
the SHIPPED template there unmodified, and it would revert your committed `GATE_MODEL`,
`GATE_PROVIDER` and `MUTATION_POLICY` to its defaults. Named first unconditionally it failed the
mirror case: an already filled-in `.env.pipeline.example` lost every key it shares with
`.env.example`, and since both derive from the same template that is all of them. Either way the
values parse, survive the read-back and carry no `REPLACE_ME`, so nothing reports them.

**Form (b) names the pipeline's example and your live file, and no other example** — while form
(a), which is CREATING the file, names every example that exists, because one left out there is one
whose values revert to another file's. That asymmetry is in the SOURCE SET, not the principle, and
it is deliberate.

The one example (b) names is **the file carrying YOUR pipeline configuration**: an edited
`.env.pipeline.example` when you have one, otherwise whichever file holds the shipped template (a
greenfield init writes it to `.env.example`, and there that file is the pipeline's own), otherwise
the target step 0 named. Form (a) asks a different question of the same disk — which file HOLDS the
template, since there it supplies only what nothing else declares. Answering both with that first
question dropped your decisions: a pristine `.env.example` beside the `.env.pipeline.example` you
filled in made the merge name the pristine one, and your live `.env` came back with the shipped
`GATE_MODEL` and `MUTATION_POLICY`, parsing cleanly and carrying no `REPLACE_ME` to catch it.

Last-wins protects every key
your live `.env` already declares; it does nothing for a key your `.env` OMITS, and that key would
arrive carrying the project example's value — and an example's values are dummies and defaults by
construction (`your_api_key_here`, a `DRY_RUN=false` default). A committed `.env.example` that has
drifted ahead of your worktree's `.env` would write those into it where the app had been falling
back to its own in-code default, with nothing reporting it: syntactically valid, the read-back
passes because the example declared it and it survived, and a dummy carries no `REPLACE_ME` for the
run-time refusal to catch. A live `.env` is already your chosen configuration; the project's example
says what the app's config should CONTAIN. Re-merging appends the template's text again
rather than replacing it, which parses correctly because the last declaration still wins. Assembling
anywhere else configures nothing: every gate reads `.env` and no other path.

**Never `cat` those two files together.** A source with no trailing newline fuses its last key onto
the first line of the next, and the parser reads the merged line as a different value — on this very
repository that turned `DRY_RUN=true` into `DRY_RUN=false` on every worktree, silently, for the life
of a task (issue #108). `env_assemble.py` joins with an explicit separator and reads every declared
key back out of the result; a key that did not survive stops the step and nothing is written. Fill in
every `REPLACE_ME` before the first gate call — a run is refused while one is left, naming the key.
The refusal is scoped to what your run actually reads: every key your own `.env` declares, plus the
pipeline's own variables (`GATE_*`, `OPENROUTER_*`, `AUTHOR_FAMILY`, `MUTATION_*`, `SPEC_*`) from the
environment, so an unrelated tool's scaffold variable can never wedge a gate. `OPENROUTER_API_KEY`
counts under **both** providers — opencode inherits it from the environment, which is one of the two
documented ways to authenticate it. Ask on demand with
`python3 "$AV/scripts/env_assemble.py" check`.

Re-running `install.sh <target> --check` later classifies each file NEW / UPDATE / SAME / **DRIFT**
(your local edits — skipped, never clobbered) / **GONE**. `--prune` removes upstream-deleted files you
haven't modified.

---

## D. First feature — HITL (human) spec-review

Drive the chain (Claude Code: the agents auto-delegate / invoke by name; opencode: `@avenger-…`).

```text
1. @avenger-task-analyst "add a health endpoint that reports uptime and version"
      -> docs/features/health-endpoint/task-analysis.md   (the feature's work_kind: greenfield)
      • Read ONCE, by the solution-architect. Each spec then carries its own work_kind in
        frontmatter, so no per-spec stage ever opens this file (skills/pipeline-conventions:
        The document read path).
2. @avenger-solution-architect        -> overview.md
3. @avenger-implementation-planner    -> plan.md   (phases, each with candidate specs <n>.<k>)
4. @avenger-spec-writer               -> phases/1-endpoint/specs/1.1-health/spec.md
      • On write, the spec gate fires automatically (scripts/hook_spec_gate.sh). Before any model
        call, two mechanical checks run: the requirement cap (over 12 the spec SPLITS into siblings,
        it is never rejected for size) and scripts/subprocess_check.py, which flags any test that
        spawns a process without @pytest.mark.subprocess("<why>"). Register that marker in the
        project's pytest config and point SUBPROC_CHECK_PATHS at your tests if they are not at
        tests/ (skills/tdd).
      • Then the gate itself: observe -> triage -> decide, and it stamps spec_gate: approved|blocked.
        blocked -> back to spec-writer. Exactly four things block: a missing requirement, a
        contradiction, an untestable criterion, an unhandled critical edge case. Everything else is a
        NOTE, which blocks nothing and lands in spec-notes.md beside the spec, read once by the
        implementer.
      • The spec lands with review_status: pending.

5. /spec-review docs/features/health-endpoint/phases/1-endpoint/specs/1.1-health/spec.md
      • The machine gate already ran on the write; this command does not re-run it and runs no
        second rubric. It reads spec_gate first and stops if it is blocked.
      • You are grilled ONE question at a time against the spec-review checklist, each with a
        recommendation. Answer them; when the bar is met it sets review_status: approved.
      • Re-reviewing a spec that is already approved AND implemented covers its DIFF only
        (skills/spec-review-checklist names what still warrants a full pass).
      • Implementation does NOT start until review_status: approved AND spec_gate: approved.
        That wall is what pre-agrees the seams the tests get written at.

# per spec in the phase:
6. @avenger-backend-architect <spec>  -> tests/1-endpoint/ + test-mapping.md + src/...
      • Red -> green, one vertical slice at a time (skills/tdd): one failing test at the
        requirement's seam, then the minimal code to pass it, then the next slice.
      • Set status: done LAST, after test-mapping.md has its rows and the phase suite is green.
        The stamp is not believed on sight: it smoke-checks that spec's mapping and its phase
        suite (model called only if it fails), and a stamp that fails either check is REVERTED to
        status: in-progress (scripts/spec_done_guard.py) before the hook fails. A row whose
        requirement-id cell is still the template's R<n>.<k>.<m> placeholder counts as no row.
        A spec whose every requirement is `binding: none` owes no row and is never asked for one.

# once every spec in the phase is green:
7. @avenger-verifier 1-endpoint
      • Runs the full phase suite and traces coverage per requirement `binding:` — a `binding: e2e`
        id is covered by the journey that lists it, `binding: none` is never a gap. It also DRIVES
        ADVERSARIAL EXECUTION against a real collaborator on any requirement whose subject is a
        secret, a resource lifetime or a concurrency invariant. Every command it relies on runs
        through scripts/verifier_evidence.py, and verdict.json names that transcript: a pass with no
        proof of execution is refused.
      • There is NO dedicated reader for gamed tests. The cross-family reading pass was removed and
        nothing inherits it; partial cover is the mutation gate, skills/tdd naming the anti-patterns
        to the implementer while it writes, and the human spec-review. A gamed test noticed while
        tracing coverage is still a fail — `gamed-test` stays in the verdict schema for exactly that.
      • Writes docs/features/<feat>/phases/1-endpoint/verdict.json. On pass the phase's tests LOCK.
      • Mutation runs by default in ADVISORY mode: it reports the score and its survivors and
        never blocks. MUTATION_POLICY=enforce blocks; MUTATION_POLICY=off runs nothing.
      • Its BOOKKEEPING is a script, not a finding: scripts/verifier_precheck.py decides untraced
        ids, stale gate stamps and missing headings on every commit, over the phases that commit
        touches — the whole phase at handover, and everything under `gate_ci.sh --full`. 26% of this
        stage's measured findings used to be that class.
      • The loop is CAPPED at 3 attempts (scripts/verifier_attempts.py). At the cap: carry the
        remainder as known-open in handover.md, waive it, or escalate.

7b. @avenger-breaker 1-endpoint   (ONLY when a spec in the phase RESOLVES TO criticality: critical -
                                   scripts/criticality.py, where an absent, blank, unrecognised or
                                   unreadable field resolves to critical, issue #101)
      • Then it is not optional: it writes breaker.json beside verdict.json - a `clean` verdict
        naming what it attacked, or a `found` one naming its counterexample. A vacuous record is
        refused like a missing one, and step 8 below is REFUSED without it
        (scripts/breaker_gate.py). Waivable only through the disclosed-exception ledger
        (exceptions.json, --rule breaker).

8. @avenger-handover 1-endpoint
      • Writes the phase's CONTRACT CARD, handover.md — binding contracts, decisions, artifact
        links, next phase, hard-capped at 6144 bytes and checked. Everything else goes to
        handover-archive.md beside it, which no stage reads. Nothing is deleted.
      • Mirrors the verdict + any waived findings into handover.md. The hook checks verdict.json is
        present and passing, the Breaker record when one is owed, and that this phase answered every
        `## Open items` row the prior card carried; it never calls a model.

9. Ship: git commit (pre-commit floor checks staged specs' gate stamps + requirement cap + tests)
   -> PR (CI floor: + verifier pre-check + amendments + mutation).
```

---

## E. Same feature — AUTOMATED spec-review

Everything is identical **except step 5** — no human interrogation. Pick one:

**Per-spec (explicit):**
```text
/spec-review docs/features/health-endpoint/phases/1-endpoint/specs/1.1-health/spec.md --auto
```
**Hands-off for the whole session** (the spec-review runs itself right after each spec write):
```bash
export SPEC_REVIEW_MODE=auto        # set BEFORE launching Claude Code / opencode
```

What happens in auto mode:
- Nothing extra runs. **The spec gate already ran on the spec's write** — observe, triage, decide —
  and under `SPEC_REVIEW_MODE=auto` it stamps `review_status: approved` itself, because in an
  unattended run the machine gate is the whole wall. `/spec-review --auto` only reports where the
  spec stands.
- **approved** → the chain continues to the implementer.
- **blocked** → `review_status` stays `pending`, the gate's findings are printed, and it routes back
  to `avenger-spec-writer`. Exactly four things can block: a missing requirement, a contradiction, an
  untestable criterion, an unhandled critical edge case. Everything else was a **note** — recorded in
  `spec-notes.md` beside the spec, blocking nothing.
- Any error (missing key, same-family model, no verdict, an invented triage category) → **fails
  closed**, no approval.

**opencode equivalent** (no slash commands there): set `SPEC_REVIEW_MODE=auto` and the plugin
(`.opencode/plugin/pipeline-gates.ts`) runs `scripts/hook_spec_gate.sh` on spec write, exactly as
Claude Code's hook does — the gates have one implementation. To re-run it by hand against one spec,
write the spec again; the gate skips an unchanged body by design, so an edit is what re-gates it.

> **HITL vs automated, when?** Use HITL for high-stakes or ambiguous specs where you want a human to
> own the judgment; use automated for throughput. The machine gate is identical in both — the only
> difference is whether a human also signs off, or the gate carries `review_status` because nobody
> is there.

---

## F. Break-glass + troubleshooting

- **Break-glass** (only override): `GATE_BYPASS="why" git commit …`, or export it in your own shell
  before starting the session, which covers every in-session hook.
  It overrides a *failing* gate, appends who/when/which-gate/why to `gate-overrides.log`, prints a
  visible `⚠ BYPASSED`, and you must record it in the phase `handover.md`. Never silent.
  **Under `/avenger-run --auto` an agent must pass the reason from a file** —
  `GATE_BYPASS="$(cat <file>)" git commit …` — because the reason is prose and the auto deny regex
  reads the whole command string (`skills/pipeline-conventions`). `export` is not a substitute there:
  env vars do not survive between an agent's Bash calls. Both shapes are equivalent for *you*, typing
  it yourself. **Write the reason as prose, multi-line if it needs to be** — the log is one
  tab-separated record per line, and every writer normalises the reason through
  `scripts/bypass_reason.sh` (newlines and tabs collapsed to spaces, nothing dropped) rather than
  asking you to keep it on one line. The Verifier's per-finding waiver
  (`verdict.json` `break_glass` + `waiver_reason`) is logged through that same writer, so a
  multi-paragraph waiver reason is safe too.
- **Gate models**: two variables, documented together in `docs/templates/env.example` — `GATE_MODEL`
  (gemini) runs the spec gate's **observe** pass and `GATE_TRIAGE_MODEL` runs its cheaper **triage**
  pass. There is no third: the Verifier's cross-family reading pass was removed, and the spec gate is
  the pipeline's only remaining model gate. Set
  `GATE_MODEL=<id>` to route the observe pass, e.g. `export GATE_MODEL=opencode-go/deepseek-v4-pro`
  (OpenCode's DeepSeek V4 Pro, provider `opencode`). Keep `AUTHOR_FAMILY` a different family than
  `GATE_MODEL` or the gate fails closed. Left unset, `GATE_TRIAGE_MODEL` now **defaults to
  `GATE_MODEL`** — the model you already configured and proved reachable — rather than to a
  hardcoded model on its own provider; it used to default to a bare `deepseek/deepseek-chat`, which
  resolves to OpenRouter regardless of `GATE_PROVIDER`, so every spec write failed closed on a
  third, unconfigured provider (issue #48).
- **opencode build models**: `MODEL_MAP` in `scripts/sync_opencode.py` maps the Claude model tiers to
  OpenRouter ids (`claude-opus-5` / `claude-sonnet-5` / `claude-haiku-4.5`). Re-check these against
  `https://openrouter.ai/api/v1/models` when the tiers move; a stale id fails at request time, not at
  sync time.
- **Code not under `src/`?** Update the path glob in `hook_verifier.sh`, `gate_ci.sh`,
  `.opencode/plugin/pipeline-gates.ts`, and `module-path` in `cosmic-ray.toml`.
- **`cause=cross-family`** — the gate model shares `AUTHOR_FAMILY`, so there is no independence to
  have. Point it at another vendor. If there genuinely is no other vendor to reach, disclose it
  rather than faking it: `GATE_SAME_FAMILY_WAIVER="$(cat <reason-file>)"` (prose in a file, per the
  break-glass rule above) lets the gate judge and marks the verdict as same-family everywhere it is
  recorded. Editing `AUTHOR_FAMILY` to get past this is never the remedy — it is the one route that
  leaves the verdict looking independent.
- **A gate "stops" unexpectedly** — that's fail-closed. Check: `OPENROUTER_API_KEY` set? gate model a
  different family than `AUTHOR_FAMILY`? provider reachable? The stderr says which. One cause is
  worth knowing by name: `implausible-latency` means the verdict came back faster than the call can
  physically be made, so it is presumed not to have run — check the provider and the runner rather
  than the spec, and set `GATE_MIN_LATENCY_MS` only if your model genuinely is that fast.
- **A phase will not close on a passing verdict** — check whether it carries execution evidence.
  `python3 scripts/verifier_evidence.py check <phase-dir> --verdict <phase-dir>/verdict.json` names
  exactly what is missing and what would satisfy it. The usual cause is a verification command run
  outside the recorder, or specs/tests edited after the transcript was recorded.
- **A subagent is sent back at its own stop** - that's the per-stage skill audit
  (`scripts/hook_skill_audit.sh` on `SubagentStop`), asking the same question the close-time audit
  asks, early enough that the remedy is opening the named `SKILL.md` in that stage. It never stops
  the same agent twice and it fails open; `SKILL_AUDIT_OFF=1` disables it, leaving the close-time
  audit at handover and in CI. See `skills/pipeline-conventions`.
- **Regenerate after editing canonical sources**: `python3 scripts/sync_opencode.py` (agents/skills).
  The opencode plugin and `AGENTS.md` are hand-maintained.
