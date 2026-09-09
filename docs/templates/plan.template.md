---
feature: <feature>
type: implementation-plan
status: draft
created: YYYY-MM-DD
readers: avenger-spec-writer @ per spec; phase-handover @ per phase (the next phase's entry only); carried_items.py @ per deferral (this phase's Done when table only)
---

# Implementation Plan: <Feature>

## Overview
<!-- 1–3 sentences; reference overview.md, don't restate it -->

## Phase plan (dependency / risk order)

### Phase 1 — <slug>
- **Goal**: <!-- the cohesive, independently verifiable increment -->
- **Depends on**: none
- **Candidate specs**:
    - 1.1 <subslug> — <one line>
    - 1.2 <subslug> — <one line>   <!-- a phase may have several specs; a simple phase has only 1.1 -->
- **Scope**: in — …; out (deferred) — …
- **Work kind**: greenfield | migration | refactor
  <!-- migration and refactor are ZERO-BEHAVIOUR-CHANGE: the Spec Writer carries this into every
       spec's `work_kind`, and scripts/behaviour_drift.py then holds the phase's diff to it - each
       literal, operator or control-flow edge that changes cites the requirement that authorised
       it. Any intended behaviour change in such a phase is greenfield work with its own
       requirement. Say it here, at the phase, before a spec is written. -->
- **Touches**: <!-- real paths -->
- **Done when**: <!-- outcome that proves the phase works; the Verifier runs once here. KEEP THE
  PROSE - it is what a human reads. The table below is the same Done when in the form a gate reads:
  a finding may be DEFERRED out of this phase only when the requirement it is against carries
  none of these conditions (scripts/done_when.py, scripts/carried_items.py defer). Without the
  table the phase cannot defer at all, and nothing else about how it closes changes. One row per
  condition, stable ids; the Spec Writer tags the requirements that make each one true with
  `done_when: DW-<n>` on their declaration line. REPLACE THE PLACEHOLDER ROW - a `<n>` id is not a
  condition.
  `verification` is a CLOSED vocabulary of two and EVERY row declares one (issue #116):
  `gate-verifiable` - some gate in this pipeline can evaluate it - or `human-observed` - none can,
  because it is what a person sees, hears or judges. A `human-observed` condition is then carried
  verbatim in the verdict's `unverified_by_gate` and named on the contract card, so `pass` reads as
  "pass on everything I can check, and here is what I cannot" instead of as a criterion that was
  checked. `scripts/verifier_precheck.py` refuses a row that declares neither. -->

  | done-when | outcome | verification |
  |-----------|---------|--------------|
  | DW-<n> | <one condition, as something someone can watch happen> | <gate-verifiable or human-observed> |

## Risks & mitigations
<!-- how the ORDERING mitigates risk -->

## Notes for the Spec Writer
<!-- shared contracts/naming/sequencing that must stay consistent across phases and specs -->
