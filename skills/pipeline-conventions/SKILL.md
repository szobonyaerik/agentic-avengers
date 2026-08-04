---
name: pipeline-conventions
description: The shared rules of the agentic-avengers pipeline — the agent chain, phase/spec layout and ID scheme, gates, and the locked-after-verify rule. Load this whenever you are acting as any pipeline agent or are unsure how phases, specs, tests, or gates fit together.
---

# Pipeline conventions

The single source of truth for how plan → build → verify fits together in this repo. Every agent
references this. (A plugin's CLAUDE.md is not loaded as context, so this skill is the canonical home
for these rules; `/pipeline-init` can also copy them into a target repo's own CLAUDE.md.)

> This pipeline is the sibling of `klm-agentic-pipeline` and deliberately shares its semantics. The
> only intended differences are: **(1)** this one runs on Claude Code + opencode rather than GitHub
> Copilot, **(2)** it adds an automated **Fidelity Gate** before the human spec review, **(3)** it
> keeps a **feature-level e2e** stage and **spec-isolation-review**, and **(4)** its mutation gate has
> a deterministic, diff-scoped scorer. Anything else that diverges is drift — fix it here.

## The chain

`task-analyst → solution-architect → implementation-planner → spec-writer → [fidelity gate] →
[human spec review] → backend/frontend implementer → verifier → (breaker, optional) → handover`,
looped per phase until the feature is done, then a single feature-level **e2e** stage, then the
**ship gate** (`no-mistakes`: lint, docs, push, PR, CI), and finally the **retrospective triage**
(`pipeline-retrospective`). The retrospective runs last on purpose — a defect the ship gate caught
that no avengers gate covers is exactly what it exists to record.

The implementer authors **both tests and code** test-first (there is no separate test-author); the
**Verifier is the independent check**.

## Phases and specs

- **Phase** = one cohesive, **independently verifiable slice**. Owned by the Implementation Planner.
- A phase contains **one or more numbered specs** `<n>.<k>` (phase.spec). Owned by the Spec Writer.
- **Layout:**
  ```
  docs/features/<feature>/
    task-analysis.md
    overview.md
    plan.md
    scoped/review-<slice>.md      # spec-isolation-review, when used
    e2e-mapping.md                # written once, at feature close
    pipeline-observations.md      # what the run revealed about the PIPELINE; triaged at close
    phases/<n>-<slug>/
      specs/<n>.<k>-<subslug>/
        spec.md
        test-mapping.md           # per SPEC, not per phase
      verdict.json                # the Verifier's persisted verdict for the phase
      handover.md                 # written after the Verifier passes the phase
  tests/<feature>/<n>-<slug>/<n>.<k>-<subslug>/...
  tests/e2e/<feature>/...         # feature-level only
  ```
- **Requirement IDs:** `R<n>.<k>.<m>` (phase.spec.requirement). Globally unique and traceable. Every
  test authored or ported for a spec maps to exactly one id in that spec's `test-mapping.md`; an
  inherited migration suite need not be exhaustively remapped.
- Artifact templates live in `docs/templates/`.

## Gates

- **Fidelity Gate (per spec, automated, cross-family)** — *this repo only.* Fires on spec write via
  `scripts/hook_fidelity.sh` + `prompts/fidelity-rubric.md`, stamps `fidelity_verdict`; `NO-GO` routes
  back to the Spec Writer. It is a cheap machine pre-filter, **not** a substitute for the human review
  that follows.
- **Human spec review (per spec)** — a human runs `grill-me` against the spec using
  `spec-review-checklist`, then sets `review_status: approved`. This is the last gate before
  implementation. Automated mode (`/spec-review --auto`, `SPEC_REVIEW_MODE=auto`) exists for
  unattended runs and fails closed like any gate.
  A spec reaches the implementer only when `fidelity_verdict != NO-GO` **and** `review_status: approved`.
- **Verifier (per phase, cross-family)** — after every spec in the phase is green, the Verifier runs
  the full suite, traces coverage, and puts the phase-mapped/changed tests and their directly
  referenced helpers through a **targeted test-quality review on a cross-family model** (the
  independence check). It expands only on explicit criticality or evidence, then passes or routes
  back. Fail closed — a green suite with no completed review is an *unreviewed* phase, not a pass. It
  persists `verdict.json`. **On pass, the phase's tests lock.**
- **Mutation gate (optional)** — off by default and most teams leave it off. Only when a project sets
  `MUTATION_POLICY` to `enforce`/`advisory` does the Verifier run it; otherwise no mutation tool runs
  anywhere. It is **not** the independence mechanism — the Verifier's test-quality review is.
- **Breaker** — critical/security paths only, optional.
- **Feature-level e2e** — once, after the final phase is green (see below).
- **Ship gate (per feature, `no-mistakes`)** — runs **once**, after the last phase is verified and the
  e2e suite is written, on the feature branch. It covers what no avenger stage does: lint, docs,
  push, PR and CI. It does **not** replace the Verifier — it has no `R<n>.<k>.<m>` traceability, no
  bounded test-quality review, and no `verdict.json`.
  - **When it runs, and what stops it.** Wired as `/avenger-run` §4a, immediately **before** the
    retrospective triage so its findings feed that triage. It runs in interactive **and `--auto`**
    runs alike — it is not interactive-only. Under `--auto` the orchestrator drives the gate's
    `auto-fix` and `no-op` findings itself, but an **`ask-user` finding halts the run**, recorded
    verbatim, exactly as `--auto` halts on a spec-review NO-GO: `no-mistakes` marks a finding
    `ask-user` because it challenges the user's deliberate intent or changes product behaviour, so an
    unattended run must not answer it. `/avenger-run --auto --ship-yes` passes `--yes` to
    `no-mistakes`, which resolves `ask-user` findings too — the user's standing consent for the
    pipeline to answer questions it flagged as theirs, per-run and deliberately not the default.
  - **It is the one thing in the pipeline that pushes.** It pushes the branch and opens the PR in
    both modes, stops at `outcome: checks-passed`, and never merges. The orchestrator itself still
    never pushes and never opens a PR. The `--auto` hard-deny list neither permits nor blocks this:
    `no-mistakes` pushes from inside the daemon's own worktree, in its own process, so no `git push`
    ever reaches `hook_autoapprove.sh`.
  - **While a `no-mistakes` run is active it owns both the findings and the fixes**, so the avenger
    "route back to the implementer" rule is suspended for its duration. Never `abort`/`rerun` mid-run
    to hand-fix something. This is exactly why it runs once at feature close and never per phase: by
    then every phase is verified and locked, so there is no live fix-ownership conflict.
  - **Deliberate same-family divergence.** Its pipeline agent is pinned to Anthropic Opus
    (`.no-mistakes.yaml` → `agent: claude`, Opus pinned via `agent_args_override` in
    `~/.no-mistakes/config.yaml`). This is a **conscious exception to the cross-family rule below**,
    not an oversight and not a break-glass bypass: no-mistakes runs in the daemon's own disposable
    worktree with no shared context with the stage that wrote the code, so it decorrelates *context*
    while accepting shared *family* blind spots. The per-phase gates — fidelity, spec-review,
    verifier — are unaffected and stay cross-family.

## Hard rules

- **Locked-after-verify:** the implementer authors tests test-first and owns them *until* the Verifier
  passes the phase; from that point the phase's tests are **locked** and weakening them requires
  re-verification. Tests are derived from the spec, never shaped to fit code. Locked forbids
  *weakening*, not *adding*: a Breaker counterexample or a surviving mutant routes back to the
  implementer to add a case.
- **Independence lives in the Verifier:** because the implementer writes its own tests, a different
  model family reads the bounded phase review set for tautological / implementation-coupled /
  missing-edge anti-patterns and routes gamed tests back. Full test execution stays broad; semantic
  reading does not scan unrelated unchanged tests.
- **Fresh model ≠ author:** the model that *forms the judgement* must not share the implementer's
  family. Every subagent here is Anthropic, so the Verifier **agent** cannot itself be cross-family —
  it orchestrates, and delegates the reading of the tests to `scripts/verifier_review.sh` →
  `gate_runner.py` on `$VERIFIER_GATE_MODEL` (default `google/gemini-3.1-pro-preview`). `gate_runner` refuses
  a same-family model, and CI asserts the same statically. Opus-vs-Sonnet is **not** decorrelation.
  The **only** sanctioned exception is the feature-close `no-mistakes` ship gate above, which is
  same-family on purpose and documented as such — every *per-phase* gate stays cross-family.
- **Under `--auto`, prose belongs in a file and the command reads it — never on the command line.**
  `hook_autoapprove.sh` matches its hard-deny regex against the **whole** Bash command string, so it
  matches **content, not intent**: any author-written prose passed as a command-line argument can
  **deny the command carrying it** merely by naming `git push` or `gh pr create`, even though that
  command pushes nothing. Narrowing the regex is the wrong fix — it would spare real pushes too.

  **The rule is the invariant, not the list below.** It covers any free-text argument, including ones
  added after this was written and ones nobody has documented yet. Ask the question, do not consult an
  enumeration: *could an author have phrased this value differently?* If yes it is prose → file. If
  the value is fully determined by a template and the only substitutions are ids, paths and fixed
  keywords, it is not prose → inline is fine.
  - **How.** Write the text to a file with the `Write` tool, then read it inline: `--intent "$(cat
    <file>)"`, `--note "$(cat <file>)"`, `GATE_BYPASS="$(cat <file>)" git commit …`. Never `cat`/
    `echo`/a heredoc to *create* it — a heredoc puts the prose straight back on the command line and
    is denied identically. Put the file somewhere gitignored (`.lavish/`, `${TMPDIR}`). It also stops
    backticks in the text being eaten by the shell as command substitution.
  - **Examples, deliberately non-exhaustive** — `--intent` and `--instructions` on `no-mistakes axi
    run`/`respond`, `--note` and `--evidence` on `scripts/pipeline_observations.py append`, and
    `GATE_BYPASS` on a break-glass invocation. Anything else carrying author-written words is covered
    by the rule whether or not it appears here; a value's absence from this list is not a waiver.
    Note that the **test decides per value, not per flag**: `--evidence` takes a path today, so it is
    not prose and stays inline — but that is the test's answer, not an exemption granted to the flag,
    and an `--evidence` value carrying words goes in a file like any other.
  - **`GATE_BYPASS` takes the file shape too**, and it is *not* a declared exception. Being a shell
    assignment prefix rather than a flag changes nothing — `GATE_BYPASS="$(cat <file>)" git commit …`
    is valid shell, sets the variable for exactly that command, and is allowed by the hook, while the
    inline prose prefix is denied. `export`ing it instead is **not** an agent-side substitute: env
    vars do not survive between Bash tool calls, so an `export` in one call is gone by the `git
    commit` in the next. Exporting is the *human's* delivery, in their own shell before the session
    starts, where the hook never sees it at all.
  - **Not covered:** a command merely *printed* for the user to run — nothing executes it. The test
    throughout is whether an agent chose the words **and** a shell will see them.
- **Gates fail closed.**
- **Break-glass bypass** is allowed but recorded — whole-gate via `GATE_BYPASS`, per-finding via
  `verdict.json` `break_glass` + a mandatory `waiver_reason` — in `handover.md` and
  `gate-overrides.log`, and visible on the PR. The reason is author-written prose, so under `--auto`
  it comes from a file (`GATE_BYPASS="$(cat <file>)" …`) like every other free-text argument above.
- **Artifacts on disk** with YAML frontmatter — the chain survives cold sessions.

## Where the models run

- **Mechanical gates run in CI and in hooks** (`scripts/gate_ci.sh`, the pre-commit floor): the test
  suite, the static cross-family assertion, artifact presence (`review_status: approved`,
  `verdict.json` passing), and mutation only if a team turned it on. Auditable, can't be silently
  skipped.
- **Model-based gates run in-chat** — the Verifier's triage and test-quality review, and the grill-me
  spec review. CI only checks their **committed artifacts**. The one exception is the automated
  **Fidelity Gate**, which is a hook and does call a model in-session; it never runs in CI.

## Two learning logs — keep them apart

The pipeline records what it learns in **two** places, with different subjects, scopes and
destinations. Putting a note in the wrong one buries it.

| | `docs/lessons/` (`self-improvement`) | `pipeline-observations.md` (`pipeline-retrospective`) |
|---|---|---|
| Subject | the **work** — a pytest trap, a migration gotcha | the **machinery** — a gate that misfires, a stage that churns |
| Scope | **per project**, committed, team-shared | per feature, triaged, then filed upstream |
| Written by | **any agent**, whenever something is learning-worthy | the **orchestrator**, during `/avenger-run` |
| Ends up | in this repo, read at session start | as an issue on the **agentic-avengers** repo |

**Delivery is a hook, not a directive in this file.** A line here reaches only the agents that
actually load this skill, which is how the lessons log stayed dormant in the first place — so
`scripts/hook_lessons.sh` fires on **`SubagentStart`**, matches `agent_type` against `LESSONS_AGENTS`
(default `avenger-`, unanchored so plugin-scoped names match), and injects a short **pointer**: how
many entries `docs/lessons/lessons.json` holds and the read procedure below. It never inlines the log
or a prose file — it runs on every spawn. Unlike `ponytail` it reaches **every** avenger agent,
including the Verifier, Breaker and bug-hunter: a "write less code" persona conflicts with their job,
prior lessons do not. It **fails closed** — bad payload, unmatched agent, bad regex, and a missing,
unparseable or empty index all inject nothing, so a project that never wrote a lesson sees no change.
`LESSONS_OFF=1` disables it. opencode has no subagent-start event, so its agents do not get this
injection; they pick the procedure up from `skills/self-improvement` only.

The procedure the pointer refers to: read the *index only*, filter to your role and task, and open
just the handful of prose files that matter. Write a lesson the moment something is learning-worthy
— a user correction, a self-caught mistake, or a confirmed-good approach — appending or refining,
never overriding. If the file is missing, skip silently.

Test: "would this help someone building a *different* project with this pipeline?" → it is a
pipeline observation. "Would it help someone building *this* project again?" → it is a lesson.

## Agent tooling

Every canonical agent declares an explicit `tools:` allowlist (`Read, Write, Glob, Grep, Bash`, plus
`Edit` for the agents that rewrite files) and **no MCP** in any of them. So MCP
servers available in the main thread (browser automation, issue trackers) are **unreachable inside a
pipeline subagent**; only CLIs invoked through `Bash` are. Where a stage needs a browser — feature
e2e against a UI, Breaker poking a real page — use `npx -y chrome-devtools-axi`, not
`mcp__claude-in-chrome__*`, and finish with `npx -y chrome-devtools-axi stop` — it leaves a
background browser server alive after the invoking process exits. Prefer plain `curl` when no
browser is actually needed.

## Implementer test modes (see the `tdd` skill)

The implementer loads `skills/tdd/SKILL.md` on every spec and picks the mode from `work_kind`:

- **Greenfield** → vertical red→green: one seam → one failing test → just enough code → repeat.
- **Migration** → parity-first: the existing suite is the contract; run it against the migrated code,
  and add characterization tests only at genuine gaps on pre-agreed critical seams.
- **Refactor** → baseline-first parity: use the migration procedure without porting tests; behavior
  remains unchanged unless a separate greenfield requirement explicitly says otherwise.

## Driving the chain (`/avenger-run`)

The chain can be walked by hand, one stage at a time, or driven by `commands/avenger-run.md`, which
makes the **main session** the orchestrator (a subagent has no Task tool, so it cannot spawn stages —
only the main thread can). Position comes from `scripts/pipeline_state.py`, which reads the artifacts
on disk (`fidelity_verdict`, `review_status`, `status`, `verdict.json`) and returns the single stage
the feature owes next — so a run resumes after a `/clear`, a compaction, or a new session. It stops
for `plan.md` approval and each spec-review unless `--auto`, retries a stage twice before halting,
runs the Breaker only on `criticality: critical`, obeys `MUTATION_POLICY`, and commits per verified
phase, then twice more at feature close — the e2e stage's output *before* the ship gate (whose
precondition is a clean tree already carrying `tests/e2e/<feature>/`) and the retrospective artifacts
*after* it. **The orchestrator itself never pushes**; the feature-close ship gate above does, in both
modes. Full detail in `docs/AUTOMATE.md` §2.

## Implementer minimalism (`skills/ponytail`)

The implementers — and **only** the implementers — carry a minimalism ladder they climb before writing
production code: does this need to exist at all (YAGNI) → already in this codebase → stdlib → native
platform feature → already-installed dependency → one line → minimum that works.

- **Delivery is a hook, not self-activation.** `scripts/hook_ponytail.sh` fires on **`SubagentStart`**
  and matches `agent_type` against `PONYTAIL_AGENTS`
  (default `avenger-backend-architect|avenger-frontend-developer`, unanchored so plugin-scoped names
  match). SessionStart context never reaches subagents, so a SessionStart hook would inject the
  ruleset everywhere *except* where code is written. Passive `SKILL.md` availability is weaker still —
  upstream measured **zero** self-activations in ten sessions.
- **It fails closed.** An unreadable payload, an unknown `agent_type`, a bad regex or a missing skill
  file injects nothing. The Verifier, the Breaker and the bug-hunter must never receive it: their job
  is to demand *more* tests and more counterexamples. `PONYTAIL_OFF=1` disables it everywhere.
- **Production code only.** The ladder never removes a test, a negative case or a seam, and never
  applies rung 1 to an `R<n>.<k>.<m>` in an approved spec — a requirement that looks unnecessary is a
  route-back, not a deletion. On conflict, `skills/tdd`, this file and the spec win.
- **No gate.** `/ponytail-review` scans a diff on demand and is advisory: no artifact, no verdict,
  nothing blocked. `/ponytail` loads the skill into the main thread for inline implementation, which
  the `SubagentStart` hook cannot reach. Neither is on by default in the main thread, which also
  writes specs and runs verifier triage.
- **Runtime coverage.** Claude Code gets hook injection; opencode has no subagent-start event, so its
  implementers get the ladder from the agent prompt line only.

## Feature-level e2e (`skills/e2e-author`)

- Written by the implementer **once per feature, after the final phase is green** — never per phase.
- **1-3 tests, 5 is a hard ceiling.** They prove the assembled system delivers the feature's goal;
  they are not where edge cases live.
- Live in `tests/e2e/<feature>/`, trace to the goal quoted from `overview.md` rather than a spec id
  (the single exception to "no spec id → no test"), and are recorded in `e2e-mapping.md`.
- **Excluded from the mutation gate and from the phase verifier's scope.** They run at feature close
  and in CI.
