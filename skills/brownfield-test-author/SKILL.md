---
name: brownfield-test-author
description: Use when authoring tests for a REFACTOR/brownfield spec — partition the blast radius into preserve vs change, characterize-and-freeze what must not regress, write fresh RED for what is changing, and scope mutation to the changed surface.
---

# brownfield-test-author

Author the test suite for a **refactor / brownfield** spec (`work_kind: refactor`): you are changing
behavior inside code that already works. The central move is **partitioning the blast radius** — the
spec declares, per requirement, whether it is *preserve* (must NOT regress) or *change* (deliberately
new). You write two kinds of test accordingly, and you scope mutation to the changed surface only.

> **New / experimental mode** (AVENGERS §8). Validate the preserve-vs-change partition and diff-scoped
> mutation on a real refactor before trusting it; prove it here before any back-port.

The suite you produce is the frozen contract. You never relax it to make the refactor pass; a
genuinely wrong test routes back to you.

## Inputs
- The refactor spec (`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`) with
  each `R<n>.<k>.<m>` tagged **preserve** or **change**.
- The `task-analysis.md` `work_kind: refactor` block — preserve-vs-change intent and blast radius.

## Procedure

1. **Partition the requirements.** From the spec, split every `R<n>.<k>.<m>` into two sets:
   **preserve** (behavior that must survive the refactor unchanged) and **change** (behavior the
   refactor introduces or alters). If the spec is unclear which a requirement is, route it back — the
   partition is the whole point.

2. **Characterize-and-freeze the preserve set.** For each preserve requirement, write a test that pins
   the behavior **as it is today** and passes against the *current* code (assert current observable
   behavior). Run it now — it must be **green** before the refactor. Docstring: `"""spec: R3.1.1 |
   preserve — <behavior held constant>"""`. These are the regression guard.

3. **Write fresh RED for the change set.** For each change requirement, write ≥1 positive and ≥1
   negative test for the *new* behavior. These must **fail now** (the new behavior doesn't exist yet).
   Docstring: `"""spec: R3.1.4 | change/positive — <new behavior>"""`.

4. **Surface pre-existing failures — never adopt them.** If, while characterizing, you find tests or
   behaviors already broken before your change, **report them**; do not fix them, do not fold them into
   this spec's contract, and do not let them expand scope. They are out of the blast radius.

5. **Scope mutation to the changed surface.** Refactor mode runs **cosmic-ray diff-scoped**: the
   mutation config's `module-path`/filters are limited to the files the spec's *change* set touches, so
   mutants are only planted where behavior is actually changing. (The verifier generates this
   diff-scoped `cosmic-ray.toml` from the changed files — see `mutation-interpret` + `pipeline-conventions`.)

6. **Confirm the split state, then freeze.** Run `pytest -q tests/<phase>/`: preserve tests **green**,
   change tests **RED**. Record every test in `test-mapping.md` with its `preserve`/`change` tag and
   spec id, and declare the suite frozen.

## test-mapping.md additions
Add a `partition` column:

```markdown
| test | spec id | type | partition |
|---|---|---|---|
| test_existing_orders_still_fill | R3.1.1 | characterization | preserve |
| test_new_trailing_stop_triggers | R3.1.4 | positive | change |
| test_trailing_stop_ignores_noise | R3.1.4 | negative | change |
```

## Done when
- Every requirement is partitioned preserve vs change; the split is explicit.
- Preserve tests are green against current code (regression guard); change tests are RED.
- Any pre-existing failures are reported, not adopted or fixed.
- The diff-scoped mutation surface (changed files) is identified for the verifier.
- `test-mapping.md` records partition + spec id; the suite is declared frozen.
