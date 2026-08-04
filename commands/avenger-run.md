---
description: Drive the plan-build-verify pipeline end to end for one feature — resumable from the artifacts on disk, stops for plan approval and spec-review, commits per verified phase.
argument-hint: "<feature-id> [\"<brief or path to brief>\"] [--auto] [--ship-yes] [--from <stage>]"
disable-model-invocation: true
---

You are the **orchestrator** for `$ARGUMENTS`. You do not write production code, tests, or specs
yourself — you invoke the stage agents, read what they produced, and decide the next move. The one
exception is git (below).

Parse `$ARGUMENTS`: the first token is the **feature id**; a quoted string or a path is the **brief**
(required only on the first run); `--auto` runs unattended; `--ship-yes` lets the §4a ship gate
resolve its own `ask-user` findings and is **valid only together with `--auto`** — reject it on an
interactive run, where you can simply ask; `--from <stage>` forces a starting stage instead of the
resolved one.

## 1. Preflight

Run these before anything else, and stop with the fix if one fails:

- `git rev-parse --abbrev-ref HEAD` — if on `main`/`master`, create `feat/<feature-id>`. Never work on
  the default branch.
- `docs/features/<feature-id>/` exists? If not and a brief was given, create it. If `docs/features/`
  itself is missing, tell the user to run `/plan-build-verify:pipeline-init <feature-id>` first and stop.
- `OPENROUTER_API_KEY` set (or `opencode` on PATH). Gates fail closed without it — do not start a run
  that will halt at the first gate.
- **`no-mistakes` on PATH and `.no-mistakes.yaml` at the repo root.** The §4a ship gate needs both,
  in interactive *and* `--auto` runs. Missing → stop now and say so: §4a fires at `done`, after every
  phase is already built, and discovering it there wastes the whole run.
  `/plan-build-verify:pipeline-init` scaffolds the config.
- **`lavish-axi` on PATH** — unless `--auto`, which skips both surfaces that use it. The plan
  approval stop (§3) and the retrospective triage (§4b) render through it and there is **no markdown
  fallback on purpose**: a stop that silently degrades to a plain read is a gate weakened invisibly,
  which is the failure mode this pipeline exists to prevent. Missing → stop and tell the user to
  install it.
- **Untriaged pipeline observations** — load `skills/pipeline-retrospective` and sweep **every**
  feature, not just this one:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" pending --root "${CLAUDE_PROJECT_DIR}"
  ```

  Anything listed has observations no human has seen — triage them (§4b) before starting new work.
  Skip when `--auto`: there is nobody to triage with, and they keep until the next interactive run.
  This sweep is the *only* thing that recovers what an `--auto` run learned, because `done` is a
  terminal state and will never re-fire that feature's own close.
- **Prior lessons** — load `skills/self-improvement`. If `docs/lessons/lessons.json` exists, read the
  index only, filter to this feature's stack and task, and open just the few prose files that matter.
  Missing file: skip silently.
- With `--auto`: export `SPEC_REVIEW_MODE=auto` for the run, and **arm the permission bypass**:

  ```bash
  date +%s > "${CLAUDE_PROJECT_DIR}/.avenger-auto"
  ```

  While that sentinel exists, `scripts/hook_autoapprove.sh` auto-approves tool calls — in this thread
  and in every subagent — so an unattended run never stalls on a prompt. Tell the user it is armed.
  It is removed by the Stop hook when the turn ends, and expires after `AVENGER_AUTO_TTL_MIN`
  (default 240) if the session dies first. **Remove it yourself the moment the run finishes or
  halts:**

  ```bash
  rm -f "${CLAUDE_PROJECT_DIR}/.avenger-auto"
  ```

  `git push`, `gh`/`gh-axi` PR, issue, release and gist creation, publish commands, `rm` and `sudo`
  are **denied outright** while it is
  armed and no setting re-enables them. If the run genuinely needs one, halt and tell the user to run
  it themselves. The §4a ship gate is the deliberate exception and needs no allowance: it pushes and
  opens the PR from inside the daemon's **own** worktree, in its own process, so no `git push` ever
  reaches this hook to be denied.

  Without `--auto`: do not write the sentinel. Spec-review is the HITL grill and will block, which is
  the point.

## 2. The loop

Repeat until the resolver reports `done`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pipeline_state.py" <feature-id> --root "${CLAUDE_PROJECT_DIR}"
```

It returns `{stage, phase, spec, spec_path, criticality, reason}` derived from the artifacts on disk —
**this is the source of truth for where the feature stands**, not your memory of what you just ran.
Re-run it after every stage. It is what makes a run resumable across a `/clear`, a compaction, or a
new session: invoking `/avenger-run <feature-id>` with no brief picks up exactly where it stopped.

Map the stage to a subagent and invoke it with the feature id, the artifact paths, and the resolver's
`reason` as context:

| `stage` | Do |
|---|---|
| `task-analyst` | `plan-build-verify:avenger-task-analyst` with the brief |
| `solution-architect` | `plan-build-verify:avenger-solution-architect` |
| `implementation-planner` | `plan-build-verify:avenger-implementation-planner` → **then stop, see §3** |
| `spec-writer` | `plan-build-verify:avenger-spec-writer` for the named phase/spec |
| `fidelity-gate` | The gate runs automatically on the spec write. Re-read the spec; if `fidelity_verdict` is still absent, the hook did not fire — report that and stop rather than proceeding ungated |
| `spec-review` | `/plan-build-verify:spec-review <spec_path>` (add `--auto` when running unattended) |
| `implementer` | `plan-build-verify:avenger-backend-architect`, or `avenger-frontend-developer` when the spec is UI. It writes tests **and** code, test-first |
| `verifier` | `plan-build-verify:avenger-verifier` for the phase — then §4 |
| `handover` | `plan-build-verify:avenger-handover` for the phase — then §5 |
| `e2e-author` | The implementer once, in `e2e-author` mode, for the whole feature |
| `done` | **Ship gate (§4a), retrospective triage (§4b), the feature-close commit (§5), then** report and stop |

`--from <stage>` overrides the first iteration only; afterwards the resolver drives.

## 3. Stop for the plan

After `plan.md` is written, **stop and show the user the phase breakdown** — goals, order, and what
each phase delivers. Wait for approval before any spec is written. A bad plan burns every phase
downstream, and this is the cheapest place to catch it.

**Show it as a review surface, not a wall of markdown.** Load the `lavish` skill and open its
`plan`, `diagram` and `input` playbooks before writing any HTML.

1. Render `docs/features/<feature>/plan.md` into `.lavish/<feature>-plan.html`: the phase list with
   each phase's goal, delivery and spec count; a **mermaid** dependency graph showing build order;
   and the risks and open questions. Follow the `input` playbook so the user can mark each phase
   approved or send it back, rather than having to describe changes in prose.
2. Serve it, then poll in the foreground:
   ```bash
   lavish-axi .lavish/<feature>-plan.html
   lavish-axi poll .lavish/<feature>-plan.html
   ```
   The poll blocks until the user sends. That is expected — **never kill it**. If it dies anyway,
   re-run it; queued feedback is not lost.
3. **Feed the returned annotations back to `avenger-implementation-planner` as revisions**, then
   re-render and poll again. Loop until the user approves. Do not start `spec-writer` on a plan
   carrying unresolved annotations.
4. Design source: this pipeline is language-agnostic and ships no design system, so use the
   Tailwind + DaisyUI CDN path from `lavish-axi design` unless the *target project* has its own —
   check the project the plan is about, not this repo. Say which source you used.

**Under `--auto`, skip the whole stop** — plan approval and the lavish surface alike. There is no
human to poll and a foreground `poll` would hang the run indefinitely.

`.lavish/` is scratch: add it to the target repo's `.gitignore` rather than committing artifacts.

## 4. After the Verifier passes a phase

1. **Breaker** — only if the resolver reports `criticality: critical` for the phase. Invoke
   `plan-build-verify:avenger-breaker`. A counterexample routes back to the implementer to **add** a
   test (the suite is locked; additions only).
2. **Mutation** — do nothing unless `MUTATION_POLICY` is `advisory` or `enforce`. It is off by default
   and is not the independence mechanism.
3. Then `handover`.

## 4a. Ship gate (`no-mistakes`) — at `done`, once per feature

The last phase is verified and the e2e suite is written. Everything the avengers gate covers is
green — and none of it covered lint, docs, push, PR or CI. That is this stage.

**It runs in `--auto` runs too.** The only thing `--auto` changes is what happens on an `ask-user`
finding:

- **Default under `--auto`** — drive the `auto-fix` and `no-op` findings on your own judgement
  exactly as you would interactively, but on an **`ask-user` finding, halt the run**, with the
  finding recorded **verbatim** in the report (id, file, full description). This is the same halt
  `--auto` already performs on a spec-review NO-GO. `no-mistakes` marks a finding `ask-user`
  precisely because it challenges the user's deliberate intent or changes product behaviour, so an
  unattended run must not answer it. Nothing is lost: the `no-mistakes` run stays parked on the
  branch and the user resumes interactively to answer it.
- **`--ship-yes`** (only valid with `--auto`) passes `--yes` to `no-mistakes`, which treats every
  actionable finding — `ask-user` included — as consent to fix, so the gate drives itself all the way
  to `checks-passed` with no halt. `--yes` is the user's **standing consent for the pipeline to
  resolve questions it flagged as theirs**. It is per-run, opt-in, and deliberately not the default.

So an `--auto` run **can** push and open a PR. That is intended, not a leak: the push happens inside
the daemon's own worktree where `hook_autoapprove.sh` never sees it, and §5 records the ship gate as
the one exception to "the orchestrator never pushes" in **both** modes.

**You must be on the feature branch.** `no-mistakes axi respond` resolves to the *current branch's*
active run and takes no `--run` flag, so from the wrong branch it returns `error: no step is
awaiting approval` while the real run sits parked and invisible. Preflight already branched you to
`feat/<feature-id>`; do not switch away mid-gate.

1. **Preconditions.** Every phase has `verdict.json` with `verdict: pass`, `tests/e2e/<feature>/`
   exists, and the working tree is clean and committed on `feat/<feature-id>`. Any missing → stop.
2. **Start the run**, passing the feature's goal as intent:
   ```bash
   no-mistakes axi run --intent "<goal from overview.md, plus the decisions, tradeoffs and
     deliberate divergences recorded in plan.md and each handover.md>"
   ```
   Add `--yes` **only** when the run was invoked with `--auto --ship-yes`.
   **A thin intent is actively harmful** — the review uses it to tell a deliberate decision from a
   mistake, so anything the pipeline chose on purpose (a same-family gate, a disabled feature, a
   waived finding) must be stated or it gets flagged as a defect. Write the intent to a file and
   pass `"$(cat file)"`: backticks in an inline string are eaten as shell command substitution.
3. **Drive it.** Read every return. On a `gate:`, respond; loop until an `outcome:`. Steps take
   minutes — a long call is working, not stalled. Never idle-wait; the run does not advance on its
   own.
   - `auto-fix` / `no-op` findings: decide yourself.
   - **`ask-user` findings: relay verbatim** — id, file, full description — and let the user decide.
     Never approve, fix or skip one on your own judgement. Under `--auto` there is nobody to relay
     to, so **halt** and put it verbatim in the report instead; under `--auto --ship-yes` the `--yes`
     you already passed resolves it and you keep driving.
   - **Never hand-fix while a run is active.** The pipeline owns both findings and fixes; the
     route-back-to-implementer rule is suspended for the duration. Do not `abort`/`rerun` to go fix
     something yourself — that discards in-flight work.
4. **Stop at `outcome: checks-passed`.** The PR is ready; tell the user to review and merge. Do not
   wait for the merge — the CI monitor handles rebase-on-conflict by itself.
5. **If it ends `failed`** (an agent crash mid-fix will do this), the pipeline's commits are
   preserved but unpushed. Read `branch_sync.next_action` and follow it — `axi sync --recover` when
   it says `recover_custody`. Never improvise a reset, rebase or branch replacement to recover.
6. **Log what the gate found** as pipeline observations before moving on — one entry per finding, not
   one per run. A defect the ship gate caught that every avengers gate missed is a finding *about the
   pipeline*, and it is the single most valuable input the retrospective gets:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" append <feature-id> \
     --root "${CLAUDE_PROJECT_DIR}" --kind other \
     --note "ship gate found <what>; no avengers gate covers it because <why>"
   ```
   **Pick the `--kind` per observation** from the taxonomy in `skills/pipeline-retrospective` — a
   coverage gap no avengers gate reaches is `other`, an avengers gate that *did* catch it is
   `success`. It is **not** `gate-friction`: that kind means a rubric is too strict, which is the
   opposite signal, and hardcoding it would skew every triage card.

**Do not merge, and do not tell the user a run is finished until an `outcome:` says so.** Merging a
branch the pipeline still owns splits it from the pipeline head and strands the fix commits.

## 4b. Retrospective triage — at `done`, after the ship gate

Runs **after** §4a so it can include what the ship gate found. Load `skills/pipeline-retrospective`
and follow its triage procedure. In short:

1. **Final sweep** — re-read every phase's `verdict.json`, `gate-overrides.log` and the specs'
   `fidelity_verdict` stamps, and append anything the run revealed that you did not log live.
2. **Render a lavish triage artifact** (its `input` playbook) with one card per observation — kind,
   evidence paths, and the change it implies — then `lavish-axi` it and `poll`.
3. **The user selects.** Selecting nothing is a valid, complete triage.
4. **File only the selected ones** as `pipeline-improvement` issues on
   `szobonyaerik/agentic-avengers`, titled as the *change*, with the evidence paths in the body.
5. **Always close the loop**, even when nothing was selected:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" resolve <feature-id> \
     --root "${CLAUDE_PROJECT_DIR}"
   ```

**Under `--auto`: skip this section entirely.** There is no human to poll and a foreground
`lavish-axi poll` would hang the run. Leave the log `triage: pending`; the next interactive run's
preflight sweep picks it up. Do **not** auto-file issues instead — `hook_autoapprove.sh` denies
`gh`/`gh-axi` issue creation outright while the auto sentinel is armed, so attempting it will fail.

## 5. Git

- Branch once, in preflight.
- After each phase has a passing `verdict.json` **and** its `handover.md`, commit everything for that
  phase with a conventional-commit message naming the phase and the verdict, e.g.
  `feat(<feature>): phase 2-api verified (12 tests, verdict pass)`.
- **Commit the feature-close artifacts at `done`** — as the last action of the run: after §4b
  resolves the triage, or, under `--auto` where §4b is skipped, straight after §4a. Never earlier.
  `pipeline-observations.md` is appended by §4a step 6 and rewritten to `triage: done` by §4b step 5,
  both *after* the ship gate already opened the PR, and no per-phase commit covers them — under
  `--auto` it is left at `triage: pending` and still must be committed, or the next interactive run's
  preflight sweep has nothing on disk to find. Sweep the tree and commit whatever the close produced
  (the observation log, `e2e-mapping.md` if still untracked):
  ```bash
  git add docs/features/<feature-id>
  git commit -m "docs(<feature-id>): feature-close pipeline observations"
  ```
  This is not optional bookkeeping: **§4a step 1 requires a clean tree**, so artifacts left dirty
  here are what stops the *next* feature's ship gate.
  **Read `branch_sync` before committing**, from `no-mistakes axi` or `axi status`, and act on its
  `next_action.code`: `continue_active_run` means the pipeline still owns the branch — keep driving
  it and make **no** local commit yet; `sync` means run `no-mistakes axi sync` first, then commit on
  top; `recover_custody` means `no-mistakes axi sync --recover` first. Never reset, rebase, force or
  replace the branch by hand.
  Then say plainly in the report that this commit landed **after** the PR was opened, is therefore
  not in it, and is unpushed — pushing it stays the user's call like every other push.
- **Never push, never open a PR yourself.** Those are the user's call. Say the commands at the end
  instead. The **one** exception is the §4a ship gate, in **both** interactive and `--auto` runs:
  `no-mistakes` pushes the branch and opens the PR as part of its own pipeline, and stops at
  `checks-passed` for the user to review and merge. It never merges. The rule is about *you*: the
  orchestrator never pushes, in either mode.

## 6. Retries and halting

- A stage that fails gets **2 retries** (3 attempts total). Then halt and print: the stage, the
  artifact, the gate verdict, and the `route_back` reason. Everything stays on disk — the user fixes
  and re-invokes to resume.
- **Mutation is tighter**: if the same phase bounces twice on survivors, stop immediately. Either the
  requirement sits below the seam (→ spec-writer) or the threshold is wrong for this codebase (→ ask
  the user). Do not let the implementer farm narrow tests to chase mutants.
- Route-backs to honour: fidelity/spec-review NO-GO → spec-writer, then re-gate. Verifier code failure
  → implementer. Verifier test-quality finding → implementer, to fix or add its own tests. Breaker
  counterexample → implementer, additions only.

**Record what the friction says about the pipeline.** Every retry, route-back, repeated NO-GO and
break-glass is evidence about the *machinery*, and you are the only agent that sees all of it. Log it
the moment it happens — not at the end from memory, because this run may resume in a different
session:

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" append <feature-id> \
  --root "${CLAUDE_PROJECT_DIR}" \
  --kind <gate-friction|route-back|bypass|stage-churn|success|other> \
  --note "<what happened, and what it suggests about the pipeline>" --evidence "<path>"
```

Log **successes** too — a gate that caught something real is the evidence for keeping it. Full
procedure and the triage step in `skills/pipeline-retrospective`.

## 7. Hard rules — the same invariants as a manual run

- **Never weaken a locked test.** The implementer owns `tests/` until the Verifier passes the phase.
  After that the suite is locked: additions demanded by a gate are allowed, weakening is not.
- **Never bypass a gate.** Only an explicit `GATE_BYPASS="reason"` from the user, which is logged to
  `gate-overrides.log`, shown visibly, and recorded in `handover.md`.
- **Never skip a stage because it looks unnecessary.** The resolver decides; you execute.
- Respect `work_kind` (greenfield | migration | refactor) for the implementer's test mode.
  `e2e-author` is not selected by `work_kind` — it runs once, at feature close.
- Integration-level tests by default; a `narrow` test needs written justification in `test-mapping.md`.
- On any fail-closed gate: **stop and explain**. Never push past it.

## 8. Report

At the end (done or halted) print: phases completed, the verdict for each, tests added, what stage it
stopped at and why, and the commits made.

Then, depending on how the run ended:
- **Ship gate reached `checks-passed`** — give the PR link and ask the user to review and merge.
  Say plainly what the gate *fixed that the pipeline missed* (its `fixes` table): those are the
  defects every avengers gate let through, and hiding them wastes the run's most useful signal.
  If the §5 feature-close commit landed after the PR opened, say it is local and unpushed, and give
  the `branch_sync` next action rather than a raw `git push`.
- **Ship gate halted on an `ask-user` finding under `--auto`** — reproduce the finding **verbatim**
  (id, file, full description), say the `no-mistakes` run is parked on the branch, and tell the user
  to resume interactively to answer it. Do not summarise it away or guess the answer.
- **Ship gate skipped (preconditions unmet)** — say so explicitly and print the command the user
  should run, rather than implying the feature is shipped.
- **Anything still `triage: pending`** — say which features, so the user knows observations are
  queued and will surface on the next interactive run.
