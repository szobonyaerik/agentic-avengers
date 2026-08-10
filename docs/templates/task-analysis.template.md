---
feature: <feature>            # <!-- kebab slug; anchors docs/features/<feature>/ -->
type: task-analysis
status: draft
created: YYYY-MM-DD
language: <python|java|cpp>   # <!-- drives stack-specific gate policy downstream -->
work_kind: <migration|greenfield|refactor>  # <!-- selects the implementer's tdd-skill mode -->
---

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
