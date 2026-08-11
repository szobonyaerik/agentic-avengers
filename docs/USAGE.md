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
```

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
      • On write, the Fidelity Gate fires automatically (cross-family). NO-GO -> back to spec-writer.
      • The spec lands with review_status: pending.

5. /spec-review docs/features/health-endpoint/phases/1-endpoint/specs/1.1-health/spec.md
      • First, no model: scripts/subprocess_check.py flags any test that spawns a process without
        @pytest.mark.subprocess("<why>"). Register that marker in the project's pytest config and
        point SUBPROC_CHECK_PATHS at your tests if they are not at tests/ (skills/tdd).
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
      • Setting status: done smoke-checks the phase suite (model called only if it fails).

# once every spec in the phase is green:
7. @avenger-verifier 1-endpoint
      • Cross-family (family B != the implementer's A). Runs the full phase suite, traces coverage
        per requirement `binding:` — a `binding: e2e` id is covered by the journey that lists it,
        `binding: none` is never a gap. It also READS THE TESTS over a bounded review set — the tests
        mapped to the phase plus the test files it changed, and their direct helpers. A gamed test
        (tautological / implementation-coupled / missing-negative) fails the phase even when green.
      • Writes docs/features/<feat>/phases/1-endpoint/verdict.json. On pass the phase's tests LOCK.
      • Mutation runs by default in ADVISORY mode: it reports the score and its survivors and
        never blocks. MUTATION_POLICY=enforce blocks; MUTATION_POLICY=off runs nothing.
      • Its BOOKKEEPING is a script, not a finding: scripts/verifier_precheck.py decides untraced
        ids, stale gate stamps and missing headings on every commit, over the phases that commit
        touches — the whole phase at handover, and everything under `gate_ci.sh --full`. 26% of this
        stage's measured findings used to be that class.
      • The loop is CAPPED at 3 attempts (scripts/verifier_attempts.py). At the cap: carry the
        remainder as known-open in handover.md, waive it, or escalate.

8. @avenger-handover 1-endpoint
      • Writes the phase's CONTRACT CARD, handover.md — binding contracts, decisions, artifact
        links, next phase, hard-capped at 6144 bytes and checked. Everything else goes to
        handover-archive.md beside it, which no stage reads. Nothing is deleted.
      • Mirrors the verdict + any waived findings into handover.md. The hook checks verdict.json is
        present and passing; it never calls a model.

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
- **Gate models**: decorrelated by default — `GATE_MODEL` (gemini) runs the spec gate's **observe**
  pass, `GATE_TRIAGE_MODEL` (deepseek) runs its cheaper **triage** pass, and `VERIFIER_GATE_MODEL`
  (gemini) runs the Verifier's test-quality review. Set `GATE_MODEL=<id>` to route the observe pass, e.g.
  `export GATE_MODEL=opencode-go/deepseek-v4-pro` (OpenCode's DeepSeek V4 Pro, provider `opencode`).
  Keep `AUTHOR_FAMILY` a different family than `GATE_MODEL` or the gate fails closed.
- **opencode build models**: `MODEL_MAP` in `scripts/sync_opencode.py` maps the Claude model tiers to
  OpenRouter ids (`claude-opus-5` / `claude-sonnet-5` / `claude-haiku-4.5`). Re-check these against
  `https://openrouter.ai/api/v1/models` when the tiers move; a stale id fails at request time, not at
  sync time.
- **Code not under `src/`?** Update the path glob in `hook_verifier.sh`, `gate_ci.sh`,
  `.opencode/plugin/pipeline-gates.ts`, and `module-path` in `cosmic-ray.toml`.
- **A gate "stops" unexpectedly** — that's fail-closed. Check: `OPENROUTER_API_KEY` set? gate model a
  different family than `AUTHOR_FAMILY`? provider reachable? The stderr says which. The Verifier's
  review adds two of its own: a review set over `VERIFIER_SRC_LIMIT` (refused before the model is
  called) and a verdict that shows no sign of having read the set. Neither is worked around by
  dropping files — see `skills/verifier-triage`.
- **Regenerate after editing canonical sources**: `python3 scripts/sync_opencode.py` (agents/skills).
  The opencode plugin and `AGENTS.md` are hand-maintained.
