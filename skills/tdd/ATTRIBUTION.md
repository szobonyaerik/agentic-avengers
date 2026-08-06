# Attribution

`SKILL.md`, `tests.md` and `mocking.md` in this directory originate from Matt Pocock's skills
collection and reached this repo via the `klm-agentic-pipeline`, which adapted them.

- **Upstream**: https://github.com/mattpocock/skills — `skills/engineering/tdd`
- **License**: MIT (see upstream `LICENSE`)
- **Adapted by**: `klm-agentic-pipeline`, then ported here 2026-07-28

## What the adaptation changed

The upstream skill assumes an interactive human and a TypeScript codebase. The pipeline version:

- **Replaces interactive seam negotiation with the spec.** Upstream says *"before writing any test,
  write down the seams under test and confirm them with the user."* Here the seams are already agreed:
  each `R<n>.<k>.<m>`, its `binding:` and the acceptance criteria that binding calls for define the
  observable behavior, and the spec's interfaces/contracts name the boundary. An untestable boundary is a spec defect — route it back to
  the Spec Writer rather than inventing a seam.
- **Adds the three `work_kind` modes inline** — greenfield (red→green), migration (parity-first) and
  refactor (baseline-first, behavior unchanged). These were previously separate skills in this repo
  (`migration-test-author`, `brownfield-test-author`); folding them into one skill is the KLM shape and
  those two directories were deleted.
- **Makes the examples language-agnostic** (pseudocode, not TypeScript), because the pipeline targets
  Python/Java/C++/TypeScript alike.
- **Names the Verifier as the reader of the anti-patterns** — the three anti-patterns are exactly what
  `agents/avenger-verifier.md` reads the implementer's tests for, and adds *locked-after-verify*.

Keep this file in step if the skill is re-synced from either upstream.
