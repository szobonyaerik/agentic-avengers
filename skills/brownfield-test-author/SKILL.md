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

5. **Mutation is already diff-scoped — you don't configure it.** Every mode now runs cosmic-ray
   scoped to the phase's diff: `scripts/hook_mutation.sh` appends a `[cosmic-ray.filters.git-filter]`
   section naming the diff base and runs `cr-filter-git`, which skips mutants outside the changed
   lines. Mutants are only planted where behavior actually changed. You do not hand-write a
   diff-scoped config; if you think the scope is wrong, say so in the handover rather than editing
   `cosmic-ray.toml`. (See `mutation-interpret` + `pipeline-conventions` §8.)

6. **Confirm the split state, then freeze.** Run `pytest -q tests/<phase>/`: preserve tests **green**,
   change tests **RED**. Record every test in `test-mapping.md` with its `preserve`/`change` tag and
   spec id, and declare the suite frozen.

## Level: pin behavior at the seam
Both partitions follow the pipeline default, `level: integration` — characterize and test through the
public seam (the entry point a caller uses), not the internals. This matters more here than anywhere
else: a *preserve* test that asserts on an internal helper pins the **current implementation** rather
than the behavior, so the refactor you are guarding breaks it by design and you get a route-back on a
test that was never wrong. Pin what the caller observes and the refactor is free to move everything
behind it. `narrow` needs a justification, exactly as in greenfield.

## test-mapping.md additions
Add a `partition` column alongside the standard `level`/`justification`:

```markdown
| test | spec id | type | level | justification | partition |
|---|---|---|---|---|---|
| test_existing_orders_still_fill | R3.1.1 | characterization | integration | — | preserve |
| test_new_trailing_stop_triggers | R3.1.4 | positive | integration | — | change |
| test_trailing_stop_ignores_noise | R3.1.4 | negative | integration | — | change |
```

## Done when
- Every requirement is partitioned preserve vs change; the split is explicit.
- Preserve tests are green against current code (regression guard); change tests are RED.
- Every test pins its behavior at a seam; any `narrow` row carries a justification.
- Any pre-existing failures are reported, not adopted or fixed.
- `test-mapping.md` records partition + level + spec id; the suite is declared frozen.
