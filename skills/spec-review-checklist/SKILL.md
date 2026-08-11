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
- [ ] Every requirement declares a **`binding:`**, and the declaration matches the behavior:
      - **`e2e`** — observable by an end user. Verified by a **journey** test covering it together
        with the other `e2e` requirements on the same path — **not by its own test**.
      - **`integration`** — observable *only* under concurrency, fault injection, or schema
        migration. Gets its own test, and **states in one sentence why an e2e cannot see it.**
        A missing sentence is a missing justification: route it back.
      - **`none`** — a structural or build-time property. Gets **no test**; it is enforced by CI, by
        a type checker, or not at all.
- [ ] Acceptance criteria are stated for what the binding actually verifies — a pass condition and
      at least one failure/edge condition for each `integration` requirement, and for each journey
      that carries a group of `e2e` ones. `none` requirements need neither.

> **Why this replaced "paired criteria on every requirement".** That rule made suite size a
> mechanical function of requirement count: one measured feature turned 288 requirement ids into 458
> tests, 4.87 lines of test per line of source. It manufactured tests from **ids**, not from
> **risk**, and nothing anywhere in the pipeline pushed back. The tiers restore the missing question:
> *is this requirement worth binding, and at what price?* The trade is deliberate and named — a red
> journey tells you which journey broke, not which line. Accept that; it is what the granularity cost.

## Cost and blast radius
The pipeline has no other stage that can see these. Every later stage — fidelity, cross-family
review, verification — reads for **correctness**, and none of the three below is incorrect.

- [ ] **Does any test this requirement implies spawn a subprocess, or does its runtime scale with the
      size of the suite?** If yes it does not belong in the default suite: mark it
      `@pytest.mark.subprocess("<why>")` (or the stack's equivalent) with a justification, or restate
      the requirement so it is observable in-process. `scripts/subprocess_check.py` answers the first
      half mechanically — run it, do not guess.
- [ ] **Is this behavior observable by an end user?** If yes it is `binding: e2e` and belongs to a
      journey, not to a test of its own.
- [ ] **Does any new line here reject a value an existing caller already passes?** A spec once called
      an extension "purely additive" while strengthening a locked constructor's precondition, and
      broke sixteen callers. Additive means *no existing call changes meaning* — check the callers,
      do not take the word.

## Coherence
- [ ] The spec does not contradict `overview.md`'s `## Contracts and Decisions` header (the stable
      section that carries the interfaces, decisions and invariants a spec can contradict).
- [ ] The spec does not contradict a contract on the **immediately prior phase's contract card**
      (`handover.md`, the *Binding contracts* table).
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

## Re-gating a spec that is already implemented
A spec that has **already been approved and has reached the implementer** (`review_status: approved`
and `status: in-progress`/`done`) is re-gated **on its changes only**. Text that was approved before
and has not changed is **out of bounds** — you may read it for context, but it is not a finding.

This is not leniency, it is arithmetic. One spec went through automated review three times on one
rubric with one model: REVIEW, then REVIEW, then a **NO-GO naming requirements the same model had
passed twice, unchanged**. A gate verdict is a sample from a distribution, not a fact about the
artifact — and variance in a gate that fails closed costs far more than variance in one that fails
open. Re-reading settled text just re-rolls the dice on work that is already built.

Mechanically: `scripts/spec_gate_cache.py` keeps the body each gate last judged, and
`scripts/hook_spec_review.sh` hands the automated reviewer a bundle — the approved body as
reference, the current spec, and a `## CHANGES SINCE APPROVAL` diff. When no kept body is available
the bundle is not built and the whole spec is gated, which is the safe direction.

**What still warrants a full re-gate**, because each one can invalidate text that did not change:
- the **requirement set** changed — an id added, removed, renumbered, or re-scoped;
- **Scope**, **Interfaces / contracts**, or `work_kind` changed;
- a `binding:` changed, since that decides whether a requirement is tested at all;
- the Verifier routed the phase back with a **coverage gap** — the question is what the spec failed
  to require, which unchanged text is exactly where to look;
- the spec was never approved. **A first gate is always full**, in every case.

## Reviewer self-test (grill-me drives these)
- Can you explain both summaries to a non-technical stakeholder without defining additional terms?
- For each `integration` requirement: can you state the exact test that would fail if it were
  violated, *and* why an end-to-end journey could not have seen the same failure?
- For each `e2e` requirement: which journey covers it?
- For each `none` requirement: what enforces it, if anything — and are you content that nothing does?
- Can you point to where in `overview.md` each key decision is justified?
- If you can't answer, the spec is not ready — route it back to the Spec Writer.
