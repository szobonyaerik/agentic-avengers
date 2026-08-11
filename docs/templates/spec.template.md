---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
depends_on: []
work_kind: greenfield    # <!-- greenfield | migration | refactor — the implementer's tdd mode.
                              CARRIED HERE, not looked up in task-analysis.md: a stage that fires per
                              spec must never open a second document for one field. -->
status: draft
review_status: pending   # <!-- only the human reviewer sets this to 'approved', after grill-me -->
criticality: standard    # <!-- standard | critical — 'critical' runs the Breaker on this phase -->
readers: fidelity gate @ on write; spec-review @ per spec; implementer @ once; verifier bundle @ changed specs only
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
<!-- A SINGLE verifiable behavior each; not a bundle. Every one declares a `binding:`, which decides
     whether and where it is verified:
       e2e         — an end user can observe it. Carried by a JOURNEY below, never its own test.
       integration — visible ONLY under concurrency, fault injection, or schema migration. Gets its
                     own test, and must say in one sentence why an e2e cannot see it.
       none        — structural or build-time. NO test. Name what enforces it (a CI job, a type
                     checker, or nothing — "nothing" is often the right answer).
     Default to e2e. This tiering is what keeps suite size tied to risk instead of to id count. -->
- R<n>.<k>.1 — `binding: e2e` — …
- R<n>.<k>.2 — `binding: integration` — … Why an e2e cannot see it: …
- R<n>.<k>.3 — `binding: none` — … Enforced by: …

## Journeys
<!-- One per user-observable path, each covering SEVERAL e2e requirements. This is where the e2e tier
     is verified. Do not also ask for one test per requirement. -->
- J1 — <!-- what the user does, end to end --> — covers R<n>.<k>.1, …

## Acceptance criteria
<!-- For each `integration` requirement and each journey: a pass condition AND at least one
     fail/edge condition — the implementer needs both to write the red→green slice.
     `binding: none` requirements get none; there is nothing to run. -->
- R<n>.<k>.2 — passes when: …; fails when: …
- J1 — passes when: …; fails when: …

## Interfaces / contracts
<!-- real signatures, schemas, error modes -->

## Out of scope / assumptions
