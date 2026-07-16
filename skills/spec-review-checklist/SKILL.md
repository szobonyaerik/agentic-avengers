---
name: spec-review-checklist
description: The bar a human reviewer defends during /spec-review — every requirement single, verifiable, ID'd, with paired criteria; no contradiction with the overview or a delivered contract; migration and refactor specs carry their mode-specific obligations.
---

# spec-review-checklist

The standard a spec must clear in the **human grill-me review** (`/spec-review`) before its tests can
lock. Use it with `grill-me`: each unmet item becomes a one-at-a-time question. A spec passes only when
**every** applicable item holds; otherwise route back to the Spec Writer (do not set `review_status: approved`).

## Universal bar (every spec)
- [ ] **Single behaviors.** Each `R<n>.<k>.<m>` is exactly one observable behavior — no "and"-compound
      requirements hiding two behaviors under one id.
- [ ] **Verifiable.** Each requirement can be turned into a concrete pass/fail test. No unmeasurable
      wording ("handles gracefully", "fast enough") survives.
- [ ] **Stable IDs.** Every requirement carries an `R<n>.<k>.<m>` id; nothing is un-ID'd.
- [ ] **Paired criteria.** Every requirement states at least one pass condition AND at least one
      failure/edge condition (the Test-Author's positive/negative pair).
- [ ] **Edge cases named.** Boundaries, duplicates, empty/oversized input, and unauthorized paths are
      specified, not left implicit.
- [ ] **No internal contradiction.** Requirements and acceptance criteria don't conflict with each other.
- [ ] **Overview coherence.** Nothing contradicts `overview.md` (interfaces, decisions, boundaries).
- [ ] **Cross-phase coherence.** Nothing redefines or breaks a contract a prior phase's `handover.md`
      marked delivered, and the spec doesn't re-claim scope a prior phase already completed.
- [ ] **Goal alignment.** The spec actually serves the feature goal from `task-analysis.md`.

## Migration specs (`work_kind: migration`) — additionally
- [ ] **Names the existing tests** being ported (real paths) and where they move to.
- [ ] **Parity is explicit.** The spec states that ported assertions are unchanged and names the parity
      bar (the baseline the ported suite must reproduce).
- [ ] **Gaps flagged.** Requirements with no inherited test are called out for characterization.

## Refactor / brownfield specs (`work_kind: refactor`) — additionally
- [ ] **Preserve-vs-change declared.** Every requirement is tagged **preserve** (must not regress) or
      **change** (deliberately new). No requirement is ambiguous about which it is.
- [ ] **Blast radius stated.** The files/modules the change may touch are named (this bounds the
      diff-scoped mutation surface).
- [ ] **Pre-existing failures excluded.** The spec does not fold already-broken behavior into its scope.

## Verdict
- **Approved** → set `review_status: approved` on the spec only when every applicable box is checked
  *and* `fidelity_verdict != NO-GO`.
- **Route back** → any unchecked box that grilling could not resolve. Record the specific items and
  return to the Spec Writer. When unsure, route back.
