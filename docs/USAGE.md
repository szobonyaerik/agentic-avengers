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
```

> **Cross-family invariant:** gates must run on a different vendor family than the author. Build agents
> = anthropic; fidelity gate = DeepSeek; verifier/mutation/spec-review = Gemini. If a gate model shares
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
      -> docs/features/health-endpoint/task-analysis.md   (sets work_kind: greenfield)
2. @avenger-solution-architect        -> overview.md
3. @avenger-implementation-planner    -> plan.md   (phases, each with candidate specs <n>.<k>)
4. @avenger-spec-writer               -> phases/1-endpoint/specs/1.1-health/spec.md
      • On write, the Fidelity Gate fires automatically (cross-family). NO-GO -> back to spec-writer.
      • The spec lands with review_status: pending.

5. /spec-review docs/features/health-endpoint/phases/1-endpoint/specs/1.1-health/spec.md
      • You are grilled ONE question at a time against the spec-review checklist, each with a
        recommendation. Answer them; when the bar is met it sets review_status: approved.
      • Tests do NOT lock until review_status: approved AND fidelity_verdict != NO-GO.

# per spec in the phase:
6. @avenger-test-author  <spec>       -> tests/1-endpoint/ + test-mapping.md   (locked RED, greenfield mode)
7. @avenger-backend-architect <spec>  -> src/...   (turn RED green; never edits tests)

# once every spec in the phase is green:
8. @avenger-handover 1-endpoint
      • Writing handover.md fires the PER-PHASE verifier: full suite + R<n>.<k>.<m> coverage trace +
        cosmic-ray mutation. Survivors route back to the Test-Author; code failures to the implementer.

9. Ship: git commit (pre-commit floor runs fidelity on staged specs + tests) -> PR (CI floor: + mutation).
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
- A cross-family AI reviewer (Gemini) runs `prompts/spec-review-rubric.md` — the same checklist a human
  would defend.
- **GO / REVIEW** → it stamps `review_status: approved` and the chain continues to the Test-Author.
- **NO-GO** → `review_status` stays `pending`, it prints the findings, and routes back to
  `avenger-spec-writer`. It is a real second opinion, **not** a rubber stamp.
- Any error (missing key, same-family model, no verdict) → **fails closed**, no approval.

**opencode equivalent** (no slash commands there): set `SPEC_REVIEW_MODE=auto` and the plugin
(`.opencode/plugin/pipeline-gates.ts`) does it on spec write. To run it by hand against one spec:
```bash
python3 "$AV/scripts/gate_runner.py" \
  --rubric "$AV/prompts/spec-review-rubric.md" \
  --model google/gemini-2.5-pro --author-family anthropic --print-verdict \
  --target <spec.md>
# GO/REVIEW -> edit the spec's `review_status: pending` to `approved`; NO-GO -> fix the spec.
```

> **HITL vs automated, when?** Use HITL for high-stakes or ambiguous specs where you want a human to
> own the judgment; use automated for throughput once you trust the checklist. Both write the same
> `review_status: approved` and both are cross-family + fail-closed — the only difference is who answers.

---

## F. Break-glass + troubleshooting

- **Break-glass** (only override): `GATE_BYPASS="why" git commit …`, or export it for in-session hooks.
  It overrides a *failing* gate, appends who/when/which-gate/why to `gate-overrides.log`, prints a
  visible `⚠ BYPASSED`, and you must record it in the phase `handover.md`. Never silent.
- **opencode build models**: `MODEL_MAP` in `scripts/sync_opencode.py` still points at
  `claude-opus-4/sonnet-4/haiku-4`. Update these to current OpenRouter ids before driving opencode
  agents (the gate models DeepSeek/Gemini are already correct).
- **Code not under `src/`?** Update the path glob in `hook_verifier.sh`, `gate_ci.sh`,
  `.opencode/plugin/pipeline-gates.ts`, and `module-path` in `cosmic-ray.toml`.
- **A gate "stops" unexpectedly** — that's fail-closed. Check: `OPENROUTER_API_KEY` set? gate model a
  different family than `AUTHOR_FAMILY`? provider reachable? The stderr says which.
- **Regenerate after editing canonical sources**: `python3 scripts/sync_opencode.py` (agents/skills).
  The opencode plugin and `AGENTS.md` are hand-maintained.
