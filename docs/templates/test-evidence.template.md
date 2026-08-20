---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
stage: test-evidence
created: YYYY-MM-DD
readers: implementer @ on route-back only; avenger-verifier @ on route-back only
---

<!-- The sidecar to test-mapping.md. `test-mapping.md` is the TABLE and nothing else; everything
     below used to live inside it, where it was re-read on every verifier attempt. One measured
     feature carried 285 KB — 59.3% of its test-mappings — as prose outside any table, on every
     attempt.

     Nothing is deleted. This file is committed and durable; it is simply opened only when it is
     worth its tokens, which is on a route-back: the implementer fixing a finding, and the Verifier
     checking the fix. -->

# <n>.<k>-<subslug> — test evidence

## Mutation evidence
<!-- Only if the project turned the mutation gate on. Surviving mutants, what each one proved, and
     the case added for it. -->

## Route-back history
<!-- Finding id → what the Verifier said → what changed. One entry per route-back. -->

## Build order
<!-- The vertical slices, in the order they went red → green, where the order itself is load-bearing. -->

## Deviations from the spec
<!-- What was implemented differently and why, and whether it was routed back to the spec-writer. -->

## Tests covering no requirement, disclosed
<!-- Anything in the phase's test tree that maps to no R<n>.<k>.<m>, named openly rather than
     quietly excluded. -->
