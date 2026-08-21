"""Tests for the pipeline state resolver.

`/avenger-run` is resumable, and resume must not be a model guess: after a /clear the orchestrator
asks this module where the feature stopped. The dangerous direction is reporting a stage as finished
when it is not — that skips a gate — so the ordering cases below pin "still owes work" hard.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import applicability  # noqa: E402
import breaker_gate  # noqa: E402
import spec_gate_cache  # noqa: E402
import verdict_currency  # noqa: E402
import verifier_precheck  # noqa: E402
from pipeline_state import (  # noqa: E402
    FeatureNotFoundError,
    next_stage,
)


def write_breaker(feature: Path, phase: str, data: dict) -> None:
    """A record as the Breaker is instructed to write it - `readers` included, since the gate
    refuses one without it exactly as `doc_read_path.py` does."""
    record = {"readers": list(breaker_gate.READERS), **data}
    (feature / "phases" / phase / "breaker.json").write_text(json.dumps(record))

SPEC = """---
feature: demo
phase: {phase}
spec: {spec}
status: {status}
review_status: {review_status}
spec_gate: {spec_gate}
criticality: {criticality}
---

# Spec
"""


def write_feature(root: Path, *, docs: tuple[str, ...] = ()) -> Path:
    """Create docs/features/demo with the named feature-level artifacts."""
    feature = root / "docs" / "features" / "demo"
    feature.mkdir(parents=True)
    for name in docs:
        (feature / name).write_text("---\nfeature: demo\n---\n")
    return feature


def write_spec(
    feature: Path,
    phase: str,
    spec: str,
    *,
    status: str = "draft",
    review_status: str = "pending",
    spec_gate: str = "approved",
    criticality: str = "standard",
) -> Path:
    """Create one spec under its phase, with gate stamps in the frontmatter."""
    spec_dir = feature / "phases" / phase / "specs" / spec
    spec_dir.mkdir(parents=True)
    path = spec_dir / "spec.md"
    path.write_text(
        SPEC.format(
            phase=phase,
            spec=spec,
            status=status,
            review_status=review_status,
            spec_gate=spec_gate,
            criticality=criticality,
        )
    )
    return path


def write_verdict(feature: Path, phase: str, verdict: str) -> None:
    """Persist the Verifier's verdict for a phase."""
    (feature / "phases" / phase / "verdict.json").write_text(
        json.dumps({"feature": "demo", "phase": phase, "verdict": verdict})
    )


def test_missing_feature_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FeatureNotFoundError):
        next_stage(tmp_path, "demo")


def test_empty_feature_starts_at_task_analyst(tmp_path: Path) -> None:
    write_feature(tmp_path)
    assert next_stage(tmp_path, "demo").stage == "task-analyst"


def test_task_analysis_present_goes_to_solution_architect(tmp_path: Path) -> None:
    write_feature(tmp_path, docs=("task-analysis.md",))
    assert next_stage(tmp_path, "demo").stage == "solution-architect"


def test_overview_present_goes_to_implementation_planner(tmp_path: Path) -> None:
    write_feature(tmp_path, docs=("task-analysis.md", "overview.md"))
    assert next_stage(tmp_path, "demo").stage == "implementation-planner"


def test_plan_present_but_no_phases_goes_to_spec_writer(tmp_path: Path) -> None:
    write_feature(tmp_path, docs=("task-analysis.md", "overview.md", "plan.md"))
    assert next_stage(tmp_path, "demo").stage == "spec-writer"


def planned(tmp_path: Path) -> Path:
    """A feature that has cleared planning — the starting point for phase-level cases."""
    return write_feature(tmp_path, docs=("task-analysis.md", "overview.md", "plan.md"))


def test_phase_dir_without_specs_goes_to_spec_writer(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    (feature / "phases" / "1-core" / "specs").mkdir(parents=True)
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-writer"
    assert state.phase == "1-core"


def test_a_blocked_spec_routes_back_to_spec_writer(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", spec_gate="blocked", review_status="approved")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-writer"
    assert state.spec == "1.1-a"
    assert "blocked" in state.reason


def test_ungated_spec_waits_for_the_spec_gate(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    spec = write_spec(feature, "1-core", "1.1-a")
    spec.write_text(spec.read_text().replace("spec_gate: approved\n", ""))
    assert next_stage(tmp_path, "demo").stage == "spec-gate"


def test_unapproved_spec_goes_to_spec_review(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-review"
    assert state.spec == "1.1-a"


def test_approved_spec_goes_to_the_implementer(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="draft")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "implementer"
    assert state.spec_path is not None
    assert state.spec_path.name == "spec.md"


def test_specs_are_walked_in_numeric_order(tmp_path: Path) -> None:
    """1.10 must not sort before 1.2 — a lexical sort would skip a spec."""
    feature = planned(tmp_path)
    for spec in ("1.2-b", "1.10-c"):
        write_spec(feature, "1-core", spec, review_status="approved", status="done")
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="draft")
    assert next_stage(tmp_path, "demo").spec == "1.1-a"


def test_phases_are_walked_in_numeric_order(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "10-late", "10.1-a", review_status="approved", status="draft")
    write_spec(feature, "2-early", "2.1-a", review_status="approved", status="draft")
    assert next_stage(tmp_path, "demo").phase == "2-early"


def test_all_specs_done_goes_to_the_verifier(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="done")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "verifier"
    assert state.phase == "1-core"


def test_failed_verdict_routes_back_to_the_implementer(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="done")
    write_verdict(feature, "1-core", "fail")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "implementer"
    assert "verdict" in state.reason


def test_passed_verdict_goes_to_handover(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="done")
    write_verdict(feature, "1-core", "pass")
    assert next_stage(tmp_path, "demo").stage == "handover"


def test_handover_advances_to_the_next_phase(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="done")
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "handover.md").write_text("done\n")
    write_spec(feature, "2-next", "2.1-a", review_status="approved", status="draft")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "implementer"
    assert state.phase == "2-next"


def finished_phase(tmp_path: Path) -> Path:
    """A feature whose only phase is verified and handed over."""
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="done")
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "handover.md").write_text("done\n")
    return feature


def test_last_phase_done_goes_to_e2e_author(tmp_path: Path) -> None:
    finished_phase(tmp_path)
    assert next_stage(tmp_path, "demo").stage == "e2e-author"


def test_e2e_mapping_present_means_done(tmp_path: Path) -> None:
    feature = finished_phase(tmp_path)
    (feature / "e2e-mapping.md").write_text("mapped\n")
    assert next_stage(tmp_path, "demo").stage == "done"


# ── the Breaker obligation (issue #45) ────────────────────────────────────────────────────────────
#
# All four phase-8 specs and all four phase-9 specs of one measured feature declared
# `criticality: critical`, which is what routes the Breaker — and it ran neither time, because
# nothing enforced it: the resolver reported the criticality and trusted the orchestrator to act on
# it. These tests break that gap before proving it closed: a critical phase with a passing verdict
# and no Breaker record must NOT reach `handover`.


def test_a_critical_phase_with_no_breaker_record_does_not_reach_handover(tmp_path: Path) -> None:
    """The gap issue #45 describes, reproduced: two owed Breaker runs went missing and nothing
    noticed. Without the fix this asserts the wrong thing — the resolver would report `handover`."""
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")

    state = next_stage(tmp_path, "demo")

    assert state.stage == "breaker"
    assert "breaker.json" in state.reason


def test_a_valid_breaker_record_lets_the_phase_reach_handover(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    write_breaker(
        feature, "1-core", {"verdict": "clean", "attacked": ["malformed payloads", "auth bypass"]}
    )

    assert next_stage(tmp_path, "demo").stage == "handover"


def test_a_breaker_record_with_no_verdict_still_blocks(tmp_path: Path) -> None:
    """A file existing is not the same as a record proving anything — the same "emits nothing"
    failure mode the issue names, one level down: a Breaker that writes junk rather than a verdict."""
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "breaker.json").write_text(json.dumps({"ran": True}))

    assert next_stage(tmp_path, "demo").stage == "breaker"


def test_a_record_declaring_no_readers_still_blocks(tmp_path: Path) -> None:
    """The resolver reads the same validity the read-path check does, so a record that would close
    the phase here and fail `doc_read_path.py` on the next commit routes back to the Breaker."""
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "breaker.json").write_text(
        json.dumps({"verdict": "clean", "attacked": ["replay"]})
    )

    assert next_stage(tmp_path, "demo").stage == "breaker"


def test_a_clean_verdict_naming_nothing_attacked_still_blocks(tmp_path: Path) -> None:
    """"A clean Breaker report with no attempts described is not acceptable" (agents/avenger-breaker
    .md) — this is what makes that instruction checkable rather than a sentence nobody enforces."""
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "breaker.json").write_text(
        json.dumps({"verdict": "clean", "attacked": []})
    )

    assert next_stage(tmp_path, "demo").stage == "breaker"


def test_a_found_verdict_naming_no_counterexample_still_blocks(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "breaker.json").write_text(
        json.dumps({"verdict": "found", "counterexamples": []})
    )

    assert next_stage(tmp_path, "demo").stage == "breaker"


def test_a_found_verdict_naming_a_counterexample_lets_the_phase_reach_handover(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    write_breaker(
        feature,
        "1-core",
        {"verdict": "found", "counterexamples": ["tests/demo/1-core/test_breaker_auth.py"]},
    )

    assert next_stage(tmp_path, "demo").stage == "handover"


def test_a_phase_that_already_handed_over_is_never_reopened_for_a_missing_breaker_record(
    tmp_path: Path,
) -> None:
    """A written handover.md is `shipped` evidence (§3a), and a mechanical rule binds only what is
    still OPEN. Asked before the handover check, this rule re-opened every critical phase closed
    before it existed - no phase anywhere carries a breaker.json - so the resolver parked on shipped
    code and `/avenger-run --auto` never reached the phase in flight.
    """
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "handover.md").write_text("done\n")
    write_spec(feature, "2-next", "2.1-a", review_status="approved", status="draft")

    state = next_stage(tmp_path, "demo")

    assert state.stage == "implementer"
    assert state.phase == "2-next"


def test_the_last_phase_handed_over_without_a_breaker_record_still_reaches_e2e(
    tmp_path: Path,
) -> None:
    """The same boundary at the end of the walk: a shipped phase must not wedge the feature."""
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "handover.md").write_text("done\n")

    assert next_stage(tmp_path, "demo").stage == "e2e-author"


def test_a_standard_phase_owes_no_breaker_record(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="approved", status="done")
    write_verdict(feature, "1-core", "pass")

    assert next_stage(tmp_path, "demo").stage == "handover"


def test_a_recorded_breaker_exception_lets_the_phase_reach_handover(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done", criticality="critical"
    )
    write_verdict(feature, "1-core", "pass")
    record(feature / "phases" / "1-core", "breaker", "1-core")

    assert next_stage(tmp_path, "demo").stage == "handover"


def test_criticality_is_reported_for_the_breaker_decision(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(
        feature,
        "1-core",
        "1.1-a",
        review_status="approved",
        status="done",
        criticality="critical",
    )
    state = next_stage(tmp_path, "demo")
    assert state.stage == "verifier"
    assert state.criticality == "critical"


def test_criticality_defaults_to_standard(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    spec = write_spec(
        feature, "1-core", "1.1-a", review_status="approved", status="done"
    )
    spec.write_text(spec.read_text().replace("criticality: standard\n", ""))
    assert next_stage(tmp_path, "demo").criticality == "standard"


def test_state_is_immutable(tmp_path: Path) -> None:
    write_feature(tmp_path)
    state = next_stage(tmp_path, "demo")
    with pytest.raises((AttributeError, TypeError)):
        state.stage = "tampered"  # type: ignore[misc]


def test_as_json_round_trips(tmp_path: Path) -> None:
    write_feature(tmp_path, docs=("task-analysis.md",))
    payload = json.loads(next_stage(tmp_path, "demo").as_json())
    assert payload["stage"] == "solution-architect"
    assert payload["feature"] == "demo"


PLAN_WITH_PHASES = """---
feature: demo
---

# Implementation Plan: demo

## Phase plan (dependency / risk order)

### Phase 1 — core
- **Goal**: base.

### Phase 2 — telegram runtime and ping
- **Goal**: runtime.

### Phase 3 — audit layer
- **Goal**: audit.
"""


def test_green_disk_phases_do_not_finish_a_longer_plan(tmp_path: Path) -> None:
    """The clickup-agents field defect: 3 of 13 phases green -> resolver said e2e/done.

    The plan, not the folder listing, says how many phases a feature has; a green prefix of them
    must route back to the spec-writer for the first phase nobody specced yet.
    """
    feature = finished_phase(tmp_path)
    (feature / "plan.md").write_text(PLAN_WITH_PHASES)
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-writer"
    assert state.phase == "2-telegram-runtime-and-ping"
    assert "3 phases" in state.reason and "1 exist" in state.reason


def test_gap_in_planned_phases_is_reported_not_skipped(tmp_path: Path) -> None:
    feature = finished_phase(tmp_path)
    (feature / "plan.md").write_text(PLAN_WITH_PHASES)
    write_spec(feature, "3-audit-layer", "3.1-a", review_status="approved", status="done")
    write_verdict(feature, "3-audit-layer", "pass")
    (feature / "phases" / "3-audit-layer" / "handover.md").write_text("done\n")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-writer"
    assert state.phase == "2-telegram-runtime-and-ping"


def test_all_planned_phases_built_proceeds_to_e2e(tmp_path: Path) -> None:
    feature = finished_phase(tmp_path)
    plan = PLAN_WITH_PHASES.replace("### Phase 2 — telegram runtime and ping\n- **Goal**: runtime.\n\n", "")
    plan = plan.replace("### Phase 3 — audit layer\n- **Goal**: audit.\n", "")
    (feature / "plan.md").write_text(plan)
    assert next_stage(tmp_path, "demo").stage == "e2e-author"


def test_plan_without_recognisable_headings_keeps_folder_walk_behaviour(tmp_path: Path) -> None:
    # a malformed plan must not wedge a green feature — only an explicit phase list may extend it
    feature = finished_phase(tmp_path)
    (feature / "plan.md").write_text("---\nfeature: demo\n---\n\nfreeform prose, no headings\n")
    assert next_stage(tmp_path, "demo").stage == "e2e-author"


# --- the applicability boundary: a phase closed with a recorded exception is CLOSED ---------------
#
# Measured: phase 8 of one feature closed under a captain-ordered cap with two specs never
# review-stamped and a `fail` verdict nobody re-ran, all of it disclosed in prose. The resolver read
# the missing stamps as unfinished work and parked there, so `/avenger-run --auto` could not start a
# phase from that moment on. The two obvious remedies — stamping a human sign-off nobody gave, or
# claiming a machine verdict nobody obtained — are the "looks fine" class this pipeline removes, so
# the exception is recorded as STATE and read here.


def record(phase_dir: Path, rule: str, subject: str, monkeypatch=None) -> None:
    """Write one exception straight to the ledger — the audit path has its own tests."""
    phase_dir.mkdir(parents=True, exist_ok=True)
    target = phase_dir / "exceptions.json"
    ledger = json.loads(target.read_text()) if target.is_file() else {"exceptions": []}
    ledger["exceptions"].append(
        {
            "id": f"X{len(ledger['exceptions']) + 1}",
            "rule": rule,
            "subject": subject,
            "reason": "captain-ordered cap; disclosed in the phase handover",
            "recorded_by": "captain",
            "recorded_at": "2026-08-09T00:00:00Z",
        }
    )
    target.write_text(json.dumps(ledger))


def test_an_unreviewed_spec_still_parks_without_an_exception(tmp_path: Path) -> None:
    """The direction that must not move: an absent sign-off is owed work until it is disclosed."""
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    assert next_stage(tmp_path, "demo").stage == "spec-review"


def test_a_recorded_spec_review_exception_lets_the_resolver_move_on(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    record(feature / "phases" / "1-core", "spec-review", "1.1-a")
    write_verdict(feature, "1-core", "pass")
    (feature / "phases" / "1-core" / "handover.md").write_text("done\n")
    assert next_stage(tmp_path, "demo").stage == "e2e-author"


def test_an_exception_covers_only_the_spec_it_names(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    write_spec(feature, "1-core", "1.2-b", review_status="pending", status="done")
    record(feature / "phases" / "1-core", "spec-review", "1.1-a")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-review" and state.spec == "1.2-b"


def test_an_exception_covers_only_the_rule_it_names(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", spec_gate="pending", review_status="pending", status="done")
    record(feature / "phases" / "1-core", "spec-review", "1.1-a")
    assert next_stage(tmp_path, "demo").stage == "spec-gate"


def test_a_failing_verdict_can_be_closed_by_a_recorded_exception(tmp_path: Path) -> None:
    feature = finished_phase(tmp_path)
    write_verdict(feature, "1-core", "fail")
    assert next_stage(tmp_path, "demo").stage == "implementer"
    record(feature / "phases" / "1-core", "verdict", "1-core")
    assert next_stage(tmp_path, "demo").stage == "e2e-author"


def test_an_exception_applied_is_named_on_stderr(tmp_path: Path, capsys) -> None:
    """An exception that applied silently would be the bypass the ledger exists to replace."""
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    record(feature / "phases" / "1-core", "spec-review", "1.1-a")
    next_stage(tmp_path, "demo")
    err = capsys.readouterr().err
    assert "spec-review" in err and "captain" in err


def test_an_unreadable_ledger_grants_nothing_and_says_so(tmp_path: Path, capsys) -> None:
    """Under-report: a ledger nobody can parse must not delete a stage."""
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    (feature / "phases" / "1-core" / "exceptions.json").write_text("{not json")
    assert next_stage(tmp_path, "demo").stage == "spec-review"
    assert "cannot read" in capsys.readouterr().err


def test_from_phase_enters_at_a_named_phase(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    write_spec(feature, "2-next", "2.1-a", spec_gate="pending")
    assert next_stage(tmp_path, "demo").stage == "spec-review"
    assert next_stage(tmp_path, "demo", from_phase=2).stage == "spec-gate"


def test_from_phase_names_what_it_stepped_over(tmp_path: Path, capsys) -> None:
    """It records nothing and judges nothing, so it must not be quiet about that."""
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    write_spec(feature, "2-next", "2.1-a", spec_gate="pending")
    next_stage(tmp_path, "demo", from_phase=2)
    assert "1-core" in capsys.readouterr().err


def entered_over_an_unfinished_phase(tmp_path: Path) -> Path:
    """Phase 1 owes spec-review; phase 2 is verified and handed over."""
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", review_status="pending", status="done")
    write_spec(feature, "2-next", "2.1-a", review_status="approved", status="done")
    write_verdict(feature, "2-next", "pass")
    (feature / "phases" / "2-next" / "handover.md").write_text("done\n")
    return feature


def test_from_phase_claims_nothing_feature_wide_over_a_phase_it_skipped(tmp_path: Path) -> None:
    """`done`/`e2e-author` here would drive an --auto run to feature close over an unfinished phase."""
    entered_over_an_unfinished_phase(tmp_path)
    state = next_stage(tmp_path, "demo", from_phase=2)
    assert state.stage == "unknown"
    assert "1-core" in state.reason


def test_from_phase_does_not_report_done_once_e2e_exists(tmp_path: Path) -> None:
    feature = entered_over_an_unfinished_phase(tmp_path)
    (feature / "e2e-mapping.md").write_text("mapped\n")
    assert next_stage(tmp_path, "demo", from_phase=2).stage == "unknown"


def test_from_phase_does_not_answer_the_plan_vs_disk_question(tmp_path: Path) -> None:
    """A planned phase nobody specced is a feature-wide claim too, and it is not this walk's to make."""
    feature = entered_over_an_unfinished_phase(tmp_path)
    (feature / "plan.md").write_text(PLAN_WITH_PHASES)
    assert next_stage(tmp_path, "demo", from_phase=2).stage == "unknown"


def test_entering_past_every_phase_on_disk_claims_nothing(tmp_path: Path) -> None:
    """`--from-phase 99` examines nothing at all; `done` would be a verdict over the whole feature."""
    entered_over_an_unfinished_phase(tmp_path)
    assert next_stage(tmp_path, "demo", from_phase=99).stage == "unknown"


def test_from_phase_that_skips_nothing_still_resolves_the_feature(tmp_path: Path) -> None:
    """The narrowing is what forbids a feature-wide answer, not the flag being present."""
    finished_phase(tmp_path)
    assert next_stage(tmp_path, "demo", from_phase=1).stage == "e2e-author"


ALL_DOCS = ("task-analysis.md", "overview.md", "plan.md")


def _gated_spec(tmp_path, **kwargs):
    """A feature whose one spec is implemented, approved, and stamped by the gate for real.

    `write_spec` writes an `spec_gate: approved` line and nothing else; the real gate also records
    the hash of the body it judged, which is what makes drift detectable at all.
    """
    feature = write_feature(tmp_path, docs=ALL_DOCS)
    spec = write_spec(
        feature, "1-first", "1.0-a", status="done", review_status="approved", **kwargs
    )
    spec.write_text(spec_gate_cache.stamp(spec.read_text(), "gate", "APPROVED"))
    return feature, spec


def test_a_drifted_spec_is_ungated_to_the_resolver_too(tmp_path):
    """Issue #42: one spec must not be gated and ungated at once, depending on who asks.

    `verifier_precheck` compares the body against the hash the gate recorded and calls a drifted
    spec UNGATED. The resolver read only the stamp's VALUE, so it moved on and let work proceed on
    a spec no gate has judged in its current form.
    """
    _feature, spec = _gated_spec(tmp_path)
    spec.write_text(spec.read_text() + "\n## Requirements\n- R1.0.9 nobody gated this\n")

    state = next_stage(tmp_path, "demo")

    assert state.stage == "spec-gate"
    assert "judged" in state.reason or "gated" in state.reason
    # The two readers now agree about this exact spec.
    assert verifier_precheck.stamp_fresh(spec) is False


def test_an_undrifted_spec_still_moves_on(tmp_path):
    """The stricter reading must not stop a spec whose body is exactly what the gate judged."""
    _feature, _spec = _gated_spec(tmp_path)

    assert next_stage(tmp_path, "demo").stage != "spec-gate"


def test_a_spec_the_gate_never_hashed_is_not_re_opened_by_the_resolver(tmp_path):
    """A hash that was never recorded is unknowable drift, not proven drift.

    Every spec stamped before the gate cache existed carries an approval and no hash. Routing those
    back to the spec gate would park the resolver on shipped work whose remedy is a re-gate nobody
    asked for - the wedge `applicability.py` exists to prevent. The precheck may still report them,
    because it is diff-scoped and only ever holds what the change touched.
    """
    feature = write_feature(tmp_path, docs=ALL_DOCS)
    write_spec(feature, "1-first", "1.0-a", status="done", review_status="approved")

    assert next_stage(tmp_path, "demo").stage != "spec-gate"


def test_a_recorded_exception_clears_the_drift_for_the_resolver(tmp_path, monkeypatch):
    """The same disclosed exception that clears the precheck clears the resolver (issue #67)."""
    # bypass_log.sh writes gate-overrides.log under $CLAUDE_PROJECT_DIR — never this repository.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    feature, spec = _gated_spec(tmp_path)
    spec.write_text(spec.read_text() + "\n## Requirements\n- R1.0.9 drifted\n")
    phase_dir = feature / "phases" / "1-first"
    reason = tmp_path / "why.txt"
    reason.write_text("the gate provider was down")
    applicability.record_exception(
        phase_dir, rule="spec-gate", subject="1.0-a",
        reason=reason.read_text(), recorded_by="tester",
    )

    assert next_stage(tmp_path, "demo").stage != "spec-gate"


def test_a_feature_whose_code_changed_after_its_newest_verdict_owes_an_amendment(
    tmp_path, monkeypatch
):
    """Issue #51: `done` is terminal, so this is the last point the remedy still exists."""
    feature = write_feature(tmp_path, docs=ALL_DOCS + ("e2e-mapping.md",))
    write_spec(feature, "1-first", "1.0-a", status="done", review_status="approved")
    write_verdict(feature, "1-first", "pass")
    (feature / "phases" / "1-first" / "handover.md").write_text("---\nfeature: demo\n---\n")

    monkeypatch.setattr(
        verdict_currency, "check", lambda root, fdir: "demo: 1 tracked file(s) changed"
    )

    state = next_stage(tmp_path, "demo")

    assert state.stage == "verifier"
    assert "changed" in state.reason


def test_a_current_verdict_still_reaches_done(tmp_path, monkeypatch):
    feature = write_feature(tmp_path, docs=ALL_DOCS + ("e2e-mapping.md",))
    write_spec(feature, "1-first", "1.0-a", status="done", review_status="approved")
    write_verdict(feature, "1-first", "pass")
    (feature / "phases" / "1-first" / "handover.md").write_text("---\nfeature: demo\n---\n")
    monkeypatch.setattr(verdict_currency, "check", lambda root, fdir: None)

    assert next_stage(tmp_path, "demo").stage == "done"
