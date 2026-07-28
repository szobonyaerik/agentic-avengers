---
name: verifier-triage
description: The triage procedure and verdict schema the Verifier uses to classify failures and route them, and to emit a structured pass/fail verdict for a phase. Use whenever acting as the Verifier. Always fail closed when a verdict can't be reached.
---

# verifier-triage

How the Verifier turns "the phase isn't fully green" into a routed, structured verdict — without ever
fixing anything itself.

## Pre-flight
Confirm your model family ≠ the implementer's family. If equal → emit `fail` with reason
`same-family-verification` and stop.

## Triage each non-green *or gamed* result
Because the implementer authors its own tests, you triage two kinds of problem: tests that don't pass,
and tests that pass but are gamed. All routes go back to the **implementer** (`avenger-backend-architect` /
`avenger-frontend-developer`), who owns both code and tests.
- **code issue** → the test is right, the code is wrong. Route with the failing test named.
- **wrong/gamed test** → a tautological or implementation-coupled test (see the `tdd` skill
  anti-patterns), or a test that asserts the wrong behavior. Route with the specific fix.
- **coverage gap** → a requirement id with no passing test, or an acceptance-criteria failure
  condition no test exercises. Route with the missing case named.

Never edit code or tests. Triage and route only.

## Test-quality review (the independence check)
The implementer wrote the phase's tests, so read the tests that can carry its mistakes — **not the
entire repository suite**. Mechanical execution remains broad; semantic reading is targeted.

### Who does the reading

You select the review set; **a cross-family model reads it.** Every subagent in this runtime is
Anthropic, so the Verifier agent cannot itself be decorrelated from the implementer — opus-vs-sonnet
is the same vendor and the same blind spots. Hand the set to:

```bash
scripts/verifier_review.sh <phase-dir> <review-set-file>...
```

which bundles the specs, the mappings, the test run and the sources, judges them on
`$VERIFIER_GATE_MODEL` (default `google/gemini-2.5-pro`) via `gate_runner.py`, and writes
`<phase-dir>/.verifier-review.json` with findings already carrying deterministic ids. `gate_runner`
refuses a same-family model. Fold those findings into `verdict.json` verbatim — you may add findings
it missed, never delete or downgrade one it raised. Record the scope you chose (and `reviewed_by`) in
`test_quality`; a `pass` that records no completed review is rejected by both the hook and CI.

### Build the review set (deterministic, token-bounded)
1. Read the phase specs, acceptance criteria, and every `test-mapping.md` once.
2. Build the review set as the union of:
  - every test authored or ported for the phase and named in `test-mapping.md`; and
  - every test file changed by the phase, even if the mapping omitted it. Use the repository diff
    from the clean phase-start baseline (normally `HEAD` before the uncommitted phase); if unrelated
    work makes that baseline ambiguous, fail closed and ask for the phase diff boundary.
3. An authored/ported test missing from `test-mapping.md` is a `coverage gap`; do not silently exclude
  it to save tokens.
4. For each review-set test, follow only **directly referenced** fixtures, helpers, custom assertions,
  snapshots, or test-data builders that supply setup or expected values. Read each dependency once.
  Do not recursively explore unrelated production code or unchanged tests.
5. Record the reviewed test files, dependency files, and any expansion reason in the verdict. Point to
  files; do not paste their contents into the verdict.

### Expand only on evidence
Expand beyond the default set only when:
- the spec/plan marks the surface critical or security-sensitive — include the tests for that surface;
- a changed shared test helper can alter assertions outside the mapped tests — include its affected
  test family;
- mappings or the phase diff are missing/ambiguous — fail closed unless the affected scope can be
  established; or
- a suspicious pattern in a reviewed test requires one-hop context to classify it.

Expansion is **affected-surface only**, never an automatic read of the whole inherited suite. Stop
when the question that triggered expansion is resolved. A large repository is not itself a reason to
expand.

Within that review set, flag as a `wrong/gamed test`:
- **Tautological** — expected value recomputes the implementation; the test can never disagree with
  the code.
- **Implementation-coupled** — asserts on internals/private methods/call counts, or verifies through a
  side channel instead of the public interface.
- **Missing negative/edge** — a requirement whose acceptance criteria name a failure condition that no
  test covers.
A gamed test is a `fail` even when the suite is green.

## The verdict is a persisted artifact
The verdict is not chat-only. Write it to disk at
`docs/features/<feature>/phases/<n>-<slug>/verdict.json` (co-located with `handover.md`), one file per
phase. It is both your output *and* the input the next verifier run reads back. See
`docs/templates/verdict.template.json` for the shape.

Each finding is **self-contained** — it carries the fix instruction for the routed agent and its own
break-glass waiver — so an engineer can waive a single finding without touching the whole gate. The
top-level `routed` array is *derived* from the findings that are still `open` and not waived; do not
maintain it independently.

### Finding id (deterministic, so waivers survive re-runs)
Every finding gets a stable `id` = a short hash over its identifying attributes:
`sha1(kind + "|" + spec_id + "|" + normalized_target)[:12]`, where `normalized_target` is the
repo-relative test/file/requirement path with surrounding whitespace trimmed. The same underlying
problem must always produce the same `id` across runs; a different problem must produce a different one.

### Merge-on-rerun (never clobber an engineer's waiver)
On every run, if a prior `verdict.json` exists for the phase:
1. Regenerate findings for the current state of the phase and compute each `id`.
2. For every regenerated finding whose `id` matches one in the prior file, **carry forward** its
   `break_glass`, `waiver_reason`, `waived_by`, and `waived_at` fields. New or changed findings default
   to `break_glass: false`.
3. A prior finding whose `id` no longer regenerates is resolved — mark it `status: fixed` in the
   run's evidence; it no longer blocks.

### Honoring a waiver
A finding with `break_glass: true` **and a non-empty `waiver_reason`** is treated as **non-blocking**
and set to `status: acknowledged`; it drops out of the derived `routed` array. Record the
acknowledgment in the shared audit trail (append to `gate-overrides.log` and the phase `handover.md`:
who / when / which finding id / reason).

The `waiver_reason` is **mandatory but its content is not judged** — any explanation the engineer
gives (e.g. `waiver_reason: "Engineer decision"`) is accepted and honored. If the reason looks thin or
insufficient for the finding, still honor the waiver but add a **warning** to the verdict; never block
on the quality of the reason. `waived_by` is recommended for the audit trail — warn if it is absent,
but do not block on it.

The one blocking case: a `break_glass: true` finding with **no `waiver_reason`** (missing or empty) is
**not** honored — the finding stays `open` and blocks, flagged in the verdict as an incomplete waiver.

### Verdict + bypassed
- `verdict: fail` — at least one finding is `open` (unwaived and unresolved).
- `verdict: pass`, `bypassed: false` — no findings, or all findings resolved (`fixed`).
- `verdict: pass`, `bypassed: true` — every remaining finding is `acknowledged` (waived). This is a
  *visible bypass*, never a silent clean green; it surfaces on the PR like any break-glass override.

## Verdict schema
```json
{
  "feature": "<feature>",
  "phase": "<n>-<slug>",
  "run": { "at": "YYYY-MM-DDThh:mm:ssZ", "verifier_family": "<B>", "implementer_family": "<A>",
           "cross_family_ok": true },
  "tests": { "total": 0, "passed": 0, "failed": 0 },
  "coverage": { "requirements": 0, "traced": 0, "untraced": [] },
  "test_quality": { "reviewed": true,
                    "scope": { "mode": "targeted|expanded",
                               "test_files": ["..."],
                               "dependency_files": ["..."],
                               "expansion_reasons": [] } },
  "findings": [
    {
      "id": "<12-char hash>",
      "kind": "code|gamed-test|coverage-gap",
      "spec_id": "R<n>.<k>.<m>",
      "target": "<repo-relative test/file/requirement>",
      "severity": "blocker|major|minor",
      "instruction": "<concrete fix directions for the routed agent>",
      "route_to": "avenger-backend-architect|avenger-frontend-developer",
      "status": "open|fixed|acknowledged",
      "break_glass": false,
      "waiver_reason": null,
      "waived_by": null,
      "waived_at": null
    }
  ],
  "mutation": { "enabled": false, "language": "python|java|cpp", "score": 0.0, "threshold": 0.0,
                "policy": "enforce|advisory", "survivors": [] },
  "routed": [ { "to": "avenger-backend-architect|avenger-frontend-developer", "finding_id": "<hash>",
                "reason": "code|gamed-test|coverage-gap", "spec_id": "R..." } ],
  "verdict": "pass | fail",
  "bypassed": false
}
```
`gamed-test` covers the tautological / implementation-coupled / missing-edge patterns above; name the
exact pattern in the finding's `instruction`. If the mutation gate is off (the default), set
`mutation.enabled: false` and omit the rest — do not run any mutation tool.

## Fail closed
If the suite won't run, tooling is missing, or a result is ambiguous → `verdict: fail`. A gate that
can't reach a verdict never passes.
