# Automating the pipeline (pick up after a manual run)

Once you've walked the pipeline manually and trust the gates, remove the human from the loop — **opt
in per run**. Manual (stage-by-stage) stays the default; automation is something you *invoke*, not a
mode you're stuck in. Two layers:

- **2. Auto-chain the stages** — an orchestrator agent drives the whole flow in one call.
- **3. Fully unattended** — run that headless (CI / overnight / SDK), no terminal.

Prereq for both: the automated spec-review must be on, or the chain will block waiting for a human.
```bash
export SPEC_REVIEW_MODE=auto     # /spec-review runs unattended (cross-family, fail-closed)
```

---

## 2. Auto-chain: the `avenger-orchestrator` agent

### What it is
One agent that runs the pipeline end-to-end and **reacts to gate verdicts** (dynamic, not a fixed
script): it invokes each stage, reads the artifact + the gate result, and decides the next move —
proceed, or follow `route_back` and retry the failed stage. One call replaces the manual chain:
```text
@avenger-orchestrator "Build the ClickUp agent from clickup-epic/*.md"
```

### Flow it drives
```
task-analyst → solution-architect → implementation-planner → spec-writer
  → (fidelity gate auto) → (spec-review auto)                      # per spec
  → per phase, per spec: backend/frontend-architect (tests + code, test-first)
  → avenger-verifier (cross-family: suite + trace + bounded test review → verdict.json,
                       LOCKS the suite)  → handover
  → next phase … → e2e-author (once, after the final phase) → ship
```
Route-backs it must honor (all already emitted by the gates):
- fidelity/spec-review **NO-GO** → back to `avenger-spec-writer`, then re-gate.
- verifier code failure → back to `avenger-backend-architect`.
- verifier **test-quality** finding (tautological / off-seam / untraced requirement) → back to the
  implementer to ADD or rewrite its own not-yet-locked tests.
- breaker counterexample, or a surviving mutant when a project set `MUTATION_POLICY` to
  advisory/enforce → back to the implementer, to **add** a test (the suite is locked by then —
  additions only).
- Stop after N retries on the same stage and surface the report (don't loop forever).
- **A mutation route-back loop is a signal, not a grind.** The gate passes at a threshold (default
  0.85), not at zero survivors. If the same phase bounces twice, stop and surface it: either the
  requirement is pitched below the seam (→ spec-writer) or the threshold is wrong for this codebase
  (→ ask). Do not let the implementer farm narrow tests to chase mutants.

### Hard rules it must keep (same invariants as manual)
- **Never weakens a locked test.** The implementer owns `tests/` until the Verifier passes the phase;
  after that the suite is locked and only additions demanded by a gate are allowed.
- **Never bypasses a gate** except with an explicit `GATE_BYPASS="reason"` (logged, visible).
- Respects `work_kind` (greenfield | migration | refactor) for the implementer's test mode;
  `e2e-author` is not selected by `work_kind` — it runs once, at feature close.
- **Integration-level by default**; a `narrow` test needs a written justification in `test-mapping.md`.
- Stops-and-explains on any fail-closed gate rather than pushing past it.

### How to build it (canonical-source driven)
1. Create `agents/avenger-orchestrator.md`:
   - frontmatter: `name: avenger-orchestrator`, `description: …`, `model: opus` (it reasons about
     routing), `tools: Read, Write, Edit, Glob, Grep, Bash`.
   - body: the flow above, the route-back table, the hard rules, and "invoke each stage as a subagent,
     wait for its artifact, read the gate outcome, decide next".
2. Regenerate the opencode adapter: `python3 scripts/sync_opencode.py`.
3. Bump `version` in `.claude-plugin/plugin.json`, commit, push to `main`.
4. In Claude Code: `/plugin update` → `/reload-plugins`.

> A static alternative is a slash command `commands/avenger-run.md` that just lists the stages in
> order. Prefer the orchestrator agent — it branches on `route_back` and retries; a command can't.

### Using it
- Manual by default — invoke stages yourself.
- Hands-off — call `@avenger-orchestrator "<goal>"`. That single choice is your "only if I want it".

---

## 3. Fully unattended (headless / CI / SDK)

Run the orchestrator with no interactive terminal. Gates + `gate-overrides.log` keep it honest — a
fail-closed gate self-halts the run instead of shipping junk.

### Env (export before launching)
```bash
export AUTHOR_FAMILY=anthropic
export GATE_MODEL=opencode-go/deepseek-v4-pro     # all gates on DeepSeek V4 Pro via OpenCode
export GATE_PROVIDER=opencode
export SPEC_REVIEW_MODE=auto                        # no human spec-review
# OpenCode auth present (opencode-go creds). For the git floor (pre-commit/CI) instead:
#   GATE_PROVIDER=openrouter + OPENROUTER_API_KEY + GATE_MODEL=openrouter/deepseek/deepseek-v4-pro
```

### Headless Claude Code (print mode)
```bash
cd ~/Documents/GitHub/experiment/agents
claude -p "@avenger-orchestrator Build the ClickUp agent from clickup-epic/*.md"
```
- `-p` = non-interactive, prints and exits. Wire it into a Makefile target, a cron job, or a CI step.
- Exit non-zero when a gate fails closed → your CI catches a bad run.

### Claude Agent SDK (programmatic)
For a supervisor with retries/notifications, drive it from the SDK (Python/TS): spawn the session,
send `@avenger-orchestrator "<goal>"`, stream results, act on the final report. Use when you want a
loop that kicks off multiple features or reacts to a queue (e.g. a ClickUp webhook → a pipeline run).

### The git-floor backstop (defence in depth)
Even fully unattended, the commit/PR floor re-runs the gates:
```bash
cd ~/Documents/GitHub/experiment/agents && pre-commit install
# add OPENROUTER_API_KEY as a CI secret; the pipeline-gates workflow runs on PR
```
So an autonomous in-session run that somehow slipped a gate still gets caught at commit/CI.

---

## Guardrails recap (why autonomy is safe here)
- Every gate is **cross-family + fail-closed** → stops on missing key, unreachable model, non-JSON, or
  same-family.
- Every failure **routes back** to a specific stage rather than proceeding.
- **The Verifier reviews the tests, not just the run** — the one independent judgement on a suite
  whose author also wrote the code, on a bounded review set, persisted to `verdict.json`. After it
  passes, the suite is **locked**: automation can't reshape a test to go green.
- **Break-glass** is the only override and it is logged + visible + recorded in `handover.md`.
- The **git floor** re-checks at commit/PR.

Start manual, flip on `SPEC_REVIEW_MODE=auto` + `@avenger-orchestrator` when you trust it, go headless
last.
