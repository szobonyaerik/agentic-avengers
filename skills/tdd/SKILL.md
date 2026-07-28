---
name: tdd
description: The implementer's test-driven development procedure — write tests and code together in a vertical red→green loop, one seam at a time, driven by the spec. Use whenever a backend-architect or frontend-architect implements a spec (greenfield) or migrates existing behavior. Replaces the separate test-author. Always work one slice at a time; never write all tests up front.
---

# tdd — the implementer's red→green loop

You (the implementer) author **both the tests and the code** for a spec, in a **vertical** loop:
one seam → one failing test → just enough code to pass → repeat. There is no separate test-author.
The tests you write are reviewed independently by the Verifier (a different model family) and, once the
phase passes, they **lock** (see `pipeline-conventions`: *locked-after-verify*).

This is language-agnostic. Apply it in the project's stack (Python/Java/C++/TypeScript/…); the examples
below are illustrative pseudocode, not a required language. Ground names and vocabulary in the codemap
(`codebase/MOC.md`) and any `CONTEXT.md`/ADRs in the area you touch.

## The seams — where tests go (agreed up front, in the spec)
A **seam** is the public boundary you test at: the interface where you observe behavior without reaching
inside. Tests live at seams, never against internals.

In this pipeline the seams are **already agreed in the spec**: each requirement
(`R<n>.<k>.<m>`) and its paired pass/fail acceptance criteria define the observable behavior, while
the spec's interfaces/contracts name the public boundary where it is tested. You do **not**
re-negotiate seams interactively. If that boundary is genuinely untestable as written, the spec is
wrong: stop and route it back to the spec-writer; do not invent a seam.

## Choose your mode from `work_kind` (in `task-analysis.md`)

### Greenfield (`work_kind: greenfield`) — red before green
New behavior, no prior contract. For **each requirement `R<n>.<k>.<m>`, one at a time**:
1. Write **one failing test** at the requirement's seam, straight from its acceptance criteria — a
   positive case first, then negative/edge cases as separate slices. Confirm it is **RED** (fails now).
   A test that passes before you write code is testing nothing.
2. Write **just enough code** to make it green. No speculative features, no anticipating later tests.
3. Record the test → requirement mapping in `test-mapping.md`.
4. Repeat for the next slice. Each test is a **tracer bullet** that responds to what the last slice
   taught you — never write the whole suite first (that is *horizontal slicing*, an anti-pattern below).

### Migration (`work_kind: migration`) — parity first
Behavior already exists and must be **preserved**. The **existing test suite is the contract** — do not
re-author it.
1. **Before editing, run the relevant existing suite and record the baseline result.** After each
   migrated slice, run the same suite again. Matching the known baseline (with no new failures) is
   the parity proof; a pre-existing failure is evidence only when it was captured before the change.
2. **Only** where a spec requirement (`R<n>.<k>.<m>`) exercises behavior the existing suite genuinely
   cannot reach — a real gap on a **critical/agreed seam** — write a targeted **characterization test**
   that pins the *current observable behavior*. Do not characterize exhaustively; test only at the
   pre-agreed critical seams (this is where testing effort is worth spending).
3. Preserve assertions: when a test must move to the new stack/framework (e.g. swap the old test client
   for the new), do **not** change what it asserts. A faithfully ported test is a *parity test*.
4. If an existing test asserts behavior that is genuinely wrong, **flag it** — don't silently "fix" it
   into the migration.
5. Record every ported/characterization test → requirement in `test-mapping.md`, and note gaps.

### Refactor (`work_kind: refactor`) — baseline first, behavior unchanged
Use the migration parity-first procedure, but there is no framework/stack port: capture the relevant
suite's baseline before editing, refactor in small slices, and rerun it after each slice. Add a
characterization test only when a critical behavior required by the spec is not observable in the
existing suite. Any intentional behavior change is greenfield work and must be specified explicitly,
not hidden inside the refactor.

## What a good test is
Tests verify **behavior through public interfaces**, not implementation details. Code can change
entirely; a good test shouldn't. It reads like a specification — `user can checkout with valid cart`
tells you exactly what capability exists — and survives refactors because it doesn't care about
internal structure. Expected values must come from an **independent source of truth** (a known-good
literal, a worked example, the spec) — never recomputed the way the code computes them.

See [tests.md](tests.md) for good/bad examples and [mocking.md](mocking.md) for mocking guidelines.

## Anti-patterns (the Verifier reads your tests for exactly these)
- **Implementation-coupled** — mocks internal collaborators, tests private methods, or verifies through
  a side channel (querying the database instead of using the interface). The tell: the test breaks when
  you refactor but behavior hasn't changed.
- **Tautological** — the assertion recomputes the expected value the way the code does
  (`assert add(a, b) == a + b`, a hand-derived snapshot, a constant asserted equal to itself), so it
  passes by construction and can never disagree with the code. Expected values must be independent.
- **Horizontal slicing** — writing all tests first, then all implementation. Bulk tests verify
  *imagined* behavior; they go insensitive to real changes and lock you into test structure before you
  understand the implementation. Work in **vertical slices** — one test → one implementation → repeat.

## Rules of the loop
- **Red before green** (greenfield). Write the failing test first, then only enough code to pass it.
- **One slice at a time.** One seam, one test, one minimal implementation per cycle.
- **Refactoring is not part of the loop.** Clean up after green, not during red→green.
- **Trace the spec work.** Every test authored or ported for the spec maps to exactly one
   `R<n>.<k>.<m>` in `test-mapping.md`; do not burn effort retroactively mapping an untouched inherited
   migration suite.
- **Never weaken a test to go green after the phase is verified.** Before the Verifier passes, you own
  the tests and may reshape them as the design teaches you; after it passes they are locked, and
  weakening them requires re-verification (see `pipeline-conventions`).

## Hand-off
When every requirement in the spec is green and mapped, hand the phase to the **Verifier** (a different
model family). The Verifier runs the full suite, traces coverage, and independently reviews your tests
for the anti-patterns above. If it flags a gamed or wrong test, it routes back to **you** to rewrite —
fixes stay in the implementer, not a separate agent.

---
*Adapted for the agentic-avengers pipeline from Matt Pocock's TDD skill
(github.com/mattpocock/skills, `skills/engineering/tdd`).*
