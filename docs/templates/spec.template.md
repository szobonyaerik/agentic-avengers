---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
depends_on: []
status: draft
review_status: pending   # <!-- only the human reviewer sets this to 'approved', after grill-me -->
---

# <Spec title>

## Phase summary
<!-- 1–3 sentences for a non-technical stakeholder: the whole phase's outcome, why it matters, and
     any important boundary. Derive from the phase goal/scope in plan.md. Use identical wording in
     every sibling spec. No code, paths, signatures, requirement IDs, or unexplained acronyms. -->

## Spec summary
<!-- 1–3 sentences for a non-technical stakeholder: this spec's outcome, why it matters, and any
     important boundary. Explain any unavoidable domain or technical term inline in ordinary words. -->

## Scope
<!-- what this spec delivers, and explicitly what it does NOT -->

## Requirements
- R<n>.<k>.1 — <!-- a SINGLE verifiable behavior; not a bundle -->
- R<n>.<k>.2 — …

## Acceptance criteria
- R<n>.<k>.1 — passes when: …; fails when: …
<!-- COMMON FAILURE: a pass condition with no fail/edge condition. The implementer needs BOTH to
     write the paired positive/negative red→green slices. No pass-only requirements. -->

## Interfaces / contracts
<!-- real signatures, schemas, error modes -->

## Out of scope / assumptions
