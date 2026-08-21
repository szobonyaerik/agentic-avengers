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
[human spec review] → backend/frontend implementer → verifier → (breaker, on a critical phase) → handover`,
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
      verification-evidence.json  # the Verifier's transcript: every command it ran, recorded
      evidence/                   # one redacted, capped log per recorded command (committed)
      breaker.json                # the Breaker's record — only on a phase declaring criticality: critical
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
| `spec.md` | its own gates and its own implementer; the Verifier, per phase | whole |
| `test-mapping.md` | the Verifier, per phase | **the table** |
| `test-evidence.md` | **on route-back only** | whole |
| `verdict.json` | phase-handover, feature close | whole; `report` ≤ 1500 chars |
| `verdict-attempt-<n>.json` | **nobody** — archive | — |
| `verification-evidence.json` | `verifier_evidence.py` at phase close (`hook_verifier.sh`, `gate_ci.sh`); `avenger-verifier`, writing `verdict.json`'s `execution` block | whole |
| `breaker.json` | `breaker_gate.py` at phase close (`hook_verifier.sh`, `gate_ci.sh`); `pipeline_state.py` resolving the next stage | whole |
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
  `verifier_evidence.py`, the spec re-gate cache and the mutation gate already run on — and what lets a
  repository already full of pre-rule artifacts upgrade without rewriting its history first.
  `check --all` audits everything (CI's `--full`); when git cannot say what changed, nothing is
  enforced and the check says so rather than falling back to enforcing everything.
- **Never send a reader to another document for a single field.** Every scalar a downstream stage
  needs rides in the frontmatter of the document that stage is already reading — `work_kind`,
  `criticality`, `binding` counts. This is what took `task-analysis.md` off the per-spec path.
- **A locked phase leaves the read path.** Once the Verifier passes a phase, its `spec.md` files are
  settled and a later phase reads that phase's **contract card**, not its specs. Nothing re-opens a
  verified spec: the bundle that used to is gone with the cross-family reading pass.
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
  `skills/verifier-triage`.
- **Cost is only visible at the spec gate.** Its observe pass and verification both read for
  **correctness**, and an expensive test is not incorrect.
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

     **Absent is not DEGRADED, and three shapes are DEGRADED.** An `overview.md` that exists but
     carries no `## Contracts and Decisions` heading *at all* is not "no contracts yet" — it is this
     reader never finding them, for every spec that feature will ever gate. clickup-agents ran
     eleven phases in exactly that state (its overview uses `## Interfaces & contracts` instead)
     while the gate reported success, which is issue #57: a silently degraded gate is worse than a
     failing one. Two more shapes are the same defect wearing a different face, found while proving
     the fix and confirmed rather than deferred: a heading whose section holds **only boilerplate**
     — an HTML comment, which is exactly what `docs/templates/overview.template.md` ships under this
     heading, so a freshly-templated overview that nobody has filled in yet LOOKS included and
     contradicts nothing; and `overview.md` **missing or unreadable outright**, which loses the same
     half of `contradiction` and has no silent exemption. Not even a recorded exception (§3a) reaches
     it: that ledger records against a PHASE directory, and a feature with no overview has no phase
     directory either, so there is nowhere for one to live at that point in the pipeline — this
     reader does not invent an exemption to stay green. The one absence that stays ordinary: the
     heading present with nothing under it, not even a comment — a
     feature early in planning, ordinary because it cannot be mistaken for "filled in."
     `build()` marks each degraded shape `degraded` and `main()` exits **3** — never 1 or 2, which
     mean something else here: 1 is a `check` finding, 2 is a usage error or an unexpected failure.
     **Nothing escapes `main()` as a traceback.** An uncaught exception used to exit 1, and 1 is the
     code the hook reads as "could not build, treat as absent" - a crash arriving as a routine
     absence is the same silent clean pass this whole item removes, so every unexpected failure gets
     a code no other outcome uses and its cause on stderr.
     `hook_spec_gate.sh` does not discard that exit code with `|| :`.
     It echoes it loudly **and folds a `CONTEXT DEGRADED` banner into the persisted report**, the one
     stamped into `spec_gate_cache` on APPROVED and BLOCKED alike, since no later read of a verdict
     sees a hook's stderr. On exit 3 the banner **carries the builder's own cause line verbatim**
     rather than re-authoring one: three shapes exit 3, each with its own remedy, and a durable
     record naming the wrong one prescribes a fix already applied. **Any other non-zero exit is
     recorded no more quietly**: a builder that could not run at all leaves `contradiction` checked
     against less than the closed set claims, exactly as a degraded overview does, so it folds a
     banner too - a hook-authored one marked `UNAVAILABLE`, because the remedies do not overlap. A
     degraded overview is fixed in the overview; a builder failure is a defect in the script or its
     inputs, and a banner naming the wrong one of those sends the reader to the wrong file.
     Still never a gate failure on its own; no longer
     indistinguishable from a clean pass.

     **The heading contract is checked, not merely stated.** `spec_gate_context.py check [--all]`
     walks every `docs/features/*/` feature directory for the same three degraded shapes,
     independent of any spec being gated, and runs from `gate_ci.sh` beside the read-path check. It
     walks feature DIRECTORIES rather than globbing existing `overview.md` files, because a missing
     overview can never itself be a changed path — scoping on the artifact alone would make it
     permanently unenforceable. **Diff-scoped** on the same applicability boundary as every other
     check here: a feature this change did not touch is counted and named, never blocked, so a
     repository full of pre-rule overviews can adopt it — and diff-scoped **even under `--full`**, on
     `carried_items.py`'s precedent rather than the read path's, because `overview.md` is a document
     class every consumer repo already has on disk and a full CI audit would fail its build over
     every pre-rule one at once. `check --all` stays the audit somebody runs deliberately. The remedy is the heading
     `docs/templates/overview.template.md` already mandates, filled in with real content — and
     adding it to one project's overview is **not** the fix, because that hides the symptom on that
     project and leaves the silent pass available to every other one.
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
  traceability pre-check, both of which a draft claiming to be done fails loudly — and, since issue
  #68, a stamp that fails either check does not stay `done` on disk (see below).

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
- **Verifier (per phase) — narrowed to TWO jobs.** After every spec in the phase is green, the
  Verifier runs the full suite, traces coverage per `binding:`, and drives adversarial execution
  against a real collaborator, then passes or routes back. Both jobs are recorded through
  `scripts/verifier_evidence.py`, because a stage that emits nothing is indistinguishable from one
  that never ran. Fail closed — a green suite with no **execution evidence** is an *unproven* phase,
  not a pass. It persists `verdict.json`. **On pass, the phase's tests lock.**
  - **What it keeps, and why only these.** A scout measured all **46** of its findings across 8
    phases: only **3** were user-visible defects no other stage could have found — but **two of those
    were plaintext-credential leaks**, and that is what buys the stage. So it keeps exactly (a)
    **coverage judged per `binding:`** against the requirement set, and (b) **adversarial execution
    against a real collaborator** on any requirement whose subject is a secret, a resource lifetime
    or a concurrency invariant. The third job — reading a green suite for gamed tests — is **gone**
    with the cross-family pass that carried it, and nothing inherits it (see *The Verifier* below for
    what that leaves uncovered, stated rather than implied).
  - **Its bookkeeping moved to a script.** **12 of 46 findings (26%)** were about the pipeline's own
    gate stamps, traceability rows and spec headings — 45% on the worst phase, where **attempts 2 and
    5 produced nothing but bookkeeping**, roughly 70 minutes and ~410k tokens for four
    stamp-freshness observations. Every one was mechanically decidable. `scripts/verifier_precheck.py`
    now decides them for no tokens: every requirement id appears in some `test-mapping.md` row for
    its phase (`binding: none` exempt by construction), the gate stamp is fresh for every spec, and
    every spec still has its `## Acceptance criteria` heading. **A defect that recurred twice, six
    attempts apart, in one phase, because nothing checked it continuously** — so it runs on **every
    commit**, and **diff-scoped**, the same "you are responsible for what you change" rule as the
    verifier evidence, the spec re-gate cache and the mutation gate: the phases the commit touches from
    `gate_ci.sh`, the **whole phase** at handover from `scripts/hook_verifier.sh`, and everything
    under `gate_ci.sh --full`. A full audit on every commit would hard-fail a consumer repo's CI over
    locked phases nobody touched; when git cannot say what changed, nothing is enforced and the check
    says so out loud rather than falling back to enforcing everything.
  - **A stale gate stamp has two remedies, and both of them clear the check.** Write the spec again
    to re-gate it, or record a disclosed `spec-gate` exception for that spec
    (`applicability.py record <phase-dir> --rule spec-gate --subject <n>.<k>-<subslug>
    --reason-file <f>`), which `verifier_precheck.py` reads as the **excepted** evidence of the
    applicability boundary below and reports as unenforced instead of blocking. The exception is the remedy that survives **the gate provider
    being down** — recording one makes no gate call at all, so it is reachable in exactly the
    circumstance re-gating is not. An **amendment is not a remedy here** and is deliberately not
    named as one: `amendments.py` re-verifies requirement ids at the Verifier and never touches the
    spec-gate hash this check compares, so a phase that opened, closed and evidenced one still
    reported UNGATED. A remedy that cannot clear its own check is worse than none — it sends a
    worker in a circle before giving up.
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
    `verdict_findings`. A verdict of **`fail`** at or past the cap is what stops.
  - **A verdict must evidence its own execution.** The stage used to record
    `test_quality.reviewed: true` — a boolean it wrote about itself, which both `hook_verifier.sh`
    and `gate_ci.sh` accepted as the phase's independence, so a stage that skipped its work was
    indistinguishable from one that did it. Every command the Verifier relies on now runs through
    `scripts/verifier_evidence.py record`, which stores the argv, the exit code, the **measured**
    wall clock, the sha256 of the command's output and a digest of the specs and tests it ran
    against; `verdict.json` carries `execution.chain`, the head of the hash chain over those runs.
    A pass with no transcript, a transcript with no passing `suite` run, a log that does not hash to
    its recorded digest, runs recorded against content that has since changed, or a verdict whose
    chain does not match the record on disk — each is refused, by the hook and by CI, with the
    remedy named at the refusal. Diff-scoped on the applicability boundary even under `--full`, and
    waivable through the disclosed-exception ledger (`--rule execution-evidence`), because a phase
    that closed before this rule existed can never acquire a transcript for it.

- **Mutation gate — `advisory` by DEFAULT.** `MUTATION_POLICY` = `advisory` (default: runs, reports
  the score and its survivors, **never blocks**) · `enforce` (fails closed) · `off` (runs no mutation
  tool anywhere). It was off by default; it is on because it is deterministic, diff-scoped, needs no
  model below `MUTATION_MIN_SCORE`, and **every non-discriminating test this project has ever caught
  was caught by mutation** — including two in one phase that neither spec gate nor a green 281-test
  suite surfaced. Advisory never blocks, so the cost of the default being wrong is a line of output.
  With the cross-family reading pass removed it is now the pipeline's **only** systematic signal
  about non-discriminating tests — still advisory, still not a wall, and named as partial cover
  rather than a replacement (see *The Verifier* below).
- **Breaker** — critical/security paths only, run when the resolver reports `stage: breaker` (any
  spec in the phase declares `criticality: critical`). Not optional in practice: it was owed on
  every phase-8 and phase-9 spec of one feature and ran on neither, with zero trace anywhere in that
  feature's docs or tests, because nothing checked for it (issue #45). It now persists `breaker.json`
  beside `verdict.json`, and a critical phase does not close without a valid one
  (`scripts/breaker_gate.py`). **A stage that emits nothing is indistinguishable from a stage that
  never ran**, which is why the record — not the run — is what is checked.
  - **Valid means non-vacuous.** A `clean` verdict must name what it **attacked**; a `found` verdict
    must name its **counterexample**. Either one empty is refused exactly like a missing record,
    because *"a clean report with no attempts described is not acceptable"* was already the agent's
    own written instruction and this is what makes it checkable. The record also declares `readers`,
    as a top-level key since JSON has no frontmatter — the same declaration the read path requires of
    every document it governs, asked here so the record that closes a phase is the record
    `doc_read_path.py` accepts on the next commit.
  - **Three call sites, deliberately unequal.** `scripts/hook_verifier.sh` is **authoritative** — it
    fires on the `handover.md` write, unconditionally, and is the only point that catches the
    omission before a human or an unattended run believes the phase is done. `scripts/gate_ci.sh` is
    a **backstop**, diff-scoped for the same reason the carried-items sweep is: the obligation lands
    on a phase directory tree every consumer repo already has on disk, and the hook already holds the
    phase being closed (`breaker_gate.py check --all` is the audit). `scripts/pipeline_state.py` is
    **routing** — it reports `stage: breaker` so the orchestrator acts on the obligation instead of a
    human remembering to; it is not the enforcement, which is what makes a caller ignoring it
    harmless.
  - **Asked only while the phase is still OPEN** (see *The applicability boundary*): before
    `handover.md` exists. A phase that already handed over carries no record and is counted, never
    re-opened — asked any earlier, the resolver parked on shipped phases and `--auto` could not
    reach the phase in flight. Waivable only through the same disclosed-exception ledger as every
    other rule here, `--rule breaker`.
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
  coverage judged per `binding:`, no recorded execution evidence, and no `verdict.json`.
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
    while accepting shared *family* blind spots. The pipeline's one remaining per-phase MODEL gate —
    the spec gate — is unaffected and stays cross-family.

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
   `verifier_evidence.py`, `spec_gate_cache.py`, `doc_read_path.py`, `verifier_precheck.py` and
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

- **The rule set is CLOSED** — `spec-gate`, `spec-review`, `verdict`, `requirement-cap`, `breaker`,
  `execution-evidence`, and every one of them is read by a named call site. A rule outside it is a
  hard error naming what was invented, never a silent no-op: a ledger entry nothing reads is an
  exception that does not exist, and it would surface as a phase wedged on a rule everybody believed
  was waived. A further one is a
  deliberate edit to `applicability.RULES` together with the call site that reads it —
  `subprocess-cost` was dropped from the set for exactly that reason: the cost gate uses the
  *untouched* evidence, not this one.
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
- **A prior card's section has THREE states, not two.** An explicit `none` means nothing is carried
  and the phase proceeds. **No section at all owes nothing** - a repository upgrades instead of being
  held hostage by history it never touched. A section that is **present and declares neither an item
  nor an explicit `none`** is neither of those and **fails closed** (exit 2, undecidable, not exit 1):
  what the phase inherits cannot be determined, which is the state phase 9's bullet rows under an
  `### Open items` heading were read as "nothing carried" in, and a card left holding the template's
  own unfilled placeholder rows is the same state. The remedy is on the **prior** card - rewrite its
  section as the documented table, a row per item or an explicit `none` row. The parser is
  deliberately not widened to accept a second row shape: a closed set with a loud, specific refusal,
  never silent inference of a new shape.
- The CI sweep catches that undecidable prior card **per phase**, so one card does not abort the scan
  of the rest; a corrupt `carried.json` is a different failure and still propagates as exit 2.
- The CI sweep (`carried_items.py check`) is **diff-scoped even under `--full`**, deliberately
  unlike `verifier_precheck`: this obligation lands on a document class every consumer repo has, so a full audit would fail its CI over cards written
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
- **The Verifier's value is EXECUTION, not reading.** Across 8 measured phases it produced 46
  findings; 3 were user-visible defects no other stage could have found, and **two of those were
  plaintext-credential leaks found by planting an adversarial value and running it against a real
  Postgres**. So it keeps two jobs: coverage judged per `binding:`, and adversarial execution on
  secrets, resource lifetimes and concurrency invariants. Both are recorded through
  `verifier_evidence.py`, because a stage that emits nothing is indistinguishable from one that
  never ran.
- **The cross-family reading pass is GONE, and nothing inherits it.** A different vendor's model used
  to read the bounded phase review set for tautological / implementation-coupled / missing-edge
  patterns. It returned GO with zero findings on a phase containing real defects, and the hypothesis
  testing whether it earned its cost came back unmeasured. **What that leaves uncovered is stated
  rather than implied:** gamed tests have no dedicated reader. The remaining cover is partial and
  named — the mutation gate (advisory, deterministic, the one signal that has actually caught them
  here), `skills/tdd` naming the anti-patterns to the implementer *while it writes*, and the human
  spec-review setting criteria a gamed test would have to contradict. `gamed-test` stays in the
  verdict schema for a finding raised in passing; nothing reads the suite for them as a stage.
- **Fresh model ≠ author, where a model gate still exists.** The model that *forms a gate's
  judgement* must not share the implementer's family. Every subagent here is Anthropic, so no agent
  is itself cross-family — the surviving model gate is the **spec gate**, whose `gate_runner.py`
  refuses a same-family model and whose family CI asserts statically against `$GATE_MODEL`.
  Opus-vs-Sonnet is **not** decorrelation. The **only** sanctioned exception is the feature-close
  `no-mistakes` ship gate above, which is same-family on purpose and documented as such.
- **A gate verdict must be physically possible.** `scripts/gate_plausibility.py` refuses any reached
  verdict whose measured latency is below `GATE_MIN_LATENCY_MS` (default 250 ms): one measured phase
  recorded a **4 ms GO** from a model gate — less time than the provider CLI takes to start — and it
  was consumed as a pass. Applied to pass and fail alike, and never to a refusal that reached no
  provider, which records its near-zero latency on purpose. `GATE_MIN_LATENCY_MS=0` disables it and
  says so on every call.
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
    same module asserts `metrics processes x AVENGER_METRICS_TIMEOUT + suite collections x
    COLLECT_TIMEOUT_S <= headroom` — both counts derived from what the scripts spawn and import,
    because the sink's breaker is per-process state and a blocked writer therefore costs the full
    per-call bound in each one, and because `phase-open`/`phase-close` size the suite by running
    `pytest --collect-only`, a child whose natural duration is a property of somebody else's test
    tree. `COLLECT_TIMEOUT_S` lives in `gate_timeouts.py` beside the headroom it spends, and
    `pipeline_metrics.py` reads it from there rather than carrying a second copy.
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
- **A same-family gate is refused, and the waiver is EXPLICIT — never a false author family.**
  `cause=cross-family` is a correct refusal with one broken remedy: the only route through used to be
  declaring a `--author-family` that is not the author's, and when the author genuinely is the gate's
  own family there is no truthful value to write. A rule whose remedy is unavailable is a wedge, not
  a gate (the same shape as the requirement cap on a shipped spec, §3a). So the assertion is waived
  by naming the waiver: **`GATE_SAME_FAMILY_WAIVER="<why>"`** (or `--same-family-waiver`), which
  `scripts/gate_runner.py` applies to `cross-family` **and to nothing else** — an unknown vendor is a
  family nobody could resolve, not one somebody chose to share, so it stays refused. An empty reason
  is not a waiver, and `AUTHOR_FAMILY` keeps its truthful value either way.
  **A waived run is visibly waived, everywhere the call is recorded**, because a verdict that
  silently dropped the assertion would be worse than the wedge: it converts a loud refusal into a
  quiet false assurance. The runner announces `GATE SAME-FAMILY WAIVER IN FORCE` on stderr **before**
  it calls anything; the call's metrics `note` carries the reason (an existing field of the closed
  `gate_calls` schema, §6d — no new key); `hook_spec_gate.sh` folds a `SAME-FAMILY WAIVER` banner
  into the report it stamps **with** the verdict, on approvals and blocks alike, since nothing that
  reads a verdict later sees a hook's stderr; and the override is **audited or it does not hold** —
  one line through `scripts/bypass_log.sh` into `gate-overrides.log`, and a waiver that could not be
  logged stops the gate. `gate_ci.sh`'s static audit honours the same knob, and says so loudly, so
  one disclosed decision does not wedge a second place. Unset, everything above is inert and the
  assertion fires exactly as it always has.
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
- **Model-based gates run in-chat** — the Verifier's triage, and the grill-me spec review. CI only checks their **committed artifacts**. The one exception is the automated
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

**Writing metrics can never fail a phase — with one deliberate exception, `defect`, below.** Every
failure — no writer configured, an unwritable record, a refusal, a hang, a crash — is swallowed,
written to `.avenger-metrics.log`, and reported as "not recorded". Every metrics CLI call in a hook
exits 0 on an emission path. **Measurement, not a gate**: a metrics bug that blocked delivery would
be a self-inflicted outage in the thing meant to make delivery cheaper. An unwritable record makes
firstmate's CLI *block* rather than fail, so one timeout abandons the writer for the rest of that
process — the fail-open property has to hold in wall clock, not only in exit codes.

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
| the **count** of verification attempts, and nothing about what each one changed | **derived from `verdict.json` and its archives** — never counted per invocation | that script runs several times inside one attempt (a timed-out call, a diagnostic retry); one phase recorded **8** against a real attempt of 1 and a cap of 3 that had never fired, and read against that cap the number says the cap failed. Retries stay visible in `gate_calls[]` with their `failure_cause` — only the attribution was wrong. Repeated calls converge |
| tests before and after — **collected pytest test items** (`pytest --collect-only`, minus the test root's `e2e/`), the same population `hook_verifier.sh`'s own `pytest -q` reports, **never `def test_` lines** | `hook_spec_gate.sh` (first spec write) and the orchestrator's `phase-close` (after the phase's commit) | counted **the same way at both ends**, so the delta is a real delta and not two counting methods. A static count of test *functions* put 917/973 in the record against 1092/1164 observed in the same phase — a parametrized function is one `def` and several items (issue #46). The collection is bounded by `gate_timeouts.collect_timeout()` and runs in its own process group; a collection that cannot answer leaves the field **absent**, never 0 |
| the phase's **close** and the `elapsed_minutes` it took | the orchestrator, running `phase-close` itself immediately after the phase's own commit (`commands/avenger-run.md` §5) - **never a hook** | close means **landed**, not implemented. `handover.md` being *written* is the Verifier's precondition, not the phase landing: an amendment can still reopen the phase, the Verifier can still route it back, and the commit can still be blocked, so a stamp taken there understates the phase by verification, route-backs and close - its most expensive stages - and the too-early number is indistinguishable from a good one (issue #46). `hook_verifier.sh` cannot see the commit land, so it no longer stamps; `record_phase_close` refuses the write itself while anything under the phase directory is still uncommitted (`applicability.changed_paths`, the same git scope every other check uses), so a misordered caller records **nothing** rather than a false close |
| **which stage found each defect** | `hook_verifier.sh` (over `verdict.json` at phase close), `hook_mutation.sh`, and the `defect` command for stages a script cannot see | the single most valuable field, and the only one **unrecoverable after the run** |
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
`AVENGER_METRICS_CMD` — **read from the project's `.env` as well as from the environment**, because
the hooks all get it through `load_env.sh` while `pipeline_metrics.py defect` is the one command a
STAGE runs directly, from a subagent shell that inherited no export, and it therefore resolved
nothing and lost the defect in silence. Resolution lives in `scripts/metrics_sink.py`, the one point
every emission passes through, so a writer named once in `.env` is found by hooks and stages alike;
the real environment still wins. Without any of that a run records nothing and **says so once**, because a measurement
layer quietly doing nothing is the failure the record exists to remove. That announcement names the
state that is actually true, and there are two: **nothing named at all** (not on `PATH`,
`AVENGER_METRICS_CMD` unset), or **a named path that is not an executable file** — missing, or with
no exec bit. Reporting the first for the second hands the reader a remedy they already applied.
`AVENGER_METRICS_OFF=1` disables it silently. opencode's adapter drives the same `hook_*.sh`, so gate calls, spec rounds and
phase boundaries are recorded there too; it has no subagent-start or read event, so skill loads are
not.

**`recorded_by` says WHO wrote the defect down, and every route here says `stage`.** It is a
different question from `found_by` — what CAUGHT it — and neither is derived from the other. The
field has three answers and the third is the point: `stage` (the stage that caught it emitted it as
it ran), `operator` (a person transcribed it afterwards), and **absent, meaning the record predates
the field**, which a reader must not resolve to either. Two phases' defects were entered by hand and
that fact survived only as prose; an unstamped emission from this pipeline would be indistinguishable
from one of those. So `record_defect` stamps every defect it writes — the `defect` command, the
verifier's findings and the mutation survivors alike — and **that is the single write to `defects[]`
in the module**, held single by test, so a fourth route cannot land unstamped. Nothing here can emit
`operator`: a person transcribing after the fact is a different producer and states it through
firstmate's own CLI. **Nothing back-fills** — a record written before the field stays valid exactly
as it is.

**Version skew costs the field, never the defect.** firstmate's key surface is closed, so a writer
older than a field refuses the whole entry over it — which would take `found_by`, the one
unrecoverable field, down with it. The sink retries once without the keys the CALLER named as
droppable (`_optional`; the sink still knows nothing about the schema), so the defect lands, the
provenance is simply absent, and the loss is said on stderr. It stays measurement, not a gate.

**`defect` is the one command that is loud about failing (issue #66).** Every other emission point
runs from a hook's `|| true` and must stay exit-0 no matter what. `defect` is run directly by the
stage that caught something, off any hook, so nothing else is fail-open on its behalf — an emission
that could not be written exits non-zero and prints why on stderr, unless `AVENGER_METRICS_OFF=1` is
set (that is configured behaviour, not a failure). **That exit says one of three things, and they do
not share a remedy or an owner.**

- **A writer IS configured and the write failed** (the writer refused, hung, or the record could not
  be written): retryable, and the message says so. Read the `[metrics]` line above it, fix the cause,
  and re-run the exact command. *Configured* means **named**, not working — a broken
  `AVENGER_METRICS_CMD` path lands here on purpose, because making that path executable is a remedy
  that exists and a re-run after it succeeds.
- **No writer is configured at all** — `fm-pipeline-metrics.sh` is not on `PATH` and
  `AVENGER_METRICS_CMD` is unset: **terminal, and addressed to the operator, not to you.** This is
  the expected state of a standalone install with no firstmate home, it is a standing property of the
  environment, and re-running fails identically. **Do not retry it** — the defect could not be
  recorded, say so and move on. Under `--auto` it is worth surfacing to the operator rather than
  looping on. The operator's remedies are pointing `AVENGER_METRICS_CMD` at firstmate's own
  `fm-pipeline-metrics.sh`, or `AVENGER_METRICS_OFF=1` to record none deliberately and silently.
- **`--phase-ref` resolves to no phase** — the writer was never reached and nothing about it is known
  to be wrong: **the ARGUMENT is what must change.** That is a caller that typed the command wrong,
  so it exits **2**, the same code `argparse` already returns for one, rather than reporting a write
  that never happened; re-running the exact command can only fail identically, which is the loop the
  split exists to end. `AVENGER_METRICS_OFF=1` does not quiet it, for the same reason it does not
  quiet a parse error: turning emission off is a statement about recording, not a licence to name a
  phase that does not exist.

Each shape opens with its own marker and **no marker contains another**, because that stem is what a
stage matches on. Whichever fired, the defect did not land, and `found_by` is not recoverable once
the phase moves on.

**The Verifier's own attribution (`verifier-findings`, from `hook_verifier.sh` at phase close) stays
non-blocking** — it runs behind `|| true` and never fails the phase — **but its stderr is not
discarded.** It is the highest-volume defect-attribution path there is, and a run that dropped every
verifier-attributed defect must not look like a run that found none.

## Closing the release loop — the executing plugin vs. the merged repository (issue #65)

Phases run from `$CLAUDE_PLUGIN_ROOT`, the plugin release Claude Code cached on install — not this
repository. A fix merged and reviewed here is inert for every running phase until someone remembers
to cut a release and refresh that cache; measured directly, the cached copy still carried a defect a
merged PR had already fixed, and phases kept running against it. "Remember to cut a release" is a
sentence claiming behaviour nothing enforces, the same class every other rule on this list exists to
close.

**The version is observed, never assumed.** `scripts/plugin_release.py` derives it from the copy
that is actually running (`$CLAUDE_PLUGIN_ROOT`, the same variable every hook already resolves its
own path from) — a static constant would read identically from a fresh copy and a stale one, which
is exactly the failure mode. `record_plugin_version` rides it into the phase's `gate_calls[]` at
phase-open, on existing keys rather than a new top-level field: firstmate's metrics schema is closed
and its producer contract is "add no key" (see "The pipeline measures itself as it runs" above), so
this is `record_triage_decision`'s precedent applied a second time — the fact firstmate has no field
for goes into bounded `note` text under a stage of its own (`plugin-version`), not into a field this
repo invented. Closed means closed in the row's **values** too: `verdict` is firstmate's enum
(`GO|REVIEW|NO-GO|error|killed`) and its writer refuses a row `validate` would refuse, so the drift
status is mapped onto it (`fresh` → GO, `stale` → NO-GO, `unknown` → REVIEW) and kept verbatim in
`note`. Passed through as its own token, the row is rejected, the refusal is swallowed by the
fail-open path every measurement runs on, and the executing version is recorded nowhere — which is
the one thing this row exists to prevent, and is invisible to any test using a double that enforces
no schema.

**The halt EXECUTES; the preflight only makes it arrive earlier.** `check` was mechanical from the
start and the STOP was not: `/avenger-run` §1 told the orchestrator to halt, and an orchestrator that
never read that line, or read it and continued, ran every phase against stale code with no signal
anywhere - the same defect class, one layer out from the one this section is about.
`scripts/hook_plugin_release.sh` refuses instead. It is a **`PreToolUse`** hook, and it fires on the
spawn of any `avenger-*` stage: the run stops at the last moment before stale code would build a
phase, with nobody having to remember anything. `PreToolUse` deliberately, and not `SubagentStart`
where every other stage-scoped hook here lives - that event **cannot block** (Claude Code documents
exit 2 there as showing stderr to the user while the subagent proceeds anyway), so a halt written
there would be prose with a shebang on it. The decision reads `tool_input.subagent_type`, never the
spawn tool's NAME, which is `Task` in some harness versions and `Agent` in others; a hook keyed to
the wrong name would silently never fire. `/avenger-run` §1 still runs the check, but now as a cheap
early report: skipping it changes *when* the operator learns, never *whether* the run is stopped.

**Everything that is not a verdict fails open**, and the boundary is the point of the whole thing: no
`jq`, no `python3`, an unreadable payload, a bad `PLUGIN_RELEASE_STAGES` regex, or a checker that
crashed lets the spawn through, said out loud on stderr rather than passing invisibly. The verdict is
read as the exit code **and** the checker's own `STALE:` marker, because a Python traceback exits 1
too and a crash arriving as a halt would prescribe "cut a release" for a defect a release cannot fix.
Break-glass is the ordinary one: `GATE_BYPASS="reason"` proceeds and is logged through
`bypass_log.sh` like every other gate. **opencode does not carry this** - its adapter hooks
`tool.execute.after`, which has no pre-spawn moment to refuse at, so there the §1 preflight report is
the only signal and says so. `tests/test_hook_plugin_release.py` proves the halt by removing it: the
stale case goes red when the refusal, its wiring in `hooks/hooks.json`, or the marker check is taken
out, and the never-halt cases are what keep it from being a bigger hammer than the finding.

`STALE` (the executing copy's content differs from the merged repository) stops the
run and names the fix; `UNKNOWN` (no source repository resolvable on this machine) is reported on
stderr and left unenforced — the same applicability boundary every other check here draws around a
scope it cannot resolve. **Two things resolve a source repository, and nothing else does**:
`AVENGER_SOURCE_REPO`, the explicit per-machine override; or the project being worked on being this
plugin's own repository, decided by its `.claude-plugin/plugin.json` `name` matching the EXECUTING
copy's — not merely by that manifest existing. A consumer using this pipeline to develop *their own*
Claude Code plugin has `CLAUDE_PROJECT_DIR` pointing at an unrelated plugin repository, and comparing
the executing release against that payload can never match: a permanent STALE whose prescribed remedy
cannot clear it, which is the unclearable wedge §3a exists to prevent. So an unrecognised project is
UNKNOWN — reported, never enforced — exactly like a machine with nothing configured at all, and a
consumer who wants the guard enforced sets `AVENGER_SOURCE_REPO`. Comparison is by content hash of
the shipped payload
(`agents/`, `skills/`, `commands/`, `prompts/`, `scripts/`, `hooks/`, `.claude-plugin/`,
`docs/templates/` — the rest of `docs/` is this repo's own documentation of itself, not payload),
not by version string alone, because a forgotten version bump would otherwise read as a clean
release. When the source repository is a git checkout, the comparison reads its COMMITTED `HEAD`,
never the live working tree — hashing the working tree directly made any in-progress uncommitted
edit under the shipped payload read as STALE, including during the very session making that edit,
which would wedge anyone developing this repo and guarantee the guard gets bypassed. An uncommitted
change is instead surfaced as a separate `dirty` signal on the result (never a fourth status, never
enough on its own to fail `check`) — a dirty tree and a stale release are two different conditions,
and conflating them is the same disease this whole fix is about, one level in.

**The release step is one command, and it is not done until a harness would actually load it.**
`python3 scripts/plugin_release.py cut --repo <path> --cache-root <path>` copies the shipped payload
into `<cache-root>/<version>/`, refuses to overwrite a version whose content already differs (a
version is released once, so a forgotten bump fails loudly instead of silently clobbering the
previous release under its own number), and refuses to release a payload missing one of its own
expected paths (silent breakage nobody notices until a downstream user does). Run with no flags it
also re-points the install registry (`~/.claude/plugins/installed_plugins.json`,
`AVENGER_PLUGIN_PIN_PATH`/`DEFAULT_PIN_PATH`) at the new release — copying files into a new cache
directory nothing points at is not a release, it is the exact defect this issue describes,
reproduced inside its own fix. The closing check re-reads the registry from disk after writing (not
the in-memory object) and confirms the pin's `installPath` genuinely resolves `plugin_version()` back
to the version just released — `cut` cannot report success while the pin does not actually resolve
to it. `--no-pin` skips the registry entirely; every *other* caller (`cut()` the function, `check`)
still has no default `cache_root`/`pin_path` of its own — no hook, no gate and no test can touch a
real installation without naming both paths explicitly. `tests/test_plugin_release.py` proves every
one of these guards both ways — a stale cache against a fixed repo reads `STALE`, a pin pointed at a
target whose own manifest does not match the claimed version raises before reporting success, both
brought into agreement read clean — per issue #69's rule that a guard is proven by going red before
it is proven by going green.

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

## A `status: done` stamp is not a completion signal by itself (issue #68)

A spec's own implementer writes `status: done` into its frontmatter, and used to keep working
afterward — `test-mapping.md`, `test-evidence.md` and the phase's mutation gate all landed later.
In phase 11 a worker had armed a wedge guard on that stamp, watching for it as the signal that it
was safe to dispatch the next spec's implementer into the same worktree. It fired at 24 minutes
while the agent was still running. Had the guard been trusted, two implementers would have run in
one worktree against one shared database — forbidden outright, and phase 9 measured why: a `git
stash` from one implementer swallowed the other's uncommitted work, and the shared database
produced foreign-key violations plus a spurious lint failure.

Telling people not to wait on the stamp fixes nothing — that sentence has already been written five
times in two days about three different stamps. So the stamp is made **self-correcting** instead:
the moment `hook_verifier.sh` sees `status: done` land on a `spec.md` (its `spec-done` trigger), it
checks, mechanically, whether the spec is actually done — its own `test-mapping.md` carries at
least one recorded row, and its phase's test suite is green — **before** letting the stamp stand.
A **recorded** row is one that says something: the mapping template ships three placeholder rows
and `skills/tdd` points every implementer at it, so a row whose **requirement-id cell** still
carries the template's `R<n>.<k>.<m>` syntax counts as nothing. Counting rows would pass exactly the
state this issue names. Only that first cell is read — a real id cannot contain angle brackets,
while the other columns legitimately can (`Result<Config, Error>`, `rejects n < 5 or > 10`), and
misreading one of those would revert a correctly-stamped spec.
Either check failing REVERTS `status: done` back to `status: in-progress`
(`scripts/spec_done_guard.py`) and then fails the hook. A premature `done` does not survive the
check that reads it.

**The revert acts only on evidence scoped to the same thing it rewrites**, and three states leave
the stamp exactly as written while still failing the hook:

- **A spec that owes no mapping row.** A `binding: none` requirement is structural or build-time and
  gets no test and no mapping row by construction (§4a), so a spec whose every declared requirement
  is `binding: none` has a legitimately row-less mapping and is never asked for one. The obligation
  is read from `requirement_cap.declared_bindings`, which already owns the declaration layout and
  where a binding sits inside it; a requirement declaring **no** binding is owed a trace, and a
  layout the parser cannot read is undecidable rather than exempt. **A spec declaring no requirement
  at all is its own stop, never this exemption** — the two read off the same empty binding list, and
  taking the second for the first would leave a `done` stamp standing over an absent mapping for
  every spec whose requirements were never written down, which is issue #68's own state. There the
  remedy is to declare the requirements, so it is said that way and the stamp is left as written.
- **A red suite that could only be run repository-wide.** With no phase test directory resolvable
  the hook runs the whole tree minus e2e — the permanent state of a project whose tests do not live
  under `tests/` — and one unrelated failure there is not evidence about one spec, especially where
  the implementer is told outright that pre-existing failures are expected.
- **Break-glass.** `GATE_BYPASS` leaves the stamp `done`: overridden, audited in
  `gate-overrides.log`, and *reachable*. Reverting underneath the override produced a bypass with no
  end state at all — `fail()` cleared the failure, the spec stayed `in-progress`, re-stamping
  re-fired the hook, and no disclosed exception can be recorded since `spec-done` is not in
  `applicability.RULES`. A bypass an operator cannot act on is a trap, not an escape hatch.

**The claim is exactly as wide as the mechanism.** This is a `PostToolUse` hook matched on
`Write|Edit|MultiEdit` (`hooks/hooks.json`), so what is enforced is: **no Write/Edit/MultiEdit tool
call can leave a false `done` stamp on disk.** A stamp written through Bash — `sed -i`, a heredoc,
`python3 -c` — never reaches this hook and is outside this mechanism's reach. Saying otherwise
would be one more sentence claiming behaviour nothing enforces, which is the class this issue is
curing.

**And it binds only the TRANSITION into `done`** (the applicability boundary, §3a). The trigger
fires on any write to a `spec.md` that merely *contains* `status: done`, so `spec_done_guard.py`
compares against the file's **committed HEAD** version: a stamp that was already `done` there
belongs to a SHIPPED spec, and it is counted and named on stderr, never reverted. Rewriting it
would delete the single evidence `applicability.spec_shipped` reads — flipping the requirement cap
from counting that spec to blocking it with a split a shipped spec cannot take, and re-routing a
completed spec back to `stage: implementer` — with no exception recordable for it, since
`spec-done` is not in `applicability.RULES`. When git cannot say what is committed, the scope is
unknowable, so nothing is enforced and the check says so out loud.

**This binds `status: done` specifically and does not generalize.** Other stamps this pipeline
writes (`spec_gate:`, `review_status:`, `verdict.json`'s `verdict`) already have their own single
writer and single reader (`spec_gate_state.py`, `/spec-review`, `hook_verifier.sh`'s handover
branch) and are not touched here — a caller still may not treat any of them as a liveness signal;
the only reliable completion signal in this harness is the agent/task notification, never a written
marker and never process polling. What changed is narrower: the one marker this issue was filed
about now corrects itself instead of trusting the writer.

## Driving the chain (`/avenger-run`)

The chain can be walked by hand, one stage at a time, or driven by `commands/avenger-run.md`, which
makes the **main session** the orchestrator (a subagent has no Task tool, so it cannot spawn stages —
only the main thread can). Position comes from `scripts/pipeline_state.py`, which reads the artifacts
on disk (`spec_gate`, `review_status`, `status`, `verdict.json`, `amendments.json`, `exceptions.json`,
`breaker.json`) and returns the single stage the feature owes next — so a run resumes after a
`/clear`, a compaction, or a new session. It stops for `plan.md` approval and each spec-review unless
`--auto`, retries a stage twice before halting, routes to the Breaker on `criticality: critical` and
does not walk past a critical phase that has no record of one, obeys `MUTATION_POLICY`, and commits
per verified phase, then twice more at feature close — the e2e stage's output *before* the ship gate (whose
precondition is a clean tree already carrying `tests/e2e/<feature>/`) and the retrospective artifacts
*after* it. **The orchestrator itself never pushes**; the feature-close ship gate above does, in both
modes. Full detail in `docs/AUTOMATE.md` §2.

## Stage effort is DECLARED in the definition, never handed over at spawn

Each stage's reasoning effort is the `effort:` key in its own `agents/<stage>.md` frontmatter. The
harness reads that key and applies it when the stage is spawned. **No caller supplies it**, and the
delegation tool has no parameter that could carry one.

That is the whole rule, and it exists because the opposite was written down for ten phases.
`commands/avenger-run.md` carried a per-stage effort table, called reasoning effort *"the largest
lever on cost that does not change what any gate checks"*, and instructed the orchestrator to hand
each subagent its effort at spawn time. Nothing could obey an instruction naming a parameter that
does not exist, no phase metrics record ever mentioned effort, and every stage of every phase ran at
the session default. The largest cost lever the pipeline believed it had had never once been pulled,
and any past claim that a cheap profile held quality was a claim about a profile that was never in
force.

`scripts/stage_effort.py check` is what stops that returning, and it runs on every commit through
`gate_ci.sh`. Three failures, one per way the state comes back: a stage that declares no effort (it
runs at the session default), a document stating a level the definitions do not or naming a stage no
definition backs (a table with nothing behind it), and any instruction to hand a stage its effort at
spawn time. `stage_effort.py table` renders the allocation from the definitions themselves, which is
the copy the harness reads. **It is not diff-scoped**, for the same reason `doc_read_path.py
check --sources` is not: these are canonical stage instructions, always open to change, never shipped
artifacts a later rule would hold hostage. A tree with no `agents/` — a vendored install — is
reported as *nothing checked* rather than passing invisibly.

Two limits, said rather than implied. **A declared effort is not an observed one, and nothing here
observes what ran.** A model that does not support a level is silently downgraded and only the
harness sees it. This check proves the allocation exists and agrees everywhere it is written down; it
does **not** prove the lever is pulled, and a retrospective may not read a declaration as a
measurement. The observed half is tracked as `fm-metrics-stage-efforts-field` — the phase metrics
record has no field for a resolved effort, firstmate owns that schema, and the design there requires
`resolved` to come from observation rather than a self-report, exactly as `skill_loads[]` does. And
**opencode does not carry this** — `sync_opencode.py` says so on every run rather than emitting a key
the provider would drop, because a value that looks carried and is not is the same defect one runtime
over.

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
