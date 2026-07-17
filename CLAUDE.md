# Agentic Avengers Pipeline

The canonical rules live in `skills/pipeline-conventions/SKILL.md`. This file mirrors the essentials
for Claude Code sessions. Runtimes: **Claude Code + opencode**.

## Pipeline conventions

### 1. Artifact Documentation
Every stage writes a markdown artifact with YAML frontmatter:
- Feature-level → `docs/features/<feature>/` (`task-analysis.md`, `overview.md`, `plan.md`, `fidelity-report.md`, `scoped/review-<slice>.md`, `e2e-mapping.md`)
- Phase-level → `docs/features/<feature>/phases/<n>-<slug>/` (`test-mapping.md`, `implementation-report.md`, `test-execution-report.md`, `handover.md`)
- Spec-level → `docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`
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
cross-family AI reviewer). A spec reaches the Test-Author only when `fidelity_verdict != NO-GO` AND
`review_status: approved`.

### 4. Tests as Frozen Contract
Tests are a **FROZEN CONTRACT**. No agent may edit files under `tests/` except the Test-Author. If a
test looks wrong, route the concern back to the Test-Author — never reshape a test to pass. Three
test-author modes by `work_kind`: greenfield · migration · refactor/brownfield. Plus **e2e-author**,
which is not selected by `work_kind` — it runs once per feature, after the final phase is green.

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
Excluded from the mutation gate and the per-edit verifier hook.

### 5. Phase Ordering
Phases are built in dependency/risk order, one at a time, fully through build-and-verify.

### 6. Gates
Gates run on a fresh **cross-family** model (family ≠ author) and **fail closed** — a gate that can't
reach a verdict (missing key, provider down, non-JSON, same-family) stops. **Break-glass**
(`GATE_BYPASS="reason"`) is logged to `gate-overrides.log`, shown visibly, and recorded in
`handover.md` — never silent.

**Mutation = cosmic-ray**, once per phase, **diff-scoped** (`cr-filter-git` skips mutants outside the
phase's changed lines). The verdict is **deterministic** — `scripts/mutation_score.py`, not a model:
score `>= MUTATION_MIN_SCORE` (default **0.85**) → GO with no model call; below → survivors go to the
gate model to be named as missing cases, and the phase routes back to the Test-Author. The threshold is
**not 100%** on purpose — chasing zero survivors is what multiplies narrow tests. Runs
`cosmic-ray baseline` first: a mutant counts as killed whenever the test command fails, so a broken
suite would otherwise score a perfect 1.0.

### 6a. Gates fire on "done", not on every edit
Tests are locked RED before the implementer starts, so red is the expected state during a build.
Gates trigger on a spec reaching `status: done` (smoke-check the phase suite) and on `handover.md`
(Verifier, then mutation) — never per code edit. The implementer's own `pytest tests/<phase>/` is the
inner loop: free, no model call. A gate also never re-judges an unchanged spec —
`scripts/spec_gate_cache.py` hashes the spec body per gate, so `status: done` can't re-roll a fresh
verdict over an approved spec.

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
