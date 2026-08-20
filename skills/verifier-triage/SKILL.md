---
name: verifier-triage
description: The triage procedure and verdict schema the Verifier uses to classify failures and route them, and to emit a structured pass/fail verdict for a phase. Use whenever acting as the Verifier. Always fail closed when a verdict can't be reached.
---

# verifier-triage

How the Verifier turns "the phase isn't fully green" into a routed, structured verdict — without ever
fixing anything itself.

## Pre-flight

**Run everything through the recorder.** A verdict is consumable only when a transcript backs it:

```bash
python3 scripts/verifier_evidence.py record <phase-dir> --kind suite -- pytest -q tests/<feature>/<n>-<slug>
python3 scripts/verifier_evidence.py record <phase-dir> --kind adversarial --note "<what you planted>" -- <cmd>
python3 scripts/verifier_evidence.py chain <phase-dir>   # -> verdict.json "execution": {"chain": …}
```

`record` runs the command in its own process group, writes its combined output to a log beside the
record, and stores the argv, exit code, **measured** wall clock, the sha256 of that output and a
digest of the specs and tests it ran against. It returns the command's own exit code — it never
swallows a failure. `check` refuses a record whose log does not hash to its digest, whose runs
predate a change to the phase's specs or tests, that holds no passing `suite` run, or whose chain
the verdict does not name. `scripts/hook_verifier.sh` and `gate_ci.sh` both run it.

This replaced `test_quality.reviewed` — a boolean the verifying agent wrote about itself, which both
gates accepted as the phase's independence. Every refusal names what would satisfy it; if a phase
genuinely cannot produce a transcript, disclose it
(`applicability.py record <phase-dir> --rule execution-evidence …`) rather than omitting it.

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

A **stale gate stamp** is cleared by writing the spec again to re-gate it, or — when the gate
provider cannot be reached, which is when re-gating is unavailable — by recording a disclosed
`spec-gate` exception for that spec (`applicability.py record <phase-dir> --rule spec-gate --subject
<n>.<k>-<subslug> --reason-file <f>`), which the pre-check reads. An **amendment does not clear it**:
it re-verifies requirement ids here, and never touches the spec-gate hash the pre-check compares.

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

## Adversarial execution (what actually buys this stage)

A scout measured all 46 findings this stage produced across 8 phases of one feature. **3 of 46** were
user-visible defects no other stage could have found - and **two of those were plaintext-credential
leaks**, found by planting an adversarial value and executing it against a real Postgres. Not by
reading anything.

So on any requirement whose subject is a **secret**, a **resource lifetime**, or a **concurrency
invariant**: plant a value a real deployment would produce, drive it through the real collaborator,
and look at what came back - the log line, the stored row, the connection still open. Record it:

```bash
python3 scripts/verifier_evidence.py record <phase-dir> --kind adversarial \
    --note "planted a password containing a URL-unsafe character" -- <the command that drives it>
```

A green suite is not a reason to skip this. It is the state this exists to disbelieve.

## The cross-family reading pass is GONE - and what that leaves uncovered

This stage used to have a third job: hand a bounded set of the phase's tests to a model on another
vendor's family and have it read them for tautological, implementation-coupled and missing-negative
patterns. That pass **returned GO with zero findings on a phase that contained real defects**, and
the hypothesis testing whether it earned its cost came back unmeasured. It is removed.

**Nothing inherits it, and that is deliberate.** Doing the reading yourself would be same-family
self-review wearing the removed gate's name: you are an Anthropic model and so is the implementer,
and opus-vs-sonnet is not decorrelation.

**What is genuinely uncovered now, stated rather than implied:** gamed, tautological and
implementation-coupled tests have no dedicated reader. What still touches them is partial and named:

- the **mutation gate** (`MUTATION_POLICY=advisory` by default) kills non-discriminating tests
  deterministically, diff-scoped, with no model below the threshold - the one signal that has
  actually caught them in this repository, and still advisory rather than a wall;
- **`skills/tdd`** names the anti-patterns to the implementer *while it writes*, which is earlier and
  cheaper than catching them afterwards;
- the **human spec-review** sets acceptance criteria a gamed test has to contradict.

If you notice a gamed test while tracing coverage or executing adversarially, raise it - `gamed-test`
is still in the verdict schema and a gamed test is still a `fail` on a green suite. Just do not read
the suite for them as a stage, and never record a review that did not happen.


## Record which stage found each defect

`scripts/hook_verifier.sh` records every finding in your `verdict.json` as a defect attributed to
`verifier` when the phase closes, and `hook_mutation.sh` records what mutation found. **What no
script can see is the rest of what you catch** — a Breaker counterexample, a bug found by driving the real path
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
exits non-zero and prints why on stderr, unless `AVENGER_METRICS_OFF=1` is set (configured behaviour,
not a failure). Read which of the three failures it names before doing anything:

- **`DEFECT NOT RECORDED - WRITE FAILED`** (exit 1), which also says **"re-run this exact command"** -
  a writer is configured and the write itself failed. Retryable and yours: read the `[metrics]` line
  above it, fix the cause, and re-run the exact command.
- **`DEFECT NOT RECORDED - NO METRICS WRITER CONFIGURED`** (exit 1), which also says **"DO NOT
  re-run it"** - nothing on `PATH`, `AVENGER_METRICS_CMD` unset. **Terminal, and not yours to fix.**
  It is the expected state of a standalone install with no firstmate home and every retry fails
  identically, so **do not re-run it**: note in your verdict report that the defect could not be
  recorded, and carry on. Under `--auto` it is worth surfacing to the operator rather than looping
  on.
- **`DEFECT NOT RECORDED - UNRESOLVABLE --phase-ref`** (exit **2**, the usage-error code) - the
  writer was never reached and nothing about it is known to be wrong: **the argument is.** Yours to
  fix, but by CHANGING the command, not repeating it: re-run with a `--phase-ref` naming an existing
  phase directory or a path inside one, such as `docs/features/<feature>/phases/<n>-<slug>`.
  `AVENGER_METRICS_OFF=1` does not quiet this one - turning emission off is a statement about
  recording, not a licence to name a phase that does not exist.

Read the marker to its end: all three messages open `DEFECT NOT RECORDED`, so that stem alone decides
nothing, and the re-run sentence no longer separates them either. No marker contains another, which
is what makes the full marker safe to match on.

Either way the defect did not land, and `found_by` is not recoverable once the phase moves on.

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
  `coverage`, `findings` or `execution` a second time in sentences; every consumer of this file
  reads the structured fields. One measured phase's `report` was 12,991 characters.
- **The schema is frozen.** Add a finding, not a bespoke top-level key. `amendments` was the first
  extension since it was frozen, and `execution` (replacing `test_quality`) the second; both were
  made **here, at the schema**, which is the sanctioned way and the only one: it is an array of amendment **ids** — the records themselves live in
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
  "run": { "at": "YYYY-MM-DDThh:mm:ssZ" },
  "tests": { "total": 0, "passed": 0, "failed": 0 },
  "coverage": { "requirements": 0, "traced": 0, "untraced": [] },
  "execution": { "evidence": "verification-evidence.json",
                 "chain": "<scripts/verifier_evidence.py chain <phase-dir>>",
                 "runs": 0 },
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
