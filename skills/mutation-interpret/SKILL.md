---
name: mutation-interpret
description: How to run and interpret the OPTIONAL mutation gate per language when a project has turned it on. Off by default and most teams leave it off; it is an extra signal, not the pipeline's independence mechanism (that is the Verifier's test-quality review). Use only when MUTATION_POLICY is enforce or advisory.
---

# mutation-interpret

The mutation gate is **optional and off by default** — most teams never turn it on. It is an *extra*
signal of test strength, **not** the independence mechanism: independence comes from the Verifier
reading the implementer's tests for anti-patterns. Only run anything in this skill when a project has
explicitly set `MUTATION_POLICY` to `enforce` or `advisory`. When it is `off`, run **no** mutation
tool anywhere.

**When enabled: mutation score, not coverage, is the stop signal.** Coverage says a line ran; mutation
says a test would *notice* if that line were wrong.

## Gate policy — `MUTATION_POLICY`
The gate has one control, `MUTATION_POLICY`, set per project (in `pipeline-gates.yml` env or via
`install.sh --mutation-policy`), default **off**:
- **enforce** — fail-closed: a low score fails the phase.
- **advisory** — compute + report the score in the verdict; do NOT block.
- **off** — skip the gate entirely (raise it to advisory/enforce when the team is ready).

## Recommended policy per language
| Language | Tool (example) | Recommended policy | On low score (when enforcing) |
|----------|----------------|--------------------|-------------------------------|
| Python   | cosmic-ray          | **enforce** | route survivors to the implementer; phase fails |
| Java     | PIT (pitest)        | **enforce** | route survivors to the implementer; phase fails |
| C++      | mull / Dextool      | **advisory**    | record score in verdict; do NOT block |

These are *recommendations for teams that opt in*; the default is `off`. The C++ tooling is heavy and
slow; advisory keeps the loop moving for the weakest-tooled stack without pretending the gate is as
strong there. Thresholds are project-set in `scripts/mutation/` config.

## Surviving mutants
Each survivor is a behavior no test catches. For each:
1. Name the mutant (file, line, mutation) in the verdict.
2. Route to the **implementer** (`avenger-backend-architect` / `avenger-frontend-developer`) with the specific missing
   assertion (usually a negative one).
3. The implementer adds the case, confirms it now kills the mutant. Then re-verify.

## Reporting
Always record the score, threshold, policy, and the survivor list in the Verifier verdict — even when
advisory — so the platform owner can see the real test strength per phase.
