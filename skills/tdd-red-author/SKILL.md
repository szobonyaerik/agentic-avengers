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

2. **Write paired cases per requirement — at the seam.** For EACH requirement write at least:
   - one **positive** test (the behavior succeeds / the right thing is produced), and
   - one **negative** test (bad input, duplicate, unauthorized, or boundary is correctly
     rejected or has no effect).
   Annotate every test with the spec id it traces to, as the first docstring line:
   `"""spec: R1.1.1 | positive — ..."""`. One requirement may need several negatives.

   **Drive every case through the seam** — the public entry point a caller actually uses (the HTTP
   handler, the service method, the CLI command), with its real collaborators wired up. Do not
   reach past the seam to assert on an internal helper, and do not mock a collaborator that lives
   inside it. Tests that bind to internal structure are the ones that get rewritten every time the
   implementation moves; tests bound to the seam survive refactors, which is the whole point of a
   frozen contract. Use real infrastructure where the project provides it (a real database, not a
   mock); mock only what crosses a trust or cost boundary — a third-party API, an LLM, a vault.

   This is the **default and it is `level: integration`.** A `narrow` test — asserting on a single
   function in isolation — is allowed ONLY when a requirement has no reachable seam, and then you
   must record the reason in `test-mapping.md`'s `justification` column. "It was easier to write"
   is not a justification. If a requirement seems to *need* a narrow test, first ask whether the
   requirement itself is pitched below the seam and should route back to the Spec Writer.

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
| test | spec id | type | level | justification |
|---|---|---|---|---|
| test_signed_payload_persists_once | R1.1.1 | positive | integration | — |
| test_replay_does_not_double_insert | R1.1.1 | negative | integration | — |
| test_invalid_signature_rejected | R1.1.2 | negative | integration | — |
| test_cron_expr_rejects_bad_field | R1.1.3 | negative | narrow | pure parser; no caller seam exists until phase 3 wires the scheduler |
```

`level` is one of `integration` (the default), `e2e` (feature-level only — see `e2e-author`), or
`narrow`. `justification` is `—` unless the level is `narrow`, in which case it must say why no seam
was reachable. A `narrow` row with an empty or hand-waved justification is a review finding.

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

Note the level these are pitched at: every case goes through `handle_webhook` — the seam — with a
real `db`. None of them names the dedup helper, the signature verifier, or the persistence layer.
The implementer can restructure all three internally and this suite never moves. That is what
`level: integration` buys, and why it is the default.

## Done when
- Every requirement has ≥1 positive and ≥1 negative test, each traced to a spec id `R<n>.<k>.<m>`.
- Every test drives its requirement through a public seam; every `narrow` row carries a real
  justification.
- `pytest -q tests/<phase>/` shows all new tests RED.
- `test-mapping.md` exists with the table (incl. `level`) and the frozen-contract note.