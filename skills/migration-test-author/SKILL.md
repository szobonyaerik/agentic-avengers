---
name: migration-test-author
description: Use when authoring tests for a MIGRATION spec — port an existing suite to the new home without changing its assertions, prove parity, and freeze.
---

# migration-test-author

Author the test suite for a **migration** spec (`work_kind: migration`): behavior that already exists
and is already tested is moving to a new home. Your job is **not** to invent new expectations — it is
to carry the existing contract across intact and prove it still holds, then freeze it.

The ported suite is the frozen contract the implementer must satisfy. You never weaken it, and you
never change an assertion to make the new code pass — a genuinely wrong ported test routes back to
you, it is not reshaped.

## Inputs
- The migration spec (`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`) —
  it names the existing tests being ported and the parity bar.
- The `task-analysis.md` `work_kind: migration` block — the existing-test situation (paths, pass state).

## Procedure

1. **Inventory.** Locate every existing test that covers the behavior being migrated. List them with
   their current path and current pass/fail state. Run them once against the *current* code to record
   the baseline — this is the parity reference.

2. **Assess coverage — flag gaps.** For each `R<n>.<k>.<m>` in the spec, note whether an existing test
   already covers it. Requirements with **no** inherited test are gaps; record them explicitly. Do not
   silently leave them uncovered.

3. **Port without changing assertions (parity).** Move the inherited tests to `tests/<phase>/`,
   adapting only imports/paths/fixtures to the new structure — **the assertions stay byte-for-byte
   equivalent**. Annotate each ported test's first docstring line with the spec id and `migrated`:
   `"""spec: R2.1.3 | migrated — <what it asserts>"""`. If a test can't be ported without changing an
   assertion, that is a spec question — route it back, don't rewrite it.

4. **Characterize the gaps.** For each gap from step 2, write a characterization test that pins the
   behavior the migration must preserve (assert what the current system actually does). Mark these
   `characterized` in the docstring.

5. **Confirm parity, then freeze.** Run `pytest -q tests/<phase>/`. The ported + characterization suite
   must reproduce the baseline (same pass set). Record the mapping in `test-mapping.md` and state the
   suite is frozen.

## Why mutation matters here
Migration risk is silent regression, not a missing feature. **Mutation (cosmic-ray) is the proof** the
inherited suite still catches the bugs it used to: if a mutant survives, the ported suite lost a guard
in the move — add the specific killing case (as the Test-Author) and re-freeze.

## Level: parity beats the integration default
The pipeline's default is `level: integration`, but **parity outranks it here**. A ported test keeps
whatever level it already had — rewriting a narrow inherited test into an integration one changes its
assertions, which step 3 forbids. Record the level the test actually has; a `narrow` ported row needs
no justification beyond `ported — parity`, because the decision was made by whoever wrote it
originally, not by you.

New **characterization** tests you write for gaps (step 4) are not ported, so they DO follow the
integration default: drive them through the seam, and justify any `narrow` one.

## test-mapping.md additions
Reuse the standard table; add a `source` column for ported rows:

```markdown
| test | spec id | type | level | justification | source |
|---|---|---|---|---|---|
| test_rate_limit_rejects_over_cap | R2.1.3 | migrated | integration | — | tests_legacy/test_limits.py::test_over_cap |
| test_window_key_format | R2.1.3 | migrated | narrow | ported — parity | tests_legacy/test_limits.py::test_key |
| test_empty_window_allows_first    | R2.1.4 | characterized | integration | — | (gap — no prior test) |
```

## Done when
- Every named existing test is ported with assertions unchanged, each traced to a spec id.
- Every requirement without an inherited test has a characterization test (gaps are covered, not hidden).
- `pytest -q tests/<phase>/` reproduces the recorded baseline (parity holds).
- `test-mapping.md` records source + type + level; the suite is declared frozen.
