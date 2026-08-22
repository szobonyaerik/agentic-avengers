"""The checks that make a stopped producer visible.

Every one of these is written against the shape two measured phases actually took: a record that
held nothing, next to artifacts that described plenty. The distinction the whole module turns on is
between *nothing to report* and *nobody reported* - so each check has a test for the gap, a test for
the clean case that must not fire, and a test for the state it deliberately declines to judge.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import (  # noqa: F401
    git_init,
    git_land,
    real_sink,
    stored,
    stub_sink,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import emission_gate  # noqa: E402
import pipeline_metrics as metrics  # noqa: E402


def phase(project: Path, number: int = 12, slug: str = "poll") -> Path:
    path = project / "docs" / "features" / "demo" / "phases" / f"{number}-{slug}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def verdict(path: Path, name: str, attempt: int, result: str, findings: list[str]) -> None:
    (path / name).write_text(
        json.dumps(
            {
                "attempt": attempt,
                "verdict": result,
                "findings": [{"id": f, "kind": "code", "instruction": f} for f in findings],
            }
        ),
        encoding="utf-8",
    )


# ── defects: the record must say as much as the phase's own verdicts do ──────────────────────────


def test_a_passing_verdict_hides_the_attempt_that_found_everything(stub_sink):  # noqa: F811
    """Phase 12's shape exactly: six findings on attempt 1, a clean pass on attempt 2, nothing in
    the record. Read from `verdict.json` alone the phase looks like one that found nothing."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict-attempt-1.json", 1, "fail", ["aaa", "bbb", "ccc"])
    verdict(phase_dir, "verdict.json", 2, "pass", [])
    metrics.record_phase_open(str(phase_dir))

    code, lines = emission_gate.check_defects(str(phase_dir))

    assert code == emission_gate.GAP
    assert "3 finding(s)" in lines[0] and "0 defect(s)" in lines[0]
    assert "aaa" in "\n".join(lines)


def test_the_check_passes_once_the_defects_are_actually_recorded(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict-attempt-1.json", 1, "fail", ["aaa", "bbb", "ccc"])
    verdict(phase_dir, "verdict.json", 2, "pass", [])
    metrics.record_phase_open(str(phase_dir))

    metrics.record_verifier_findings(str(phase_dir))

    assert emission_gate.check_defects(str(phase_dir))[0] == emission_gate.CLEAN


def test_a_defect_another_stage_found_does_not_pay_for_the_verifier(stub_sink):  # noqa: F811
    """The comparison is scoped to `found_by: verifier` on both sides. A mutation survivor or a
    hand-run probe is a real defect and a real entry, and neither says the Verifier's were kept."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict.json", 1, "fail", ["aaa"])
    metrics.record_phase_open(str(phase_dir))
    metrics.record_defect("12", identifier="D1", summary="found by hand", found_by="execution",
                          real=True, stage_reached="verification", severity="security")

    assert emission_gate.check_defects(str(phase_dir))[0] == emission_gate.GAP


def test_a_phase_with_no_record_is_not_checked_and_says_so(stub_sink, monkeypatch):  # noqa: F811
    """The standing state of any repository with no firstmate writer. A measurement layer that
    blocks delivery when it is simply not configured is a self-inflicted outage."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict.json", 1, "fail", ["aaa"])
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")

    code, lines = emission_gate.check_defects(str(phase_dir))

    assert code == emission_gate.CLEAN
    assert "NOT CHECKED" in lines[0]


def test_an_unreadable_archive_is_undecidable_and_never_a_gap(stub_sink):  # noqa: F811
    """Two stops, two remedies. Read as empty, a corrupt archive would LOWER the bar - the silent
    pass this module exists to remove - and `emit the defects` cannot repair malformed JSON."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict.json", 2, "pass", [])
    (phase_dir / "verdict-attempt-1.json").write_text("{ not json", encoding="utf-8")
    metrics.record_phase_open(str(phase_dir))

    code, _ = emission_gate.check_defects(str(phase_dir))

    assert code == emission_gate.UNDECIDABLE


def test_another_project_s_phase_number_is_not_this_phase(stub_sink):  # noqa: F811
    """A firstmate home holds one filename namespace for every pipeline it runs, so phase 12 of
    another project is a different phase - and judging this one against its defects is nonsense."""
    project, store, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict.json", 1, "fail", ["aaa"])
    metrics.record_phase_open(str(phase_dir))
    record = json.loads((store / "phase-12.json").read_text(encoding="utf-8"))
    record["project"] = "somebody-else"
    (store / "phase-12.json").write_text(json.dumps(record), encoding="utf-8")

    code, lines = emission_gate.check_defects(str(phase_dir))

    assert code == emission_gate.CLEAN and "NOT CHECKED" in lines[0]


def test_the_sweep_holds_only_the_phases_this_change_touches(stub_sink):  # noqa: F811
    """Diff-scoped, on the applicability boundary: a rule added after the tree it runs on must ask
    what THIS change is responsible for, or a repository full of pre-rule phases can never adopt."""
    project, _, _ = stub_sink
    git_init(project)
    old = phase(project, 1, "old")
    verdict(old, "verdict.json", 1, "fail", ["old-1"])
    metrics.record_phase_open(str(old))
    git_land(project, "an earlier phase, already closed")

    assert emission_gate.sweep_defects(project)[0] == emission_gate.CLEAN

    fresh = phase(project, 2, "new")
    verdict(fresh, "verdict.json", 1, "fail", ["new-1"])
    metrics.record_phase_open(str(fresh))

    code, lines = emission_gate.sweep_defects(project)
    assert code == emission_gate.GAP
    assert "2-new" in "\n".join(lines) and "1-old" not in "\n".join(lines)


# ── close: a landed phase must not carry a null `closed` ─────────────────────────────────────────


def test_a_landed_phase_with_no_close_stamp_is_a_gap(stub_sink):  # noqa: F811
    """The trap this exists for, in one test. The metric that watched the close stamp counted
    OVERRIDES CORRECTING it and reported zero - which read as success and described a producer that
    had stopped. A check that only looks for a wrong value can never see an absent one."""
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    (phase_dir / "handover.md").write_text("landed", encoding="utf-8")
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "phase 12 lands")

    code, lines = emission_gate.check_close(project)

    assert code == emission_gate.GAP
    assert "closed=null" in lines[0]


def test_a_stamped_phase_passes(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    (phase_dir / "handover.md").write_text("landed", encoding="utf-8")
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "phase 12 lands")
    assert metrics.record_phase_close(str(phase_dir)) is True

    assert emission_gate.check_close(project)[0] == emission_gate.CLEAN
    assert stored(store, "12")["closed"] is not None


def test_a_phase_that_has_not_landed_is_not_owed_a_stamp(stub_sink):  # noqa: F811
    """`closed` means LANDED (issue #46). Asking for it before the commit is the premature stamp
    that issue removed, so the check must not reintroduce it from the other direction."""
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, "verdict.json", 1, "fail", ["aaa"])
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "open")
    (phase_dir / "handover.md").write_text("written, not committed", encoding="utf-8")

    assert emission_gate.check_close(project)[0] == emission_gate.CLEAN


def test_no_landed_phase_has_a_record_is_reported_rather_than_passed_silently(
    stub_sink, monkeypatch  # noqa: F811
):
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    (phase_dir / "handover.md").write_text("landed", encoding="utf-8")
    git_land(project, "phase 12 lands")
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")

    code, lines = emission_gate.check_close(project)

    assert code == emission_gate.CLEAN
    assert "NOT CHECKED" in lines[0]


# ── the CLI, which is what the hook and CI actually run ──────────────────────────────────────────


@pytest.mark.subprocess("the hook and gate_ci.sh run this as a command; its exit code IS the gate")
def test_the_cli_exit_codes_are_the_ones_the_callers_branch_on(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, "verdict.json", 1, "fail", ["aaa"])
    metrics.record_phase_open(str(phase_dir))

    assert emission_gate.main(["defects", str(phase_dir)]) == emission_gate.GAP
    metrics.record_verifier_findings(str(phase_dir))
    assert emission_gate.main(["defects", str(phase_dir)]) == emission_gate.CLEAN
    assert emission_gate.main(["close", "--root", str(project)]) == emission_gate.CLEAN


def test_a_path_that_names_no_phase_is_undecidable(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    assert emission_gate.check_defects(str(project))[0] == emission_gate.UNDECIDABLE
