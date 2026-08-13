---
feature: <feature>
phase: <n>-<slug>
stage: handover
model: <model>
created: YYYY-MM-DD
status: green
next: <next-phase-slug | e2e | ship>
verdict: pass | pass (bypassed) | fail   <!-- from verdict.json in this phase dir -->
mutation: n/a (off) | <score> (policy <enforce|advisory>)
readers: avenger-spec-writer @ per spec (prior cards); spec gate @ the immediately prior card; spec-review (human grill) @ the immediately prior card; e2e-author @ feature close
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
- Mutation: n/a (off) | <score> (policy <enforce|advisory>)
- Bypasses: none | <scope> / who / when / reason   <!-- copy FROM gate-overrides.log; only scripts/bypass_log.sh writes it -->
- Amendments: none | <A-id> <R ids touched> [security]   <!-- ids from amendments.json; this is where a later phase sees the phase moved after it was first verified -->
- Exceptions: none | <X-id> <rule> / <subject> — <one clause>   <!-- ids, rule and subject from exceptions.json (`applicability.py list <phase-dir>`); the reason prose stays there and in gate-overrides.log. Recording an exception is MANUAL, so a forgotten one is invisible until a later phase wedges on it; this line is what puts it in front of the next phase. -->
- Carried known-open: none | <finding-id> <one line>   <!-- verification is capped at 3 attempts, so some findings are CARRIED rather than fixed. This card is the only place they stay visible; a carried finding recorded nowhere is the cap turning into silent attrition. -->

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
<!-- EVERYTHING THIS PHASE CARRIES FORWARD, INCLUDING WHAT IT PREDICTS. A table, not a narrative:
     of 8 items carried as prose across 53.6 KB, exactly one was ever picked up by a later phase -
     the id carried them, not the story. Phase 8 wrote a correct, specific prediction into prose and
     phase 9 shipped exactly that defect, because prose is owed to nobody.
     `kind` is `open-finding` (carried at the attempt cap) or `forward-claim` (something a later
     phase must handle). The NEXT phase must answer every row - built, tested, or declined with a
     reason (scripts/carried_items.py) - and does not close until it has.
     A row, or an explicit `none` row. SILENCE IS NOT NONE, and this section is checked.
     ON THE LAST CARD (frontmatter `next:` is `e2e` or `ship`) no phase follows to answer these, so
     every `forward-claim` row must name an ISSUE instead - `#<number>` or an issue URL, anywhere in
     the row. A presence check and nothing more; this phase does not close without it. -->
| id | kind | one-line title | where the detail lives |
|----|------|----------------|------------------------|
| OBS-<n> | open-finding | <title> | verdict.json#observations[<n>] |
| FWD-<n> | forward-claim | <what a later phase must handle, and from which phase it bites> | this card |

## Next phase
> <next-phase-slug> — needs from this phase: <the one or two things it depends on>.
