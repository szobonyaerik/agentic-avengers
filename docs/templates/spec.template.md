---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
depends_on: []
work_kind: greenfield    # <!-- greenfield | migration | refactor — the implementer's tdd mode.
                         #      CARRIED HERE, not looked up in task-analysis.md: a stage that fires
                         #      per spec must never open a second document for one field.
                         #      migration and refactor are ALSO the zero-behaviour-change contract:
                         #      scripts/behaviour_drift.py then holds the phase's whole diff to it,
                         #      and a literal, operator or control-flow edge that changes without
                         #      citing a requirement (`# behaviour: R<n>.<k>.<m>` on the statement)
                         #      is a BLOCKING finding. An intended behaviour change here is
                         #      greenfield work: give it a requirement in this spec and cite it. -->
status: draft
spec_gate: pending       # <!-- pending | approved | blocked — THE machine gate, set by
                         #      scripts/hook_spec_gate.sh. It replaced `fidelity_verdict` and the
                         #      automated half of spec-review: two rubrics asking overlapping
                         #      questions of these same bytes at the same moment, which once passed
                         #      one and failed the other on byte-identical text. -->
review_status: pending   # <!-- only the human reviewer sets this to 'approved', after grill-me.
                         #      Under SPEC_REVIEW_MODE=auto the gate carries it, because in an
                         #      unattended run the machine gate is the whole wall. -->
criticality:             # <!-- standard | critical — 'critical' runs the Breaker on this phase,
                         #      and the phase then does NOT close without the Breaker's
                         #      breaker.json record beside verdict.json.
                         #      REQUIRED, and shipped BLANK on purpose: a blank value resolves to
                         #      `critical` and is announced on stderr as the default it is
                         #      (scripts/criticality.py, issue #101), so a spec nobody edited gets
                         #      the STRONGER pipeline. It used to resolve to `standard`, so a spec
                         #      that never wrote this line silently lost its adversarial stage —
                         #      and a template that pre-filled `standard` made that opt-out the
                         #      template's decision rather than an author's.
                         #      Writing `standard` deliberately is how a phase opts out. -->
readers: spec gate @ on write; implementer @ once; avenger-verifier @ per phase; carried_items.py @ per deferral (requirement `done_when:` tags only)
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
<!-- AT MOST 12. The cap is counted by scripts/requirement_cap.py BEFORE any model sees this spec,
     and over it the spec SPLITS into siblings <n>.<k> — it is never rejected for being large. That
     order matters: the gate this replaced had no size ceiling anywhere, so the only response
     available to a rejected spec was more text, and one spec went 25k -> 51k characters across four
     rejected rounds.

     A SINGLE verifiable behavior each; not a bundle. Every one declares a `binding:`, which decides
     whether and where it is verified:
       e2e         — an end user can observe it. Carried by a JOURNEY below, never its own test.
       integration — visible ONLY under concurrency, fault injection, or schema migration. Gets its
                     own test, and must say in one sentence why an e2e cannot see it.
       none        — structural or build-time. NO test. Name what enforces it (a CI job, a type
                     checker, or nothing — "nothing" is often the right answer).
     Default to e2e. This tiering is what keeps suite size tied to risk instead of to id count.

     A requirement that makes one of the phase's Done when conditions true (the `| done-when |`
     table under this phase in plan.md) also carries `done_when: DW-<n>[, DW-<m>]` on the same line.
     That tag is what decides whether a Verifier finding against the requirement may be DEFERRED to a
     later phase (scripts/done_when.py): tagged, it blocks and stays this phase's to fix; untagged,
     it may leave with its measurement. Every condition the plan declares must be carried by at
     least one requirement in the phase, or nothing can be deferred from it. -->
- R<n>.<k>.1 — `binding: e2e` — `done_when: DW-<n>` — …
- R<n>.<k>.2 — `binding: integration` — … Why an e2e cannot see it: …
- R<n>.<k>.3 — `binding: none` — … Enforced by: …

## Journeys
<!-- One per user-observable path, each covering SEVERAL e2e requirements. This is where the e2e tier
     is verified. Do not also ask for one test per requirement. -->
- J1 — <!-- what the user does, end to end --> — covers R<n>.<k>.1, …

## Acceptance criteria
<!-- For each `integration` requirement and each journey: a pass condition AND at least one
     fail/edge condition — the implementer needs both to write the red→green slice — AND a
     `drives:` field naming what the test pushes through: the seam, command or planted input.
     "Sweep both adapters for this class" is satisfied by reading; `drives:` is what cannot be.
     scripts/hook_spec_gate.sh refuses a criterion without it before any paid call (issue #96).
     A criterion nothing can drive (a visual, an external system) declares `drives: undriven (<why>)`
     rather than being refused; what that declaration must carry is issue #116's decision.
     `binding: none` requirements get none; there is nothing to run. -->
- R<n>.<k>.2 — passes when: …; fails when: … — drives: <seam | command | planted input>
- J1 — passes when: …; fails when: … — drives: <the user-facing entry point, end to end>

## Interfaces / contracts
<!-- real signatures, schemas, error modes -->

## Out of scope / assumptions
