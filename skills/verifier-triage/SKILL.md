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

Then run the **mechanical pre-check**, which owns what used to be a quarter of this stage's output:

```bash
python3 scripts/verifier_precheck.py <phase-dir>
python3 scripts/amendments.py due <phase-dir>       # security amendments are never batched
python3 scripts/verifier_attempts.py check <phase-dir>
```

**Bookkeeping is not a finding any more.** Twelve of 46 findings measured across 8 phases (26%, and
45% on the worst) were about gate stamps, traceability rows and spec headings — two whole attempts
produced nothing else, at roughly 70 minutes and ~410k tokens for four stamp-freshness observations.
All of it was mechanically decidable, and `verifier_precheck.py` decides it on every commit. If it
reports something, say so and let it be fixed mechanically; do **not** turn it into a `verdict.json`
finding and do not spend an attempt on it.

**Three attempts, and route-backs are bundled.** 16 of 20 re-attempts were this stage routing back
to itself. Raise everything you can see in one pass, with your uncertainty stated, rather than
holding a finding for the next attempt. At the cap: carry the remainder as known-open in
`handover.md`, waive them explicitly, or escalate — a fourth attempt is not one of the three.

**An open amendment scopes the re-verification.** `amendments.py scope <phase-dir>` prints the
requirement ids a post-verification change touched; verify **those**, not the phase. Record the
amendment ids you folded in as `amendments` in the verdict, and close each with
`amendments.py close <phase-dir> <A-id> --evidence <path>`.

## Triage each non-green *or gamed* result
Because the implementer authors its own tests, you triage two kinds of problem: tests that don't pass,
and tests that pass but are gamed. All routes go back to the **implementer** (`avenger-backend-architect` /
`avenger-frontend-developer`), who owns both code and tests.
- **code issue** → the test is right, the code is wrong. Route with the failing test named.
- **wrong/gamed test** → a tautological or implementation-coupled test (see the `tdd` skill
  anti-patterns), or a test that asserts the wrong behavior. Route with the specific fix.
- **coverage gap** → a requirement the spec asked to be bound that nothing binds, or an
  acceptance-criteria failure condition no test exercises. Route with the missing case named.

**Read the requirement's `binding:` before calling a gap.** Not every id is owed a test of its own,
and treating one as owed is how a suite grows without anyone deciding to:

| `binding:` | A gap is | Not a gap |
|---|---|---|
| `e2e` | no journey in `test-mapping.md` lists this id, or the journey covering it is red | it has no test of its own — by design, the journey is the test |
| `integration` | no passing test maps to this id | — |
| `none` | never a gap | it has no test at all — the spec says so, and CI or a type checker or nothing enforces it |

A missing `binding:` is a **spec** defect, not a coverage gap: the phase should not have reached you,
so fail closed and say the spec never declared one. And if you believe a requirement is bound at the
wrong tier, that is still a finding against the spec — never a test you ask the implementer to add on
top of the binding the gate approved.

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

which bundles the specs (only those changed since the last completed review — the rest are named as
carried forward and their findings merged back; `skills/pipeline-conventions`), their mappings, the
test run and the sources, judges them on
`$VERIFIER_GATE_MODEL` (default `google/gemini-3.1-pro-preview`) via `gate_runner.py`, and writes
`<phase-dir>/.verifier-review.json` with findings already carrying deterministic ids. `gate_runner`
refuses a same-family model. It also refuses **before** the model call when the review set exceeds
`VERIFIER_SRC_LIMIT` (default 400000 chars), and **after** it when the verdict names none of
the files it was handed or reports itself as partial — a truncated or hollow review is an
unreviewed phase, not a pass. Raise the cap to what the gate model can actually read, or split
the set and merge findings; never drop files to fit. Fold those findings into `verdict.json` verbatim — you may add findings
it missed, never delete or downgrade one it raised. Record the scope you chose (and `reviewed_by`) in
`test_quality`; a `pass` that records no completed review is rejected by both the hook and CI.

### Build the review set (deterministic, token-bounded)
1. Read the phase specs, acceptance criteria, and every `test-mapping.md` **table** once. The
   `test-evidence.md` beside each mapping is **not** part of this pass — open it only when you are
   routing a finding back or checking a fix to one, which is the moment its mutation evidence and
   route-back history are worth their tokens.
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
- **Unrealistically-shaped external identifiers** — a fixture whose values for an external system's
  identifiers have a shape no real deployment produces. 1,009 passing tests used ids an order of
  magnitude smaller than the real ones, against an `int32` column, and a credential-refusal path
  raised before it could fire: the control shipped non-functional behind a green suite, and the
  defect was found by the delivery gate driving a real value, not by any of those tests.
A gamed test is a `fail` even when the suite is green.

## Record which stage found each defect

`scripts/verifier_review.sh` already records the cross-family review's own findings as defects
attributed to `verifier`, and `hook_mutation.sh` records what mutation found. **What no script can
see is the rest of what you catch** — a Breaker counterexample, a bug found by driving the real path
by hand, one found by reading the code outside any gate. That attribution is the single most valuable
number the pipeline produces about itself (one phase set showed the running suite catching 3 of 15
genuine defects) and it is **unrecoverable once the run is over**, so record it while you have it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pipeline_metrics.py" defect \
  --phase-ref docs/features/<feature>/phases/<n>-<slug> --id <finding-id> \
  --summary "$(cat .lavish/<feature>-defect.md)" \
  --found-by breaker --stage-reached implementation --severity correctness
```

Write the summary to a gitignored file first and read it inline as `"$(cat …)"`. That is not style:
a summary is author-written free text, and under `--auto` the deny regex matches the whole Bash
command string, so prose that merely *names* a denied command denies the command carrying it
(`pipeline-conventions`, Hard rules).

`--found-by` takes firstmate's fixed vocabulary — `spec-gate`, `review-gate`, `verifier`, `breaker`,
`mutation`, `running-suite`, `probe`, `execution`, `measurement`, `human-review`, `ci`, `other` (which
then needs `--found-by-note`). Add `--not-real` for a defect in a test, fixture or artifact: it cost
real time and belongs in the record, but it must not inflate the count of genuine product defects.
This is measurement, but `defect` is the one emission command that is **loud about failing** (issue
#66): it runs directly rather than from a hook's `|| true`, so an emission that could not be written
(no writer configured, the writer command missing, a non-zero exit from the writer, an unwritable
record) exits **1** and prints why on stderr, unless `AVENGER_METRICS_OFF=1` is set (configured
behaviour, not a failure). Treat that non-zero exit as a real failure: read the `[metrics]` line
above it, fix the cause, and re-run the exact command - the defect did not land, and `found_by` is
not recoverable once the phase moves on.

## The verdict is a persisted artifact
The verdict is not chat-only. Write it to disk at
`docs/features/<feature>/phases/<n>-<slug>/verdict.json` (co-located with `handover.md`), one file per
phase. It is both your output *and* the input the next verifier run reads back. See
`docs/templates/verdict.template.json` for the shape.

### It is read twice per phase, so it stays small in two specific ways
`verdict.json` is opened by phase-handover and again at feature close, and it is the highest-rework
artifact class the pipeline produces. Two rules keep it from growing without anyone deciding to:

- **A superseded attempt is archived, not nested.** When a re-run supersedes a previous attempt,
  write that previous attempt to `verdict-attempt-<n>.json` beside the verdict and keep only its
  `attempt` number and a one-line outcome in the live file. Do **not** nest a `previous_attempts[]`
  archive inside it: one measured phase's verdict was 130 KB of which **83 KB (64%) was superseded
  attempts**, re-read in full every time anything opened the current one. The archive files carry
  `"readers": []` — nothing is instructed to read them, and nothing is deleted.
- **`report` is capped at 1500 characters.** It is free prose, and its job is the *headline
  judgement plus anything the structured fields cannot say* — a disagreement you are recording
  rather than acting on, an ambiguity, a scope note. It is not a place to narrate `tests`,
  `coverage`, `findings` or `test_quality` a second time in sentences; every consumer of this file
  reads the structured fields. One measured phase's `report` was 12,991 characters.
- **The schema is frozen.** Add a finding, not a bespoke top-level key. `amendments` is the one
  extension since it was frozen, and it was made **here, at the schema**, which is the sanctioned way
  and the only one: it is an array of amendment **ids** — the records themselves live in
  `amendments.json`, so the verdict gains one short line rather than a nested ledger. A verdict then
  reads *verified at attempt N, plus amendments A1..An*. One feature's verdicts grew
  `judgement_w29`, `judgement_synchronisation_audit`, `judgement_with_for_update` and
  `locked_file_audit` — four keys no reader knew to look for, which is the same as not recording
  them. If something genuinely has no home in the schema, it is a `finding` or it is a line in
  `report`.

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
acknowledgment in the shared audit trail — **through `scripts/bypass_log.sh`, never by appending to
`gate-overrides.log` yourself**:

```bash
GATE_BYPASS="$(jq -r '.findings[] | select(.id=="<finding-id>") | .waiver_reason' <verdict.json>)" \
  bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/bypass_log.sh" verifier <finding-id> <waived_by>
```

It stamps who / when / finding id / reason in the one record grammar every writer of that log shares,
and normalises the reason through `scripts/bypass_reason.sh` first. That matters here specifically:
`waiver_reason` is a JSON string, so it carries newlines freely, and the rule below says its content
is *not judged* — so nothing rejects a two-line reason. Hand-appending one would split a single waiver
into two records, the second with no timestamp, no author and no finding id, in the accountability
record for the only sanctioned way to override a gate. Then mirror the same record into the phase
`handover.md`.

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
  "readers": ["phase-handover @ per phase", "feature close @ once"],
  "attempt": 1,
  "superseded_attempts": [ { "attempt": 1, "verdict": "fail", "outcome": "<one line>",
                             "archived_to": "verdict-attempt-1.json" } ],
  "amendments": ["A1", "A2"],
  "report": "<= 1500 chars: the headline judgement, plus only what the structured fields cannot say",
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
`coverage.requirements` and `coverage.traced` count only the requirements the spec asked to be bound —
`binding: e2e` (traced by the journey listing the id) plus `binding: integration`. A `binding: none` id
is outside the count and never appears in `untraced`, so a phase with unbound requirements still
reports `traced == requirements`.

`gamed-test` covers the tautological / implementation-coupled / missing-edge patterns above; name the
exact pattern in the finding's `instruction`. If the mutation gate is off (the default), set
`mutation.enabled: false` and omit the rest — do not run any mutation tool.

## Fail closed
If the suite won't run, tooling is missing, or a result is ambiguous → `verdict: fail`. A gate that
can't reach a verdict never passes.
