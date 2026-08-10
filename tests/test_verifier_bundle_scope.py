"""The Verifier used to re-send every spec in a phase on every attempt.

`verifier_review.sh` globbed `specs/*/spec.md` and every `test-mapping.md` unconditionally, so a
phase's second attempt paid for its first attempt again. One measured bundle reached ~832k tokens,
and one phase had to be split into four chunks to fit a context at all. PR #27's diff-only rule
covers spec RE-GATES and never reached this bundle.

Scoping a gate down is the easiest place in this repo to write a fail-open by accident, so the tests
split evenly between "does it shrink" and "does it still hold the bar":

  * a carried spec's open findings come forward, and an open one forces NO-GO even when this run's
    narrower review said GO;
  * no state, a corrupt state, or `VERIFIER_SCOPE=full` sends everything;
  * what was carried is stated in the artifact, so a scoped review can be audited afterwards.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from verifier_bundle_scope import STATE_NAME, finalize, plan  # noqa: E402


def phase(tmp_path: Path, specs=("8.0-alpha", "8.1-beta")) -> Path:
    phase_dir = tmp_path / "phases" / "8-demo"
    for name in specs:
        spec = phase_dir / "specs" / name
        spec.mkdir(parents=True)
        (spec / "spec.md").write_text(f"---\nfeature: demo\n---\n\n- R{name.split('-')[0]}.1 a req\n")
        (spec / "test-mapping.md").write_text("| test | req |\n|---|---|\n| test_a | R1 |\n")
    return phase_dir


def review(phase_dir: Path, verdict: str = "GO", findings=()) -> dict:
    """Write a verdict as the gate runner would, then finalize it as the shell does."""
    out = phase_dir / ".verifier-review.json"
    out.write_text(json.dumps({
        "verdict": verdict,
        "report": "reviewed test_a.py",
        "findings": list(findings),
    }))
    code = finalize(phase_dir, out)
    return {"exit": code, "verdict": json.loads(out.read_text())}


# ── the shrink ───────────────────────────────────────────────────────────────


def test_a_first_run_sends_everything(tmp_path: Path) -> None:
    """No previous review means nothing to carry — the full bundle, exactly as before."""
    phase_dir = phase(tmp_path)
    result = plan(phase_dir)
    assert len(result["review"]) == 2
    assert result["carry"] == []


def test_a_second_run_only_sends_what_changed(tmp_path: Path) -> None:
    """The defect: attempt two re-sent every requirement of every spec in the phase."""
    phase_dir = phase(tmp_path)
    review(phase_dir)

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    result = plan(phase_dir)
    assert [Path(p).name for p in result["review"]] == ["8.1-beta"]
    assert [e["spec"] for e in result["carry"]] == ["8.0-alpha"]


def test_a_changed_test_mapping_re_reviews_its_spec(tmp_path: Path) -> None:
    """The mapping is how a requirement reaches a test; editing it changes the review."""
    phase_dir = phase(tmp_path)
    review(phase_dir)
    mapping = phase_dir / "specs" / "8.0-alpha" / "test-mapping.md"
    mapping.write_text(mapping.read_text() + "| test_b | R2 |\n")

    assert [Path(p).name for p in plan(phase_dir)["review"]] == ["8.0-alpha"]


def test_a_new_spec_is_reviewed_not_carried(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    review(phase_dir)
    added = phase_dir / "specs" / "8.2-gamma"
    added.mkdir(parents=True)
    (added / "spec.md").write_text("---\nfeature: demo\n---\n\n- R8.2.1 a req\n")

    assert [Path(p).name for p in plan(phase_dir)["review"]] == ["8.2-gamma"]


# ── and the bar it must not shrink ───────────────────────────────────────────


FINDING = {
    "id": "abc123abc123",
    "kind": "coverage gap",
    "spec_id": "R8.0.1",
    "target": "tests/demo/8-demo/test_a.py",
    "status": "open",
    "break_glass": False,
}


def test_an_open_finding_on_a_carried_spec_still_holds_the_phase(tmp_path: Path) -> None:
    """The fail-open this scoping could have been: a spec whose finding is unfixed stops being sent,
    so nothing re-reports it and the phase passes on a review that never looked at it."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[FINDING])

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    result = review(phase_dir, verdict="GO")

    assert result["exit"] == 2, "a carried open finding must not be passed over"
    assert result["verdict"]["verdict"] == "NO-GO"
    ids = [f["id"] for f in result["verdict"]["findings"]]
    assert FINDING["id"] in ids, "the carried finding must survive into this run's verdict"


def test_a_resolved_carried_finding_no_longer_blocks(tmp_path: Path) -> None:
    """Carrying findings forward must not make a phase unpassable — `fixed` is respected."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[{**FINDING, "status": "fixed"}])
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    assert review(phase_dir, verdict="GO")["exit"] == 0


def test_a_waived_carried_finding_no_longer_blocks(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[{**FINDING, "break_glass": True}])
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    assert review(phase_dir, verdict="GO")["exit"] == 0


def test_what_was_carried_is_recorded_in_the_verdict(tmp_path: Path) -> None:
    """A scoped review whose scope is invisible cannot be audited after the fact."""
    phase_dir = phase(tmp_path)
    review(phase_dir)
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    verdict = review(phase_dir)["verdict"]
    assert verdict["reviewed_specs"] == ["8.1-beta"]
    assert [c["spec"] for c in verdict["carried_specs"]] == ["8.0-alpha"]


def test_scoping_is_switchable_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the scoping itself is under suspicion, one variable returns the old behaviour."""
    phase_dir = phase(tmp_path)
    review(phase_dir)
    monkeypatch.setenv("VERIFIER_SCOPE", "full")
    assert len(plan(phase_dir)["review"]) == 2
    assert plan(phase_dir)["carry"] == []


def test_a_lost_state_file_sends_everything(tmp_path: Path) -> None:
    """The cache is rebuildable, and losing it must cost tokens rather than coverage."""
    phase_dir = phase(tmp_path)
    review(phase_dir)
    (phase_dir / STATE_NAME).unlink()
    assert len(plan(phase_dir)["review"]) == 2


def test_a_corrupt_state_file_sends_everything(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    review(phase_dir)
    (phase_dir / STATE_NAME).write_text("{not json")
    assert len(plan(phase_dir)["review"]) == 2


def test_a_state_file_from_another_version_sends_everything(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    review(phase_dir)
    (phase_dir / STATE_NAME).write_text(json.dumps({"version": 99, "specs": {"8.0-alpha": {}}}))
    assert len(plan(phase_dir)["review"]) == 2


def test_a_run_with_nothing_changed_sends_everything(tmp_path: Path) -> None:
    """Scoping down to zero specs is not a cheaper review — the model judges coverage against the
    requirements, and a bundle carrying none has nothing to judge against."""
    phase_dir = phase(tmp_path)
    review(phase_dir)
    result = plan(phase_dir)
    assert len(result["review"]) == 2
    assert result["carry"] == []


def test_a_finding_on_a_failed_spec_is_re_raised_when_a_sibling_changes(tmp_path: Path) -> None:
    """A spec whose review said NO-GO carries its FINDING, not a clean bill: while it is open, the
    phase stays NO-GO however narrow the next review is."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[FINDING])
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")
    assert review(phase_dir, verdict="GO")["verdict"]["verdict"] == "NO-GO"
