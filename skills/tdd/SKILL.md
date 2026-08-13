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
(`R<n>.<k>.<m>`) and its acceptance criteria define the observable behavior, while
the spec's interfaces/contracts name the public boundary where it is tested. You do **not**
re-negotiate seams interactively. If that boundary is genuinely untestable as written, the spec is
wrong: stop and route it back to the spec-writer; do not invent a seam.

## The spec decides what gets a test — read `binding:` before you write one

Every requirement declares a `binding:`, set by the spec-writer and settled at the spec-review gate.
It is not advisory and it is not yours to re-open: writing a test the binding does not call for is
the same defect as skipping one it does.

| `binding:` | What you write | Where it maps in `test-mapping.md` |
|---|---|---|
| `e2e` | **No test of its own.** It is covered by the **journey** the spec groups it into, alongside the other `e2e` requirements on that path. | the journey's row lists every id it covers, `level: e2e` |
| `integration` | **One test**, at the seam, driving the failure the spec says an e2e cannot see — concurrency, fault injection, or migration. | its own row, `level: integration` (or `narrow` with the written justification) |
| `none` | **Nothing.** CI, a type checker, or nothing at all enforces it. | no row |

**One journey, many requirements.** Write the journey once and add ids to its row as you cover them
— do not fork it per requirement. The trade is deliberate: a red journey tells you which journey
broke, not which line. That is what the granularity bought, and it was bought on purpose, because
per-requirement pairing once turned 288 requirement ids into 458 tests.

Traceability is unchanged in substance: **every requirement with `binding: e2e` or `integration`
appears in at least one `test-mapping.md` row, and every test lists the ids it covers.** A journey
lists several; an integration test lists one. A requirement with no row and no `binding: none` is a
coverage gap and the Verifier will route it back.

**No test may spawn a subprocess** unless it carries `@pytest.mark.subprocess("<why a real process is
required>")` — or the stack's equivalent — and the justification is not optional. The marker also
reads on a test **class**, and when every test in a file (or a class) spawns, declare it once as
`pytestmark = pytest.mark.subprocess("<why>")` in that module or class body rather than per test.
The nearest declaration wins: method over class, class over module.
Register the marker in the project's pytest config so an unregistered spelling shows up as a warning
instead of being silently ignored:

```ini
markers =
    subprocess(why): this test spawns a real process, for the stated reason.
```

`scripts/subprocess_check.py` enforces this from the spec-gate hook, on every spec write in both
modes; it is the only stage that can see the cost, since every reading stage reads for correctness
and a subprocess is not incorrect. It
scans `$SUBPROC_CHECK_PATHS` (os.pathsep-separated), falling back to `tests/`; set it in the
project's `.env` when the tests are elsewhere, or the gate scans nothing and says so on stderr.

**It blocks on the files this change touched.** A spawner in a test you did not open is counted and
named, never blocking — repository-wide, it refused every spec write of one measured phase over 17
undeclared spawners in locked phases nobody was working on. So declaring one is a thing you do for
your own tests as you write them, and `subprocess_check.py --all` is how you audit the rest
deliberately.

## `test-mapping.md` is a table; the evidence lives beside it

`test-mapping.md` carries **the table and nothing else** — requirement id, test names, `level`, one
sentence of why. Everything else you would otherwise write there goes to **`test-evidence.md`** in
the same directory: mutation evidence, route-back history, build order, deviations from the spec, and
tests covering no requirement. **Nothing is deleted; the sidecar is committed.**

The reason is where each file sits on the read path. `test-mapping.md` is re-bundled to the
cross-family reviewer on every verifier attempt, so a phase pays for it once per attempt; one
measured feature had **59.3% of its test-mappings as prose outside any table** — 285 KB — riding
along on every one of those attempts. `test-evidence.md` is opened **on route-back only**, by the
implementer fixing the finding and by the Verifier checking it, which is exactly when that prose is
worth its tokens.

**Both halves declare who reads them, in their own frontmatter** — that is the rule that stops the
cost coming back (`skills/pipeline-conventions` § *The document read path*), and a check enforces it,
so a mapping written without it fails the gate. Templates: `docs/templates/test-mapping.template.md`
and `docs/templates/test-evidence.template.md`.

```markdown
---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
stage: test-mapping
readers: avenger-verifier @ per phase; verifier bundle @ changed specs only
---
| requirement id(s) | test name(s) | level | why |
```

```markdown
---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
stage: test-evidence
readers: implementer @ on route-back only; avenger-verifier @ on route-back only
---
```

## Choose your mode from `work_kind` (in the spec's own frontmatter)

### Greenfield (`work_kind: greenfield`) — red before green
New behavior, no prior contract. Work through the spec's **journeys and `integration` requirements,
one at a time** — never its raw requirement list, which includes ids that get no test of their own:
1. Write **one failing test** at the seam, straight from the acceptance criteria — for a journey, the
   user's path end to end; for an `integration` requirement, the failure the spec says an e2e cannot
   see. Add negative/edge cases as separate slices where the criteria call for them. Confirm it is
   **RED** (fails now). A test that passes before you write code is testing nothing.
2. Write **just enough code** to make it green. No speculative features, no anticipating later tests.
3. Record the test → requirement ids in `test-mapping.md`. A journey's row grows as it covers more.
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
- **Unrealistically-shaped external identifiers** — a fixture whose ids for an *external* system (a
  chat or user id from another platform, an account number, a token, a key) have a shape no real
  deployment produces. **1,009 tests passed** against Telegram ids around 970 million while real
  supergroup ids are an order of magnitude larger and the column was `int32`; a `/setkey` refusal
  raised `DataError: value out of int32 range` before it could fire, so the pasted credential stayed
  visible in the group. Every one of those tests was green and none could see it. Where a spec pins
  an example for an external identifier, use **that** shape; where it does not, use a value a real
  deployment produces — at the magnitude, length and character set the real thing has — and prefer a
  named constant in the fixture over a literal, so the shape has one place to be corrected.
- **Horizontal slicing** — writing all tests first, then all implementation. Bulk tests verify
  *imagined* behavior; they go insensitive to real changes and lock you into test structure before you
  understand the implementation. Work in **vertical slices** — one test → one implementation → repeat.

## Rules of the loop
- **Red before green** (greenfield). Write the failing test first, then only enough code to pass it.
- **One slice at a time.** One seam, one test, one minimal implementation per cycle.
- **Refactoring is not part of the loop.** Clean up after green, not during red→green.
- **Trace the spec work.** Every test authored or ported for the spec lists the `R<n>.<k>.<m>` ids it
   covers in `test-mapping.md` — one for an `integration` test, several for a journey — and every
   requirement not marked `binding: none` appears in at least one row. Do not burn effort
   retroactively mapping an untouched inherited migration suite.
- **Do not add a test the spec's bindings did not ask for.** If a behavior looks unbound, that is a
   route-back to the spec-writer, not a test you write on your own initiative. "It already works" is
   indeed the state that precedes a silent regression — and there are infinitely many correct
   behaviors, so that reasoning without a budget justifies writing tests forever. The budget is the
   spec.
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
