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
looped per phase until the feature is done, then a single feature-level **e2e** stage before ship.

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
  `gate_runner.py` on `$VERIFIER_GATE_MODEL` (default `google/gemini-2.5-pro`). `gate_runner` refuses
  a same-family model, and CI asserts the same statically. Opus-vs-Sonnet is **not** decorrelation.
- **Gates fail closed.**
- **Break-glass bypass** is allowed but recorded — whole-gate via `GATE_BYPASS`, per-finding via
  `verdict.json` `break_glass` + a mandatory `waiver_reason` — in `handover.md` and
  `gate-overrides.log`, and visible on the PR.
- **Artifacts on disk** with YAML frontmatter — the chain survives cold sessions.

## Where the models run

- **Mechanical gates run in CI and in hooks** (`scripts/gate_ci.sh`, the pre-commit floor): the test
  suite, the static cross-family assertion, artifact presence (`review_status: approved`,
  `verdict.json` passing), and mutation only if a team turned it on. Auditable, can't be silently
  skipped.
- **Model-based gates run in-chat** — the Verifier's triage and test-quality review, and the grill-me
  spec review. CI only checks their **committed artifacts**. The one exception is the automated
  **Fidelity Gate**, which is a hook and does call a model in-session; it never runs in CI.

## Implementer test modes (see the `tdd` skill)

The implementer loads `skills/tdd/SKILL.md` on every spec and picks the mode from `work_kind`:

- **Greenfield** → vertical red→green: one seam → one failing test → just enough code → repeat.
- **Migration** → parity-first: the existing suite is the contract; run it against the migrated code,
  and add characterization tests only at genuine gaps on pre-agreed critical seams.
- **Refactor** → baseline-first parity: use the migration procedure without porting tests; behavior
  remains unchanged unless a separate greenfield requirement explicitly says otherwise.

## Feature-level e2e (`skills/e2e-author`)

- Written by the implementer **once per feature, after the final phase is green** — never per phase.
- **1-3 tests, 5 is a hard ceiling.** They prove the assembled system delivers the feature's goal;
  they are not where edge cases live.
- Live in `tests/e2e/<feature>/`, trace to the goal quoted from `overview.md` rather than a spec id
  (the single exception to "no spec id → no test"), and are recorded in `e2e-mapping.md`.
- **Excluded from the mutation gate and from the phase verifier's scope.** They run at feature close
  and in CI.
