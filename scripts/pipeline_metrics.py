#!/usr/bin/env python3
"""What the pipeline observes about itself, emitted at the moment it observes it.

"Did the pipeline get better?" used to be answered by archaeology across commits, retros and chat.
firstmate owns the answer's shape — one record per phase, `docs/pipeline-metrics.md` — and this
module is the half only the pipeline can see: the gate calls, the spec rounds, the verification
attempts, the skills a stage actually loaded, and above all WHICH STAGE FOUND EACH DEFECT, which is
unrecoverable once the run is over.

TWO PROPERTIES DECIDE EVERY DESIGN CHOICE BELOW.

**Emitted as the run happens, never reconstructed at the end.** A phase that dies mid-run still
leaves its numbers behind. Phase 8 died and was recovered three times; every one of those recoveries
would have lost the lot under a write-at-the-end design. So each fact is written by the stage that
sees it, at the moment it sees it, and no stage is trusted to remember anything for later.

**Writing metrics can never fail a phase — except the one command nothing wraps in `|| true`.**
Everything here funnels through `metrics_sink`, which swallows every failure and reports it as
`False`. Every emission point but one is called from a hook's fail-open path and this module adds no
`sys.exit` of its own there — its CLI exits 0 even when nothing could be written. `defect` is the
exception: it is the single field the record exists for, the only one unrecoverable after the run,
and it is always run directly by a stage rather than from a hook, so nothing else is fail-open on its
behalf. A `defect` call that could not be written exits 1 and says why on stderr (issue #66) — a
recorder that quietly does nothing is indistinguishable from one with nothing to record.

That loud exit comes in THREE SHAPES, because the remedy is a different party's in each. When a
writer IS configured and the write failed, the stage can fix the cause and re-run the exact command,
so the message says so (exit 1). When NO writer is configured at all - nothing on `PATH`,
`AVENGER_METRICS_CMD` unset - that is a standing property of the environment, the expected state of a
standalone install with no firstmate home, and re-running only fails identically; that message is
addressed to the operator, says the defect could not be recorded, and tells the stage to move on
rather than loop (exit 1). When `--phase-ref` resolves to no phase, nothing about the writer is known
to be wrong and the ARGUMENT is what must change, so that one exits `USAGE_ERROR` (2), the same code
a mistyped command already gets. Each shape opens with its own marker and none of the three contains
another, because that stem is what a stage discriminates on.
`AVENGER_METRICS_OFF=1` is none of them: it is a deliberate choice, and stays silent - except for the
bad argument, which it does not quiet, for the same reason it does not quiet a parse error.

Emission is attached to the *fact*, not to the caller. `record_gate_call` lives inside
`gate_runner.py`, the one place every gate call passes through, so a new gate is instrumented by
existing. `record_spec_round` records one COMPLETED gate evaluation — a run that REACHED a verdict,
approved or blocked — and refuses anything else; it is idempotent by CONTENT, reusing the shipped
rebuildable gate
cache to remember which body it last counted — so any caller may call it on any spec write and the
record converges instead of double-counting a round.

CLI, for the shell emission points (all fail open, all exit 0, except `defect` — see above):
    pipeline_metrics.py spec-round <spec.md> --verdict approved|blocked
    pipeline_metrics.py gate-killed --stage <s> [--spec-path <p>] [--phase-dir <d>]
    pipeline_metrics.py verifier-attempts <phase-dir>  (derived from verdict.json, not a counter)
    pipeline_metrics.py verifier-findings <phase-dir> <verdict.json>
    pipeline_metrics.py breaker-findings <phase-dir> <breaker.json>
    pipeline_metrics.py mutation-survivors <phase-dir> <mutation-score.json>
    pipeline_metrics.py skill-load --stage <s> --skill <k> --evidence <where>
    pipeline_metrics.py skill-required --stage <s>
    pipeline_metrics.py defect --phase-ref <p> --id <i> --summary <s> --found-by <f> ...
        (exits 1 when the write failed or no writer is configured, 2 when --phase-ref names no phase)
    pipeline_metrics.py test-command <phase-dir>   (what ran, beside what the project declares)
    pipeline_metrics.py phase-open <phase-dir>
    pipeline_metrics.py phase-close <phase-dir>
    pipeline_metrics.py provenance <phase-ref>   (report: what the pipeline stamped vs. hand-entered)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess  # noqa: S404 — `git ls-files`, fixed argv, to ask whether the closing document landed
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
import gate_timeouts  # noqa: E402
import metrics_sink as sink  # noqa: E402
import plugin_release  # noqa: E402
import proc_group  # noqa: E402
import skill_contract  # noqa: E402
import subprocess_check  # noqa: E402
import verifier_attempts  # noqa: E402
from spec_gate_cache import keep, normalized, previous, split_spec  # noqa: E402

#: `docs/features/<feature>/phases/<n>-<slug>/…` — the phase number is in the path, so no stage has
#: to be told which phase it is in.
PHASE_IN_PATH = re.compile(r"(?:^|/)phases/(\d{1,2})-[^/]+")

#: `…/specs/<n>.<k>-<subslug>/spec.md` — the spec id firstmate records is `<n>.<k>`.
SPEC_IN_PATH = re.compile(r"(?:^|/)specs/(\d{1,2})\.(\d+)-[^/]+")

#: Requirement ids as pipeline-conventions defines them: `R<n>.<k>.<m>`.
REQUIREMENT_ID = re.compile(r"\bR\d+\.\d+\.\d+\b")

#: `pytest --collect-only -q`'s own summary line ("123 tests collected in 0.4s" / "1 test collected
#: in ..."). `count_tests` parses this rather than counting `def test_` lines statically (issue #46):
#: a static count gives the number of test FUNCTIONS, while every suite run this pipeline reports —
#: `hook_verifier.sh`'s own `pytest -q` — reports the number of collected test ITEMS, and a
#: parametrized function is one `def` and several items. One field, `tests_before`/`tests_after`,
#: was carrying two different populations under one name (917/973 recorded against 1092/1164
#: observed in the same phase); this is what makes it the SAME population as the number every
#: verifier run already prints.
TESTS_COLLECTED = re.compile(r"^(\d+) tests? collected", re.MULTILINE)

#: Which rubric a gate is judging against says which stage made the call, with no caller to ask.
#:
#: The spec gate's two passes are TWO STAGES here, never one. Collapsing them would hide the exact
#: number the ratchet fix is judged by — how many observations the observe pass produced against how
#: many the triage pass turned into blockers — and a filter that blocks everything would then be
#: indistinguishable from one that blocks nothing. `tests/test_pipeline_metrics.py` holds this map
#: exhaustive over the rubrics `prompts/` actually ships, so a rubric added later cannot land with
#: its calls recorded under a name derived from its filename by accident.
RUBRIC_STAGE = {
    "spec-gate-observe.md": "spec-gate-observe",
    "spec-gate-triage.md": "spec-gate-triage",
}

#: Every `gate_errors` cause, mapped onto firstmate's `(verdict, failure_cause)` pair. PR 1 made a
#: gate name its own failure; this is what makes that distinction durable. The map is exhaustive
#: over `CAUSES` by test, so a cause added upstream cannot quietly land in `other` — losing exactly
#: the separation the field exists for.
CAUSE_MAP: dict[str, tuple[str, str]] = {
    "timeout": ("killed", "timeout"),
    "provider-payment-required": ("error", "http-402"),
    "provider-unreachable": ("error", "provider-unreachable"),
    "no-verdict": ("error", "unparseable"),
    # `unparseable`, not `timeout`: the call RETURNED and there was nothing in it to read, which is
    # the state that token names; recording it as a timeout would reinstate the wrong remedy (raise
    # the budget) the cause was split off to end. Not a token of its own - firstmate owns that
    # vocabulary (§6d). The distinction lives on the gate's `cause=` line and its verbatim output.
    "provider-empty-response": ("error", "unparseable"),
    # Not `unparseable`: the reply parsed fine. What failed is the CLAIM that a provider
    # produced it, so it is an error whose cause is the gate not having run.
    "implausible-latency": ("error", "other"),
    "config": ("error", "other"),
    "cross-family": ("error", "other"),
    "unknown-vendor": ("error", "other"),
    "runner-untrusted": ("error", "other"),
    "provider-not-found": ("error", "other"),
    # `other`, not `provider-unreachable`: nothing remote failed — this is contention on the
    # provider CLI's own local state lock (issue #50), and recording it as a reachability failure
    # would reinstate the exact wrong diagnosis the cause was added to prevent. It is not given a
    # `failure_cause` token of its own because firstmate owns that vocabulary and this repo owns no
    # part of it; a token their schema refuses costs the whole entry. The distinction lives where
    # the operator reads it — the gate's `cause=provider-locked` line and its verbatim output.
    "provider-locked": ("error", "other"),
    "provider-error": ("error", "other"),
    "io": ("error", "other"),
    "internal": ("error", "other"),
}

#: Not a `gate_errors` cause: the harness killing the HOOK is observed by the hook's own signal
#: trap, because the runner it killed never gets to say anything. A 120s hook around a 300s call
#: read for a day as a model size ceiling; this is the record that tells those apart.
HOOK_KILLED = "hook-killed"

#: What a pass that ANSWERED but reached no verdict records. The spec gate's observe pass replies
#: with `observations` and is told it cannot block; `spec_gate_triage.py` derives the verdict from
#: what it reported. That is neither a failure (nothing went wrong) nor a NO-GO (no judgement was
#: made), and `_outcome` would call an empty verdict NO-GO — a rejection this pass never issued,
#: landing in the ledger on every spec write. Deliberately an EXISTING token rather than a new one:
#: firstmate owns the record's vocabulary, the `stage` field already says which pass this was, and
#: inventing a value the writer might refuse would lose the row entirely to the fail-open path.
NO_VERDICT = "REVIEW"

#: Free text in the record stays one line and bounded — `note` is context, not a transcript.
NOTE_LIMIT = 300


# --- resolving where we are ---------------------------------------------------------------------


def _phase_from(*candidates: str | None) -> str | None:
    """The two-digit phase from the first path that carries one. Phase refs are always padded."""
    for candidate in candidates:
        if not candidate:
            continue
        match = PHASE_IN_PATH.search(str(candidate))
        if match:
            return f"{int(match.group(1)):02d}"
    return None


def resolve_phase(*candidates: str | None) -> str | None:
    """The phase this observation belongs to: the environment's answer, else the path's."""
    declared = (os.environ.get("AVENGER_METRICS_PHASE") or "").strip()
    if declared:
        try:
            return f"{int(declared.split('-')[0]):02d}"
        except ValueError:
            return None
    return _phase_from(*candidates, os.environ.get("AVENGER_METRICS_PHASE_DIR"))


def resolve_spec(*candidates: str | None) -> str | None:
    """The `<n>.<k>` spec id this observation belongs to, when it is scoped to one."""
    declared = (os.environ.get("AVENGER_METRICS_SPEC") or "").strip()
    if declared:
        return declared
    for candidate in candidates:
        if not candidate:
            continue
        match = SPEC_IN_PATH.search(str(candidate))
        if match:
            return f"{match.group(1)}.{match.group(2)}"
    return None


def stage_from_rubric(rubric: str | None) -> str:
    """Which stage a gate call belongs to, read off the rubric it judges against."""
    declared = (os.environ.get("AVENGER_METRICS_STAGE") or "").strip()
    if declared:
        return declared
    name = Path(rubric).name if rubric else ""
    if name in RUBRIC_STAGE:
        return RUBRIC_STAGE[name]
    return re.sub(r"(-rubric)?\.md$", "", name) or "unknown"


def _clean(text: str | None) -> str | None:
    """One bounded line, so a note stays a note."""
    if not text:
        return None
    flat = " ".join(str(text).split())
    return flat[:NOTE_LIMIT] or None


def _now() -> str:
    """RFC 3339 in UTC with a trailing Z, the only timestamp shape the record accepts."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- gate calls ---------------------------------------------------------------------------------


def _rounds_closed(record: dict | None, spec: str | None) -> int:
    """How many rounds this spec has COMPLETED - one entry per body a gate reached a verdict on."""
    if not record or not spec:
        return 0
    for entry in record.get("specs") or []:
        if entry.get("id") == spec:
            return len(entry.get("bytes_by_round") or [])
    return 0


def _spec_round(
    record: dict | None, spec: str | None, spec_path: str | None = None
) -> int:
    """The round a gate call belongs to: the one IN FLIGHT, not the number already closed.

    A round is recorded when the gate reaches a verdict, which is after the calls that produced it
    (see `record_spec_round`). So a call made while judging a body nobody has counted yet belongs to
    `closed + 1`. Without the spec's path there is nothing to compare the body against, and the
    closed count is the best answer available.
    """
    closed = _rounds_closed(record, spec)
    if not spec or not spec_path:
        return max(1, closed)
    try:
        _frontmatter, body = split_spec(Path(spec_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return max(1, closed)
    counted = previous(Path(spec_path), "metrics")
    if closed and counted is not None and normalized(counted) == normalized(body):
        return closed  # this exact body's round is already on the record
    return closed + 1


def _attempt(
    record: dict | None, stage: str, spec: str | None, spec_path: str | None = None
) -> int:
    """The attempt a gate call belongs to: the spec's round, or the phase's verification attempt.

    `verification_attempts` counts the attempts already CONCLUDED - `verifier_attempts.current()`,
    the highest attempt with a verdict on record (issue #120). It used to be written as
    `current() + 1`, the attempt in flight, which this branch could read straight off. So the
    conversion is now explicit here: a verifier gate call is made while an attempt is being decided,
    before its verdict exists, which is `concluded + 1` - the same "closed, plus the one in flight"
    shape `_spec_round` uses. Reading the field unconverted files every such call one attempt low.

    Its limit, which `_spec_round` closes by comparing the body and this has no equivalent for: a
    call made after the attempt's verdict landed is attributed to the next attempt. No gate runs in
    the verification hooks today (§6a), so this branch is reached only through
    `AVENGER_METRICS_STAGE=verifier`.
    """
    declared = (os.environ.get("AVENGER_METRICS_ATTEMPT") or "").strip()
    if declared:
        try:
            return int(declared)
        except ValueError:
            pass
    if stage == "verifier":
        concluded = (record or {}).get("verification_attempts") or 0
        try:
            return max(1, int(concluded) + 1)
        except (TypeError, ValueError):
            return 1
    return _spec_round(record, spec, spec_path)


def record_gate_call(
    *,
    model: str,
    rubric: str | None = None,
    target: str | None = None,
    model_family: str | None = None,
    latency_ms: int | None = None,
    verdict: str | None = None,
    cause: str | None = None,
    detail: str | None = None,
    provider: str | None = None,
    note: str | None = None,
    stage: str | None = None,
) -> bool:
    """Record one call to a judging model, with its measured latency and its outcome.

    `latency_ms` is the OBSERVED wall clock of the call — not the configured timeout, and not what
    the tooling claimed. The two diverged by an order of magnitude once, and that divergence is why
    the field exists.

    `stage` is normally read off the rubric, which is what makes a new gate instrumented by
    existing. It is passed explicitly only where there is no rubric to read: a hook recording the
    gate it just killed, and the deterministic decide step. Those calls still belong to a named
    stage — the spec gate makes two provider calls, and "which of them was killed" is the whole
    reason the record is kept.

    Returns False, quietly, whenever the call could not be attributed to a phase (a `--selftest`,
    a gate run outside the artifact tree) or the record could not be written.
    """
    try:
        spec_path = os.environ.get("AVENGER_METRICS_SPEC_PATH") or (
            target if target and target.endswith("spec.md") else None
        )
        phase = resolve_phase(spec_path, target, rubric)
        if phase is None:
            return False
        # `AVENGER_METRICS_STAGE` still wins: it is the operator's override, and a caller-supplied
        # name is a default for the no-rubric case, not a way around it.
        stage = (
            (os.environ.get("AVENGER_METRICS_STAGE") or "").strip()
            or stage
            or stage_from_rubric(rubric)
        )
        spec = resolve_spec(spec_path, target)
        read = open_record(phase)
        if not read.usable:
            if read.state == RECORD_UNREADABLE:
                sink.note(
                    f"gate call not recorded for phase {phase}: the record could not be READ, and "
                    f"a call is filed under the attempt the record says is in flight - taken for a "
                    f"record holding nothing it would be filed under attempt 1, where `sink.add` "
                    f"converges by id and a later call REPLACES the first attempt's row. Nothing "
                    f"is written; this call is lost rather than another one overwritten."
                )
            return False
        attempt = _attempt(read.record, stage, spec, spec_path)
        token, failure_cause = _outcome(verdict, cause)
        return sink.add(
            phase,
            "gate_calls",
            id=f"{spec or 'p' + phase}-a{attempt}-{stage}",
            stage=stage,
            spec=spec,
            attempt=attempt,
            model=model,
            model_family=model_family,
            latency_ms=latency_ms,
            verdict=token,
            failure_cause=failure_cause,
            note=_clean(
                f"provider={provider or '?'}"
                + (f" cause={cause} {detail}" if cause else "")
                + (f" {note}" if note else "")
            ),
        )
    except Exception as exc:  # noqa: BLE001 — measurement never propagates a failure
        sink.note(f"gate call not recorded: {type(exc).__name__}: {exc}")
        return False


#: The stage that decides a spec gate's verdict. It is a stage in the ledger even though no model
#: runs in it — the whole point of the redesign is that a SCRIPT derives the verdict — so it gets a
#: row like any other gate outcome, with the model named as what it actually is.
TRIAGE_DECIDE_STAGE = "spec-gate-decide"
TRIAGE_DECIDE_MODEL = "none (scripts/spec_gate_triage.py)"


def record_triage_decision(
    *,
    spec_path: str | None,
    observations: int,
    blocking: int,
    notes: int,
    approved: bool,
) -> bool:
    """Record the filter's own arithmetic: what it saw, what it blocked on, what it noted.

    This is the number the ratchet fix is judged by. The two model passes report and classify; this
    is the count of how many observations survived as blockers, and it belongs in the ledger rather
    than in a log, because a filter that blocks everything and a filter that blocks nothing are
    otherwise indistinguishable without reading transcripts.

    Deliberately an ordinary `gate_calls` row on existing keys, not a new one: firstmate owns the
    record's shape and this repo owns no part of it, so the counts ride in `note` — bounded free
    text that is already there — under a stage of its own.
    """
    try:
        return record_gate_call(
            model=TRIAGE_DECIDE_MODEL,
            rubric=None,
            stage=TRIAGE_DECIDE_STAGE,
            target=spec_path,
            latency_ms=0,
            verdict="GO" if approved else "NO-GO",
            note=f"observations={observations} blocking={blocking} notes={notes}",
        )
    except Exception as exc:  # noqa: BLE001 — measurement never fails the gate it measures
        sink.note(f"triage decision not recorded: {type(exc).__name__}: {exc}")
        return False


def _outcome(verdict: str | None, cause: str | None) -> tuple[str, str | None]:
    """firstmate's `(verdict, failure_cause)` for a call that either answered or did not."""
    if cause == HOOK_KILLED:
        return "killed", "killed-by-harness"
    if cause:
        if cause not in CAUSE_MAP:
            # `tests/test_pipeline_metrics.py` asserts CAUSE_MAP covers every `gate_errors` cause,
            # so reaching this means a cause was added without deciding what it is in the record.
            # Recorded as `error`/`other` rather than dropped, and said out loud rather than not.
            sink.note(
                f"gate failure cause {cause!r} has no mapped outcome — recorded as other"
            )
        return CAUSE_MAP.get(cause, ("error", "other"))
    token = (verdict or "").strip().upper()
    if token in ("GO", "PASS"):
        return "GO", None
    if token == "REVIEW":
        return "REVIEW", None
    return "NO-GO", None


# --- spec rounds --------------------------------------------------------------------------------


class SpecRoundUndecided(Exception):
    """This call cannot be shown to be a round, so nothing is recorded. Always fails the caller."""


#: The verdicts a COMPLETED gate evaluation ends in. Closed, and a token outside it is a hard
#: failure naming what was invented — the same discipline `spec_gate_triage.BLOCKING` and
#: `applicability.RULES` run on. Guessing would put the ambiguity straight back.
SPEC_ROUND_VERDICTS = ("approved", "blocked")


def record_spec_round(spec_path: str, verdict: str | None = None) -> int | None:
    """Record one COMPLETED gate evaluation of this spec: its body size and its requirement count.

    ## What a round IS, stated once, here, because this is where it is decided

    **A spec round is one completed gate evaluation of a spec body: a run of the spec gate that
    reached a verdict.** An approval and a block are both rounds — a block is a completed
    evaluation, and the ratchet this measures is built out of blocks. Three things are NOT:

    * **A gate that never ran.** A spec written outside the gate's trigger — phase 12 authored one
      through a shell heredoc — has zero rounds. That is the correct answer, not a third convention.
    * **A gate that ran and reached no verdict.** A provider that refused for billing (phase 13's
      actual case), an unreachable one, a killed hook. Those stay in `gate_calls[]` with their
      `failure_cause`, which is where a failed call belongs.
    * **A replayed verdict over an unchanged body.** Nothing was evaluated; a stored verdict was
      reread.

    This used to be measured at the WRITER — the hook counted the body the moment it got past the
    cache check, before either paid call — so an attempt that then failed counted as a round. Phase
    12 ended with three counting conventions in one phase and phase 13 added two more, which means
    `spec_rounds` could not be compared between phases at all. The verdict is REQUIRED here rather
    than asked for at the caller: a future caller wired one line earlier cannot reintroduce the gap.

    Growth across `bytes_by_round` is the ratchet made visible — one spec went 25k -> 51k while
    being rewritten to satisfy a gate, and nothing anywhere noticed.

    Idempotent by CONTENT, not by caller discipline. The body this last counted is kept in the
    shipped rebuildable gate cache under its own key, so one round is recorded per genuinely judged
    body. Losing that cache costs one duplicated round, which is why it is a cache and not an
    artifact.

    A body is remembered as counted only once the record actually took it. A refused write that
    still cached its body would make the next call believe the round was already there, and that
    round would be missing from `bytes_by_round` forever — a hole in the one growth series this
    measures. Returns None when nothing was recorded, so the next verdict retries the round.
    """
    token = (verdict or "").strip().lower()
    if token not in SPEC_ROUND_VERDICTS:
        raise SpecRoundUndecided(
            f"a spec round is one COMPLETED gate evaluation, so it is recorded with the verdict it "
            f"reached: one of {', '.join(SPEC_ROUND_VERDICTS)}. Got {verdict!r}. Nothing was "
            f"recorded — a gate that reached no verdict is not a round, and it is already visible "
            f"in gate_calls[] with its failure_cause."
        )
    try:
        path = Path(spec_path)
        phase = resolve_phase(spec_path)
        spec = resolve_spec(spec_path)
        if phase is None or spec is None:
            return None
        try:
            _, body = split_spec(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        read = open_record(phase)
        if not read.usable:
            if read.state == RECORD_UNREADABLE:
                sink.note(
                    f"spec round not recorded for {spec}: the record could not be READ, and "
                    f"`bytes_by_round` is rewritten from what it already holds - taken for an "
                    f"empty series the upsert would replace every earlier round of this spec with "
                    f"this one, and the growth this measures is exactly the history it would "
                    f"delete. Nothing is written and the body is not cached, so the next verdict "
                    f"records the round."
                )
            return None
        rounds = list(_spec_entry(read.record, spec).get("bytes_by_round") or [])
        counted = previous(path, "metrics")
        if counted is not None and normalized(counted) == normalized(body) and rounds:
            return len(rounds)  # this exact body is already one of the rounds above

        rounds.append(len(body.encode("utf-8")))
        if not sink.add(
            phase,
            "specs",
            id=spec,
            requirements=len(set(REQUIREMENT_ID.findall(body))),
            bytes_by_round=rounds,
        ):
            return None
        after = open_record(phase)
        if after.usable:
            _stamp(phase, spec_rounds=_spec_rounds(after.record))
        keep(path, "metrics", body)
        return len(rounds)
    except Exception as exc:  # noqa: BLE001
        sink.note(f"spec round not recorded: {type(exc).__name__}: {exc}")
        return None


def _spec_entry(record: dict | None, spec: str) -> dict:
    for entry in (record or {}).get("specs") or []:
        if entry.get("id") == spec:
            return entry
    return {}


def _spec_rounds(record: dict | None) -> int:
    """How many times the spec set was written: the most any one spec has been."""
    return max(
        (
            len(entry.get("bytes_by_round") or [])
            for entry in (record or {}).get("specs") or []
        ),
        default=1,
    )


#: The stage a plugin-version row belongs to, mirroring `TRIAGE_DECIDE_STAGE`: no model runs here
#: either, and the row exists so the fact has a place in the ledger rather than nowhere at all.
PLUGIN_VERSION_STAGE = "plugin-version"
PLUGIN_VERSION_MODEL = "none (scripts/plugin_release.py)"

#: `gate_calls[].verdict` is a CLOSED enum firstmate owns — `GO|REVIEW|NO-GO|error|killed` — and its
#: writer refuses any row `validate` would refuse. A drift status passed through verbatim ("STALE")
#: is not in it, so the row is rejected, the refusal is swallowed by the fail-open path every
#: measurement here runs on, and the executing version ends up recorded NOWHERE, which is the one
#: thing this row exists to prevent. So the status is MAPPED, and the untranslated token stays in
#: `note` where free text is allowed: a stale copy reads as the rejection it is, a fresh one as GO,
#: and an unresolvable comparison as `NO_VERDICT` for the reason that token exists elsewhere in this
#: file — answered, but no judgement made. A status with no mapping is said out loud rather than
#: silently re-inventing an out-of-enum verdict.
PLUGIN_VERSION_VERDICTS = {"fresh": "GO", "stale": "NO-GO", "unknown": NO_VERDICT}


def record_plugin_version(phase: str) -> bool:
    """Record which plugin copy actually executed this phase (issue #65).

    Not a new top-level field: firstmate's schema is closed and its producer contract is "add no
    key" (pipeline-conventions §6d) — a new field is firstmate's decision, not this repo's. This
    follows the precedent `record_triage_decision` already set for a fact firstmate has no field
    for: an ordinary `gate_calls` row on EXISTING keys, carrying the detail in `note`, bounded free
    text the schema already has. `id` is fixed per phase, so a phase that opens its record more than
    once (a concurrent hook, a resumed run) converges on one row instead of appending duplicates.

    `plugin_release.check()` derives the version from the copy that is actually running — never a
    static constant a stale cached copy would carry unchanged just the same as a fresh one.
    """
    try:
        result = plugin_release.check()
        note = (
            f"status={result.status} executing_version={result.executing_version} "
            f"source_version={result.source_version} root={result.executing_root}"
        )
        verdict = PLUGIN_VERSION_VERDICTS.get(result.status)
        if verdict is None:
            sink.note(
                f"plugin drift status {result.status!r} has no mapped verdict — "
                f"recorded as {NO_VERDICT}"
            )
            verdict = NO_VERDICT
        return sink.add(
            phase,
            "gate_calls",
            id=f"p{phase}-{PLUGIN_VERSION_STAGE}",
            stage=PLUGIN_VERSION_STAGE,
            spec=None,
            attempt=1,
            model=PLUGIN_VERSION_MODEL,
            model_family=None,
            latency_ms=0,
            verdict=verdict,
            failure_cause=None,
            note=_clean(note),
        )
    except Exception as exc:  # noqa: BLE001 — measurement never fails the phase it measures
        sink.note(f"plugin version not recorded: {type(exc).__name__}: {exc}")
        return False


#: What a helper agent's two rows are stamped with. No model decides either of them: the dispatch is
#: observed by `scripts/hook_helper_budget.sh` at `PreToolUse` and the return by
#: `scripts/hook_helper_spend.sh` at `SubagentStop`.
HELPER_DISPATCH_STAGE = "helper-dispatch"
HELPER_SPEND_STAGE = "helper-spend"
HELPER_MODEL = "none (scripts/helper_budget.py)"

#: A number the harness did not report is named as absent rather than defaulted to zero. Token
#: counts are the case that matters: the harness panel shows them and the hook payload does not
#: carry them, so `tokens=0` would read as a free helper rather than as an unmeasured one.
HELPER_UNRECORDED = "unrecorded"


def record_helper_dispatch(
    phase: str, *, agent_type: str, budget_s: int, token: str
) -> bool:
    """Record that a helper agent was dispatched, and with what declared budget (issue #118).

    Emitted at the DISPATCH rather than at the phase close, and separately from the spend row, for
    the reason the issue exists: five helpers were measured frozen for 90 minutes and none of them
    ever returned, so a record written only when a helper finishes cannot see the case. A dispatch
    row with no matching `helper-spend` row IS the helper that never came back, and the count of
    dispatches is the number `compare` had no way to ask for at all.

    Not a new top-level field: firstmate's schema is closed and its producer contract is "add no
    key" (pipeline-conventions §6d). This follows `record_plugin_version` and
    `record_triage_decision` - an ordinary `gate_calls` row on existing keys, with the detail in
    `note`. `verdict` is `NO_VERDICT` because a dispatch judges nothing; the budget check's own
    verdict is the hook's exit code, and a refused dispatch never reaches this at all.
    """
    try:
        return sink.add(
            phase,
            "gate_calls",
            id=f"p{phase}-{HELPER_DISPATCH_STAGE}-{token}",
            stage=HELPER_DISPATCH_STAGE,
            spec=None,
            attempt=1,
            model=HELPER_MODEL,
            model_family=None,
            latency_ms=0,
            verdict=NO_VERDICT,
            failure_cause=None,
            note=_clean(f"agent={agent_type} budget_s={budget_s}"),
        )
    except Exception as exc:  # noqa: BLE001 - measurement never fails the phase it measures
        sink.note(f"helper dispatch not recorded: {type(exc).__name__}: {exc}")
        return False


def record_helper_spend(
    phase: str,
    *,
    agent_type: str,
    token: str,
    elapsed_s: int,
    budget_s: int | None,
    tokens: int | None = None,
) -> bool:
    """Record what a returning helper actually spent: its elapsed time, and its tokens if reported.

    `latency_ms` carries the elapsed time because that is the field that already means "how long
    this took", and the verdict says whether it stayed inside the budget its dispatch declared -
    `NO-GO` for a helper that overran, which is the row a retrospective is looking for. A helper
    with no paired dispatch (spawned before this rule, or under `HELPER_BUDGET_OFF=1`) has no
    declared budget, so it is recorded as `NO_VERDICT` with the budget named absent rather than
    judged against a number nobody stated.
    """
    try:
        if budget_s is None:
            verdict = NO_VERDICT
        else:
            verdict = "NO-GO" if elapsed_s > budget_s else "GO"
        note = (
            f"agent={agent_type} elapsed_s={elapsed_s} "
            f"budget_s={HELPER_UNRECORDED if budget_s is None else budget_s} "
            f"tokens={HELPER_UNRECORDED if tokens is None else tokens}"
        )
        return sink.add(
            phase,
            "gate_calls",
            id=f"p{phase}-{HELPER_SPEND_STAGE}-{token}",
            stage=HELPER_SPEND_STAGE,
            spec=None,
            attempt=1,
            model=HELPER_MODEL,
            model_family=None,
            latency_ms=max(0, int(elapsed_s)) * 1000,
            verdict=verdict,
            failure_cause=None,
            note=_clean(note),
        )
    except Exception as exc:  # noqa: BLE001 - measurement never fails the phase it measures
        sink.note(f"helper spend not recorded: {type(exc).__name__}: {exc}")
        return False


# --- verification attempts, suite size, phase boundaries -----------------------------------------


def record_verification_attempts(phase_dir: str) -> int | None:
    """Record how many verification ATTEMPTS this phase has run, from the verdict record on disk.

    `verification_attempts` is firstmate's "how many verification attempts ran, whatever their
    verdict", and it is read against a cap of 3. It used to increment on every invocation of the
    removed cross-family reading pass's script: one measured phase recorded **8** - three timed-out
    review calls plus five diagnostic retries - while `verdict.json` correctly said `attempt: 1` and
    the real cap sat at 1 of 3, never fired. Read against that cap, 8 says the cap failed and a
    declared hypothesis was disproved; both would have been false. Then the script that counted was
    deleted, and with it the only caller, so every record the pipeline wrote after that carried
    **null** while the phases' own verdicts read `attempt: 4` (issue #120).

    So the count comes from where the cap gets it: `verifier_attempts.current()`, the highest attempt
    on record across `verdict.json` and the `verdict-attempt-<n>.json` archives the Verifier writes.
    It is emitted where the fact is DECIDED - on every verdict write (`hook_verifier.sh`) - and again
    at the handover, and **repeated invocations converge** on the same number rather than
    accumulating, which is the producer contract firstmate's record requires and exactly what a
    retry must not disturb. Retries and provider failures are not lost, they are attributed
    correctly: `gate_calls[]` carries one entry per call with its `failure_cause`.

    A phase with no verdict on record has run no attempt, so nothing is written: `null` keeps
    meaning "not measured" and 0 would claim a measurement nobody took. An unreadable verdict record
    leaves the stored value alone, which is a measurement failing open like every other one here.
    """
    try:
        phase = resolve_phase(phase_dir)
        if phase is None:
            return None
        count = verifier_attempts.current(Path(phase_dir))
        if count == 0:
            return 0
        _stamp(phase, verification_attempts=count)
        return count
    except Exception as exc:  # noqa: BLE001
        sink.note(f"verification attempts not recorded: {type(exc).__name__}: {exc}")
        return None


def test_roots() -> list[Path]:
    """Where the project's tests live — EVERY root `subprocess_check.test_roots()` resolves.

    IMPORTED rather than restated. This used to re-read `$SUBPROC_CHECK_PATHS` here, which is the
    same declaration read in a second place, and the second copy is the one that drifts: the third
    reader, `hook_verifier.sh`, never read the declaration at all and ran a hardcoded
    `--ignore=tests/e2e` over no root, so on any project whose tests are not at `tests/` the suite
    this counted and the suite the gate ran were different populations (issue #120).

    All of them, not the first. Collapsing a multi-root declaration to `test_roots()[0]` is the same
    defect one notch narrower: `hook_verifier.sh` runs `--ignore=<root>/e2e <root>` for every
    declared root, so counting only the first stamps `tests_before`/`tests_after` from a population
    the gate never ran. Absolute, against the project root a caller already knows.
    """
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    return [root / declared for declared in subprocess_check.test_roots()]


def pytest_argv() -> list[str]:
    """How to invoke pytest, preferring the SAME `pytest` `hook_verifier.sh` runs.

    That hook runs the binary off `PATH`; running `-m pytest` under whichever interpreter happens to
    be executing this hook is a different program whenever the two disagree, and where pytest is on
    `PATH` but not importable here it is no program at all — collection prints no summary line and
    the field silently disappears, where the static count it replaced always produced a number. The
    interpreter form stays as the fallback for an install where only that one exists.
    """
    found = shutil.which("pytest")
    return [found] if found else [sys.executable, "-m", "pytest"]


def count_tests() -> int | None:
    """Suite size: the number of items `pytest --collect-only` would run, minus e2e.

    The SAME population `hook_verifier.sh` reports when it runs the phase's suite (`pytest -q`,
    `--ignore=<test root>/e2e <test root>` per declared root on the full-suite fallback) — collected
    test items, not `def test_` lines (issue #46). Both the collected roots and the ignored e2e
    directories come from `test_roots()`, so a project that points `SUBPROC_CHECK_PATHS` elsewhere
    excludes ITS e2e directories rather than a `tests/e2e` that does not exist there, and a project
    declaring several roots counts all of them.

    A declared root that does not EXIST is skipped rather than handed to pytest, the same rule the
    hook applies: pytest treats a missing path as a usage error, which would read as a red suite
    where a project with no tests yet must simply collect nothing.

    Bounded through `proc_group.run_bounded`, never `subprocess.run(timeout=…)`: this runs inside
    `hook_spec_gate.sh`, and a raw timeout stops the process it started and nothing else — leaving
    xdist workers holding the inherited pipes and turning the bound into the unbounded hang that
    gets the whole hook killed. The budget is `gate_timeouts.collect_timeout()`, which is the same
    number that module checks the hook's headroom against.

    None on anything that stops collection from answering — no test root, no pytest, a timeout, an
    import error — the same "not counted" the prior static count used, so `record_phase_open`/
    `record_phase_close` still treat a None here as "skip the field", never a 0.
    """
    roots = [root for root in test_roots() if root.is_dir()]
    if not roots:
        return None
    project_root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    argv = [*pytest_argv(), "-q", "--collect-only"]
    for root in roots:
        argv += [f"--ignore={root / 'e2e'}", str(root)]
    try:
        result = proc_group.run_bounded(
            argv, gate_timeouts.collect_timeout(), cwd=str(project_root)
        )
    except OSError:
        return None
    if result.timed_out:
        sink.note(
            f"suite size not counted: collection exceeded {gate_timeouts.collect_timeout()}s "
            f"({result.elapsed:.0f}s measured) and its process group was killed"
        )
        return None
    match = TESTS_COLLECTED.search(result.stdout)
    return int(match.group(1)) if match else None


#: The four states a read of a phase record can be in, and there are no others. `sink.show` answers
#: a bare `None` for all four, and every caller here used to re-decide which one it meant - one
#: state at a time, a round of review apart: an unreadable record read as an empty one re-stamped a
#: sealed close, an absent one read as unreadable dropped the provenance row on the write that would
#: have created it, and a writer that is simply switched OFF - the documented normal state of a
#: standalone install with no firstmate home - was diagnosed as a record that could not be read,
#: prescribing a retry to an operator who had turned emission off on purpose. The decision lives
#: here now, once, and no caller in this module branches on a `None` again.
RECORD_PRESENT = "present"
RECORD_ABSENT = "absent"
RECORD_UNREADABLE = "unreadable"
RECORD_NOT_CONFIGURED = "not-configured"


class RecordRead(NamedTuple):
    """One read of a phase record: WHICH state it is in, and the contents when it has any."""

    state: str
    record: dict

    @property
    def usable(self) -> bool:
        """Whether `record` may be read - it exists, whether this call opened it or found it."""
        return self.state in (RECORD_PRESENT, RECORD_ABSENT)


def read_record(phase: str) -> RecordRead:
    """The phase's record, READ. This creates nothing, and no caller of it may.

    Not configured is asked FIRST and is never a failure: emission off, no writer resolvable, or a
    writer this process already abandoned all answer `None` to every call, and nothing was read
    because nothing was asked. A read-failure diagnostic there sends its reader to repair a writer
    they deliberately do not have.

    A read alone cannot tell "no record yet" from "the writer refused" - `sink.show` answers `None`
    to both - and the only thing that could tell them apart is OPENING the record, which is a write.
    So a pure read reports what it can honestly claim, that it found no record, and `open_record` is
    where a caller that is about to write asks the question a write may ask.
    """
    if not sink.enabled():
        return RecordRead(RECORD_NOT_CONFIGURED, {})
    record = sink.show(phase)
    if record is None:
        return RecordRead(RECORD_ABSENT, {})
    return RecordRead(RECORD_PRESENT, record)


def open_record(phase: str) -> RecordRead:
    """The record a WRITER is about to write to, opened when it does not exist yet.

    Every caller here goes on to write, and `sink.add`/`sink.set_fields` both `ensure` first, so
    this opens nothing they were not already opening. It is a separate function from `read_record`
    rather than a flag on it because the difference is whether the caller may CREATE a record: a
    reporter that picked up the wrong default would fabricate the very population issue #120
    counts - a phase record with a null `elapsed_minutes`, for a phase that never ran, seeded from
    another phase's ledger by `init --from`.

    Opening is also what tells the two states apart. An absent record is opened and returned with
    its contents; one that is STILL not readable after a successful open is the genuine failure, and
    `usable` is false for it - its contents are unknown, never empty, which is what stops a refused
    read re-stamping a sealed close or narrowing a series the record already holds.
    """
    read = read_record(phase)
    if read.state != RECORD_ABSENT:
        return read
    if sink.ensure(phase):
        opened = sink.show(phase)
        if opened is not None:
            return RecordRead(RECORD_ABSENT, opened)
    return RecordRead(RECORD_UNREADABLE, {})


def record_phase_open(phase_dir: str) -> bool:
    """Stamp when the phase was dispatched and how big the suite was, once."""
    phase = resolve_phase(phase_dir)
    if phase is None:
        return False
    read = open_record(phase)
    if not read.usable:
        return False
    record_plugin_version(phase)
    record = read.record
    fields: dict[str, object] = {}
    if record.get("opened") is None:
        fields["opened"] = _now()
    if record.get("tests_before") is None:
        counted = count_tests()
        if counted is not None:
            fields["tests_before"] = counted
    return _stamp(phase, **fields) if fields else True


#: The document whose committed presence is what LANDING means. `commands/avenger-run.md` §5 commits
#: a phase "after each phase has a passing verdict.json AND its handover.md", and a phase closed at
#: the attempt cap still writes the card (its remainder is carried there). So the card is the one
#: artifact every closing phase commits and no earlier commit can - a spec commit, a plan commit, an
#: amendment commit all leave the directory clean and none of them is the phase landing.
CLOSING_DOCUMENT = "handover.md"


def _committed(path: Path) -> bool | None:
    """Whether `path` is tracked by git - in the index, which with a clean scope means in HEAD.

    None when git cannot answer (no repository, no `git`, a hung `git`), never read as "committed".
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                "git",
                "-C",
                str(path.parent),
                "ls-files",
                "--error-unmatch",
                "--",
                path.name,
            ],
            capture_output=True,
            text=True,
            timeout=sink.timeout(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    # `--error-unmatch` exits 1 for a path git knows nothing about; anything else is git failing.
    return False if proc.returncode == 1 else None


def not_landed_reason(phase_dir: Path) -> str | None:
    """Why `phase_dir` has not LANDED, or None once it has.

    `docs/pipeline-metrics.md` defines close as landed, not implemented, because a stamp taken at
    implementation completion understates the phase by verification, route-backs and close - its
    most expensive stages - and the too-early number is indistinguishable from a good one (issue
    #46). Landing has TWO evidences here, and issue #120 is what the first alone measured:

    * **The closing document is committed.** "Nothing under the directory is uncommitted" was the
      whole test, and a spec commit satisfies it: phase 5 of one measured feature was stamped
      `closed` at its SPEC commit, `elapsed_minutes: 10`, two hours and sixteen minutes before it
      landed and before any verdict existed - and because a close SEALS the record, every defect
      and gate call the phase produced after that was refused by the writer. `handover.md` being
      *written* is still not landing (it is the Verifier's precondition); `handover.md` being
      *committed* is what §5 says the landing commit contains.
    * **Nothing under the directory is uncommitted** - `applicability.changed_paths`, reused rather
      than re-derived so this agrees with every other "what did the diff touch" question in the
      pipeline. An amendment can still reopen a phase after its card exists; while it does, the
      directory is dirty and this refuses.

    Returns None when git cannot answer at all, WRAPPED as a reason by the caller - a producer that
    cannot observe landing must not claim it.

    The evidences are written ONCE, in `_landing`. This and `phase_landed` are projections of that
    one answer, and their callers decide with different halves of it - `record_phase_close` with the
    words, `emission_gate.landed_phases` with the verdict. Written twice, a third evidence added to
    one leaves the gate calling a phase landed that the stamp refuses, and prescribing a
    `phase-close` that by construction records nothing.
    """
    return _landing(phase_dir)[1]


def phase_landed(phase_dir: Path) -> bool | None:
    """Whether `phase_dir` has LANDED: its closing document committed and nothing left uncommitted.

    True once landed; False when git answered and it has not; None when git cannot answer - never
    read as "landed". `not_landed_reason` carries the words; `_landing` decides both.
    """
    return _landing(phase_dir)[0]


def _landing(phase_dir: Path) -> tuple[bool | None, str | None]:
    """The one landing decision: (has it landed, why not). The evidences are stated only here.

    Three verdicts, and the third is not a lighter version of the second: True with no reason;
    False with the reason; and None - git could not answer - which also carries a reason and must
    never be read as landed. Callers take the half they need and cannot disagree about the rest.
    """
    scope = applicability.changed_paths(phase_dir)
    if scope is None:
        return None, "git could not say whether it has landed"
    if applicability.touched(phase_dir, scope):
        return False, "still has uncommitted changes"
    committed = _committed(Path(phase_dir) / CLOSING_DOCUMENT)
    if committed is None:
        return None, f"git could not say whether its {CLOSING_DOCUMENT} is committed"
    if not committed:
        return False, (
            f"has no committed {CLOSING_DOCUMENT} - a commit that leaves the phase directory clean "
            f"is not the phase landing until its contract card is in it (a spec or plan commit "
            f"is not a close)"
        )
    return True, None


#: The mutation gate's stage name in `gate_calls[]`, and the cause a run that never happened carries.
#: `MUTATION_POLICY=advisory` was set for two whole phases while the gate never once ran — no
#: `cosmic-ray.toml` at the repo root and the tool not installed. The hook said so on stderr and
#: exited 0, correctly, because advisory never blocks; but nothing DURABLE distinguished "the gate
#: did not run" from "the gate ran and found nothing", so a hypothesis settled on the premise that
#: `MUTATION_POLICY=advisory` was its changed variable when the gate under test had never executed.
#:
#: Advisory still does not block — that is a deliberate decision, not the defect. What changes is
#: that the absence is recorded where a later reader looks, on an existing collection, because
#: firstmate's schema is closed and a new key is their decision rather than this repo's.
MUTATION_STAGE = "mutation"
MUTATION_UNAVAILABLE_CAUSE = "did-not-run"
MUTATION_UNAVAILABLE_MODEL = "none (cosmic-ray)"


def record_mutation_unavailable(phase_dir: str, reason: str) -> bool:
    """Record that the mutation gate could not run, and why. Never fails the phase.

    Idempotent by content: the hook fires per handover write, and the producer contract firstmate's
    record requires is that repetition converges rather than accumulating.
    """
    try:
        phase = resolve_phase(phase_dir)
        if phase is None:
            return False
        return sink.add(
            phase,
            "gate_calls",
            id=f"p{phase}-{MUTATION_STAGE}-unavailable",
            stage=MUTATION_STAGE,
            spec=None,
            attempt=1,
            model=MUTATION_UNAVAILABLE_MODEL,
            model_family=None,
            latency_ms=0,
            verdict=NO_VERDICT,
            failure_cause=MUTATION_UNAVAILABLE_CAUSE,
            note=_clean(f"mutation gate did not run: {reason}"),
        )
    except Exception as exc:  # noqa: BLE001 — measurement never fails the phase it measures
        sink.note(f"mutation unavailability not recorded: {type(exc).__name__}: {exc}")
        return False


#: Deferrals in `gate_calls[]` (issue #115), on the precedent `record_plugin_version` and
#: `record_mutation_unavailable` set for a fact firstmate's closed schema has no field for: an
#: ordinary row on EXISTING keys, the counts in `note`. A phase that defers everything must be as
#: visible as one that fixes everything, and the record is where that is read.
DEFERRAL_STAGE = "deferral"
DEFERRAL_MODEL = "none (scripts/carried_items.py)"


def record_deferrals(phase_dir: str) -> bool:
    """Record how many findings this phase deferred and how many it re-carried. Never fails the phase.

    Idempotent by id: `carried_items.py defer`/`recarry` emit it the moment the fact is decided, and
    `hook_verifier.sh` emits it again at the handover, so the row converges on the final counts
    rather than appending one per call. Deferred and re-carried are counted separately because they
    are different facts - a phase that originated five deferrals and one that passed five along both
    read `deferred=5` if they were folded together.
    """
    try:
        import carried_items  # lazy: carried_items emits through this module

        phase = resolve_phase(phase_dir)
        if phase is None:
            return False
        records = carried_items.deferrals(Path(phase_dir))
        here = Path(phase_dir).name
        originated = [r for r in records if str(r.get("origin") or here) == here]
        recarried = [r for r in records if str(r.get("origin") or here) != here]
        owners = sorted({str(r.get("owner")) for r in records if r.get("owner")})
        note = (
            f"deferred={len(originated)} recarried={len(recarried)} "
            f"owners={','.join(owners) or '-'} "
            f"ids={','.join(str(r.get('id')) for r in records) or '-'}"
        )
        return sink.add(
            phase,
            "gate_calls",
            id=f"p{phase}-{DEFERRAL_STAGE}",
            stage=DEFERRAL_STAGE,
            spec=None,
            attempt=1,
            model=DEFERRAL_MODEL,
            model_family=None,
            latency_ms=0,
            verdict=NO_VERDICT,
            failure_cause=None,
            note=_clean(note),
        )
    except Exception as exc:  # noqa: BLE001 — measurement never fails the phase it measures
        sink.note(f"deferrals not recorded: {type(exc).__name__}: {exc}")
        return False


#: What the declared-vs-run comparison is stamped with. No model decides it: `suite_command` reads
#: the project's own `.no-mistakes.yaml` and the transcript `verifier_evidence.py` wrote.
TEST_COMMAND_STAGE = "test-command"
TEST_COMMAND_MODEL = "none (scripts/suite_command.py)"

#: A project that DECLARES no test command. Not the same as one whose run could not be read
#: (`suite_command.UNKNOWN`), and the row says which, because the first is a settled property of
#: the project and the second is a measurement that failed.
TEST_COMMAND_UNDECLARED = "undeclared"

#: A `.no-mistakes.yaml` that exists and could not be read for its `test:` key - unreadable bytes,
#: or the key stated twice with different values. A THIRD state, kept apart from `undeclared` for
#: the reason every absence here is named rather than resolved to the nearest answer: "this project
#: has no test command" and "nobody could tell what its test command is" have different remedies.
TEST_COMMAND_UNREADABLE = "unreadable"


def record_test_command(phase_dir: str) -> bool:
    """Record the command the phase's suite actually ran, beside the one the project declares (#119).

    The defect this measures is a step that logged `no test command configured` and improvised an
    invocation while the project plainly declared one; the improvised run PASSED, so nothing in the
    record distinguished it from the declared run passing. Recording the pair is what makes a
    mismatch visible afterwards - `verifier_precheck` is what refuses the pass at the time.

    Not a new top-level field: firstmate's schema is closed and its producer contract is "add no
    key" (pipeline-conventions §6d). This follows `record_plugin_version`, `record_helper_dispatch`
    and `record_deferrals` - an ordinary `gate_calls` row on existing keys with the detail in
    `note`. `id` is fixed per phase, so the handover emitting it more than once converges on one
    row instead of appending duplicates.

    The verdict is the comparison itself, and its three values are three different facts: `GO` when
    the run is the declared command, `NO-GO` when it is a different command or could not be read at
    all under a declaration, and `NO_VERDICT` when the project declares nothing, because there is
    then nothing to judge and a `GO` there would read as a comparison that was made.
    """
    try:
        import suite_command  # lazy: only the handover asks this

        phase = resolve_phase(phase_dir)
        if phase is None:
            return False
        base = suite_command.repo_root(Path(phase_dir))
        declaration = suite_command.declared(base)
        run = suite_command.observed(Path(phase_dir))
        if declaration.problem is not None:
            declared_text, verdict = TEST_COMMAND_UNREADABLE, NO_VERDICT
        elif declaration.command is None:
            declared_text, verdict = TEST_COMMAND_UNDECLARED, NO_VERDICT
        else:
            declared_text = declaration.command
            verdict = (
                "GO"
                if run.argv is not None
                and suite_command.runs_declared(run.argv, declaration.command)
                else "NO-GO"
            )
        return sink.add(
            phase,
            "gate_calls",
            id=f"p{phase}-{TEST_COMMAND_STAGE}",
            stage=TEST_COMMAND_STAGE,
            spec=None,
            attempt=1,
            model=TEST_COMMAND_MODEL,
            model_family=None,
            latency_ms=0,
            verdict=verdict,
            failure_cause=None,
            note=_clean(f"declared={declared_text} ran={run.command}"),
        )
    except Exception as exc:  # noqa: BLE001 — measurement never fails the phase it measures
        sink.note(f"test command not recorded: {type(exc).__name__}: {exc}")
        return False


def record_phase_close(phase_dir: str) -> bool:
    """Stamp when the phase landed, the suite it landed with, and the wall clock it took.

    Refuses the write while `phase_dir` is not yet landed (issue #46, sharpened by issue #120: landed
    means the contract card is COMMITTED, not merely that a commit left the directory clean) - a
    producer that cannot observe landing must not claim it, and this one now can. Called at the
    wrong moment, this is a no-op: no `closed`, no `elapsed_minutes`, no `tests_after`, and a note on
    why.

    CONVERGES on a phase already closed, writing nothing at all. Two producers stamp the close -
    `hook_phase_close.sh` on every commit that touches the phase, and the orchestrator's own
    `phase-close` - and a phase directory is routinely committed again after it closed. A closed
    record SEALS its measurements, so a second stamp is not a repeat, it is drift: firstmate refuses
    it outright, and this would spend a refusal on every later commit to say what the record already
    says. Producing the same answer twice must cost nothing and change nothing.

    `elapsed_minutes` is derived from `opened`, which only a spec WRITE through the Write/Edit hook
    stamps (`hook_spec_gate.sh` -> `phase-open`). A phase whose specs were authored through a shell
    heredoc has no `opened`, and its elapsed time is then genuinely unmeasured: it is left null and
    SAID, never invented from a later moment, because a stamp is worth what its trigger point is
    worth and "the first commit that touched the directory" is not dispatch.

    Both of those read the record, so both take the state `read_record` names rather than deciding
    for themselves what a `None` meant. Read as "empty", a transient refusal defeats the converge
    guard and re-stamps a sealed record, and it prints the never-opened note above - a confident
    diagnosis prescribing a remedy for a cause that does not apply, on the one field this whole
    issue exists to repair. So an unreadable record writes NOTHING and says so; an ABSENT one is
    opened and written as always; and emission being OFF writes nothing and says nothing, because a
    writer nobody configured did not fail to read anything.
    """
    phase = resolve_phase(phase_dir)
    if phase is None:
        return False
    why = not_landed_reason(Path(phase_dir))
    if why is not None:
        sink.note(f"phase {phase} close not recorded: {phase_dir} {why}")
        return False
    read = open_record(phase)
    if not read.usable:
        if read.state == RECORD_UNREADABLE:
            sink.note(
                f"phase {phase} close not recorded: the record could not be READ. A failed read is "
                f"not an empty record - taken for one it defeats the converge guard below and "
                f"re-stamps a SEALED record, and it makes a phase that WAS opened look like one "
                f"nothing ever opened, which is the single state `elapsed_minutes` is left null "
                f"and explained for. Nothing is written; the next close stamp on this phase "
                f"records it."
            )
        return False
    record = read.record
    if record.get("closed") is not None:
        return True
    closed = _now()
    fields: dict[str, object] = {"closed": closed}
    counted = count_tests()
    if counted is not None:
        fields["tests_after"] = counted
    elapsed = _elapsed_minutes(record.get("opened"), closed)
    if elapsed is not None:
        fields["elapsed_minutes"] = elapsed
    else:
        sink.note(
            f"phase {phase}: elapsed_minutes NOT recorded - the record carries no `opened` "
            f"({record.get('opened')!r}). Nothing stamped the phase open: `phase-open` fires from "
            f"hook_spec_gate.sh on a spec written through the Write/Edit tool, and a spec authored "
            f"through a shell heredoc or on a runtime without that hook fires nothing. The close is "
            f"still stamped; the elapsed time is genuinely unmeasured and stays null rather than "
            f"being invented from a later moment."
        )
    return _stamp(phase, **fields)


def _elapsed_minutes(opened: str | None, closed: str) -> int | None:
    """Whole minutes from dispatch to close, including every minute nobody was working."""
    if not opened:
        return None
    try:
        start = datetime.strptime(opened, "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(closed, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds() // 60))


# --- provenance: what the pipeline stamped itself ---------------------------------------------------

#: The measurement scalars a close SEALS (`docs/pipeline-metrics.md`, "A closed record seals its
#: measurements"). These are the fields a person can type in afterwards and the fields this pipeline
#: stamps as the run happens, under one name, so the record alone cannot say which happened - which
#: is how four of fourteen records in one home came to carry a null `elapsed_minutes` and eleven
#: hand-entered close stamps without anything noticing (issue #120).
MEASUREMENT_SCALARS = (
    "opened",
    "closed",
    "elapsed_minutes",
    "spec_rounds",
    "verification_attempts",
    "connection_drops",
    "tests_before",
    "tests_after",
    "turns",
    "output_tokens",
    "cache_read_tokens",
)

#: The row that says which of those the PIPELINE wrote. Not a new top-level field: firstmate's schema
#: is closed and its producer contract is "add no key" (§6d), so this follows the precedent
#: `record_triage_decision`, `record_plugin_version` and `record_mutation_unavailable` set for a fact
#: the schema has no field for - an ordinary `gate_calls` row on EXISTING keys, the fact in `note`,
#: bounded free text the schema already has. `defects[]` already carries this per entry as
#: `recorded_by` (firstmate PR 17); this is the same statement for the scalars, in the form the
#: contract allows. The proper home is a per-scalar `recorded_by` in the schema, which is firstmate's
#: decision and is said in the PR that shipped this rather than pre-empted here.
#:
#: It is written BEFORE `closed`, because `closed` seals every collection, `gate_calls` included: a
#: row that arrives after the close is refused, and a refused provenance row is the one thing this
#: row must never be. The reader (`provenance_report`) intersects the list with the scalars that
#: actually hold a value, so a field this row names that a failed write left null is not counted as
#: stamped - which is what makes writing the row a step early safe rather than an over-claim.
PROVENANCE_STAGE = "metrics-provenance"
PROVENANCE_MODEL = "none (scripts/pipeline_metrics.py)"
PROVENANCE_PREFIX = "pipeline-stamped:"


def _provenance_id(phase: str) -> str:
    return f"p{phase}-{PROVENANCE_STAGE}"


def stamped_fields(record: dict | None) -> set[str]:
    """The measurement scalars the pipeline says it stamped, read back off the provenance row."""
    for entry in (record or {}).get("gate_calls") or []:
        if isinstance(entry, dict) and entry.get("stage") == PROVENANCE_STAGE:
            note = str(entry.get("note") or "")
            if note.startswith(PROVENANCE_PREFIX):
                return {
                    name.strip()
                    for name in note[len(PROVENANCE_PREFIX) :].split(",")
                    if name.strip()
                }
    return set()


def _record_provenance(phase: str, names: set[str]) -> bool:
    """Merge `names` into the phase's provenance row. Fail-open, converging by id.

    The row only ever GROWS within a phase, so the merge needs the names already on it — and it is
    written by upsert, which REPLACES the row it converges on. A read that could not answer is
    therefore not the same as a row holding nothing: merging a failed read against an empty set
    would rewrite the row as the names of this stamp alone. Then `provenance_report` reports every
    earlier pipeline-written scalar under `scalars_not_by_pipeline` — the instrument built to tell a
    pipeline stamp from a hand-entered one, mislabelling its own, with no error anywhere.

    So an unreadable record writes NOTHING and says so. The row keeps what it had: incomplete for as
    long as the read fails, never narrowed to a wrong set. A record that simply does not exist yet
    is the ordinary first stamp, is opened by `read_record`, and is written as always; emission
    switched off writes nothing and says nothing, since there is no row anywhere to narrow.
    """
    try:
        read = open_record(phase)
        if not read.usable:
            if read.state == RECORD_UNREADABLE:
                sink.note(
                    f"provenance not recorded for phase {phase}: the record could not be read, and "
                    f"the row is merged into what it already names — writing it now would narrow "
                    f"it to {','.join(sorted(n for n in names if n in MEASUREMENT_SCALARS))}"
                )
            return False
        known = stamped_fields(read.record) | {
            n for n in names if n in MEASUREMENT_SCALARS
        }
        if not known:
            return True
        return sink.add(
            phase,
            "gate_calls",
            id=_provenance_id(phase),
            stage=PROVENANCE_STAGE,
            spec=None,
            attempt=1,
            model=PROVENANCE_MODEL,
            model_family=None,
            latency_ms=0,
            verdict=NO_VERDICT,
            failure_cause=None,
            note=f"{PROVENANCE_PREFIX} {','.join(sorted(known))}",
        )
    except Exception as exc:  # noqa: BLE001 — measurement never fails the phase it measures
        sink.note(f"provenance not recorded: {type(exc).__name__}: {exc}")
        return False


def _stamp(phase: str, **fields: object) -> bool:
    """Set measurement scalars AND record that the pipeline set them.

    Every scalar this module writes goes through here, so the provenance row cannot miss one by a
    caller forgetting it. `closed` is written LAST and alone: it seals the collections, and the
    provenance row has to be in `gate_calls` before that happens.
    """
    if not fields:
        return True
    ordinary = {k: v for k, v in fields.items() if k != "closed"}
    if ordinary and not sink.set_fields(phase, **ordinary):
        return False
    _record_provenance(phase, set(fields))
    if "closed" in fields:
        return sink.set_fields(phase, closed=fields["closed"])
    return True


def provenance_report(record: dict) -> dict:
    """What fraction of this record the pipeline wrote itself - the field that makes issue #120's
    class self-detecting. A scalar with a value that the provenance row does not name was entered by
    something other than this pipeline (a person, or a producer that predates the row)."""
    stamped = stamped_fields(record)
    held = [name for name in MEASUREMENT_SCALARS if record.get(name) is not None]
    by_pipeline = [name for name in held if name in stamped]
    by_hand = [name for name in held if name not in stamped]
    defects: dict[str, int] = {}
    for defect in record.get("defects") or []:
        if isinstance(defect, dict):
            key = defect.get("recorded_by") or "(absent)"
            defects[key] = defects.get(key, 0) + 1
    return {
        "phase": record.get("phase"),
        "scalars_held": len(held),
        "scalars_by_pipeline": by_pipeline,
        "scalars_not_by_pipeline": by_hand,
        "fraction_by_pipeline": (len(by_pipeline) / len(held)) if held else None,
        "defects_by_recorded_by": defects,
    }


# --- defects: which stage found each one ---------------------------------------------------------

#: The Verifier's finding kinds — the vocabulary `skills/verifier-triage`'s verdict schema declares
#: — mapped onto what the defect actually is. A gamed test and a coverage gap are defects in the TEST
#: SET — real cost, but they must not inflate the count of genuine product defects, which is the
#: number `found_by` exists to split. `tests/` asserts these keys against that schema, so a change to
#: the verifier's vocabulary moves this map instead of silently invalidating it: an unmatched kind
#: falls back to `real=True` and every finding would count as a product defect.
VERIFIER_KIND_REAL = {"code": True, "gamed-test": False, "coverage-gap": False}

#: What `recorded_by` says about every defect THIS pipeline writes: a stage caught it and the same
#: stage emitted it as it ran. There is no route through this module a person reaches by hand — the
#: `defect` command is run by the stage that caught something, and the other two routes read an
#: artifact a script produced — so `stage` is a statement about the writer, never an inference about
#: the caller. A person transcribing a defect after the fact is a different producer: they use
#: firstmate's own CLI and state `operator` there, which is why nothing here can emit that value.
#:
#: It answers a different question from `found_by`, and neither is derived from the other:
#: `found_by` is WHAT caught the defect, this is WHO wrote it down.
RECORDED_BY = "stage"

#: The keys of a defect entry this pipeline will give up rather than lose the entry over.
#: `recorded_by` landed in firstmate's record on 2026-08-20; the record's key surface is closed, so
#: an older firstmate refuses an entry carrying it. Losing the provenance of one defect is a lost
#: measurement; losing the defect is a lost defect, and measurement is never allowed to cost that.
DEFECT_OPTIONAL_FIELDS = ("recorded_by",)


def record_defect(
    phase: str,
    *,
    identifier: str,
    summary: str,
    found_by: str,
    real: bool,
    stage_reached: str,
    severity: str,
    found_by_note: str | None = None,
) -> bool:
    """Record one defect and, above all, WHAT CAUGHT IT.

    This is the field that showed the running suite catching 3 of 15 real defects while mutation,
    probes, review and direct execution caught the rest — and it is the one fact in the whole record
    that cannot be recovered after the run.

    Every defect this module writes also carries `recorded_by: stage` (`RECORDED_BY`), because every
    route here IS a stage emitting as it runs. Left off, a phase's defects would be indistinguishable
    from the phases whose defects a person transcribed afterwards — the two phases that were, and
    whose provenance survived only as prose, which is the whole reason the field exists.
    """
    fields: dict[str, object] = {
        "id": identifier,
        "summary": _clean(summary) or identifier,
        "real": bool(real),
        "found_by": found_by,
        "recorded_by": RECORDED_BY,
        "stage_reached": stage_reached,
        "severity": severity,
    }
    if found_by == "other":
        fields["found_by_note"] = _clean(found_by_note) or "unclassified"
    return sink.add(phase, "defects", _optional=DEFECT_OPTIONAL_FIELDS, **fields)


def record_verifier_findings(phase_dir: str, verdict_path: str | None = None) -> int:
    """Turn every finding the Verifier has CONCLUDED in this phase into a defect attributed to it.

    `found_by` is the one field in the record that cannot be reconstructed after a run, and this is
    the pipeline's highest-volume path into it.

    **It reads the whole verdict history, not one file.** It used to read only the verdict handed to
    it, at the handover — and `skills/verifier-triage` archives a superseded attempt to
    `verdict-attempt-<n>.json`, leaving nothing but its number in `verdict.json`, while a passing
    verdict carries no findings at all. So a phase that raised six findings on attempt 1 and passed
    on attempt 2 closed reporting none: one measured phase recorded a single defect against at least
    five it produced, four of them found by the Verifier by executing code. `verdict_path` is
    accepted and ignored for the callers that still pass it; `verifier_attempts.verdict_records`
    owns which files carry findings.

    **It is called where the fact is DECIDED**, on every write of a verdict (`hook_verifier.sh`), not
    only at the phase close — a finding raised on attempt 1 and fixed by attempt 2 is already gone
    from the live verdict before any close-time reader opens it. Idempotent by finding id, so the
    per-write emission and the close-time one converge on one entry.
    """
    phase = resolve_phase(phase_dir)
    if phase is None:
        return 0
    written = 0
    for path in verifier_attempts.verdict_records(Path(phase_dir)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # One malformed archive does not cost the other attempts their defects — the same reason
            # `verifier_attempts.attempts` walks past an archive it cannot read.
            sink.note(f"verifier findings not recorded from {path.name}: {exc}")
            continue
        if not isinstance(payload, dict):
            continue
        written += _record_findings_of(phase, payload)
    return written


def _record_findings_of(phase: str, payload: dict) -> int:
    """Record one verdict record's findings. Returns how many entries were written."""
    written = 0
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "").strip()
        identifier = str(finding.get("id") or "").strip()
        if not identifier:
            continue
        if record_defect(
            phase,
            identifier=f"verifier-{identifier}",
            summary=str(
                finding.get("instruction")
                or finding.get("target")
                or kind
                or identifier
            ),
            found_by="verifier",
            real=VERIFIER_KIND_REAL.get(kind, True),
            stage_reached="implementation",
            severity="correctness",
        ):
            written += 1
    return written


def record_mutation_survivors(phase_dir: str, score_path: str) -> bool:
    """Record that mutation found behaviours no test catches, from the score it actually produces.

    Deliberately ONE entry carrying the counts, not one per mutant: `mutation_score.py` emits how
    many survived, not which — the identities live in a `cosmic-ray dump` this never reads. Inventing
    a per-mutant record from a number would put a summary in the record that names nothing. Each
    survivor the Verifier turns into a missing case is its own defect, recorded by the Verifier
    through the `defect` command at the moment it reads it.
    """
    phase = resolve_phase(phase_dir)
    if phase is None:
        return False
    try:
        payload = json.loads(Path(score_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sink.note(f"mutation survivors not recorded: {exc}")
        return False
    survivors = payload.get("survivors")
    if not isinstance(survivors, int) or survivors <= 0:
        return False
    return record_defect(
        phase,
        identifier="mutation-survivors",
        summary=(
            f"{survivors} of {payload.get('tested', '?')} tested mutants survived "
            f"(score {payload.get('score', '?')}) — behaviours no test catches"
        ),
        found_by="mutation",
        real=False,
        stage_reached="verification",
        severity="correctness",
    )


#: `breaker.json`'s verdict that carries defects; `clean` names what was attacked and carries none.
BREAKER_FOUND = "found"

#: The id prefix a Breaker counterexample's defect carries, and the `found_by` `emission_gate` reads
#: it back under.
BREAKER_DEFECT_PREFIX = "breaker-"


def counterexample_identity(counterexample: object) -> str | None:
    """The defect id ONE Breaker counterexample gets. Asked by the emitter AND by the floor.

    Identity was defined twice, independently, in two modules that have to agree: this emitter
    hashed the whitespace-normalized text, while `emission_gate.described_counterexamples` counted
    the set of raw `.strip()`ed strings and `check_defects` required the recorded count to reach the
    described one. Two counterexamples differing only in internal whitespace were then ONE entry to
    the emitter and TWO to the floor - a handover gate reporting a GAP whose own printed remedy,
    re-running this emitter, could by construction never raise the count. That is the same wedge a
    truncated id already produced once, surviving one normalization further along, which is what
    makes the split the defect rather than either normalization. So there is one owner and both
    sides ask it: a later change to what counts as "the same counterexample" moves both or neither.

    Empty (or whitespace-only) is not a counterexample and has no identity - None, never a shared
    id every blank would converge onto.

    Two properties the earlier rounds established and this keeps. **Stable** across repeated
    emission, so the per-write hook and the close-time pass converge on one entry each. **Bounded**,
    so an arbitrarily long counterexample cannot become the record's identity field - the digest is
    over the WHOLE text, so the readable prefix truncates without identity truncating with it.
    """
    text = str(counterexample or "").strip()
    if not text:
        return None
    digest = hashlib.sha1(  # noqa: S324 — identity, not security
        " ".join(text.split()).encode("utf-8")
    ).hexdigest()[:10]
    return f"{BREAKER_DEFECT_PREFIX}{text[:80]}-{digest}"


def record_breaker_findings(phase_dir: str, breaker_path: str) -> int:
    """Turn every counterexample the Breaker LANDED into a defect attributed to it.

    The Breaker is the stage that found phase 8's plaintext-credential leaks by constructing inputs,
    and until this existed nothing recorded what it found: `skills/verifier-triage` asked the
    Verifier to run `defect --found-by breaker` by hand, which is an instruction with no mechanism
    (issue #120, "stages find defects they never emit"). `breaker.json` is the Breaker's own record
    and `breaker_gate.py` already refuses a `found` verdict naming no counterexample, so the
    counterexamples are the finding set, read where they are written - `hook_verifier.sh` on every
    `breaker.json` write - and again at the handover. Idempotent by counterexample, so the two
    converge on one entry each.

    Identity is a digest of the WHOLE counterexample, the construction `record_spec_gate_findings`
    already uses, with a readable prefix. A truncated id is not identity: `emission_gate` counts the
    full strings, so two counterexamples sharing a long prefix upserted onto one entry while the
    floor counted two - a permanent handover GAP whose own printed remedy, re-running this emitter,
    could never raise the count. Bounded, and stable across repeated emission, so the per-write hook
    and the close-time pass still converge on one entry each.

    `breaker.json` carries no severity, so every entry is `correctness`; the Breaker attacks
    security paths, and a reader who needs the split reads the counterexample test. Said here rather
    than guessed at.

    `stage_reached` is `verification`, firstmate's "the last stage the defect passed through
    undetected". The Breaker runs only AFTER a passing verdict - `pipeline_state.py` reaches its
    branch once the verdict passes and no amendment is owed - so a counterexample it lands got past
    implementation AND verification, and recording `implementation` would read as a defect
    verification would still have caught, crediting the Verifier in the one column beside `found_by`
    that cannot be recovered after the run. `record_mutation_survivors`, the other post-verdict
    recorder here, already says `verification`.
    """
    phase = resolve_phase(phase_dir)
    if phase is None:
        return 0
    try:
        payload = json.loads(Path(breaker_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sink.note(f"breaker findings not recorded: {exc}")
        return 0
    if not isinstance(payload, dict) or payload.get("verdict") != BREAKER_FOUND:
        return 0
    written = 0
    for counterexample in payload.get("counterexamples") or []:
        text = str(counterexample or "").strip()
        identity = counterexample_identity(counterexample)
        if identity is None:
            continue
        if record_defect(
            phase,
            identifier=identity,
            summary=f"Breaker counterexample: {text}",
            found_by="breaker",
            real=True,
            stage_reached="verification",
            severity="correctness",
        ):
            written += 1
    return written


def record_spec_gate_findings(spec_path: str | None, blocking: list[dict]) -> int:
    """Record each BLOCKING observation the spec gate derived as a defect the spec gate found.

    `found_by: spec-gate` is in firstmate's vocabulary - "caught while judging the spec, before
    implementation" - and nothing emitted it: `record_triage_decision` records the arithmetic (how
    many observations, how many blocked) and the blockers themselves went to the spec writer and
    nowhere else. Called from `spec_gate_triage.py` at the decide step, the one place the verdict is
    derived, so it is reached on exactly the path the defect is found on.

    `real` is False: a spec is an artifact, and the schema keeps artifact defects out of the
    product-defect count. Idempotent by statement, so a body re-gated over the same blocker
    converges on one entry.

    **Fail-open at the definition, not at the call site**, like every other recorder here. Its one
    caller is `spec_gate_triage.py`, whose exit code IS the gate's verdict and whose only escape
    hatch is `guard_scope.run`, which re-raises anything that is not a `SystemExit`: an exception
    from this emission would leave the process exiting 1, the code that script spells `BLOCKED`. A
    measurement may not decide a verdict (§6d), and putting the guard here rather than around the
    call is what stops the next caller reintroducing the hole by forgetting the wrapper.
    """
    try:
        if not spec_path or not blocking:
            return 0
        phase = resolve_phase(spec_path)
        spec = resolve_spec(spec_path)
        if phase is None or spec is None:
            return 0
        written = 0
        for observation in blocking:
            if not isinstance(observation, dict):
                continue
            statement = str(observation.get("statement") or "").strip()
            if not statement:
                continue
            digest = hashlib.sha1(  # noqa: S324 — identity, not security
                " ".join(statement.split()).encode("utf-8")
            ).hexdigest()[:10]
            category = str(observation.get("category") or "blocking")
            if record_defect(
                phase,
                identifier=f"spec-gate-{spec}-{digest}",
                summary=f"[{category}] {statement}",
                found_by="spec-gate",
                real=False,
                stage_reached="spec",
                severity="correctness",
            ):
                written += 1
        return written
    except Exception as exc:  # noqa: BLE001 — measurement never decides a verdict
        sink.note(f"spec gate findings not recorded: {type(exc).__name__}: {exc}")
        return 0


# --- skill loads ----------------------------------------------------------------------------------


def current_phase() -> str | None:
    """The phase in flight, for an observation that arrives with no path to derive one from.

    A skill load has no artifact path, so the phase comes from the artifact tree instead: the most
    recently touched phase directory. That is sound because phases are built one at a time, fully
    through build-and-verify (pipeline-conventions §5) — there is only ever one in flight. No phase
    directory at all means no record to write to, which is reported as None rather than guessed.
    """
    declared = resolve_phase("")
    if declared:
        return declared
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    newest: tuple[float, str] | None = None
    for phase_dir in root.glob("docs/features/*/phases/*"):
        if not phase_dir.is_dir():
            continue
        phase = _phase_from(str(phase_dir))
        if phase is None:
            continue
        try:
            touched = max(
                (p.stat().st_mtime for p in phase_dir.rglob("*") if p.is_file()),
                default=phase_dir.stat().st_mtime,
            )
        except OSError:
            continue
        if newest is None or touched > newest[0]:
            newest = (touched, phase)
    return newest[1] if newest else None


def observing_stage(agent_type: str = "") -> str:
    """Whose load this is: the harness's own answer, else the one subagent currently running.

    A PostToolUse payload does not always name the agent that made the call, and attributing a
    subagent's load to the main thread would leave that stage's seeded requirement unanswered — a
    silent non-load that never happened. `.agent-activity.jsonl` already exists to say which
    subagents are alive right now, so it is read here rather than guessed at. Anything ambiguous
    (none live, or several) is the main thread, which is itself a real stage: it writes specs and
    runs verifier triage.
    """
    named = re.sub(r"^.*:", "", (agent_type or "").strip())
    if named:
        return named
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    log = Path(os.environ.get("ACTIVITY_LOG") or root / ".agent-activity.jsonl")
    balance: dict[str, int] = {}
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            agent = re.sub(r"^.*:", "", str(event.get("agent_type") or "").strip())
            if not agent:
                continue
            balance[agent] = balance.get(agent, 0) + (
                1 if event.get("event") == "SubagentStart" else -1
            )
    except OSError:
        return "main-thread"
    live = [agent for agent, count in balance.items() if count > 0]
    return live[0] if len(live) == 1 else "main-thread"


def record_skill_load(
    phase: str, *, stage: str, skill: str, evidence: str, loaded: bool = True
) -> bool:
    """Record that a stage had a skill's content in context — on evidence, never on an instruction.

    An instruction to load a skill is not a load. `loaded` is true only where something observed the
    content arriving, and `evidence` names what observed it.

    A seed (`loaded=False`) never overwrites an observed load. Both are written by SubagentStart
    hooks and the harness may run them in either order, so making this an ordering rule in
    hooks.json would be a guard attached to one caller instead of to the fact: evidence, once
    observed, is not unobserved by a later seed.
    """
    # Normalised here, not at each caller: a seed and the load that answers it arrive from two
    # different hooks, one of which sees "plan-build-verify:avenger-verifier" where the other sees
    # "avenger-verifier". Two spellings of one stage are two rows, and a requirement answered in the
    # row nobody reads is the silence this collection exists to break.
    stage = observing_stage(stage)
    identity = f"{stage}:{skill}"
    if not loaded:
        read = open_record(phase)
        if not read.usable:
            return False
        for entry in read.record.get("skill_loads") or []:
            if entry.get("id") == identity and entry.get("loaded"):
                return True
    fields: dict[str, object] = {
        "id": identity,
        "stage": stage,
        "skill": skill,
        "required": skill in skill_contract.required_skills(stage),
        "loaded": bool(loaded),
    }
    if loaded:
        fields["evidence"] = _clean(evidence) or "observed"
    return sink.add(phase, "skill_loads", **fields)


# --- CLI -------------------------------------------------------------------------------------------


def _phase_of_ref(ref: str) -> str | None:
    """A phase from a path, or from a bare number typed by a caller that has one."""
    direct = re.fullmatch(r"\d{1,2}", (ref or "").strip())
    return f"{int(ref):02d}" if direct else resolve_phase(ref)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    spec_round = sub.add_parser(
        "spec-round", help="record one COMPLETED gate evaluation of a spec"
    )
    spec_round.add_argument("spec")
    spec_round.add_argument(
        "--verdict",
        required=True,
        choices=list(SPEC_ROUND_VERDICTS),
        help="the verdict the gate REACHED. A round is a completed evaluation; "
        "a gate that could not answer is not one.",
    )

    killed = sub.add_parser(
        "gate-killed", help="record a gate the harness killed mid-call"
    )
    killed.add_argument("--stage", required=True)
    killed.add_argument("--spec-path")
    killed.add_argument("--phase-dir")
    killed.add_argument("--model", default=os.environ.get("GATE_MODEL", "unknown"))

    attempts = sub.add_parser(
        "verifier-attempts",
        help="record how many verification attempts are on record, derived from verdict.json",
    )
    attempts.add_argument("phase_dir")

    findings = sub.add_parser(
        "verifier-findings", help="record the Verifier's findings as defects"
    )
    findings.add_argument("phase_dir")
    findings.add_argument("verdict")

    breaker = sub.add_parser(
        "breaker-findings", help="record the Breaker's counterexamples as defects"
    )
    breaker.add_argument("phase_dir")
    breaker.add_argument("breaker")

    survivors = sub.add_parser(
        "mutation-survivors", help="record surviving mutants as defects"
    )
    survivors.add_argument("phase_dir")
    survivors.add_argument("score")

    unavailable = sub.add_parser(
        "mutation-unavailable",
        help="record that the mutation gate could not run, and why",
    )
    unavailable.add_argument("phase_dir")
    unavailable.add_argument("reason")

    load = sub.add_parser("skill-load", help="record an observed skill load")
    load.add_argument("--stage", required=True)
    load.add_argument("--skill", required=True)
    load.add_argument("--evidence", required=True)
    load.add_argument("--phase-ref", default="")
    load.add_argument(
        "--not-loaded",
        action="store_true",
        help="record a required skill with no observed load",
    )

    required = sub.add_parser(
        "skill-required", help="print the skills a stage's contract names"
    )
    required.add_argument("--stage", required=True)

    deferrals = sub.add_parser(
        "deferrals", help="record how many findings a phase deferred and re-carried"
    )
    deferrals.add_argument("phase_dir")

    test_command = sub.add_parser(
        "test-command",
        help="record the suite command that ran beside the one the project declares",
    )
    test_command.add_argument("phase_dir")

    defect = sub.add_parser("defect", help="record a defect and what caught it")
    defect.add_argument(
        "--phase-ref",
        required=True,
        help="a phase directory, a path inside one, or a bare phase number; one "
        "that resolves to no phase exits 2 rather than reporting a failed "
        "write, because the argument is what must change",
    )
    defect.add_argument("--id", required=True, dest="identifier")
    defect.add_argument("--summary", required=True)
    defect.add_argument("--found-by", required=True)
    defect.add_argument("--stage-reached", default="implementation")
    defect.add_argument("--severity", default="correctness")
    defect.add_argument("--found-by-note")
    defect.add_argument(
        "--not-real",
        action="store_true",
        help="a defect in a test, fixture or artifact, not in the product",
    )

    for name in ("phase-open", "phase-close"):
        boundary = sub.add_parser(name, help=f"stamp the phase {name.split('-')[1]}")
        boundary.add_argument("phase_dir")

    provenance = sub.add_parser(
        "provenance",
        help="report what fraction of the record the pipeline wrote itself",
    )
    provenance.add_argument("phase_ref")

    spool = sub.add_parser(
        "spool",
        help="list the writes firstmate's seal turned away; --drain replays them (issue #98)",
    )
    spool.add_argument(
        "--phase", default=None, help="a two-digit phase; default every phase"
    )
    spool.add_argument(
        "--drain",
        action="store_true",
        help="replay the queued writes now; exit 1 while any is still refused",
    )

    return parser


class UnresolvablePhaseRef(Exception):
    """A `--phase-ref` that names no phase — the ARGUMENT is the cause, not the writer.

    Kept distinct from every emission failure because the remedy is: routed through the write-failed
    shape, a stage is told the write failed and to "re-run this exact command", which can only fail
    identically, since the command is what is wrong. That is the retry loop the two-shape split
    exists to end, one cause further out.
    """


def _dispatch(args: argparse.Namespace) -> bool | None:
    """Run one CLI command. Prints nothing to stdout except a value the caller asked for.

    Returns whether the record was written, but only `main`'s handling of `defect` reads it. Every
    other command here is called from a hook's `|| true` fail-open path (`hook_spec_gate.sh`,
    `hook_verifier.sh`, `hook_mutation.sh`) and must keep exiting 0 no matter what — a non-zero exit
    there would stop the turn over a missing number. `defect` is the one command a stage runs
    directly, off that path, specifically so it can be told whether its own catch landed.
    """
    if args.command == "spec-round":
        value = record_spec_round(args.spec, verdict=args.verdict)
        print(value if value is not None else 1)
    elif args.command == "gate-killed":
        record_gate_call(
            model=args.model,
            rubric=None,
            stage=args.stage,
            target=args.spec_path or args.phase_dir,
            cause=HOOK_KILLED,
            detail="the harness killed the hook while the gate was still running",
        )
    elif args.command == "verifier-attempts":
        record_verification_attempts(args.phase_dir)
    elif args.command == "verifier-findings":
        record_verifier_findings(args.phase_dir, args.verdict)
    elif args.command == "breaker-findings":
        record_breaker_findings(args.phase_dir, args.breaker)
    elif args.command == "mutation-survivors":
        record_mutation_survivors(args.phase_dir, args.score)
    elif args.command == "mutation-unavailable":
        record_mutation_unavailable(args.phase_dir, args.reason)
    elif args.command == "deferrals":
        record_deferrals(args.phase_dir)
    elif args.command == "test-command":
        record_test_command(args.phase_dir)
    elif args.command == "skill-load":
        phase = _phase_of_ref(args.phase_ref) or current_phase()
        if phase:
            record_skill_load(
                phase,
                stage=args.stage,
                skill=args.skill,
                evidence=args.evidence,
                loaded=not args.not_loaded,
            )
    elif args.command == "skill-required":
        for name in sorted(skill_contract.required_skills(args.stage)):
            print(name)
    elif args.command == "defect":
        phase = _phase_of_ref(args.phase_ref)
        if phase is None:
            sink.note(
                f"defect {args.identifier} not recorded: --phase-ref {args.phase_ref!r} does not "
                "resolve to a phase"
            )
            raise UnresolvablePhaseRef(args.phase_ref)
        return record_defect(
            phase,
            identifier=args.identifier,
            summary=args.summary,
            found_by=args.found_by,
            real=not args.not_real,
            stage_reached=args.stage_reached,
            severity=args.severity,
            found_by_note=args.found_by_note,
        )
    elif args.command == "phase-open":
        record_phase_open(args.phase_dir)
    elif args.command == "phase-close":
        record_phase_close(args.phase_dir)
    elif args.command == "provenance":
        phase = _phase_of_ref(args.phase_ref)
        if phase is None:
            raise UnresolvablePhaseRef(args.phase_ref)
        read = read_record(phase)
        if read.state == RECORD_NOT_CONFIGURED:
            sink.note(
                f"provenance: nothing to report for {args.phase_ref!r} - no metrics writer is "
                f"configured, so this project keeps no phase record for anyone to have written."
            )
        elif read.state != RECORD_PRESENT:
            sink.note(f"provenance: no record for {args.phase_ref!r} yet")
        else:
            print(json.dumps(provenance_report(read.record), indent=2, sort_keys=True))
    elif args.command == "spool":
        return _spool_command(args.phase, args.drain)
    return None


def _spool_command(phase: str | None, drain: bool) -> bool:
    """Report the queue, and replay it when asked. Reports the OUTCOME: what landed, what did not."""
    waiting = sink.queued(phase)
    if not waiting:
        print(
            f"{sink.PREFIX} spool: nothing queued at {sink.spool_path()}",
            file=sys.stderr,
        )
        return True
    for entry in waiting:
        print(
            f"{sink.PREFIX} spool: phase {entry.get('phase')} refused at {entry.get('refused_at')}: "
            f"{' '.join(str(a) for a in entry.get('argv') or [])}",
            file=sys.stderr,
        )
    if not drain:
        print(
            f"{sink.PREFIX} spool: {len(waiting)} write(s) waiting. Reopen the phase "
            f"(`fm-pipeline-metrics.sh set <phase> --reopen closed:=null`), then `spool --drain`.",
            file=sys.stderr,
        )
        return True
    landed, remaining = sink.drain(phase)
    print(
        f"{sink.PREFIX} spool: drained - {landed} landed, {remaining} still refused.",
        file=sys.stderr,
    )
    return remaining == 0


def main(argv: list[str] | None = None) -> int:
    """0 on every emission path except `defect`, which is loud on purpose.

    Every other command here runs from a hook's `|| true` fail-open path, so a phase must never fail
    because a number went unrecorded. `defect` is different: it is the one field the record exists
    for and the only one unrecoverable after the run (`skills/pipeline-conventions` §6d), and it is
    always run directly by a stage rather than from a hook — so nothing anywhere is fail-open on its
    behalf. A recorder that quietly does nothing there is indistinguishable from one with nothing to
    record, which is exactly the failure this repairs — so an emission that could not be written
    exits non-zero and says why on stderr, unless the operator explicitly turned emission off via
    `AVENGER_METRICS_OFF=1`, which is configured behaviour rather than a failure.

    The non-zero exit is one code and two messages, split on whether the remedy is the stage's to
    apply. A configured writer that refused, hung or could not write is retryable, so that message
    asks for exactly that. No writer configured anywhere is not: it is the documented state of a
    standalone install, it will fail the same way on every attempt, and a stage told to "fix the
    cause and re-run" there loops instead of working, so that message is terminal, addressed to the
    operator, and says to move on.

    A `--phase-ref` that resolves to no phase is neither of those: nothing about the writer is
    wrong, the ARGUMENT is. It carries its own marker and exits `USAGE_ERROR` — the code
    `parse_args` already returns for a caller that typed the command wrong, which is what this is.
    `AVENGER_METRICS_OFF=1` does not quiet it, for the same reason it does not quiet a parse error:
    turning emission off is a statement about recording, not a licence to pass an argument that
    names nothing.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except (
        SystemExit
    ) as exc:  # a usage error is the caller's bug, and is worth the nonzero exit
        return int(exc.code or USAGE_ERROR)
    try:
        ok = _dispatch(args)
    except UnresolvablePhaseRef:
        print(
            _defect_phase_ref_message(args)
            if args.command == "defect"
            else _phase_ref_message(args),
            file=sys.stderr,
        )
        return USAGE_ERROR
    except Exception as exc:  # noqa: BLE001 — measurement never fails the thing it measures
        sink.note(f"{args.command} not recorded: {type(exc).__name__}: {exc}")
        ok = False if args.command == "defect" else None
    if (
        args.command == "defect"
        and ok is False
        and os.environ.get("AVENGER_METRICS_OFF") != "1"
    ):
        print(_defect_failure_message(args), file=sys.stderr)
        return 1
    if args.command == "spool" and ok is False:
        return 1  # an operator asked for a drain and something is still refused: say so in the exit
    return 0


#: The markers a stage reads to tell a retryable `defect` failure from a terminal one, and both from
#: a caller that named a phase that does not exist. NO ONE OF THEM CONTAINS ANOTHER, and
#: `tests/test_pipeline_metrics.py` holds them to it: a reader matching the retryable marker against
#: either other message would be told to re-run a command that can only fail the same way, which is
#: the loop this split exists to end.
DEFECT_WRITE_FAILED = "DEFECT NOT RECORDED - WRITE FAILED"
DEFECT_NO_WRITER = "DEFECT NOT RECORDED - NO METRICS WRITER CONFIGURED"
DEFECT_BAD_PHASE_REF = "DEFECT NOT RECORDED - UNRESOLVABLE --phase-ref"

#: What a caller that typed the command wrong exits with, whichever layer caught it.
USAGE_ERROR = 2


def _defect_lost(args: argparse.Namespace) -> str:
    """What every unrecorded `defect` says first, whatever stopped it."""
    return (
        f"{args.identifier} (found_by={args.found_by}) was NOT written to the metrics record. This "
        "is the single field the record exists for and it cannot be reconstructed once the run is "
        "over."
    )


def _phase_ref_message(args: argparse.Namespace) -> str:
    """What any non-`defect` command whose ref names no phase says: the ARGUMENT is the cause.

    A ref that resolves to nothing never reached the record, so it is not one of the four states a
    read can be in and must not borrow one: reported as an unreadable record it sends its reader to
    repair a writer that is working perfectly, which is the same mis-diagnosis those states were
    named to remove, one layer further out.
    """
    return (
        f"{sink.PREFIX} {args.command}: {args.phase_ref!r} does not resolve to a phase, so there "
        "was no record to read. The metrics writer was never reached and nothing about it is known "
        "to be wrong - the remedy is the ARGUMENT: name an existing phase directory (or a path "
        "inside one), such as docs/features/<feature>/phases/<n>-<slug>, or the phase number."
    )


def _defect_phase_ref_message(args: argparse.Namespace) -> str:
    """What a `defect` whose `--phase-ref` names no phase says: fix the argument, not the writer."""
    return (
        f"{sink.PREFIX} {DEFECT_BAD_PHASE_REF}: {_defect_lost(args)} The metrics writer was never "
        f"reached and nothing about it is known to be wrong: --phase-ref {args.phase_ref!r} does "
        "not resolve to a phase, so there was no record to write to. The remedy is the ARGUMENT, "
        "not the writer: re-run with a --phase-ref naming an existing phase directory (or a path "
        "inside one), such as docs/features/<feature>/phases/<n>-<slug>. Re-running it UNCHANGED "
        "fails identically."
    )


def _defect_failure_message(args: argparse.Namespace) -> str:
    """What a `defect` that could not be written says, in the two shapes it comes in."""
    lost = _defect_lost(args)
    if sink.configured():
        return (
            f"{sink.PREFIX} {DEFECT_WRITE_FAILED}: {lost} A metrics writer IS configured for this "
            "run, so the write itself failed and the remedy is yours: see the [metrics] diagnostic "
            "above for the cause, fix it, and re-run this exact command."
        )
    return (
        f"{sink.PREFIX} {DEFECT_NO_WRITER}: {lost} No writer is "
        "configured for this run: fm-pipeline-metrics.sh is not on PATH and AVENGER_METRICS_CMD is "
        "unset. That is the expected state of a standalone install with no firstmate home, and it "
        "is a standing property of this environment rather than something this command can repair. "
        "DO NOT re-run it - it will fail identically. Move on with the phase. FOR THE OPERATOR: "
        "point AVENGER_METRICS_CMD at firstmate's own fm-pipeline-metrics.sh to record defects, or "
        "set AVENGER_METRICS_OFF=1 to record none deliberately and silently. Under --auto this is "
        "worth surfacing rather than looping on."
    )


if __name__ == "__main__":
    # NOT routed through `guard_scope.run`, and that is a decision rather than an omission. This
    # module carries a scope statement in `guard_scope.toml` - what a clean run does NOT establish
    # (issue #97) - but emitting it on the clean branch would break a documented contract:
    # `AVENGER_METRICS_OFF=1` records none deliberately and SILENTLY (CLAUDE.md 6d), and most
    # callers are hooks that discard stderr, so the statement would be noise where it is read and a
    # broken promise where it is not. `scripts/guard_proof.py` carries the matching entry in
    # `EMISSION_EXCEPTIONS`, which is what keeps a statement-without-emission honest rather than
    # invisible. The one thing this module REFUSES - a spec round for a gate that reached no
    # verdict - carries its own reason where somebody is being told a verdict.
    raise SystemExit(main())
