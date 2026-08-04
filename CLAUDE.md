# Agentic Avengers Pipeline

The canonical rules live in `skills/pipeline-conventions/SKILL.md`. This file mirrors the essentials
for Claude Code sessions. Runtimes: **Claude Code + opencode**.

## Pipeline conventions

### 1. Artifact Documentation
Every stage writes a markdown artifact with YAML frontmatter:
- Feature-level → `docs/features/<feature>/` (`task-analysis.md`, `overview.md`, `plan.md`, `fidelity-report.md`, `scoped/review-<slice>.md`, `e2e-mapping.md`, `pipeline-observations.md`)
- Phase-level → `docs/features/<feature>/phases/<n>-<slug>/` (`verdict.json`, `handover.md`)
- Spec-level → `docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/` (`spec.md`, `test-mapping.md`)
- Tests → `tests/<feature>/<n>-<slug>/<n>.<k>-<subslug>/`; feature e2e → `tests/e2e/<feature>/`
```yaml
---
feature: <feature-name>
phase: <n>-<slug>          # omit for feature-level
stage: <stage-name>
model: <model-used>
verdict: <pass|fail|pending>   # gates only
created: <ISO-8601-timestamp>
links: <related-artifacts>
---
```

### 2. Multi-spec phases + ID scheme
A phase is an independently verifiable slice holding one or more numbered specs `<n>.<k>`; requirement
ids are `R<n>.<k>.<m>`. **The Verifier runs once per phase**, after every spec in it is green.

### 3. Composed quality wall (per spec)
Both gates, in order: (1) automated **Fidelity Gate** on spec write → sets `fidelity_verdict`; NO-GO
routes back. (2) **spec-review** → sets `review_status: approved`, in either **HITL** mode
(`/spec-review` grill-me) or **automated** mode (`/spec-review --auto` / `SPEC_REVIEW_MODE=auto`, a
cross-family AI reviewer). A spec reaches the implementer only when `fidelity_verdict != NO-GO` AND
`review_status: approved`. Both gates are also what pre-agrees the **seams** the tests get written at.
The Fidelity Gate is this repo's only automated model gate and is the main deliberate divergence from
`klm-agentic-pipeline`, which has no such gate.

### 4. The implementer writes the tests, test-first — and they lock at the Verifier
The **implementer** writes both the tests and the code, in a red → green loop (`skills/tdd`, vendored
from mattpocock/skills): one seam, one failing test, minimal code, repeat. **Vertical slices, never
the whole suite up front.** Red is the expected state *during* a build, not a failure.

**Locked-after-verify.** The implementer owns the phase's tests *until* `avenger-verifier` passes it;
from that point they are **locked** and weakening one requires re-verification. Locked forbids
*weakening*, not *adding* — a Breaker counterexample or a surviving mutant routes back to the
implementer to add a case.

Because the author of the code also authored its judge, **the tests get read** — on a green suite as
much as a red one — for tautological, implementation-coupled and missing-negative patterns, over a
**bounded review set** (tests mapped to the phase ∪ test files it changed, plus their directly
referenced helpers; expand only on evidence). `avenger-verifier` picks that set and persists
`verdict.json`, but the **judgement itself runs on another vendor's model** via
`scripts/verifier_review.sh` → `gate_runner.py` on `$VERIFIER_GATE_MODEL` — every subagent here is
Anthropic, so the agent cannot be its own cross-family check. It routes `wrong/gamed test` and
`coverage gap` back alongside `code issue`. That review is the pipeline's independence; it is not
optional, and it fails closed.

Three test modes by `work_kind`, all inside `skills/tdd`: **greenfield** (red → green per vertical
slice) · **migration** (parity-first — the *existing suite is the contract*, run it rather than
re-authoring it; characterize only genuine gaps at critical seams) · **refactor** (baseline-first
parity, no port; an intentional behavior change is greenfield work with its own requirement). Plus
**e2e-author**, not selected by `work_kind` — the implementer runs it once per feature, after the
final phase is green.

### 4a. Tests are integration-level by default
Every test drives its requirement through a **seam** — the public entry point a caller uses (HTTP
handler, service method, CLI) — with real collaborators. Mock only what crosses a trust/cost boundary
(third-party API, LLM, vault). `test-mapping.md` carries a `level` column: `integration` (default) ·
`e2e` · `narrow`; **`narrow` requires a written justification** and is allowed only when a requirement
has no reachable seam. Requirements with no integration surface (pure parsers, mappers) get **no
dedicated test** — they're covered transitively. Migration is exempt: parity outranks the default.
Rationale: tests bound to internals are the ones rewritten on every refactor.

### 4b. Feature-level e2e
**1-3 tests (5 max)**, written once after the last phase is green, in `tests/e2e/<feature>/`, tracing
to the goal in `overview.md` rather than a spec id (the one exception to "no spec id → no test").
Excluded from the mutation gate and the phase verifier hook.

### 5. Phase Ordering
Phases are built in dependency/risk order, one at a time, fully through build-and-verify.

### 6. Gates
Gates run on a fresh **cross-family** model (family ≠ author) and **fail closed** — a gate that can't
reach a verdict (missing key, provider down, non-JSON, same-family) stops. **Break-glass**
(`GATE_BYPASS="reason"`) is logged to `gate-overrides.log`, shown visibly, and recorded in
`handover.md` — never silent.

**The feature-close ship gate (`no-mistakes`) is the one sanctioned same-family exception.** It runs
once per feature — after the last phase is verified and the e2e suite is written — and covers what no
avenger stage does: lint, docs, push, PR, CI. Its pipeline agent is pinned to Anthropic Opus
(`.no-mistakes.yaml`, plus `agent_args_override` in `~/.no-mistakes/config.yaml`), a **deliberate
divergence** from the cross-family rule: it runs in the daemon's own disposable worktree with no
shared context with the stage that wrote the code, so it decorrelates *context* while accepting
shared *family* blind spots. It is not a break-glass bypass, and every **per-phase** gate (fidelity,
spec-review, verifier) stays cross-family. While a run is active it owns both findings and fixes, so
the route-back-to-implementer rule is suspended for its duration.

**Mutation = cosmic-ray**, once per phase, **diff-scoped** (`cr-filter-git` skips mutants outside the
phase's changed lines). The verdict is **deterministic** — `scripts/mutation_score.py`, not a model:
score `>= MUTATION_MIN_SCORE` (default **0.85**) → GO with no model call; below → survivors go to the
gate model to be named as missing cases, and the phase routes back to the implementer. The threshold is
**not 100%** on purpose — chasing zero survivors is what multiplies narrow tests. Runs
`cosmic-ray baseline` first: a mutant counts as killed whenever the test command fails, so a broken
suite would otherwise score a perfect 1.0.

**Mutation is optional and OFF by default**: `MUTATION_POLICY` = `off` (default) · `advisory` (runs
and reports the score + survivors, never blocks) · `enforce` (fails closed). It is an *extra* signal,
**not** the independence mechanism — that is the Verifier's test-quality review. When off, no mutation
tool runs anywhere. The score itself is deterministic (`scripts/mutation_score.py`, diff-scoped via
`cr-filter-git`); the Verifier interprets survivors in chat using `skills/mutation-interpret`.

### 6a. Gates fire on "done", not on every edit
The implementer runs a red → green loop, so red is an expected state throughout a build. Gates trigger
on a spec reaching `status: done` (smoke-check the phase suite; model called only on failure) and on
`handover.md` — never per code edit. **No model runs in these hooks** except the Fidelity Gate: the
Verifier is an *agent* that runs in chat and commits `verdict.json`, and the hook only checks that
artifact exists and passes. Mechanical gates in hooks and CI; model gates in chat. The implementer's
own `pytest tests/<feature>/<n>-<slug>/` is the inner loop: free, no model call. A gate also never re-judges an unchanged spec —
`scripts/spec_gate_cache.py` hashes the spec body per gate, so `status: done` can't re-roll a fresh
verdict over an approved spec.

### 6b. Implementer minimalism (`skills/ponytail`)
The implementers — and only they — climb a minimalism ladder before writing production code: does this
need to exist (YAGNI) → already in this codebase → stdlib → native platform feature → installed
dependency → one line → minimum that works. Vendored from `DietrichGebert/ponytail` (MIT), flattened
to one intensity and re-scoped.

Delivery is **`scripts/hook_ponytail.sh` on the `SubagentStart` event**, matching `agent_type` against
`PONYTAIL_AGENTS` (default `avenger-backend-architect|avenger-frontend-developer`). SessionStart
context never reaches subagents, so a SessionStart hook would inject it everywhere *except* where code
is written; a passive `SKILL.md` self-activates ~never. The hook **fails closed** — bad payload,
unknown agent, bad regex or missing skill injects nothing, so `avenger-verifier`, `avenger-breaker` and
`avenger-bug-hunter` never receive a "write less code" persona while their job is to demand more.
`PONYTAIL_OFF=1` kills it.

**Production code only.** It never removes a test, a negative case or a seam, and rung 1 never applies
to a requirement in an approved spec. On conflict `skills/tdd`, `pipeline-conventions` and the spec
win. **It is not a gate**: `/ponytail-review` is advisory (no artifact, no verdict), and `/ponytail`
loads the ladder into the main thread for inline implementation the hook cannot reach — deliberately
off by default there, since the main thread also writes specs and runs verifier triage. opencode has no
subagent-start event; its implementers get the ladder from the agent prompt line only.

### 6c. The pipeline learns from itself — two logs, kept apart
`docs/lessons/` (`skills/self-improvement`) is **per-project** and about the **work** — a pytest trap,
a migration gotcha. Any agent reads the index at start and appends when something is learning-worthy.
It was dormant until now; `pipeline-conventions` is where every agent picks it up, so it needs no
per-agent wiring.

`docs/features/<feature>/pipeline-observations.md` (`skills/pipeline-retrospective`) is about the
**machinery** — a gate that misfires, a stage that churns — and its destination is **this repo**. The
orchestrator appends observations *as they happen* (a run resumes across sessions, so end-of-run
recall is not reliable), including **successes**: a gate that caught something real is the evidence
for keeping it. At `done` they are rendered as a lavish triage; whatever the human selects becomes a
`pipeline-improvement` issue. Nothing is filed without an explicit selection.

`--auto` **records but never triages** — no human to poll. The log stays `triage: pending` and the
next interactive run's **preflight sweep** finds it; that sweep is the only recovery path, because
`done` is terminal and will never re-fire. `hook_autoapprove.sh` denies `gh`/`gh-axi` issue creation
outright while auto is armed, so the no-auto-filing rule is enforced mechanically, not just written.

### 7. Canonical-source driven
Edit `agents/`, `skills/`, `commands/`, `prompts/`, `scripts/`, `hooks/`; regenerate the opencode
adapter with `python3 scripts/sync_opencode.py`. Never hand-edit `.opencode/` — `agents/` and
`skills/` are generated, and `plugin/pipeline-gates.ts` is a thin adapter that shells out to the same
`scripts/hook_*.sh` the Claude Code hooks run. **The gates have one implementation.** Add or change a
gate in `scripts/` + `hooks/hooks.json`; the plugin needs no edit and must not grow logic of its own.

### 8. Canonical agents are project-agnostic
Agents in `agents/` carry pipeline mechanics only; they learn a project's rules by reading its
`CLAUDE.md`, spec, and `codebase/MOC.md` at run time. Never hardcode one project's stack into a
canonical agent — use `avenger-agent-factory` to ground a copy per repo. See `examples/jarvis/` for a
worked example of what grounding looks like.
