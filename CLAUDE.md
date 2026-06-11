# Agentic Avengers Pipeline

## Pipeline conventions

### 1. Artifact Documentation
Every stage writes a markdown artifact under `docs/features/` (feature-level) or `docs/features/<feature>/phases/<phase>/` (phase-level), with YAML frontmatter:
```yaml
---
feature: <feature-name>
phase: <phase-name>
stage: <stage-name>
model: <model-used>
verdict: <pass|fail|pending>
created: <ISO-8601-timestamp>
links: <related-artifacts>
---
```

### 2. Tests as Frozen Contract
Tests are a **FROZEN CONTRACT**. No agent may edit files under `tests/` except the Test-Author. If a test looks wrong, route the concern back to the Test-Author — never reshape a test to pass.

### 3. Phase Ordering
Phases are built in dependency/risk order, one at a time. Phases must be completed sequentially, respecting their dependencies and risk profiles.

### 4. Gates
Gates run on a fresh cross-family model and stop-and-explain on failure. Gate failures prevent progression to the next phase.
