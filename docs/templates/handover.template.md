---
feature: <feature>
phase: <n>-<slug>
stage: handover
model: <model>
created: YYYY-MM-DD
status: green
next: <next-phase-slug | e2e | ship>
verdict: pass | pass (bypassed) | fail   <!-- from verdict.json in this phase dir -->
mutation: n/a (off) | <score> (policy: <enforce|advisory>)
readers: avenger-spec-writer @ per spec (prior cards); spec-review @ the immediately prior card; e2e-author @ feature close
---

<!-- THIS IS A CONTRACT CARD, NOT A RECORD. HARD CAP: 6144 bytes, checked by
     `python3 scripts/doc_read_path.py check .` — over the cap is a fail.
     Everything this card cannot carry goes to handover-archive.md beside it, which NO stage reads.
     Nothing is deleted; it is relocated off the read path.
     Two tests before any paragraph enters this file — see skills/phase-handover:
       1. Restatement — is the fact already in verdict.json / gate-overrides.log / docs/lessons/ /
          git log? Then link to it; do not re-narrate it.
       2. Reader — name the agent that reads this paragraph AND the decision it changes. If you
          cannot name both, it belongs in the archive. -->

# Phase <n>-<slug> — contract card

<!-- 5 LINES MAX: what this phase delivered and what the next one depends on. Not 5 paragraphs. -->

## Binding contracts (later phases must not break these)
<!-- The one thing that exists nowhere else. One row each — no prose. -->
| contract | shape | defined in |
|----------|-------|------------|
| <name>   | <signature / fields / error mapping> | <repo-relative path> |

## Decisions locked in
<!-- One line each, naming the file that ENFORCES the decision. Reasoning goes in the archive. -->
- <decision> — enforced by `<path>`

## Gate record
- Verifier: pass | pass (bypassed) | fail
- Test-quality review: clean | findings routed; scope: targeted | expanded
- Mutation: n/a (off) | <score> (policy: <enforce|advisory>)
- Bypasses: none | <scope> / who / when / reason   <!-- copy FROM gate-overrides.log; only scripts/bypass_log.sh writes it -->

## Artifacts
<!-- Link every artifact that exists. A link is ~80 bytes; the links are what the card is FOR, and
     omitting one makes this section lie by omission. That is not a saving. -->
- verdict:        docs/features/<feature>/phases/<n>-<slug>/verdict.json
- specs:          docs/features/<feature>/phases/<n>-<slug>/specs/
- tests:          tests/<feature>/<n>-<slug>/
- implementation: docs/features/<feature>/phases/<n>-<slug>/implementation-report.md
- test-execution: docs/features/<feature>/phases/<n>-<slug>/test-execution-report.md
- archive:        docs/features/<feature>/phases/<n>-<slug>/handover-archive.md   <!-- not on the read path -->

## Open items
<!-- A table, not a narrative. Measured: of 8 items carried as prose across 53.6 KB, exactly one was
     ever picked up by a later phase. The id carried them, not the story. -->
| id | one-line title | where the detail lives |
|----|----------------|------------------------|
| OBS-<n> | <title> | verdict.json#observations[<n>] |

## Next phase
> <next-phase-slug> — needs from this phase: <the one or two things it depends on>.
