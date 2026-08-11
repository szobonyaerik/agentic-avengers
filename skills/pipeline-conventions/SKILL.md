---
name: pipeline-conventions
description: The shared rules of the agentic-avengers pipeline — the agent chain, phase/spec layout and ID scheme, gates, and the locked-after-verify rule. Load this whenever you are acting as any pipeline agent or are unsure how phases, specs, tests, or gates fit together.
---

# Pipeline conventions

The single source of truth for how plan → build → verify fits together in this repo. Every agent
references this. (A plugin's CLAUDE.md is not loaded as context, so this skill is the canonical home
for these rules; `/pipeline-init` can also copy them into a target repo's own CLAUDE.md.)

> This pipeline is the sibling of `klm-agentic-pipeline` and deliberately shares its semantics. The
> known intended differences — and the two whose status against the sibling is unconfirmed — are
> listed in one place, `README.md` § *Relationship to `klm-agentic-pipeline`*. That list is not a
> completeness claim, so a divergence absent from it is not thereby drift; the rules below are.

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
        test-mapping.md           # per SPEC, not per phase — the TABLE, and nothing else
        test-evidence.md          # mutation evidence, route-back history, build order, deviations
      verdict.json                # the Verifier's persisted verdict for the phase
      verdict-attempt-<n>.json    # a superseded attempt, archived out of verdict.json
      handover.md                 # the phase's CONTRACT CARD, written after the Verifier passes
      handover-archive.md         # everything the card does not carry
  tests/<feature>/<n>-<slug>/<n>.<k>-<subslug>/...
  tests/e2e/<feature>/...         # feature-level only
  ```
- **Requirement IDs:** `R<n>.<k>.<m>` (phase.spec.requirement). Globally unique and traceable. Every
  test authored or ported for a spec lists the ids it covers in that spec's `test-mapping.md`, and
  every requirement not marked `binding: none` appears in at least one row; an inherited migration
  suite need not be exhaustively remapped.
- Artifact templates live in `docs/templates/`.

## The document read path — what decides documentation cost

**Documentation cost is not size. It is `size x how often the document is read x how long it stays
resident in context`.** Every intuition that targets bytes on disk targets the wrong number, and a
measured run says so plainly: `task-analysis.md` is 31 KB and trivial, and cost **~465k tokens**,
because two stages opened it on every one of 30 specs **to read one frontmatter field**.
`handover.md` held 272 KB and cost **485k-1,475k tokens**, because every spec write and every spec
review re-read *every prior phase's* handover — a quadratic, and phase 8 alone paid ~527k tokens
before it wrote a line. Meanwhile `spec.md` was **990 KB, the largest artifact on disk**, and cost
comparatively little, because each one is read mostly once, by its own implementer. Nothing was
deleted to fix this; the read directives changed.

**`scripts/doc_read_path.py` is the table**, and it is the only place the path is declared —
`python3 scripts/doc_read_path.py table` prints it. The rules it encodes:

| document | read by | extent |
|---|---|---|
| `task-analysis.md` | the Solution Architect, **once, at feature start** | whole |
| `overview.md` | planner + Spec Writer whole; **spec-review reads the `## Contracts and Decisions` header only** | whole / header |
| `plan.md` | the Spec Writer, per spec | whole |
| `spec.md` | its own gates and its own implementer; the verifier bundle, changed specs only | whole |
| `test-mapping.md` | the Verifier, per phase | **the table** |
| `test-evidence.md` | **on route-back only** | whole |
| `verdict.json` | phase-handover, feature close | whole; `report` ≤ 1500 chars |
| `verdict-attempt-<n>.json` | **nobody** — archive | — |
| `handover.md` | the Spec Writer (prior cards), spec-review (the immediately prior card), e2e-author | **the card, ≤ 6144 bytes** |
| `handover-archive.md` | **nobody** — archive | — |

Four rules follow from it, and each one is enforced rather than requested:

- **Every pipeline document declares `readers:` in its own frontmatter** — who reads it and when.
  **A document no stage reads does not get written**, and an archive says so explicitly
  (`readers: none (archive of handover.md)`). This is the rule that stops the recurrence: a new
  artifact class that nobody can name a reader for is caught while it is being invented, not by the
  next cost measurement. `doc_read_path.py check` fails a document that declares none, and the
  template each writer works from carries the line, so a document authored as instructed passes.
  **That check is diff-scoped**: it enforces the artifacts the current diff touches and *counts* the
  rest on stderr without blocking, which is the same "you are responsible for what you change" rule
  the verifier bundle, the spec re-gate cache and the mutation gate already run on — and what lets a
  repository already full of pre-rule artifacts upgrade without rewriting its history first.
  `check --all` audits everything (CI's `--full`); when git cannot say what changed, nothing is
  enforced and the check says so rather than falling back to enforcing everything.
- **Never send a reader to another document for a single field.** Every scalar a downstream stage
  needs rides in the frontmatter of the document that stage is already reading — `work_kind`,
  `criticality`, `binding` counts. This is what took `task-analysis.md` off the per-spec path.
- **A locked phase leaves the read path.** Once the Verifier passes a phase, its `spec.md` files are
  settled and a later phase reads that phase's **contract card**, not its specs. The only stage that
  re-opens a verified spec is the verifier bundle, and only for specs the diff touched.
- **Change the directive where the reading is decided — the table — never one caller at a time.**
  `doc_read_path.py check --sources` scans `agents/`, `skills/`, `commands/` and `prompts/` and
  fails when a stage instruction names a document that left the read path. A guard bolted onto one
  command is a guard the next command does not have.

## Tiered requirement binding — what decides suite size

Every requirement declares a **`binding:`**, written by the Spec Writer and settled at spec-review.
It, and not the requirement count, is what determines how many tests exist.

| `binding:` | Meaning | Verified by |
|---|---|---|
| `e2e` | An end user can observe it | a **journey** shared with the other `e2e` requirements on its path — **never its own test** |
| `integration` | Observable *only* under concurrency, fault injection, or schema migration, **and the spec says in one sentence why an e2e cannot see it** | its own test at the seam |
| `none` | A structural or build-time property | CI, a type checker, or nothing. **No test.** |

`binding` decides **whether and where** a requirement is verified; the `level` column in
`test-mapping.md` (`integration` · `e2e` · `narrow`) describes **how** a test that exists drives it.
A journey's row is `level: e2e` and lists several ids; an `integration` requirement's row is
`level: integration` (or `narrow` with its written justification) and lists one.

**This replaced a rule requiring paired pass/fail criteria on every requirement.** That rule made
suite size a mechanical function of id count with no counterweight anywhere in the pipeline: one
measured feature turned 288 requirement ids into 458 tests, 4.87 lines of test per line of source,
and cost never entered any stage's judgement. The tiers put the question back. The trade is named and
accepted: **a red journey tells you which journey broke, not which line.**

Three consequences worth stating outright:
- **The journeys here are phase-level**, living with the phase's other tests. The **feature-level**
  e2e stage below is unchanged — still 1-3 tests, 5 maximum, written once at feature close.
- **The Verifier judges coverage per `binding:`, never per id.** A `binding: e2e` requirement is
  covered by the journey that lists it and a `binding: none` one is never a gap — reading the old
  one-test-per-id rule here would route back a coverage gap on every requirement the spec
  deliberately left unbound, and hand the suite straight back its lost multiplier. The rule is in
  `skills/verifier-triage`, and `prompts/verifier-review.md` carries it for the cross-family reader.
- **Cost is only visible at spec-review.** Fidelity, cross-family review and verification all read
  for **correctness**, and an expensive test is not incorrect. `scripts/subprocess_check.py` is the
  mechanical half of that gate: an AST walk over `tests/` for spawners without
  `@pytest.mark.subprocess("<why>")`. It runs on every spec write in **both** modes via
  `scripts/hook_spec_review.sh`, over `$SUBPROC_CHECK_PATHS` when a project's tests are not at
  `tests/` — an absent root scans nothing, which is CLEAN but always reported on stderr, never a
  silent pass. Deliberately **not** a wall-clock budget — seven runs of one
  unchanged suite spanned 66.43s to 137.76s on one machine, so a runtime gate would fail green suites
  at random and teach everyone to bypass it.

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
  - **A spec already approved and implemented is re-gated on its changes only.** Unchanged text was
    passed by this same gate before and is not a finding. One spec drew REVIEW, REVIEW, then a NO-GO
    naming requirements the gate itself had approved twice, unchanged — a verdict is a **sample from
    a distribution**, not a fact about the artifact, and variance in a gate that fails closed is far
    more expensive than variance in one that fails open. `scripts/spec_gate_cache.py` keeps the body
    each gate last **approved** — a rejection records its hash, its verdict and its report, but does
    not replace that body: it is the reference the next re-gate diffs against under
    `## PREVIOUSLY APPROVED`, and overwriting it would show the author changes-since-rejection while
    telling them they were changes-since-approval. `scripts/hook_spec_review.sh` hands the reviewer a
    `## CHANGES SINCE APPROVAL` diff; with no kept body the whole spec is gated, the safe direction.
    A **full** re-gate is still owed when the diff changes the requirement set, Scope, Interfaces /
    contracts, `work_kind`, or any `binding:` — and a **first** gate is always full.
- **Verifier (per phase, cross-family)** — after every spec in the phase is green, the Verifier runs
  the full suite, traces coverage, and puts the phase-mapped/changed tests and their directly
  referenced helpers through a **targeted test-quality review on a cross-family model** (the
  independence check). It expands only on explicit criticality or evidence, then passes or routes
  back. Fail closed — a green suite with no completed review is an *unreviewed* phase, not a pass. It
  persists `verdict.json`. **On pass, the phase's tests lock.**
  - **The bundle is scoped to the specs that changed.** It used to re-send every `spec.md` and every
    `test-mapping.md` on every attempt — one measured ~832k tokens, and one phase had to be split
    into four chunks to fit a context at all. The diff-only rule above covers spec *re-gates* and
    never reached this bundle. `scripts/verifier_bundle_scope.py` sends only the specs whose text
    changed since the last completed review, names the rest in the bundle as carried forward, and
    merges their findings back into this run's verdict — **an open carried finding still forces
    NO-GO**, so the scope shrinks the prompt, never the bar. **A spec that still holds an OPEN
    finding is never carried**, however unchanged its text is: a `gamed test` finding is fixed in a
    TEST file, so `spec.md` and `test-mapping.md` never change, and a spec that is never re-bundled
    is a finding that is never regenerated — one that used to hold the phase at NO-GO forever with
    no way out but deleting the state file. It goes back to the cross-family reader instead, and
    clears or reappears on its own evidence; the token saving is given up only on the specs actually
    under repair, which is exactly where economising is wrong. No state, a lost state file, nothing
    changed, or `VERIFIER_SCOPE=full` sends the whole phase; the safe direction costs tokens, not
    coverage.
- **Mutation gate (optional)** — off by default and most teams leave it off. Only when a project sets
  `MUTATION_POLICY` to `enforce`/`advisory` does the Verifier run it; otherwise no mutation tool runs
  anywhere. It is **not** the independence mechanism — the Verifier's test-quality review is.
- **Breaker** — critical/security paths only, optional.
- **Feature-level e2e** — once, after the final phase is green (see below).
- **Phase review gate (per phase, `no-mistakes`, review-only)** — after each handover,
  `no-mistakes axi run --skip=push,pr,ci`. The Verifier reads **tests**; this reads the rest of the
  phase's diff — docs, config, scripts, cross-file coherence — which otherwise goes unreviewed until
  feature close. Nothing is pushed and no PR is opened; that stays a feature-close action.
  Review findings **park** (`auto_fix.review: 0`) and route to the implementer rather than being
  self-fixed, because the phase's tests are **locked** once the Verifier passed and the pipeline must
  not rewrite them. *Unverified:* whether review is scoped to the incremental diff or re-reads the
  whole branch — measure it, and fall back to feature-close-only if settled findings return.
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
  implementer to add a case. **A locked phase also leaves the read path**: its specs are settled, so
  a later phase reads its **contract card** instead — see *The document read path* above.
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
    starts, where the hook never sees it at all. **A multi-line reason file is safe**: `$(cat …)`
    strips only trailing newlines, so interior ones reach the gate — and `scripts/bypass_reason.sh`
    collapses newlines and tabs at the writer, so `gate-overrides.log` keeps exactly one parseable
    record per bypass with every word of the reason intact. Write the reason as prose; the log owns
    its own format.
  - **Not covered:** a command merely *printed* for the user to run — nothing executes it. The test
    throughout is whether an agent chose the words **and** a shell will see them.
- **Gates fail closed** — and a gate that fails **says which failure it was**. Every stop carries a
  `cause=` from `scripts/gate_errors.py` (`timeout` · `provider-payment-required` ·
  `provider-unreachable` · `provider-not-found` · `provider-error` · `no-verdict` · `cross-family` ·
  `unknown-vendor` · `runner-untrusted` · `config` · `io` · `internal` — the last being the backstop
  for a failure nothing above recognised, which is a defect in the gate itself and not something for
  the operator to fix in their `.env`; `CAUSES` in that module is the list) and reproduces the provider's own words
  verbatim. A timeout kill, an HTTP 402 out-of-credit reply and an unreachable provider used to be
  one indistinguishable line, and the 402 was found only by probing the provider by hand — a day
  spent reading an infrastructure failure as a model failure. The classifier is a heuristic over
  vendor error strings; `provider-error` is its honest answer, and the verbatim text is the appeal.
- **Four properties keep a stop honest, and each one failed toward "looks fine" before it existed.**
  - **The hook must outlive the call it wraps.** `hooks.json` gave the gate hooks 120s while the
    provider call inside them got 300s, so the harness killed the hook 180s before the gate could
    answer — and a killed hook leaves no verdict, no report and no cause, which the run read as an
    objection. It split cleanly by duration (106s passed, 143s "failed") and was read for a day as a
    size ceiling in the gate model. `scripts/gate_timeouts.py` asserts
    `hook timeout >= GATE_CALL_TIMEOUT + headroom`, derives *which* hooks are gate hooks from what
    each one actually calls, and both the hooks and the suite check it. Raising `GATE_CALL_TIMEOUT`
    without raising `hooks.json` is a loud stop.
  - **A timeout must stop the work, not just stop waiting.** `scripts/proc_group.py` runs the
    provider child in its own process group and signals the GROUP; killing only the direct child left
    its workers running and billing, with runs REPORTING 300s observed against 569s, 3818s and 4276s.
    The reported duration is measured wall clock, never the configured constant. Being killed
    ourselves tears the group down too.
  - **A rejection carries its reasoning and leaves a record.** Only GO/REVIEW used to stamp, so a
    NO-GO left no trace of which text was refused and dropped the gate's own `report`. The hash is
    now recorded **with its verdict** on every verdict (`<gate>_gated_verdict`), so an unchanged
    rejected body replays its rejection instead of skipping past it as "unchanged". A replayed
    rejection blocks the turn exactly like a fresh one, so it **honours `GATE_BYPASS` exactly like a
    fresh one** — ignoring it there made an override a one-shot, and a bypass silently dropped breaks
    "never silent" as much as one silently taken. The kept **body**, though, is only ever the last
    approved one (above): a rejection records its hash, verdict and report and leaves that reference
    alone.
  - **The runner is not trusted by path.** `scripts/gate_runner_guard.sh` makes it identify itself
    and match its own digest before any gate uses it; `GATE_RUNNER_SHA256` pins it exactly. A
    scaffold that printed a bare `GO` having checked nothing once sat on a temp path and was believed.
- **One vendor table.** `scripts/model_vendors.py` is the only place a model id becomes a family, and
  an unrecognised vendor is a **loud refusal**, not a guess. The old table knew seven vendors and
  returned the raw model id for the rest, so `glm-5.1` and `glm-5.2` — one vendor — read as two
  different families. False independence is indistinguishable from real independence. Declare an
  unlisted vendor with `GATE_MODEL_FAMILY`, or add it to the table.
- **Break-glass bypass** is allowed but recorded — whole-gate via `GATE_BYPASS`, per-finding via
  `verdict.json` `break_glass` + a mandatory `waiver_reason` — in `handover.md` and
  `gate-overrides.log`, and visible on the PR. The reason is author-written prose, so under `--auto`
  it comes from a file (`GATE_BYPASS="$(cat <file>)" …`) like every other free-text argument above.
  `gate-overrides.log` is **one tab-separated record per line**, with the reason last because it is
  the only free-text field. A reason is prose that may carry newlines — and a record its own reason
  text could split is not an audit trail — so **every writer normalises the reason through
  `scripts/bypass_reason.sh`** before it appends. Collapse, never truncate: the reason is written to
  be read later, and `skills/phase-handover` mirrors it into `handover.md`.
  - **Nothing appends to that log by hand.** A hook bypass, a CI bypass (`scripts/gate_ci.sh`, same
    grammar with a `gates:<list>` scope) and the Verifier's per-finding waiver
    (`bypass_log.sh verifier <finding-id> <waived_by>`) all normalise the same way. `bypass_log.sh`
    is the writer for the hook and waiver paths; `gate_ci.sh` still formats its own record with the
    same grammar, which is a known duplication — see the `pipeline-improvement` issue. The waiver is
    the case that proves why: `waiver_reason` is a JSON string whose content this pipeline explicitly
    does **not** judge, so nothing stops it being two lines. `handover.md` and the retrospective sweep
    *read* the log; only `bypass_log.sh` writes it.
  - This is deliberately **structural, not a rule to remember**. A sentence claiming every writer
    behaves cannot enforce that they do — a single writer path can, and each new caller inherits the
    format instead of re-implementing it.
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

**Every lesson carries a `cost`.** A lesson is an instruction to a future session, and an instruction
with no price attached gets followed without limit. One pipeline wrote ten lessons about a single
feature and **not one was about cost** — ten ways to be more correct, zero ways to be cheaper, which
is how a suite reaches 4.87 lines of test per line of source without anyone deciding to. So the index
entry and the prose file both state what following the rule spends (tests added, agent invocations,
runtime, tokens) and, **where the rule could otherwise justify unbounded growth, the limit that stops
it**. "It already works is exactly the state that precedes a silent regression" is true, and without
a budget it licenses writing tests forever — there are infinitely many correct behaviours. Bound it:
the spec's `binding:` set is the budget. Full procedure in `skills/self-improvement`.

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
