---
name: tdd-red-author
description: Use when authoring tests for a phase before implementation — derive paired positive/negative RED tests from the phase spec and lock them.
---

# tdd-red-author

Author the failing (RED) test suite for a single **spec** `<n>.<k>` **before any implementation
exists** (greenfield mode). The tests you write become a frozen contract the implementer must
satisfy — they encode what the code *should* do, derived from the spec, never shaped to match code
that doesn't exist yet.

> This is the **greenfield** mode. For inherited suites use `migration-test-author`; for changing
> existing behavior use `brownfield-test-author`. The task's `work_kind` selects the mode.

## Inputs
- The spec / acceptance criteria for the current spec
  (`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`).
- The phase slug (e.g. `1-webhook`) and spec id (e.g. `1.1`).

## Procedure

1. **Enumerate requirements.** Read the spec's acceptance criteria and list every distinct
   requirement, each with its stable id `R<n>.<k>.<m>` from the spec. A requirement is one observable
   behavior, including its failure/edge behavior.

2. **Write paired cases per requirement.** For EACH requirement write at least:
   - one **positive** test (the behavior succeeds / the right thing is produced), and
   - one **negative** test (bad input, duplicate, unauthorized, or boundary is correctly
     rejected or has no effect).
   Annotate every test with the spec id it traces to, as the first docstring line:
   `"""spec: R1.1.1 | positive — ..."""`. One requirement may need several negatives.

3. **Write the files.**
   - Tests go in `tests/<phase>/` (e.g. `tests/1-webhook/test_idempotency.py`), using pytest. Name
     files so each spec's tests are findable within the phase dir.
   - Write/append `docs/features/<feature>/phases/<phase>/test-mapping.md` mapping each test to its
     spec id and type (see format below). The mapping is phase-level; rows accumulate across the
     phase's specs.

4. **Confirm RED.** Run `pytest -q tests/<phase>/`. Every new test MUST fail or error right now
   (no implementation exists). If any test already passes, it is asserting nothing new — rewrite
   it so it genuinely exercises the unbuilt behavior.

5. **Lock the contract.** State clearly in `test-mapping.md` and your handoff that these tests are
   frozen: per the pipeline conventions in CLAUDE.md, the implementer may not edit, relax, or
   delete any file under `tests/`. If a test is genuinely wrong, it is routed back to the
   Test-Author (you) — never reshaped by the implementer to make code pass.

## test-mapping.md format

```markdown
---
feature: <feature>
phase: <phase>
stage: test-author
model: <model>
created: <date>
---
| test | spec id | type |
|---|---|---|
| test_signed_payload_persists_once | R1.1.1 | positive |
| test_replay_does_not_double_insert | R1.1.1 | negative |
| test_invalid_signature_rejected | R1.1.2 | negative |
```

## Worked example — spec `1.1` in phase `1-webhook` (idempotent ClickUp webhook)

Requirements: **R1.1.1** a valid signed delivery is persisted exactly once; **R1.1.2** an invalid
signature is rejected and nothing is stored.

```python
# tests/1-webhook/test_idempotency.py
from webhook.receiver import handle_webhook  # not implemented yet -> import/▸ RED

def test_signed_payload_persists_once(db):
    """spec: R1.1.1 | positive — a valid signed delivery is stored exactly once."""
    resp = handle_webhook(signed_payload(task_id="T-1"))
    assert resp.status == 200
    assert db.count("tasks", task_id="T-1") == 1

def test_replay_does_not_double_insert(db):
    """spec: R1.1.1 | negative — replaying the same delivery must not create a second row."""
    payload = signed_payload(task_id="T-1")
    handle_webhook(payload)
    handle_webhook(payload)            # duplicate delivery
    assert db.count("tasks", task_id="T-1") == 1

def test_invalid_signature_rejected(db):
    """spec: R1.1.2 | negative — a forged/unsigned body is rejected (401) and not stored."""
    resp = handle_webhook(unsigned_payload(task_id="T-2"))
    assert resp.status == 401
    assert db.count("tasks", task_id="T-2") == 0
```

All three fail now (`handle_webhook` doesn't exist) → the suite is correctly RED. The negative
`test_replay_does_not_double_insert` is the one that later survives a mutation flip if the
implementer's dedup is wrong — which is exactly why it is written up front and locked.

## Done when
- Every requirement has ≥1 positive and ≥1 negative test, each traced to a spec id `R<n>.<k>.<m>`.
- `pytest -q tests/<phase>/` shows all new tests RED.
- `test-mapping.md` exists with the table and the frozen-contract note.