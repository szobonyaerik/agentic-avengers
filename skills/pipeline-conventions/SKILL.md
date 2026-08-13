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

`task-analyst → solution-architect → implementation-planner → spec-writer → [spec gate] →
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
| `overview.md` | planner + Spec Writer whole; **the spec gate and the human spec-review read the `## Contracts and Decisions` header only** | whole / header |
| `plan.md` | the Spec Writer, per spec; phase-handover, per phase (the next phase's entry only) | whole |
| `spec.md` | its own gates and its own implementer; the verifier bundle, changed specs only | whole |
| `test-mapping.md` | the Verifier, per phase | **the table** |
| `test-evidence.md` | **on route-back only** | whole |
| `verdict.json` | phase-handover, feature close | whole; `report` ≤ 1500 chars |
| `verdict-attempt-<n>.json` | **nobody** — archive | — |
| `handover.md` | the Spec Writer (prior cards), the spec gate and the human spec-review (the immediately prior card), e2e-author | **the card, ≤ 6144 bytes** |
| `handover-archive.md` | **nobody** — archive | — |
| `pipeline-observations.md` | the retrospective triage, once at feature close; the preflight sweep, frontmatter only | whole |
| `e2e-mapping.md` | feature close, once | whole |

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
  **That guard is one-directional**: it catches a removed read coming back, and nothing catches the
  inverse — a stage instructed to read something the table never declares. So the table is not
  self-verifying; a reader that is instructed but undeclared leaves it *incomplete* rather than
  wrong, which is harder to spot. The human spec-review reached this list that way. When a stage
  genuinely needs a read, add the reader to the table; never bend the instruction to match a silent
  one.

## Tiered requirement binding — what decides suite size

Every requirement declares a **`binding:`**, written by the Spec Writer and settled at the spec gate.
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
- **Cost is only visible at the spec gate.** Its observe pass, the cross-family review and
  verification all read for **correctness**, and an expensive test is not incorrect.
  `scripts/subprocess_check.py` is the mechanical half of that gate: an AST walk over `tests/` for
  spawners without `@pytest.mark.subprocess("<why>")`. It runs on every spec write in **both** modes
  via `scripts/hook_spec_gate.sh`, over `$SUBPROC_CHECK_PATHS` when a project's tests are not at
  `tests/` — an absent root scans nothing, which is CLEAN but always reported on stderr, never a
  silent pass. **It is diff-scoped** on the applicability boundary below: a spawner in a file this
  change touched blocks, one in a file it did not is counted and named. Repository-wide, it refused
  *every* spec write of one measured phase over 17 spawners in locked phases nobody had opened.
  `--all` audits the whole tree and is deliberately not wired into CI, where it would reinstate the
  same hostage one layer out. Deliberately **not** a wall-clock budget — seven runs of one
  unchanged suite spanned 66.43s to 137.76s on one machine, so a runtime gate would fail green suites
  at random and teach everyone to bypass it.

## Gates

- **The spec gate (per spec, automated, cross-family, TWO passes, ONE verdict)** — *this repo only.*
  Fires on spec write via `scripts/hook_spec_gate.sh`, stamps `spec_gate: approved | blocked`;
  `blocked` routes back to the Spec Writer.

  It replaced **two** gates — the Fidelity Gate and the automated half of spec-review — which asked
  overlapping questions of the same document at the same moment. In one measured phase a spec passed
  one and failed the other on **byte-identical text**. Both told their reviewer to judge *"without
  charity … assume gaps until the spec proves otherwise"*, and both ended *"when unsure between
  REVIEW and NO-GO, choose NO-GO"*, across seven dimensions all asking "is everything covered?" with
  **no size ceiling, requirement cap or cost dimension anywhere**. The only response available to a
  rejected spec is more text: spec 8.0 went 25k -> 51k characters and 8.2 went 40k -> 57k across four
  rejected rounds, until the gate flagged them for excess surface.

  So the gate is **report-everything, then triage**, and the verdict is not a model's to give:

  1. **Observe** (`prompts/spec-gate-observe.md`) — reports every observation it has, with **no
     verdict pressure at all**. It answers with `observations`, not `verdict`; it cannot block, and it
     is told in as many words that it is not a gate. A reviewer told to be conservative follows that
     literally, so the conservatism is removed from the pass that reads. It is handed a
     `## CONTEXT (reference only)` block (`scripts/spec_gate_context.py`) carrying **exactly** the
     extents the read path grants it — the overview's `## Contracts and Decisions` section and the
     *immediately prior* phase's contract card **bounded by `HANDOVER_MAX_BYTES`**, never the whole
     overview, never every prior phase, never `handover-archive.md`. The byte bound is not
     decoration: that cap is enforced diff-scoped, so an oversized pre-rule handover is counted and
     not blocked, and reading it whole would hand this new reader the whole cost the contract card
     removed. A truncated card is reported, never silent.
     Half of `contradiction` is a contract declared in those documents,
     so without them a closed set of four is three items and a claim. It is reference only: absent
     context is normal (phase 1 has no prior card), named on stderr, and never fails the gate.
  2. **Triage** (`prompts/spec-gate-triage.md`, a cheaper model) — classifies each observation
     against the closed set. It emits `classifications`, not a verdict. Its tie-break is the reverse
     of the old one: **when unsure, it is a note.**
  3. **Decide** (`scripts/spec_gate_triage.py`) — turns classifications into the verdict,
     deterministically. **No model decides whether a spec is blocked.**

  **The blocking set is CLOSED, and it is exactly four things:** a **missing requirement**, an
  internal **contradiction**, an **untestable criterion**, an **unhandled critical edge case**.
  Everything else is a **note**; **notes never block** and land in the spec's known-open list
  (`spec-notes.md`, read once by the implementer). The closed set is what stops the filter drifting
  back into the ratchet it replaces, and it is closed *mechanically*: a category the table does not
  know is a **hard failure naming what was invented**, never a judgement call — guessing "blocking"
  reinstates the ratchet and guessing "note" silently deletes a finding. Adding a fifth category is a
  deliberate edit to `spec_gate_triage.BLOCKING`, reviewed as such.

  **Size is decided before the gate runs, and the remedy is a split.** `scripts/requirement_cap.py`
  counts declared requirement ids and caps them at **12** (`SPEC_REQUIREMENT_MAX`). Over the cap the
  spec **splits** into siblings `<n>.<k>` under the same phase, each independently gated. It runs
  **before** any paid call, so an over-cap spec costs nothing — and the gate rubrics are told never
  to reject a spec for being large, because **a rejection for size is one more thing to grow
  around**.

  **The cap binds a spec that can still be split** (the applicability boundary below). A spec stamped
  `status: done` has shipped: its ids are already pointed at by test-mapping rows, verdict findings
  and prior handovers, so the split is unavailable and the count is *counted and named* instead of
  blocking. Two shipped specs of one measured feature declared 30 and 29 requirements against the cap
  of 12, and no verdict of any kind was reachable for them. It is not an escape hatch for a draft:
  `status: done` is stamped by the implementer, and it is what fires the phase suite and the
  traceability pre-check, both of which a draft claiming to be done fails loudly.

  **The writer is primed from this same rubric before it writes, from ONE source.** Phase 9 of one
  feature ran **fourteen** rounds on its first spec and one, three and one on the next three, while
  total spec writes barely moved against phase 8 (16 -> 19): collapsing two gates into one did not
  reduce the work, it relocated it. The writer learned what the collapsed gate blocks by being
  rejected fourteen times, and **nothing carried that learning into the next phase**, so every phase
  paid the fourteen again. `scripts/spec_rubric.py` renders the brief and
  `scripts/hook_spec_rubric.sh` delivers it at `SubagentStart` - a hook, for the reason every other
  delivery here is one: "read the rubric first" is an instruction with no mechanism.
  **Nothing in that brief is authored.** Every line is either data read out of the module that
  decides with it (`spec_gate_triage.BLOCKING`, the requirement cap) or a section of a gate prompt
  lifted **verbatim**, and `agents/avenger-spec-writer.md` no longer restates any of it. That is the
  whole design constraint: a second copy drifts, and a drifted copy is **worse than no priming**,
  because the writer is then held to a standard nobody is applying. For the same reason it **fails
  closed** - a missing prompt or a renamed section renders nothing and says so, rather than half a
  rubric. `SPEC_RUBRIC_OFF=1` disables it; opencode has no `SubagentStart` event, so its writer gets
  the pointer from the agent prompt line and renders the brief itself.

- **Human spec review (per spec)** — a human runs `grill-me` against the spec using
  `spec-review-checklist`, then sets `review_status: approved`. `review_status` now means only that:
  a **human** sign-off, written by no model except in unattended mode, where `SPEC_REVIEW_MODE=auto`
  has the machine gate carry it because nobody is there. One machine gate plus one human is not two
  rubrics; two rubrics was the defect.
  A spec reaches the implementer only when `spec_gate: approved` **and** `review_status: approved`.
  - **A spec already approved and implemented is re-gated on its changes only.** Unchanged text was
    passed by this same gate before and is not a finding. One spec drew REVIEW, REVIEW, then a NO-GO
    naming requirements the gate itself had approved twice, unchanged — a verdict is a **sample from
    a distribution**, not a fact about the artifact, and variance in a gate that fails closed is far
    more expensive than variance in one that fails open. `scripts/spec_gate_cache.py` keeps the body
    the gate last **approved** — a rejection records its hash, its verdict and its report, but does
    not replace that body: it is the reference the next re-gate diffs against under
    `## PREVIOUSLY APPROVED`, and overwriting it would show the author changes-since-rejection while
    telling them they were changes-since-approval. `scripts/hook_spec_gate.sh` hands the reviewer a
    `## CHANGES SINCE APPROVAL` diff; with no kept body the whole spec is gated, the safe direction.
    A **full** re-gate is still owed when the diff changes the requirement set, Scope, Interfaces /
    contracts, `work_kind`, or any `binding:` — and a **first** gate is always full.
  - **The hook makes TWO provider calls, and its budget says so.** `scripts/gate_timeouts.py` counts
    invocation sites — expanding a helper function by its call sites, so factoring both passes
    through one `run_pass` does not read as one call — and requires
    `hook timeout >= calls x call timeout + headroom`. A budget sized for one call is the 120s-hook
    -around-a-300s-call defect exactly, and a killed hook reports nothing at all.
- **Verifier (per phase, cross-family) — narrowed to three jobs.** After every spec in the phase is
  green, the Verifier runs the full suite, traces coverage, and puts the phase-mapped/changed tests
  and their directly referenced helpers through a **targeted test-quality review on a cross-family
  model** (the independence check). It expands only on explicit criticality or evidence, then passes
  or routes back. Fail closed — a green suite with no completed review is an *unreviewed* phase, not
  a pass. It persists `verdict.json`. **On pass, the phase's tests lock.**
  - **What it keeps, and why only these.** A scout measured all **46** of its findings across 8
    phases: only **3** were user-visible defects no other stage could have found — but **two of those
    were plaintext-credential leaks**, and that is what buys the stage. So it keeps exactly (a)
    **coverage judged per `binding:`** against the requirement set, (b) **reading a green suite for
    gamed, tautological and implementation-coupled tests**, and (c) **adversarial execution against a
    real collaborator** on any requirement whose subject is a secret, a resource lifetime or a
    concurrency invariant.
  - **Its bookkeeping moved to a script.** **12 of 46 findings (26%)** were about the pipeline's own
    gate stamps, traceability rows and spec headings — 45% on the worst phase, where **attempts 2 and
    5 produced nothing but bookkeeping**, roughly 70 minutes and ~410k tokens for four
    stamp-freshness observations. Every one was mechanically decidable. `scripts/verifier_precheck.py`
    now decides them for no tokens: every requirement id appears in some `test-mapping.md` row for
    its phase (`binding: none` exempt by construction), the gate stamp is fresh for every spec, and
    every spec still has its `## Acceptance criteria` heading. **A defect that recurred twice, six
    attempts apart, in one phase, because nothing checked it continuously** — so it runs on **every
    commit**, and **diff-scoped**, the same "you are responsible for what you change" rule as the
    verifier bundle, the spec re-gate cache and the mutation gate: the phases the commit touches from
    `gate_ci.sh`, the **whole phase** at handover from `scripts/hook_verifier.sh`, and everything
    under `gate_ci.sh --full`. A full audit on every commit would hard-fail a consumer repo's CI over
    locked phases nobody touched; when git cannot say what changed, nothing is enforced and the check
    says so out loud rather than falling back to enforcing everything.
  - **The loop is capped at 3 attempts, and route-backs are bundled.** **16 of 20 re-attempts were
    the Verifier routing back to itself.** One phase's new-finding series was 6, 2, 8, 4, 2, 1, 0, 6 —
    a gate disclosing a subset of what it could already see, one expensive round at a time.
    `scripts/verifier_attempts.py` stops the loop at the cap and prints the series, so a trickle is
    visible in the number rather than inferred. At the cap the remaining findings are **carried as
    known-open in `handover.md`, waived explicitly, or escalated** — all three honest; a fourth
    attempt is not one of them. The trade is named: some findings are carried rather than fixed.
    Enforced in `hook_verifier.sh` AND `gate_ci.sh --full`, with `GATE_BYPASS` honoured. The cap is on
    the **loop**, so a `pass` whose findings are all `fixed` or waived clears it — waiving the
    remainder is one of the three remedies above, and a check its own prescribed remedy cannot satisfy
    is a wedge, not a gate. "Still open" is not restated: it is `open_findings`, imported from
    `verifier_bundle_scope`. A verdict of **`fail`** at or past the cap is what stops.
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
- **Mutation gate — `advisory` by DEFAULT.** `MUTATION_POLICY` = `advisory` (default: runs, reports
  the score and its survivors, **never blocks**) · `enforce` (fails closed) · `off` (runs no mutation
  tool anywhere). It was off by default; it is on because it is deterministic, diff-scoped, needs no
  model below `MUTATION_MIN_SCORE`, and **every non-discriminating test this project has ever caught
  was caught by mutation** — including two in one phase that neither spec gate nor a green 281-test
  suite surfaced. Advisory never blocks, so the cost of the default being wrong is a line of output.
  It is still **not** the independence mechanism — the Verifier's test-quality review is.
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
    verbatim, exactly as `--auto` halts on a blocked spec: `no-mistakes` marks a finding
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
    while accepting shared *family* blind spots. The per-phase gates — the spec gate,
    verifier — are unaffected and stay cross-family.

## The applicability boundary — what a mechanical rule may bind

**A mechanical rule binds what is still OPEN. What is CLOSED it may count and name, never block.**
`scripts/applicability.py` is the one module that decides which, and every check on this boundary
speaks the same sentence when it counted instead of blocking.

Every check here was added after the tree it runs on, and without this each one asks *"does the whole
repository satisfy a rule we added later?"* rather than *"does what this change is responsible for
satisfy it?"*. Measured in one run of one feature, that difference produced three blocks in a single
phase — the stage resolver parked forever on a phase closed under a disclosed captain-ordered cap so
`--auto` could not start a phase at all; the requirement cap fired on two locked implemented specs
whose only prescribed remedy is a SPLIT a shipped spec cannot take, so **no verdict of any kind was
reachable for them, ever**; and the cost gate refused every spec write over 17 undeclared spawners in
locked phase-1 and phase-7 tests the phase had never opened. Three defects, one shape.

Closed has exactly **three evidences**, and a call site never invents a fourth:

1. **untouched** — the current change does not touch it (`changed_paths`, git). This is the rule
   `verifier_bundle_scope.py`, `spec_gate_cache.py`, `doc_read_path.py`, `verifier_precheck.py` and
   the mutation gate already ran before it had a name. **When git cannot say what changed the scope
   is unknowable, so nothing is enforced and the check says so out loud** — falling back to enforcing
   everything is the hostage failure the scoping removes.
2. **shipped** — the artifact's own stamps say the pipeline is past it. A spec stamped `status: done`
   has been implemented, so splitting it would renumber ids that `test-mapping.md` rows,
   `verdict.json` findings and prior handovers already point at. **A rule whose remedy is unavailable
   is not a gate, it is a wedge.**
3. **excepted** — a disclosed exception on the phase's ledger (`exceptions.json`, beside
   `verdict.json`), naming the rule, the subject, who recorded it and why.

```bash
# prose belongs in a file the command reads — the reason is author-written prose
python3 scripts/applicability.py record <phase-dir> --rule spec-review --subject 8.1-clickup-client \
  --reason-file <file> --recorded-by captain
python3 scripts/applicability.py list <phase-dir>
python3 scripts/applicability.py check <phase-dir> --rule verdict --subject 8-clickup-client
```

- **The rule set is CLOSED** — `spec-gate`, `spec-review`, `verdict`, `requirement-cap` — and every
  one of them is read by a named call site. A rule outside it is a hard error naming what was invented, never a silent
  no-op: a ledger entry nothing reads is an exception that does not exist, and it would surface as a
  phase wedged on a rule everybody believed was waived. A fifth is a deliberate edit to
  `applicability.RULES` together with the call site that reads it — `subprocess-cost` was dropped
  from the set for exactly that reason: the cost gate uses the *untouched* evidence, not this one.
- **An exception is narrow.** One rule, one subject, one phase. It is not `GATE_BYPASS`: a
  break-glass waives one gate call in one session, an exception is durable state about work that has
  shipped.
- **Audited or not recorded.** Every one is written to `gate-overrides.log` through
  `scripts/bypass_log.sh` as it is recorded, and an exception that could not be logged is not
  recorded at all — the writer **exits 2 when the append fails** (an unwritable root, a read-only
  mount, a full disk), because an exit code that cannot distinguish "logged" from "not logged" makes
  every caller's fail-closed check decorative. **2 and not merely non-zero**: every break-glass
  caller hands off with `exec bypass_log.sh …`, so the writer's code becomes the hook's, and 1 is
  not blocking to the harness — an unlogged override that lets the write through is the same silent
  pass with an extra step. **A resolver that applies one says so on stderr.**
- **Carried on the phase's contract card.** Recording an exception is manual and nothing creates the
  entry, so a forgotten one is invisible until a later phase wedges on it. `skills/phase-handover`
  lists the phase's open exceptions on the card, which is what puts an omission in front of the next
  phase.
- **An unreadable ledger grants nothing** and says so — the same under-report bias the resolver runs
  on everywhere else.

**This is what stops a captain-ordered close from wedging the run.** A phase closed with a recorded
exception is CLOSED, not incomplete, so `scripts/pipeline_state.py` walks past it. The two remedies
that do not need it — stamping a human sign-off nobody gave, or claiming a machine verdict nobody
obtained — are the "looks fine" class this pipeline exists to remove. `pipeline_state.py --from-phase
<n>` is the blunt companion: it enters at a named phase, records nothing, judges nothing, and names
every phase it stepped over. Because it judges nothing, it also **answers nothing feature-wide**: with
any phase stepped over it reports stage `unknown` rather than `done`, `e2e-author` or a missing
planned phase, all of which are claims about phases it did not open. Prefer the ledger, which fixes
the cause.

## Amendments — changing a verified phase without re-verifying all of it

The pipeline had no concept of a correction. Once a phase was verified, **any** change to it — a
one-line fix, a renamed helper, a scrub that turned out to be defeated by JSON escaping — re-opened
the whole phase and cost a full verification round. One measured phase ran **eight** verification
attempts; rounds 3 through 8 were that shape.

An **amendment** is a record on disk (`amendments.json`, beside `verdict.json`) that names the
requirement ids a post-verification change touched. **Only those re-verify**, carrying their own
evidence. A verdict then reads *verified at attempt N, plus amendments A1..An* — the attempt count
stops being the only thing that can express "this phase moved".

```bash
# prose belongs in a file the command reads — the reason is author-written prose
python3 scripts/amendments.py open <phase-dir> --requirements R8.2.30,R8.2.31 \
  --reason-file <file> [--security]
python3 scripts/amendments.py scope <phase-dir>   # the ids owed re-verification, and only those
python3 scripts/amendments.py close <phase-dir> A1 --evidence <path>
```

Two rules, both enforced rather than asked for (`scripts/hook_verifier.sh` and `gate_ci.sh --full`
run `amendments.py due`):

- **Batched at phase close.** Ordinary amendments accumulate and are re-verified together, once.
  That is the whole saving: six route-backs become one bundled pass.
- **Security is never batched.** An amendment opened with `--security` is owed re-verification
  **immediately**. A phase-8 credential leak must not wait for a batch, and the cost argument that
  justifies batching does not apply to a secret already in a log. A pending amendment on a phase
  whose verdict *already passes* is owed now for the same reason: that verdict is a claim about code
  that has since changed.

An amendment with no requirement ids is refused — the naming **is** the scope of the re-verification
it owes, so an unnamed one narrows verification to nothing while claiming to have narrowed it. A
corrupt ledger is an error, never an empty one: reading it as "no amendments" would silently drop a
pending security re-verification.

## Carried items - a handover's forward-looking claims are discharged, not merely written

Phase 8 of one measured feature recorded, verbatim, that caller-supplied identifiers would become a
problem in phases 9 to 12. Phase 9 was the first such caller and **shipped exactly that defect** - a
user-controlled path segment interpolated unencoded, so a name containing `?` or `#` retargets the
write - and the review gate caught it only after verification had already passed. The prediction was
correct, specific, actionable, and **became nothing**: no spec line, no test, no check. The same
phase produced the mirror defect, a card asserting a protection its own phase had deleted, which
then propagated into the next phase's instructions as binding. One direction over-claimed, the other
under-delivered, and **both passed every check**, because a forward-looking claim was prose and prose
is owed to nobody.

**The contract card already had the slot** - `## Open items`, a table with stable ids, kept as a
table for a measured reason (of 8 items carried as prose across 53.6 KB, exactly one was ever picked
up later; the id carried them, not the story). So nothing new sits beside it. That section is
**widened to hold forward-looking claims (`FWD-<n>`) alongside findings carried at the attempt cap
(`OBS-<n>`), and made binding** by `scripts/carried_items.py`, run from `scripts/hook_verifier.sh`
and from `gate_ci.sh` - the same both-paths rule as the attempt cap, since a rule only an in-session
hook applies stops existing the moment the phase is driven another way.

```bash
python3 scripts/carried_items.py list <phase-dir>          # what this phase owes an answer to
python3 scripts/carried_items.py discharge <phase-dir> FWD-1 --as built --by "R9.1.4 in <spec>"
python3 scripts/carried_items.py discharge <phase-dir> OBS-1 --as declined --reason-file <file>
```

- **A phase states what it carries.** Its own card's section holds a row per item or an explicit
  `none` row. **Silence is not `none`** - silence is the state phase 8's prediction was written in.
- **The next phase answers every row, and does not close until it has**: `built` into a spec
  requirement, `tested`, or `declined` with a stated reason. **`declined` is a real answer**; an item
  that belongs further out is declined and **re-carried on that phase's own card**, which is how a
  claim about phases 9-12 survives without being owed to all four at once.
- **The LAST card has no successor, so its forward claims must name an ISSUE REFERENCE** (`#<number>`
  or an issue URL) - a card whose `next:` is `e2e` or `ship` does not close otherwise. It is the one
  place the obligation above binds nobody, so it is exactly where this hole would reopen; the check
  (`carried_items.py filed`) asks only whether the row names an issue, never whether the claim was
  worth carrying.
- The **spec writer** is the stage that discharges, because it is the first that can turn a claim
  into a requirement. Discharging as `built` means a requirement states the behaviour, with its own
  id and `binding:` - not a sentence in Scope mentioning it.
- Ids are **scoped by the card that declared them**, so `OBS-1` on two cards is two items and the ids
  already in use keep working. Which card is in force is `spec_gate_context.prior_phase`'s decision,
  imported rather than re-derived: this ledger and the spec gate's CONTEXT block must not disagree
  about which phase came before.
- The ledger (`carried.json`, beside `verdict.json` in the phase that **acted**) refuses an id the
  prior card never declared - a typo would otherwise record an answer to a question nobody asked
  while the real item stayed owed - and a corrupt ledger is an error, never an empty one.
- **A pre-rule card with no section owes nothing**, so a repository upgrades instead of being held
  hostage by history it never touched. The CI sweep (`carried_items.py check`) is **diff-scoped even
  under `--full`**, deliberately unlike `verifier_precheck`: this obligation lands on a document
  class every consumer repo already has on disk, so a full audit would fail its CI over cards written
  before the rule existed. Nothing is lost by that - the hook holds the phase being *closed*, which
  is a phase the diff touches by construction - and `check --all` is there for anyone who wants the
  audit. The check runs inside the *passing* branch of the handover gate: a phase at the attempt cap
  is already not closing, and reporting an undeclared section there would answer an unasked question
  while hiding the one that stopped it.

**What this does not catch**, stated rather than implied: nothing can tell that a claim never written
down was worth writing, and nothing here detects the mirror defect of a card over-claiming. The
counterweight to that one is the rule the card already carries - a binding contract names the file
that *enforces* it, and a discharge names the artifact that answers it. Both are checkable by the
next reader; a sentence is not.

## Skills are delivered, not requested — and the load is observed

The pipeline delegates its core behaviour to thirteen skills, and it used to delegate by **asking**:
every implementer prompt said "Load `skills/tdd` before you start". That is an instruction, not a
mechanism. Nothing checked and nothing recorded, so a stage that skipped a required skill fell back
silently to whatever the model already believed — which is the failure mode this pipeline removes
from everything else. `docs/lessons/` shipped with a complete written procedure and **zero
invocations** for exactly this reason: a directive in a skill reaches only the agents that load that
skill.

**WHICH skills a stage requires is DERIVED from its own `agents/<stage>.md`.** Every agent declares
them in one `Required skills` line and `scripts/skill_contract.py` reads them out of it. There is no
table anywhere: a hand-maintained list was a second statement of a fact the definitions already
carry, and a second statement of a fact is precisely the promise-versus-enforcement gap this section
removes. **Adding `skills/<name>` to that line is enough to make it required** — and only that line
counts: a `skills/<name>` named in an agent's *prose* is not a requirement, because prose says things
a contract does not. `agents/avenger-verifier.md` names `skills/mutation-interpret` in order to say it
applies only when the mutation gate is on, and both implementers name `skills/ponytail` in order to
say the hook injects it — read as requirements, that last one turned `PONYTAIL_OFF=1` into a
permanent phase wedge. An agent with no such line has an **empty** contract, which
`required_skills.py verify` reports rather than guessing at. A skill's *directory*
is what makes a reference real, never a readable `SKILL.md` inside it — keying on the file would make
a skill whose body went missing quietly stop being required, which is the silent fallback the loud
blocker below exists to replace.

**The load is OBSERVED, never self-reported.** `scripts/hook_skill_load.sh` seeds each stage's
contract at `SubagentStart` as required-and-not-yet-observed and flips an entry on a real
`Read`/`Skill` of `skills/<name>/SKILL.md`. Entries are keyed `<stage>:<skill>` in the per-phase
metrics record's `skill_loads[]`, so the requirement and its answer are one row, and a seed never
overwrites an observed load whichever order the hooks run in. There is **no second evidence file**,
and nothing anywhere asks an agent to report its own compliance: a path that needed the agent to run
a command to prove a load would be the instruction-with-no-mechanism this section removes, one layer
up.

**Delivery is pointer plus evidenced load, decided by size** (`SKILL_INJECT_MAX_BYTES`, default
**8192**), by `scripts/hook_skills.sh` on `SubagentStart`. Injecting every body guarantees the load
and costs the same order as the reads the read-path work had just removed — every stage requires
`pipeline-conventions`, the largest file in `skills/`, on every `avenger-*` spawn. Observation is a
cheaper way to **detect** a failure to load, and a required skill with no observed load blocks the
phase anyway, so **detection beats prevention when both end the same way**:

- **At or under the ceiling → injected whole**, and the injection is **recorded as an observed load**
  with the evidence naming injection. An injected skill is never read, so without that record it
  would seed required-and-unobserved and never flip — and the audit would report a false gap on
  precisely the skills whose load is *guaranteed*.
- **Over it → a POINTER**: path, size and one-line description, and nothing to run afterwards.
  Opening the file is what records the load. **A pointer is not a suggestion** —
  `required_skills.py audit` runs at handover (`hook_verifier.sh`) and in CI (`gate_ci.sh --full`),
  and a required skill with no observed load **fails the phase**, naming the stage and the skill.
  Keyed `<stage>:<skill>`, so a load by the Verifier says nothing about whether the implementer
  loaded it.

**The same question is asked at the STAGE BOUNDARY, where the remedy still exists.** Asked only at
close, a genuine requirement arrives as a blocker on the artifact that did not cause it: one measured
phase learned at its contract card that `avenger-spec-writer` had never loaded
`skills/spec-review-checklist`, with every spec already written, gated, reviewed and implemented —
and "open the file in that stage" is not available to a stage that ended days ago. So
`scripts/hook_skill_audit.sh` runs `audit --stage <stage>` at **`SubagentStop`**, returning the
finishing stage to work over its own unanswered contract. **Same judgement, earlier question**: the
same `audit_gaps`, the same wording, the same exit code, narrower scope. It **never blocks twice**
for one stop (`stop_hook_active` says it out loud and lets the stage go), and — alone among the
hooks here — it **fails open**, because it is the early copy of a check that still blocks at close;
an unreadable payload, a foreign agent, no phase or no writer lets the stage finish. The close-time
audit is **not** replaced: opencode has no `SubagentStop` and the main thread reaches none either.
`SKILL_AUDIT_OFF=1` disables the early audit alone.

**The audit needs no session id to be scoped.** The evidence is per-phase by construction —
`hook_skill_load.sh` records nothing when no phase is in flight — so a pointer delivered in phase 1
cannot block phase 8, with no run-scoping machinery at all. The handover audit reads the phase in
flight; `--all` sweeps every phase, which is what CI runs. `SKILLS_OFF=1` makes the audit a no-op
because nothing was delivered to load, and a run with no metrics writer observed nothing and says so
rather than passing invisibly.

The saving is a **prediction, not a result**: declared as **H9** before it landed — roughly 1M tokens
saved per 8-phase feature with zero unrecorded required loads — and settled in phase 9.

**A required skill that is missing or unreadable is a loud BLOCKER in the injected context**, recorded
`loaded: false`: a required skill that is absent is not a lighter version of the rules, it is no
rules. `SKILLS_OFF=1` disables it, and everything else fails closed and delivers nothing — an
unreadable payload, an agent this pipeline does not own, an unparseable ceiling.

`skills/ponytail` is delivered by `hook_ponytail.sh` alone, which records its own load. Delivering it
here too would cost twice and would put the minimalism persona back after `PONYTAIL_OFF=1` — an off
switch that switches nothing off. Reach is scoped per skill for the same reason `hook_ponytail.sh`
excludes the Verifier and `hook_lessons.sh` does not: a skill that fights a stage's job must not
reach it.

It is also **evidenced but never required**: it is on no declared `Required skills` line, because
requiring it would make the required set depend on `PONYTAIL_OFF` and a contract an env var can
change is not a contract. Absence is still visible — a stage the hook would have reached with no
injection recorded surfaces as a **NOTE** from `required_skills.py`, which never enters the exit code
in any mode and which nothing branches on. `PONYTAIL_OFF=1` produces no note at all.

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
    without raising `hooks.json` is a loud stop. **Measurement spends that same headroom**, so the
    same module asserts `metrics processes on the hook's path x AVENGER_METRICS_TIMEOUT <= headroom`
    — the count derived from what the scripts spawn and import, because the sink's breaker is
    per-process state and a blocked writer therefore costs the full per-call bound in each one.
    Ordering (the gate answers, then records) keeps a hung writer from ever truncating the answer;
    this bound is what keeps it from getting the hook killed.
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
  **spec gate**, which is a hook and does call a model in-session (twice); it never runs in CI.

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

## The pipeline measures itself as it runs

Both logs above are prose. Neither answers **"did the pipeline get better?"** — that used to be
answered by archaeology across commits, retros and chat, which is expensive enough that it was
mostly not answered at all. It is answered now by a **per-phase metrics record that firstmate owns**:
its schema, its units, its absence semantics and every command that writes it live in firstmate's
`docs/pipeline-metrics.md` and `bin/fm-pipeline-metrics.sh`. **This repo owns no part of that
schema.** `scripts/metrics_sink.py` shells out to that CLI so the producer contract — write during
the run, keep every key present, make repetition converge, add no key — is enforced by their code
rather than restated in ours. There is deliberately no second store and no second file format; a
field the schema lacks is a change to firstmate's schema, never a key added to a record here.

**Two properties outrank recording anything, and both are tested.**

**Emitted as the run happens, never reconstructed at the end.** Each fact is written by the stage
that observes it, at the moment it observes it, so a phase that dies mid-run still leaves its numbers
behind. One phase died and was recovered three times; every recovery would have lost the lot under a
write-at-the-end design.

**Writing metrics can never fail a phase.** Every failure — no writer configured, an unwritable
record, a refusal, a hang, a crash — is swallowed, written to `.avenger-metrics.log`, and reported as
"not recorded". Every metrics CLI call in a hook exits 0 on an emission path. **Measurement, not a
gate**: a metrics bug that blocked delivery would be a self-inflicted outage in the thing meant to
make delivery cheaper. An unwritable record makes firstmate's CLI *block* rather than fail, so one
timeout abandons the writer for the rest of that process — the fail-open property has to hold in wall
clock, not only in exit codes.

**Emission is attached to the fact, never to the caller.** `record_gate_call` lives inside
`gate_runner.py`, the one place every gate call passes through, so a new gate is instrumented by
existing; it reads its own stage off the rubric it was handed. `record_spec_round` is idempotent by
**content** — it reuses the rebuildable gate cache to remember which body it last counted — so any
caller may report any spec write and the record converges instead of double-counting a round. A
seeded skill requirement never overwrites an observed load, whichever order the two hooks run in.

| Fact | Observed by | Why there |
|---|---|---|
| every gate call: model, family, **measured** latency, verdict, and on failure the `cause` | `gate_runner.py` | the single point every gate call passes through; §Gates' failure taxonomy is what it records |
| a gate the harness **killed** mid-call | the hook's own signal trap | the runner it killed cannot report its own death — this is what tells a kill apart from a NO-GO |
| spec rounds, each round's **size in bytes**, requirement count | `hook_spec_gate.sh`, on a body the gate cache says changed | one spec grew 25k → 51k while being rewritten to satisfy a gate and nothing noticed |
| the spec gate's own arithmetic: observations in, **blocking** out, **notes** out | `spec_gate_triage.py`, where the verdict is derived from them | a filter that blocks everything and one that blocks nothing are otherwise indistinguishable without reading a transcript |
| the **count** of verification attempts, and nothing about what each one changed | `verifier_review.sh`, **derived from `verdict.json` and its archives** — never counted per invocation | that script runs several times inside one attempt (a timed-out call, a diagnostic retry); one phase recorded **8** against a real attempt of 1 and a cap of 3 that had never fired, and read against that cap the number says the cap failed. Retries stay visible in `gate_calls[]` with their `failure_cause` — only the attribution was wrong. Repeated calls converge |
| tests before and after | `hook_spec_gate.sh` (first spec write) and `hook_verifier.sh` (handover) | counted **the same static way at both ends**, so the delta is a real delta and not two counting methods |
| **which stage found each defect** | `verifier_review.sh`, `hook_mutation.sh`, and the `defect` command for stages a script cannot see | the single most valuable field, and the only one **unrecoverable after the run** |
| which skills each stage actually loaded | `hook_skill_load.sh`, `hook_ponytail.sh` | an instruction to load a skill is not a load |

**One asked-for fact is deliberately not built.** "Verification attempts, **and what each one
changed**" was asked for; the table above records the attempt **count** only, and that row says so
rather than reading as a claim the code does not honour. firstmate's schema has no field for the
per-attempt delta and that schema is closed by design — a record that accepts arbitrary keys is not
an authoritative answer to "did this get better", it is whatever its last writer left there, so no
key is added here and no sidecar store is invented to hold it. No declared hypothesis needs it: H4
measures the bare `verification_attempts` count and predicts "3 or fewer". `defects[]` already
carries most of the analytical value through `found_by`, `real`, `stage_reached` and `severity`; the
one thing genuinely missing is the attempt index. Deferred as **`fm-metrics-attempt-detail`** — a
schema change is firstmate's decision, not this pipeline's.

**`found_by` is the field the record exists for.** Tracing one pipeline's defects to it showed the
running suite caught 3 of 15 genuine defects while mutation, probes, review and direct execution
caught the rest. The stages a script cannot observe record their own catches:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pipeline_metrics.py" defect \
  --phase-ref docs/features/<f>/phases/<n>-<slug> --id D3 \
  --summary "$(cat .lavish/<f>-defect.md)" \
  --found-by breaker --stage-reached implementation --severity security
```

The `"$(cat …)"` form is not optional and not a style: a defect summary is author-written free text,
and **prose belongs in a file the command reads** (§Hard rules) — under `--auto` the deny regex is
matched against the whole Bash command string, so a summary that merely *names* `git push` denies the
command carrying it. `--found-by` takes firstmate's fixed vocabulary (`spec-gate`, `review-gate`,
`verifier`, `breaker`, `mutation`, `running-suite`, `probe`, `execution`, `measurement`,
`human-review`, `ci`, `other`); `--not-real` marks a defect in a test, fixture or artifact, which
costs real time but must not inflate the product-defect count.

**Off unless a firstmate home is configured.** `fm-pipeline-metrics.sh` must be on `PATH` or named by
`AVENGER_METRICS_CMD`; without it a run records nothing and **says so once**, because a measurement
layer quietly doing nothing is the failure the record exists to remove. `AVENGER_METRICS_OFF=1`
disables it silently. opencode's adapter drives the same `hook_*.sh`, so gate calls, spec rounds and
phase boundaries are recorded there too; it has no subagent-start or read event, so skill loads are
not.

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
on disk (`spec_gate`, `review_status`, `status`, `verdict.json`, `amendments.json`, `exceptions.json`)
and returns the single stage the feature owes next — so a run resumes after a `/clear`, a compaction, or a new session. It stops
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
