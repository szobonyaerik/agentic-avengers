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

from pipeline_state import (  # noqa: E402
    FeatureNotFoundError,
    next_stage,
)

SPEC = """---
feature: demo
phase: {phase}
spec: {spec}
status: {status}
review_status: {review_status}
fidelity_verdict: {fidelity}
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
    fidelity: str = "GO",
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
            fidelity=fidelity,
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


def test_no_go_fidelity_routes_back_to_spec_writer(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    write_spec(feature, "1-core", "1.1-a", fidelity="NO-GO", review_status="approved")
    state = next_stage(tmp_path, "demo")
    assert state.stage == "spec-writer"
    assert state.spec == "1.1-a"
    assert "NO-GO" in state.reason


def test_ungated_spec_waits_for_the_fidelity_gate(tmp_path: Path) -> None:
    feature = planned(tmp_path)
    spec = write_spec(feature, "1-core", "1.1-a")
    spec.write_text(spec.read_text().replace("fidelity_verdict: GO\n", ""))
    assert next_stage(tmp_path, "demo").stage == "fidelity-gate"


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
