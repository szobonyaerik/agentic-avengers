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

- `git rev-parse --abbrev-ref HEAD` — **the run must be on `feat/<feature-id>` and stay there.**
  - On `main`/`master`: create `feat/<feature-id>`. Never work on the default branch.
  - Already on `feat/<feature-id>`: continue — that is the normal resume path.
  - On any **other** branch: stop, naming the branch you are on, and tell the user to switch
    (`git switch -c feat/<feature-id>`) or re-invoke with the feature id matching the branch. Do not
    build on it silently: §4a requires this branch, and `no-mistakes axi respond` resolves to the
    *current* branch's active run, so a mismatch surfaces only at `done` — after every phase is
    built. Same wasted-run failure the `.no-mistakes.yaml` content check below exists to prevent.
- `docs/features/<feature-id>/` exists? If not and a brief was given, create it. If `docs/features/`
  itself is missing, tell the user to run `/plan-build-verify:pipeline-init <feature-id>` first and stop.
- `OPENROUTER_API_KEY` set (or `opencode` on PATH). Gates fail closed without it — do not start a run
  that will halt at the first gate.
- **The §4a ship gate's three preconditions — the binary, an initialised repo, and a filled-in
  config.** All three are needed in interactive *and* `--auto` runs, and each is checked by
  *interrogating state*, never by printing advice and hoping:

  1. **`no-mistakes` on PATH**, and `no-mistakes doctor` reporting a runnable pipeline agent. A run
     with no configured agent fails before its first step, and `doctor` is the only thing that says
     so.
  2. **The repository initialised** — `no-mistakes init` creates the bare gate repo, the
     post-receive hook, the `no-mistakes` git remote and the DB record, and **none of that is implied
     by `.no-mistakes.yaml` existing** (`/plan-build-verify:pipeline-init` copies the config without
     initialising anything). Ask the tool, do not infer:

     ```bash
     no-mistakes axi          # home view; exits 1 with `error: repo not initialized` when it is not
     ```

     `no-mistakes status` prints the same sentence but **exits 0**, so branch on `axi`'s exit code or
     on the `error:` line — not on `status`'s exit code. Not initialised → stop and tell the user to
     run `no-mistakes init`. This is also the command §4a step 2 re-reads to find an existing run, so
     preflight and the gate ask the same question of the same source.
  3. **`.no-mistakes.yaml` at the repo root that is actually filled in.** Check the **content**, not
     just existence — `/plan-build-verify:pipeline-init` scaffolds the file with `REPLACE_ME`
     placeholders, and an unedited one passes an existence check and then runs a literal placeholder
     as a shell command at the gate:

     ```bash
     grep -nE '^[[:space:]]*(lint|test):.*REPLACE_ME' "${CLAUDE_PROJECT_DIR}/.no-mistakes.yaml"
     ```

     Match the marker **only where it is a value**. A bare `grep REPLACE_ME` over a commented config
     also matches the template's own prose, so a correctly filled-in file would fail this check
     forever. Any hit → stop, naming `.no-mistakes.yaml` and the exact keys still holding the marker
     (`commands.lint`, `commands.test`) so the user knows what to fill in.

  Any of the three failing → stop, naming the exact command that fixes it. The ship gate itself fires
  at `done`, after every phase is already built, which is exactly why all three are checked here:
  discovering any of them there wastes the whole run.
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
| `e2e-author` | The implementer once, in `e2e-author` mode, for the whole feature — **then commit its output, see §5** |
| `done` | **Ship gate (§4a), retrospective triage (§4b), the second feature-close commit (§5), then** report and stop |

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
4. **Phase review gate (`no-mistakes`, review-only)** — see §4c. Runs after the handover commit.

## 4c. Phase review gate (`no-mistakes`, review-only) — after every handover

The Verifier reads **tests**. Nothing else reads the rest of a phase's diff until feature close, so
docs, config, scripts and cross-file coherence go unreviewed for as many phases as remain. This
closes that gap without opening a PR per phase.

```bash
no-mistakes axi run --skip=push,pr,ci --intent "$(cat <intent-file>)"
```

- **`--skip=push,pr,ci` is the whole point.** Review, test, lint and docs run; nothing is pushed and
  no PR is opened. One PR per feature, at `done`, as before.
- **Intent from a file, never inline** — the same rule as §4a, and for the same two reasons.
  Scope it to *this phase*: its goal from `plan.md`, the seams it chose, and any deliberate
  divergence in it. A feature-wide intent makes the review flag settled decisions from earlier phases.
- **Review findings park, they do not self-fix** (`auto_fix.review: 0`). That is deliberate here:
  the phase's tests are **locked** the moment the Verifier passed, so the pipeline must not rewrite
  them. Route findings to the implementer the way a Verifier finding is routed — `--action skip` and
  hand it back — rather than `--action fix`. Use `--action fix` only for findings that touch neither
  tests nor locked code (docs, config, comments).
- **Under `--auto`**: same rule as §4a. Drive `auto-fix`/`no-op` findings; **halt on `ask-user`**.
  `--ship-yes` applies here too when given.
- **Do not run this on an unverified phase.** It runs *after* the Verifier and the handover commit,
  so what it reviews is a phase that already passed its own gate.

**Known unknown — measure it on the first phase you run.** It is not yet established whether
`no-mistakes` scopes review to the diff *since its last run on this branch* or re-reads the whole
branch each time. If it re-reads everything, phase 5 will re-raise findings settled in phases 1-4 and
the cost grows with each phase. Watch the first two runs; if you see settled findings return, log it
as a pipeline observation and fall back to feature-close-only until it is scoped. Do not assume
either behaviour.

## 4a. Ship gate (`no-mistakes`) — at `done`, once per feature

The last phase is verified and the e2e suite is written. Everything the avengers gate covers is
green — and none of it covered lint, docs, push, PR or CI. That is this stage.

**Load the `no-mistakes` skill and drive the gate by its procedure**, the way §3 loads `lavish` and
§4b loads `skills/pipeline-retrospective`. That skill is versioned with the binary and owns the
driving loop — preconditions, `--intent`, the gate/respond loop, the outcomes, `branch_sync`. This
section adds only what is *pipeline-specific* and does not restate it, so the two cannot drift.
Two things from it are load-bearing enough to name here:

- **Every `axi run` and `axi respond` blocks, and review/test/docs/CI take several minutes each.**
  Allow a long timeout, and **do not cancel or re-issue a call because it looks slow** — Claude
  Code's `Bash` tool defaults to 120s and caps at 600s, so **background the call** (`run_in_background`)
  rather than letting it time out. A cancelled call loses the run; a re-issued one starts a second
  (step 2). Check progress with a separate `no-mistakes axi status`.
- **The run never advances past a gate on its own.** A long call is working, not stalled — read every
  return, respond to every `gate:`, and never idle-wait.

**It runs in `--auto` runs too.** The only thing `--auto` changes is what happens on an `ask-user`
finding:

- **Default under `--auto`** — drive the `auto-fix` and `no-op` findings on your own judgement
  exactly as you would interactively, but on an **`ask-user` finding, halt the run**, with the
  finding recorded **verbatim** in the report (id, file, full description). This is the same halt
  `--auto` already performs on a spec-review NO-GO. `no-mistakes` marks a finding `ask-user`
  precisely because it challenges the user's deliberate intent or changes product behaviour, so an
  unattended run must not answer it. Nothing is lost: the `no-mistakes` run stays parked on the
  branch and the user resumes interactively to answer it. On that resume the resolver still reports
  `done`, so this section is simply re-entered — and step 2 is what stops you starting a second run
  over the parked one.
- **`--ship-yes`** (only valid with `--auto`) passes `--yes` to `no-mistakes`, which treats every
  actionable finding — `ask-user` included — as consent to fix, so the gate drives itself all the way
  to `checks-passed` with no halt. `--yes` is the user's **standing consent for the pipeline to
  resolve questions it flagged as theirs**. It is per-run, opt-in, and deliberately not the default.

So an `--auto` run **can** push and open a PR. That is intended, not a leak: the push happens inside
the daemon's own worktree where `hook_autoapprove.sh` never sees it, and §5 records the ship gate as
the one exception to "the orchestrator never pushes" in **both** modes.

**You must be on the feature branch.** `no-mistakes axi respond` resolves to the *current branch's*
active run and takes no `--run` flag, so from the wrong branch it returns `error: no step is
awaiting approval` while the real run sits parked and invisible. Preflight asserted you are on
`feat/<feature-id>` and stopped the run if you were not; do not switch away mid-gate.

1. **Preconditions.** Every phase has `verdict.json` with `verdict: pass`, `tests/e2e/<feature>/`
   exists, and the working tree is clean and committed on `feat/<feature-id>`. Any missing → stop.
2. **Look for an existing run before starting one.** `no-mistakes axi` is the home view: it lists
   this branch's runs with their status and PR. Read it first and branch on what it shows — **only a
   branch with neither an active nor a shipped run gets a new `axi run`**:
   - **A run parked at a gate** → do not start a second. Inspect it with `axi status`, answer it
     with `axi respond --action ...`, and rejoin at step 3. That is the normal state when an
     `--auto` run halted on an `ask-user` finding and the user came back interactively.
   - **A run that already reached `checks-passed` with its PR open** → the feature is already
     shipped. **Report that PR and stop.** Do not start a fresh validate-and-push cycle over an open
     PR. This is the re-entry case, not a rare one: `done` is resolved purely from `e2e-mapping.md`,
     so any later `/avenger-run <feature-id>` on a shipped feature lands right back here.
   - **Neither** → start one, passing the feature's goal as intent.

   `axi run` reattaches only while HEAD still matches the submitted head — it is **not** a
   general-purpose resume command, and §5's *second* feature-close commit moves HEAD past that
   point, which is exactly why naive re-entry starts a second run instead of rejoining the first.

   **Build the intent in a file and pass it by reading that file**, per the *prose belongs in a file*
   rule in `skills/pipeline-conventions` — this is that rule's most load-bearing
   instance, because a good intent for a shipping feature almost always names `git push` or
   `gh pr create` and would make `hook_autoapprove.sh` **deny the ship gate's own start command**:
   - **Write the file with the `Write` tool**, not with `cat`/`echo`/a heredoc. A heredoc puts the
     prose back on the command line and hits the same deny (verified: a heredoc carrying `git push`
     is denied).
   - Then start the run with the file read inline, so the command string stays prose-free:
     ```bash
     no-mistakes axi run --intent "$(cat .lavish/<feature-id>-intent.md)"
     ```

   Do not "simplify" that back to an inline string; it also stops backticks in the intent being eaten
   by the shell as command substitution. Put the file under `.lavish/` (already gitignored per §3) or
   `${TMPDIR}` so it is never committed. **`--instructions` on `axi respond` takes the same shape** —
   it is free prose relaying a user's guidance, and it is denied by exactly the same regex.

   Add `--yes` **only** when the run was invoked with `--auto --ship-yes`.
   **A thin intent is actively harmful** — the review uses it to tell a deliberate decision from a
   mistake, so anything the pipeline chose on purpose (a same-family gate, a disabled feature, a
   waived finding) must be stated or it gets flagged as a defect.
3. **Drive it** by the skill's gate/respond loop, with the timeout and backgrounding rule above.
   Where this pipeline differs from the skill's defaults:
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
5. **If it ends `failed`** (an agent crash mid-fix will do this), **the feature is not shipped** —
   no PR, nothing pushed — and the run does **not** continue as if it closed normally.
   1. **Recover custody first.** The pipeline's commits are preserved but unpushed. Read
      `branch_sync.next_action` and follow it — `axi sync --recover` when it says `recover_custody`.
      Never improvise a reset, rebase or branch replacement to recover.
   2. **Re-drive it exactly once** with `no-mistakes rerun` (a *between-runs* action, valid only
      after a terminal outcome like this). If that reaches an `outcome:`, continue from step 4 with
      it. Never leave the user parked at `failed` without either retrying or saying what blocks it.
   3. **If the re-drive also ends `failed`, halt.** Recover custody again, then stop and hand it to
      the user with the gate's output — do not retry a third time and do not report the feature as
      shipped. Still do step 6 (a ship gate that failed twice is prime retrospective evidence) and
      §5's second commit via the `recover_custody` path, then report through §8's `failed` branch.
6. **Log what the gate found** as pipeline observations before moving on — one entry per finding, not
   one per run. A defect the ship gate caught that every avengers gate missed is a finding *about the
   pipeline*, and it is the single most valuable input the retrospective gets. **Write the note to a
   file with the `Write` tool and read it inline** — same rule as the intent above, and it bites
   hardest here: §4a covers "lint, docs, push, PR or CI", so a note describing what the gate found is
   very likely to name a denied command:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" append <feature-id> \
     --root "${CLAUDE_PROJECT_DIR}" --kind other \
     --note "$(cat .lavish/<feature-id>-observation.md)"
   ```
   **Pick the `--kind` per observation** from the taxonomy in `skills/pipeline-retrospective` — a
   coverage gap no avengers gate reaches is `other`, an avengers gate that *did* catch it is
   `success`. It is **not** `gate-friction`: that kind means a rubric is too strict, which is the
   opposite signal, and hardcoding it would skew every triage card.
   An `--auto` halt at step 3 never reaches this step, so on the resumed run log the finding that
   halted it here too, alongside everything else the gate found.

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
- **Every `-m` below is a fixed template with only an id and a verdict substituted**, so it stays
  inline: the *prose belongs in a file* rule (`skills/pipeline-conventions`) turns on whether an
  author could have phrased the value differently. Do not improvise a commit subject that quotes what
  a gate found — that is prose, and under `--auto` it would deny the commit.
- **A stage's artifacts are committed before the stage that depends on them runs, and nothing the run
  produced is left uncommitted at the end.** Past the per-phase rule above, that means **two commits
  at feature close**, not one:

  1. **When `e2e-author` finishes — before §4a.** That stage belongs to no phase, so the per-phase
     rule never covers it, and §4a step 1 requires `tests/e2e/<feature>/` on a tree that is *clean
     and committed*; this commit is what makes that precondition satisfiable at all. It also puts the
     e2e suite on the branch `no-mistakes` validates — left uncommitted, the one stage documented as
     running the e2e suite would never see it.
     **Sweep everything the run has produced and not yet committed**, not a fixed list of paths — the
     e2e suite and `e2e-mapping.md`, and also `pipeline-observations.md`, which §6 requires you to
     append *the moment* something happens and which the per-phase rule stops covering once the last
     phase's commit has landed. `git status` is the authority on what is outstanding; anything left
     behind here is what makes §4a step 1 stop. `docs/lessons/` is the case that proves why a fixed
     path list fails: **any** agent may append a lesson mid-run (`skills/self-improvement`), it sits
     outside both `tests/e2e/` and `docs/features/`, and a run whose implementer logged one would
     stop the ship gate on its own clean-tree precondition.
     ```bash
     git status --short          # the authority — read it, then stage what it lists
     git add -A
     git commit -m "test(<feature-id>): feature-level e2e suite"
     ```
  2. **After §4b resolves the triage** — or, under `--auto` where §4b is skipped, after §4a. This
     covers `pipeline-observations.md` (appended by §4a step 6, rewritten to `triage: done` by §4b
     step 5) and anything else produced after the gate ran. Under `--auto` the log stays
     `triage: pending` and **still must be committed**, or the next interactive run's preflight sweep
     has nothing on disk to find. A feature where nothing was ever logged has no log and nothing to
     stage, so this second commit does not exist — do not claim it in the report.
     **`branch_sync` decides *how* to get there, never *whether*.** Read it from `no-mistakes axi` or
     `axi status` and act on `next_action.code` first:
     - `sync` — the normal path after `checks-passed`, since the gate pushed its own fix commits. Run
       `no-mistakes axi sync`, then commit on top.
     - `recover_custody` — the `failed`-outcome path (§4a step 5). Run `no-mistakes axi sync
       --recover` first, then commit on top.
     - `continue_active_run` — the pipeline still owns the branch, so you are **not at feature close
       yet**. Go back to §4a step 3 and keep driving until an `outcome:`; the commit is due after
       that, not skipped.

     Never reset, rebase, force or replace the branch by hand.
     ```bash
     git status --short          # same rule as commit 1: sweep, do not enumerate
     git add -A
     git commit -m "docs(<feature-id>): feature-close pipeline observations"
     ```
     Say plainly in the report that this second commit landed **after** the PR was opened, is
     therefore not in it, and is unpushed — pushing it stays the user's call like every other push.

  Neither is optional bookkeeping: **§4a step 1 requires a clean tree**, so anything left dirty at
  `done` is what stops the *next* feature's ship gate.
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

**Write the note to a file with the `Write` tool** and read it inline — free prose on a command line
is denied under `--auto` by content alone (`skills/pipeline-conventions`, hard rules). Apply that
rule's test per value: `--kind` is a fixed keyword and `--evidence` takes a path, so neither is prose
and both stay inline — but if an `--evidence` value ever carries words rather than a path, it goes in
a file too.

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" append <feature-id> \
  --root "${CLAUDE_PROJECT_DIR}" \
  --kind <gate-friction|route-back|bypass|stage-churn|success|other> \
  --note "$(cat .lavish/<feature-id>-observation.md)" --evidence "<path>"
```

Log **successes** too — a gate that caught something real is the evidence for keeping it. Full
procedure and the triage step in `skills/pipeline-retrospective`.

## 7. Hard rules — the same invariants as a manual run

- **Never weaken a locked test.** The implementer owns `tests/` until the Verifier passes the phase.
  After that the suite is locked: additions demanded by a gate are allowed, weakening is not.
- **Never bypass a gate.** Only an explicit `GATE_BYPASS="reason"` from the user, which is logged to
  `gate-overrides.log`, shown visibly, and recorded in `handover.md`. The reason is the user's prose,
  so under `--auto` pass it from a file — `GATE_BYPASS="$(cat <file>)" git commit …` — like `--intent`
  and `--note`; the inline prefix is denied when the reason names a denied command, and `export` does
  not carry across Bash calls. A multi-line reason file is safe — the writers normalise it through
  `scripts/bypass_reason.sh` so `gate-overrides.log` keeps one parseable record per bypass. See
  `skills/pipeline-conventions`.
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
  If §5's *second* feature-close commit was made — a feature that logged no observations has nothing
  to stage and so has no second commit — say it landed after the PR opened, is local and unpushed,
  and give the `branch_sync` next action rather than a raw `git push`.
- **Ship gate halted on an `ask-user` finding under `--auto`** — reproduce the finding **verbatim**
  (id, file, full description), say the `no-mistakes` run is parked on the branch, and tell the user
  to resume interactively to answer it. Do not summarise it away or guess the answer.
- **Ship gate ended `failed`** (including after the single re-drive in §4a step 5) — say plainly
  that **the feature is not shipped**: no PR, nothing pushed. Give the gate's failing step and its
  output, say custody was recovered and by which `branch_sync.next_action`, and hand the user the
  command to re-drive (`no-mistakes rerun`). Never end a `failed` run reporting only the commits.
- **Ship gate already shipped this feature** (§4a step 2 found a `checks-passed` run with an open
  PR) — give that PR link and say no new run was started, rather than implying fresh validation ran.
- **Ship gate skipped (preconditions unmet)** — say so explicitly and print the command the user
  should run, rather than implying the feature is shipped.
- **Anything still `triage: pending`** — say which features, so the user knows observations are
  queued and will surface on the next interactive run.
