# Agentic Avengers Pipeline

The canonical rules live in `skills/pipeline-conventions/SKILL.md`. This file mirrors the essentials
for Claude Code sessions. Runtimes: **Claude Code + opencode**.

## Pipeline conventions

### 1. Artifact Documentation
Every stage writes a markdown artifact with YAML frontmatter:
- Feature-level → `docs/features/<feature>/` (`task-analysis.md`, `overview.md`, `plan.md`, `fidelity-report.md`, `scoped/review-<slice>.md`)
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
test-author modes by `work_kind`: greenfield · migration · refactor/brownfield.

### 5. Phase Ordering
Phases are built in dependency/risk order, one at a time, fully through build-and-verify.

### 6. Gates
Gates run on a fresh **cross-family** model (family ≠ author) and **fail closed** — a gate that can't
reach a verdict (missing key, provider down, non-JSON, same-family) stops. Mutation = **cosmic-ray**
(score = 1 − survival rate). **Break-glass** (`GATE_BYPASS="reason"`) is logged to `gate-overrides.log`,
shown visibly, and recorded in `handover.md` — never silent.

### 7. Canonical-source driven
Edit `agents/`, `skills/`, `commands/`, `prompts/`, `scripts/`, `hooks/`; regenerate the opencode
adapter with `python3 scripts/sync_opencode.py`. Never hand-edit `.opencode/`.
