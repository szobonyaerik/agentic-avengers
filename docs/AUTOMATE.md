# Automating the pipeline (pick up after a manual run)

Once you've walked the pipeline manually and trust the gates, remove the human from the loop — **opt
in per run**. Manual (stage-by-stage) stays the default; automation is something you *invoke*, not a
mode you're stuck in. Two layers:

- **2. Auto-chain the stages** — `/avenger-run` turns the main session into the orchestrator.
- **3. Fully unattended** — run that headless (CI / overnight / SDK), no terminal.

Prereq for both: the automated spec-review must be on, or the chain will block waiting for a human.
```bash
export SPEC_REVIEW_MODE=auto     # /spec-review runs unattended (cross-family, fail-closed)
```

---

## 2. Auto-chain: the `/avenger-run` command

### What it is
One command that turns the main session into an orchestrator running the pipeline end-to-end and
**reacting to gate verdicts** (dynamic, not a fixed script): it invokes each stage as a subagent,
reads the artifact + the gate result, and decides the next move —
proceed, or follow `route_back` and retry the failed stage. One call replaces the manual chain:
```text
/plan-build-verify:avenger-run clickup-agent "Build the ClickUp agent from clickup-epic/*.md"
```

### Flow it drives
```
task-analyst → solution-architect → implementation-planner → spec-writer
  → (fidelity gate auto) → (spec-review auto)                      # per spec
  → per phase, per spec: backend/frontend-architect (tests + code, test-first)
  → avenger-verifier (cross-family: suite + trace + bounded test review → verdict.json,
                       LOCKS the suite)  → handover
  → next phase … → e2e-author (once, after the final phase)
  → ship gate (no-mistakes: lint, docs, push, PR, CI) → retrospective triage
```
The ship gate runs **before** the triage on purpose: a defect it catches that no avengers gate
covers is the most valuable thing the retrospective can record about the pipeline.
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

### BUILT — as a command, not an agent

This section used to say "create `agents/avenger-orchestrator.md`". **That cannot work.** A subagent
in Claude Code has no Task tool — the pipeline agents' `tools:` lists are `Read, Write, Edit, Glob,
Grep, Bash` — so an orchestrator *agent* could not spawn `avenger-spec-writer` or any other stage.
Only the main thread can spawn subagents.

So the orchestrator is **`commands/avenger-run.md`**, which turns the main session into the
orchestrator. It is not the "static list of stages" this file previously dismissed: it branches on
gate verdicts and retries, because the routing lives in the command body and the *position* comes from
`scripts/pipeline_state.py` — a deterministic resolver that reads spec frontmatter stamps
(`fidelity_verdict`, `review_status`, `status`) and `verdict.json` and returns the one stage the
feature owes next. Artifacts are the state, so a run resumes across `/clear`, compaction, or a new
session.

What it does beyond the flow above:
- **Stops twice by default** — after `plan.md` (approve the phase breakdown before specs are written)
  and at each `/spec-review`. `--auto` removes both and sets `SPEC_REVIEW_MODE=auto`.
- **2 retries per stage**, then halts with the artifact, verdict and route-back reason. Mutation halts
  on the second bounce of the same phase.
- **Breaker only on `criticality: critical`** (a spec frontmatter field); mutation only per
  `MUTATION_POLICY`.
- **Branches once, commits per verified phase, plus twice at feature close**: the e2e stage's output
  *before* the ship gate, because §4a's precondition is a clean tree that already contains
  `tests/e2e/<feature>/`; then the retrospective artifacts written *after* it, guided by
  `branch_sync.next_action`. A stage's artifacts are committed before the stage that consumes them.
- **The orchestrator never pushes and never opens a PR.** The one exception is the **§4a ship gate**,
  in interactive **and `--auto`** runs alike: `no-mistakes` pushes the branch and opens the PR from
  inside the daemon's own worktree, stops at `checks-passed`, and never merges. Under `--auto` an
  `ask-user` finding **halts** the run with the finding recorded verbatim; `--ship-yes` (only valid
  with `--auto`) passes `--yes` so the gate resolves those itself.
- **Preflight-checks `no-mistakes` (both modes) and `lavish-axi` (interactive)** and stops if either
  is missing, rather than dying at the plan stop or at feature close. Neither has a silent fallback.
  For `no-mistakes` it checks **three** states, because none implies the next: the binary plus a
  runnable pipeline agent (`no-mistakes doctor`), the repo **initialised** (`no-mistakes axi`, which
  exits 1 with `error: repo not initialized` — a `.no-mistakes.yaml` existing says nothing about the
  gate repo, the post-receive hook, the remote or the DB record), and the **content** of that config:
  a scaffolded one still holding the template's `REPLACE_ME` token fails preflight, not the ship gate.
- **Logs pipeline observations as they happen** and triages them at `done`. `--auto` records but never
  triages - there is nobody to poll - so the log stays `triage: pending` and the next *interactive*
  run's preflight sweep surfaces it, across every feature. That sweep is the only recovery path,
  because `done` is terminal. Procedure: `skills/pipeline-retrospective`.

### `--auto` and permission prompts

A slash command cannot change a running session's permission mode, so `--auto` arms a sentinel
instead: it writes `.avenger-auto` (a unix timestamp) into the project root, and
`scripts/hook_autoapprove.sh` — a `PreToolUse` hook — returns `permissionDecision: allow` while that
file exists. Because plugin hooks also fire inside subagents, this covers the implementer's `pytest`
and the Verifier's tooling too, not just the orchestrator's own calls. An unattended run never stalls
on a prompt.

It is armed **only** for an `--auto` run, and it retires three ways: the command removes it when the
run ends or halts, the `Stop` hook removes it when the turn ends, and it expires after
`AVENGER_AUTO_TTL_MIN` minutes (default 240) if the session dies without reaching either. Every
failure path in the hook — no sentinel, expired, unreadable, unparseable payload — prints nothing and
lets the normal permission flow decide. Silence is the safe default.

**Hard denials, not configurable:** `git push`, `gh`/`gh-axi` `pr|release|repo|issue|gist` with
`create|merge|edit|delete|close`, `npm|yarn|pnpm publish`, `twine upload`, `rm`, `sudo`, `dd if=`,
`mkfs`, and `curl … | sh` are **denied** while an auto run is armed. No environment variable re-enables
them — the orchestrator branches and commits but never pushes *itself*, and nothing in the pipeline
needs to delete files. The §4a ship gate is unaffected either way: it pushes from inside the
`no-mistakes` daemon's own worktree, in its own process, so no `git push` ever reaches this hook.
Issue creation is in the list because the retrospective files improvement issues
upstream and its confirmation gate is a human selecting them, which cannot happen unattended.
`AVENGER_AUTO_DENY=<regex>` only *adds* patterns. Add `.avenger-auto` to `.gitignore`
(`/plan-build-verify:pipeline-init` does this).

**The deny regex reads the whole command string, so prose belongs in a file and the command reads
it.** It matches *content*, not intent: a `--intent` explaining that the ship gate pushes, or a
`--note` reporting that `gh pr create` never ran, denies the command carrying it even though that
command pushes nothing. Narrowing the regex is the wrong fix — it would spare real pushes too.
Instead any author-written free text is written to a gitignored file with the `Write` tool and read
inline as `"$(cat <file>)"`. Never build that file with `cat`/`echo`/a heredoc — a heredoc puts the
prose straight back on the command line and is denied identically.

**This is a rule, not a list of flags.** It covers arguments added after this was written. The test is
whether an author could have phrased the value differently: `--intent`/`--instructions` on
`no-mistakes axi`, `--note`/`--evidence` on `pipeline_observations.py append`, and a `GATE_BYPASS`
reason are examples, not the boundary. `GATE_BYPASS` is no exception for being a shell assignment
prefix — `GATE_BYPASS="$(cat <file>)" git commit …` works and is allowed, while `export` does not
survive between an agent's Bash calls. A value fully determined by a template, with only ids, paths
and fixed keywords substituted, is not prose and stays inline. Full rule:
`skills/pipeline-conventions`.

### Using it
- Manual by default — invoke stages yourself.
- Hands-off — `/plan-build-verify:avenger-run <feature-id> "<brief>"`, then re-invoke with just the
  feature id to resume. Add `--auto` for unattended, and `--auto --ship-yes` when you also want the
  ship gate to resolve its own `ask-user` findings instead of halting for you.

> **opencode gap:** `sync_opencode.py` transpiles agents and links skills but does not emit
> `.opencode/command/`, so `/avenger-run` is Claude Code only. Under opencode, drive the stages
> manually — `pipeline_state.py` still works and tells you where you are.

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
claude -p "/plan-build-verify:avenger-run clickup-agent \"Build the ClickUp agent from clickup-epic/*.md\" --auto"
# The --auto sentinel handles in-session prompts; -p sessions still need a permission mode:
#   --permission-mode acceptEdits   (recommended)
```
- `-p` = non-interactive, prints and exits. Wire it into a Makefile target, a cron job, or a CI step.
- Exit non-zero when a gate fails closed → your CI catches a bad run.

### Claude Agent SDK (programmatic)
For a supervisor with retries/notifications, drive it from the SDK (Python/TS): spawn the session,
send `/plan-build-verify:avenger-run <feature-id> "<goal>" --auto`, stream results, act on the final report. Use when you want a
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

Start manual, flip on `SPEC_REVIEW_MODE=auto` + `/avenger-run --auto` when you trust it, go headless
last.
