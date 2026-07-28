---
name: spec-review-checklist
description: The concrete criteria a human uses to review a spec before implementation begins. Use during the human spec-review gate, together with grill-me, so a reviewer (especially one new to the pipeline) has an objective bar to check against instead of vibes. Always apply this before setting a spec's review_status to approved.
---

# Spec-review checklist

The spec review is the **last gate before implementation begins**. A bad spec here becomes a wrong
test contract, because the implementer writes tests straight from these requirements. Run `grill-me`
against the reviewer using these criteria; only set `review_status: approved` when every item is a
defensible "yes".

## Verifiability
- [ ] Every requirement is a **single, observable behavior** (not a bundle).
- [ ] Every requirement has a stable id `R<n>.<k>.<m>`.
- [ ] Every requirement has **paired acceptance criteria**: at least one pass AND one fail/edge
      condition. (No pass-only requirements — the implementer needs both to write the red→green slice.)

## Coherence
- [ ] The spec does not contradict `overview.md` (architecture, interfaces, decisions).
- [ ] The spec does not contradict a contract a prior phase's `handover.md` marked delivered.
- [ ] The spec honors "Notes for the Spec Writer" in `plan.md`.

## Human summaries
- [ ] `Phase summary` and `Spec summary` both exist and are each 1–3 sentences a non-technical
      stakeholder can understand without reading the technical sections.
- [ ] Each summary states the outcome, why it matters, and any important boundary; neither introduces
      a promise that the scope, requirements, or acceptance criteria do not support.
- [ ] Neither summary contains code, paths, signatures, requirement IDs, or unexplained acronyms;
      unavoidable domain or technical terms are explained inline in ordinary words.
- [ ] The phase summary accurately reflects the phase goal and scope in `plan.md` and uses the exact
      same wording in every sibling spec.

## Scope
- [ ] Scope in/out is explicit; nothing is silently assumed in scope.
- [ ] The spec is **independently testable** without reading sibling specs.

## Concreteness
- [ ] Real file paths, signatures, and data types — no hand-waving, no invented structure.

## Migration-specific (work_kind: migration)
- [ ] The spec names the **existing tests** for the surface and the **parity** that must be preserved
      (the existing suite is the contract — the implementer runs it, doesn't re-author it).
- [ ] Coverage gaps (requirements with no existing test) are called out and scoped to the **critical
      seams** where the implementer will add characterization tests — not an exhaustive re-test.

## Reviewer self-test (grill-me drives these)
- Can you explain both summaries to a non-technical stakeholder without defining additional terms?
- Can you state, for each requirement, the exact test that would fail if it were violated?
- Can you point to where in `overview.md` each key decision is justified?
- If you can't answer, the spec is not ready — route it back to the Spec Writer.
