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

That loud exit comes in TWO SHAPES, because one remedy belongs to the stage and the other does not.
When a writer IS configured and the write failed, the stage can fix the cause and re-run the exact
command, so the message says so. When NO writer is configured at all - nothing on `PATH`,
`AVENGER_METRICS_CMD` unset - that is a standing property of the environment, the expected state of a
standalone install with no firstmate home, and re-running only fails identically; that message is
addressed to the operator, says the defect could not be recorded, and tells the stage to move on
rather than loop. `AVENGER_METRICS_OFF=1` is neither: it is a deliberate choice, and stays silent.

Emission is attached to the *fact*, not to the caller. `record_gate_call` lives inside
`gate_runner.py`, the one place every gate call passes through, so a new gate is instrumented by
existing. `record_spec_round` is idempotent by CONTENT — it reuses the shipped rebuildable gate
cache to remember which body it last counted — so any caller may call it on any spec write and the
record converges instead of double-counting a round.

CLI, for the shell emission points (all fail open, all exit 0, except `defect` — see above):
    pipeline_metrics.py spec-round <spec.md>
    pipeline_metrics.py gate-killed --stage <s> [--spec-path <p>] [--phase-dir <d>]
    pipeline_metrics.py verifier-attempt <phase-dir>   (derived from verdict.json, not a counter)
    pipeline_metrics.py verifier-findings <phase-dir> <verifier-review.json>
    pipeline_metrics.py mutation-survivors <phase-dir> <mutation-score.json>
    pipeline_metrics.py skill-load --stage <s> --skill <k> --evidence <where>
    pipeline_metrics.py skill-required --stage <s>
    pipeline_metrics.py defect --phase-ref <p> --id <i> --summary <s> --found-by <f> ...   (exits 1 on failure)
    pipeline_metrics.py phase-open <phase-dir>
    pipeline_metrics.py phase-close <phase-dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_sink as sink  # noqa: E402
import skill_contract  # noqa: E402
import verifier_attempts  # noqa: E402
from spec_gate_cache import keep, normalized, previous, split_spec  # noqa: E402

#: `docs/features/<feature>/phases/<n>-<slug>/…` — the phase number is in the path, so no stage has
#: to be told which phase it is in.
PHASE_IN_PATH = re.compile(r"(?:^|/)phases/(\d{1,2})-[^/]+")

#: `…/specs/<n>.<k>-<subslug>/spec.md` — the spec id firstmate records is `<n>.<k>`.
SPEC_IN_PATH = re.compile(r"(?:^|/)specs/(\d{1,2})\.(\d+)-[^/]+")

#: Requirement ids as pipeline-conventions defines them: `R<n>.<k>.<m>`.
REQUIREMENT_ID = re.compile(r"\bR\d+\.\d+\.\d+\b")

#: A test function, for the suite-size count. Deliberately a static count rather than a pytest
#: collection: it never fails on an import error, costs nothing, and — the point — `tests_before`
#: and `tests_after` are counted the SAME way, so their difference is a real delta and not an
#: artifact of two different counting methods.
TEST_FUNCTION = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)

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
    "verifier-review.md": "verifier",
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
    "config": ("error", "other"),
    "cross-family": ("error", "other"),
    "unknown-vendor": ("error", "other"),
    "runner-untrusted": ("error", "other"),
    "provider-not-found": ("error", "other"),
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


def _spec_round(record: dict | None, spec: str | None) -> int:
    """How many rounds this spec has been through, which is the attempt a spec gate belongs to."""
    if not record or not spec:
        return 1
    for entry in record.get("specs") or []:
        if entry.get("id") == spec:
            return max(1, len(entry.get("bytes_by_round") or []))
    return 1


def _attempt(record: dict | None, stage: str, spec: str | None) -> int:
    """The attempt a gate call belongs to: the spec's round, or the phase's verification attempt."""
    declared = (os.environ.get("AVENGER_METRICS_ATTEMPT") or "").strip()
    if declared:
        try:
            return int(declared)
        except ValueError:
            pass
    if stage == "verifier":
        return max(1, int((record or {}).get("verification_attempts") or 1))
    return _spec_round(record, spec)


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
        stage = (os.environ.get("AVENGER_METRICS_STAGE") or "").strip() or stage or \
            stage_from_rubric(rubric)
        spec = resolve_spec(spec_path, target)
        record = sink.show(phase)
        attempt = _attempt(record, stage, spec)
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
    *, spec_path: str | None, observations: int, blocking: int, notes: int, approved: bool
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
            sink.note(f"gate failure cause {cause!r} has no mapped outcome — recorded as other")
        return CAUSE_MAP.get(cause, ("error", "other"))
    token = (verdict or "").strip().upper()
    if token in ("GO", "PASS"):
        return "GO", None
    if token == "REVIEW":
        return "REVIEW", None
    return "NO-GO", None


# --- spec rounds --------------------------------------------------------------------------------


def record_spec_round(spec_path: str) -> int | None:
    """Measure one spec at this round: its body size, and the requirements it now carries.

    Growth across `bytes_by_round` is the ratchet made visible — one spec went 25k -> 51k while
    being rewritten to satisfy a gate, and nothing anywhere noticed.

    Idempotent by CONTENT, not by caller discipline. The body this last counted is kept in the
    shipped rebuildable gate cache under its own key, so calling this on every spec write records
    one round per genuinely changed body. Losing that cache costs one duplicated round, which is
    why it is a cache and not an artifact.

    A body is remembered as counted only once the record actually took it. A refused write that
    still cached its body would make the next call believe the round was already there, and that
    round would be missing from `bytes_by_round` forever — a hole in the one growth series this
    measures. Returns None when nothing was recorded, so the next write retries the round.
    """
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

        record = sink.show(phase)
        rounds = list(_spec_entry(record, spec).get("bytes_by_round") or [])
        counted = previous(path, "metrics")
        if counted is not None and normalized(counted) == normalized(body) and rounds:
            return len(rounds)   # this exact body is already one of the rounds above

        rounds.append(len(body.encode("utf-8")))
        if not sink.add(
            phase,
            "specs",
            id=spec,
            requirements=len(set(REQUIREMENT_ID.findall(body))),
            bytes_by_round=rounds,
        ):
            return None
        sink.set_fields(phase, spec_rounds=_spec_rounds(sink.show(phase)))
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
        (len(entry.get("bytes_by_round") or []) for entry in (record or {}).get("specs") or []),
        default=1,
    )


# --- verification attempts, suite size, phase boundaries -----------------------------------------


def open_verification_attempt(phase_dir: str) -> int | None:
    """The verification ATTEMPT this run belongs to, derived from the verdict record on disk.

    It used to increment on every invocation of `verifier_review.sh`, which is not the same thing
    and reads as if it were. One measured phase recorded **8** — three cross-family review calls that
    timed out plus five diagnostic retries — while `verdict.json` correctly said `attempt: 1` and the
    real three-attempt cap sat at 1 of 3, never fired. Read against a cap of 3, that number says the
    cap failed and a declared hypothesis was disproved. Both would have been false, and the metric is
    the only thing that said so.

    So the attempt comes from where the cap gets it: `verifier_attempts.current()`, over
    `verdict.json` and the `verdict-attempt-<n>.json` archives the Verifier writes. This run is the
    one after the last one on record, and **repeated invocations converge** on that same number
    rather than accumulating — which is the producer contract firstmate's record requires, and
    exactly what a retry must not disturb.

    Retries and provider failures are not lost, they are attributed correctly: `gate_calls[]` already
    carries one entry per call with its `failure_cause`, written by `gate_runner.py`. Only the
    attribution was wrong. No field is added — firstmate owns that schema.

    An unreadable verdict record leaves the derivation to the record already stored, which is a
    measurement failing open exactly like every other one here.
    """
    try:
        phase = resolve_phase(phase_dir)
        if phase is None:
            return None
        attempt = verifier_attempts.current(Path(phase_dir)) + 1
        sink.set_fields(phase, verification_attempts=attempt)
        return attempt
    except Exception as exc:  # noqa: BLE001
        sink.note(f"verification attempt not recorded: {type(exc).__name__}: {exc}")
        return None


def test_root() -> Path:
    """Where the project's tests live — the same answer `subprocess_check.py` resolves."""
    declared = (os.environ.get("SUBPROC_CHECK_PATHS") or "").strip()
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    return root / (declared.split(os.pathsep)[0] if declared else "tests")


def count_tests() -> int | None:
    """Suite size, counted statically. None when there is no test root to count."""
    root = test_root()
    if not root.is_dir():
        return None
    total = 0
    for path in sorted(root.rglob("*.py")):
        try:
            total += len(TEST_FUNCTION.findall(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return total


def record_phase_open(phase_dir: str) -> bool:
    """Stamp when the phase was dispatched and how big the suite was, once."""
    phase = resolve_phase(phase_dir)
    if phase is None or not sink.ensure(phase):
        return False
    record = sink.show(phase) or {}
    fields: dict[str, object] = {}
    if record.get("opened") is None:
        fields["opened"] = _now()
    if record.get("tests_before") is None:
        counted = count_tests()
        if counted is not None:
            fields["tests_before"] = counted
    return sink.set_fields(phase, **fields) if fields else True


def record_phase_close(phase_dir: str) -> bool:
    """Stamp when the phase landed, the suite it landed with, and the wall clock it took."""
    phase = resolve_phase(phase_dir)
    if phase is None:
        return False
    record = sink.show(phase) or {}
    closed = _now()
    fields: dict[str, object] = {"closed": closed}
    counted = count_tests()
    if counted is not None:
        fields["tests_after"] = counted
    elapsed = _elapsed_minutes(record.get("opened"), closed)
    if elapsed is not None:
        fields["elapsed_minutes"] = elapsed
    return sink.set_fields(phase, **fields)


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


# --- defects: which stage found each one ---------------------------------------------------------

#: The Verifier's finding kinds — the vocabulary `prompts/verifier-review.md` and
#: `skills/verifier-triage` actually emit — mapped onto what the defect actually is. A gamed test and
#: a coverage gap are defects in the TEST SET — real cost, but they must not inflate the count of
#: genuine product defects, which is the number `found_by` exists to split. `tests/` asserts these
#: keys against the schema in `prompts/verifier-review.md`, so a change to the verifier's vocabulary
#: moves this map instead of silently invalidating it: an unmatched kind falls back to `real=True`
#: and every finding would count as a product defect.
VERIFIER_KIND_REAL = {"code": True, "gamed-test": False, "coverage-gap": False}


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
    """
    fields: dict[str, object] = {
        "id": identifier,
        "summary": _clean(summary) or identifier,
        "real": bool(real),
        "found_by": found_by,
        "stage_reached": stage_reached,
        "severity": severity,
    }
    if found_by == "other":
        fields["found_by_note"] = _clean(found_by_note) or "unclassified"
    return sink.add(phase, "defects", **fields)


def record_verifier_findings(phase_dir: str, verdict_path: str) -> int:
    """Turn the cross-family review's findings into defects attributed to the Verifier."""
    phase = resolve_phase(phase_dir)
    if phase is None:
        return 0
    try:
        payload = json.loads(Path(verdict_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sink.note(f"verifier findings not recorded: {exc}")
        return 0
    written = 0
    for finding in payload.get("findings") or []:
        kind = str(finding.get("kind") or "").strip()
        identifier = str(finding.get("id") or "").strip()
        if not identifier:
            continue
        if record_defect(
            phase,
            identifier=f"verifier-{identifier}",
            summary=str(
                finding.get("instruction") or finding.get("target") or kind or identifier
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
        for entry in (sink.show(phase) or {}).get("skill_loads") or []:
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

    spec_round = sub.add_parser("spec-round", help="measure a spec at this round")
    spec_round.add_argument("spec")

    killed = sub.add_parser("gate-killed", help="record a gate the harness killed mid-call")
    killed.add_argument("--stage", required=True)
    killed.add_argument("--spec-path")
    killed.add_argument("--phase-dir")
    killed.add_argument("--model", default=os.environ.get("GATE_MODEL", "unknown"))

    attempt = sub.add_parser(
        "verifier-attempt",
        help="print the verification ATTEMPT this run belongs to, derived from verdict.json",
    )
    attempt.add_argument("phase_dir")

    findings = sub.add_parser("verifier-findings", help="record the review's findings as defects")
    findings.add_argument("phase_dir")
    findings.add_argument("verdict")

    survivors = sub.add_parser("mutation-survivors", help="record surviving mutants as defects")
    survivors.add_argument("phase_dir")
    survivors.add_argument("score")

    load = sub.add_parser("skill-load", help="record an observed skill load")
    load.add_argument("--stage", required=True)
    load.add_argument("--skill", required=True)
    load.add_argument("--evidence", required=True)
    load.add_argument("--phase-ref", default="")
    load.add_argument("--not-loaded", action="store_true",
                      help="record a required skill with no observed load")

    required = sub.add_parser("skill-required", help="print the skills a stage's contract names")
    required.add_argument("--stage", required=True)

    defect = sub.add_parser("defect", help="record a defect and what caught it")
    defect.add_argument("--phase-ref", required=True)
    defect.add_argument("--id", required=True, dest="identifier")
    defect.add_argument("--summary", required=True)
    defect.add_argument("--found-by", required=True)
    defect.add_argument("--stage-reached", default="implementation")
    defect.add_argument("--severity", default="correctness")
    defect.add_argument("--found-by-note")
    defect.add_argument("--not-real", action="store_true",
                        help="a defect in a test, fixture or artifact, not in the product")

    for name in ("phase-open", "phase-close"):
        boundary = sub.add_parser(name, help=f"stamp the phase {name.split('-')[1]}")
        boundary.add_argument("phase_dir")

    return parser


def _dispatch(args: argparse.Namespace) -> bool | None:
    """Run one CLI command. Prints nothing to stdout except a value the caller asked for.

    Returns whether the record was written, but only `main`'s handling of `defect` reads it. Every
    other command here is called from a hook's `|| true` fail-open path (`hook_spec_gate.sh`,
    `hook_verifier.sh`, `hook_mutation.sh`) and must keep exiting 0 no matter what — a non-zero exit
    there would stop the turn over a missing number. `defect` is the one command a stage runs
    directly, off that path, specifically so it can be told whether its own catch landed.
    """
    if args.command == "spec-round":
        value = record_spec_round(args.spec)
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
    elif args.command == "verifier-attempt":
        print(open_verification_attempt(args.phase_dir) or 1)
    elif args.command == "verifier-findings":
        record_verifier_findings(args.phase_dir, args.verdict)
    elif args.command == "mutation-survivors":
        record_mutation_survivors(args.phase_dir, args.score)
    elif args.command == "skill-load":
        phase = _phase_of_ref(args.phase_ref) or current_phase()
        if phase:
            record_skill_load(phase, stage=args.stage, skill=args.skill,
                              evidence=args.evidence, loaded=not args.not_loaded)
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
            return False
        return record_defect(phase, identifier=args.identifier, summary=args.summary,
                              found_by=args.found_by, real=not args.not_real,
                              stage_reached=args.stage_reached, severity=args.severity,
                              found_by_note=args.found_by_note)
    elif args.command == "phase-open":
        record_phase_open(args.phase_dir)
    elif args.command == "phase-close":
        record_phase_close(args.phase_dir)
    return None


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
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:   # a usage error is the caller's bug, and is worth the nonzero exit
        return int(exc.code or 2)
    try:
        ok = _dispatch(args)
    except Exception as exc:  # noqa: BLE001 — measurement never fails the thing it measures
        sink.note(f"{args.command} not recorded: {type(exc).__name__}: {exc}")
        ok = False if args.command == "defect" else None
    if args.command == "defect" and ok is False and os.environ.get("AVENGER_METRICS_OFF") != "1":
        print(_defect_failure_message(args), file=sys.stderr)
        return 1
    return 0


def _defect_failure_message(args: argparse.Namespace) -> str:
    """What a `defect` that could not be written says, in the two shapes it comes in."""
    lost = (
        f"{args.identifier} (found_by={args.found_by}) was NOT written to the metrics record. This "
        "is the single field the record exists for and it cannot be reconstructed once the run is "
        "over."
    )
    if sink.configured():
        return (
            f"{sink.PREFIX} DEFECT NOT RECORDED: {lost} A metrics writer IS configured for this "
            "run, so the write itself failed and the remedy is yours: see the [metrics] diagnostic "
            "above for the cause, fix it, and re-run this exact command."
        )
    return (
        f"{sink.PREFIX} DEFECT NOT RECORDED - NO METRICS WRITER CONFIGURED: {lost} No writer is "
        "configured for this run: fm-pipeline-metrics.sh is not on PATH and AVENGER_METRICS_CMD is "
        "unset. That is the expected state of a standalone install with no firstmate home, and it "
        "is a standing property of this environment rather than something this command can repair. "
        "DO NOT re-run it - it will fail identically. Move on with the phase. FOR THE OPERATOR: "
        "point AVENGER_METRICS_CMD at firstmate's own fm-pipeline-metrics.sh to record defects, or "
        "set AVENGER_METRICS_OFF=1 to record none deliberately and silently. Under --auto this is "
        "worth surfacing rather than looping on."
    )


if __name__ == "__main__":
    raise SystemExit(main())
