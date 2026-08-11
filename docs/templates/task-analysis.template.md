---
feature: <feature>            # <!-- kebab slug; anchors docs/features/<feature>/ -->
type: task-analysis
status: draft
created: YYYY-MM-DD
language: <python|java|cpp>   # <!-- drives stack-specific gate policy downstream -->
work_kind: <migration|greenfield|refactor>  # <!-- the feature default; each spec then CARRIES its own -->
readers: avenger-solution-architect @ once, at feature start
---

<!-- `readers:` is read-path bookkeeping, not decoration: this document is opened ONCE, by the
     Solution Architect. It used to be opened by the implementer and by spec-review on every spec —
     60 times over one feature, ~465k tokens — to read `work_kind` off line 7. That field now rides
     in each spec's own frontmatter. See skills/pipeline-conventions § The document read path. -->


# Task Analysis: <Task Title>

## Context
<!-- GOOD: names the real module, its current state, and WHY now. BAD: "improve the code." -->

## Requirements
### Functional
- [ ] <!-- one observable behavior per line -->
### Non-Functional
- [ ] <!-- performance / security / compatibility, with numbers where they exist -->

## Scope
### In Scope
### Out of Scope
<!-- COMMON FAILURE: leaving Out of Scope empty. If you can't say what you're NOT doing, scope is
     still ambiguous — grill more. -->

## Technical Context
<!-- Real paths from the codemap. For a MIGRATION, state the existing test situation explicitly:
     does a suite exist? what coverage? is it bound to the old stack (needs porting)? -->

## Definition of Done (feature level)
- [ ] <!-- outcome-level only; the Spec Writer turns these into requirements with a `binding:` each -->

## Risks & Considerations
