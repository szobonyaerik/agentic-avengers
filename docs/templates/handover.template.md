---
feature: <feature>
phase: <n>-<slug>
status: done
verified: YYYY-MM-DD
---

# Phase <n>-<slug> — Handover

## What this phase delivered

## Specs in this phase
| Spec | Status | Verifier |
|------|--------|----------|
| <n>.1-<subslug> | ✅ done | pass |

## Decisions & deviations

## Gate record
- Verifier: pass | pass (bypassed) | fail   <!-- verdict.json in this phase dir -->
- Test-quality review: clean | findings routed; scope: targeted | expanded (<reason>)
- Mutation: n/a (off) | <score> (policy: <enforce|advisory>)
- Bypasses (break-glass): none | <scope> / who / when / reason   <!-- copy FROM gate-overrides.log; only scripts/bypass_log.sh writes it -->
  - Whole-gate (`GATE_BYPASS`): none | gate / who / when / reason
  - Per-finding (verdict.json `break_glass`): none | finding-id / who / when / reason

## Contracts delivered (must not be broken by later phases)

## Next step
> <!-- exact instruction for the next phase/session -->
