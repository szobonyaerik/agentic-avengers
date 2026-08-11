"""The Verifier used to re-send every spec in a phase on every attempt.

`verifier_review.sh` globbed `specs/*/spec.md` and every `test-mapping.md` unconditionally, so a
phase's second attempt paid for its first attempt again. One measured bundle reached ~832k tokens,
and one phase had to be split into four chunks to fit a context at all. PR #27's diff-only rule
covers spec RE-GATES and never reached this bundle.

Scoping a gate down is the easiest place in this repo to write a fail-open by accident, so the tests
split evenly between "does it shrink" and "does it still hold the bar":

  * a spec with an OPEN finding is never carried — it goes back to the reader, because a finding
    fixed in a TEST file changes no spec text, and carrying it either drops it (fail-open) or wedges
    the phase in a NO-GO nothing can clear;
  * a carried spec's findings still come forward, and one that reaches the merge open forces NO-GO
    even when this run's narrower review said GO;
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


def test_a_spec_with_an_open_finding_is_re_reviewed_never_carried(tmp_path: Path) -> None:
    """The two ways this could go wrong, and the one narrow path between them.

    Carrying a spec with an open finding is a fail-open if the finding is dropped and a WEDGE if it
    is kept (see the multi-run test below). Neither is acceptable, so the spec goes back to the
    reader: the finding is regenerated, and clears or reappears on its own evidence."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[FINDING])

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    result = plan(phase_dir)

    assert [Path(p).name for p in result["review"]] == ["8.0-alpha", "8.1-beta"], (
        "the spec under repair must be re-sent, whatever its text says"
    )
    assert result["carry"] == []


def test_an_open_finding_still_holds_the_phase(tmp_path: Path) -> None:
    """Re-reviewing rather than carrying must not become a way for a finding to evaporate: while the
    reader still raises it, the phase does not pass."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[FINDING])

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    result = review(phase_dir, verdict="NO-GO", findings=[FINDING])

    assert result["exit"] == 2
    assert result["verdict"]["verdict"] == "NO-GO"
    assert FINDING["id"] in [f["id"] for f in result["verdict"]["findings"]]


def test_a_carried_open_finding_forces_no_go_if_one_ever_reaches_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`plan()` no longer produces this state, which is exactly why the guard behind it is worth
    pinning: a hand-edited state file or a later change to the carry condition must still fail
    closed rather than pass a phase on a review that never looked at the finding."""
    import verifier_bundle_scope

    phase_dir = phase(tmp_path)
    monkeypatch.setattr(verifier_bundle_scope, "plan", lambda _: {
        "review": [str(phase_dir / "specs" / "8.1-beta")],
        "carry": [{"spec": "8.0-alpha", "path": str(phase_dir / "specs" / "8.0-alpha"),
                   "verdict": "NO-GO", "findings": [FINDING]}],
        "full": False,
    })

    result = review(phase_dir, verdict="GO")

    assert result["exit"] == 2, "a carried open finding must not be passed over"
    assert result["verdict"]["verdict"] == "NO-GO"
    assert FINDING["id"] in [f["id"] for f in result["verdict"]["findings"]]


def test_a_finding_fixed_in_a_test_file_does_not_wedge_the_phase_forever(tmp_path: Path) -> None:
    """The wedge, which needs REPEATED runs to show: one round looks identical either way.

    Introduced by the fix for defect 7 and caught by no-mistakes review, not by the suite that
    shipped with it. A `gamed test` finding is fixed in a TEST file, so `spec.md` and
    `test-mapping.md` never change; carrying on fingerprint alone left 8.0-alpha out of every later
    bundle, so nothing ever regenerated its finding and nothing could ever mark it fixed. Runs 2, 3
    and 4 all returned NO-GO with open_findings=1 against 8.0-alpha while every review was clean,
    and the only exits were VERIFIER_SCOPE=full or deleting the state file by hand."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[{**FINDING, "kind": "gamed test"}])

    sibling = phase_dir / "specs" / "8.1-beta" / "spec.md"
    for round_no in (2, 3, 4):
        sibling.write_text(sibling.read_text() + f"\n- R8.1.{round_no} req added in round {round_no}\n")
        reviewed = [Path(p).name for p in plan(phase_dir)["review"]]

        if round_no == 2:
            assert "8.0-alpha" in reviewed, (
                "the spec under repair must be re-sent so its finding can be regenerated"
            )
        else:
            assert "8.0-alpha" not in reviewed, (
                "with the finding gone the spec is carried again — the saving returns with the repair"
            )

        result = review(phase_dir, verdict="GO")
        carried_open = sum(c["open_findings"] for c in result["verdict"]["carried_specs"])

        assert carried_open == 0, (
            f"round {round_no}: a stale finding no review can regenerate is still holding the phase"
        )
        assert result["exit"] == 0, f"round {round_no}: every review was clean and the phase failed"
        assert result["verdict"]["verdict"] == "GO"


def test_a_resolved_carried_finding_no_longer_blocks(tmp_path: Path) -> None:
    """A settled finding is what carriage is FOR: `fixed` keeps the spec out of the bundle and does
    not make the phase unpassable."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[{**FINDING, "status": "fixed"}])
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    assert [e["spec"] for e in plan(phase_dir)["carry"]] == ["8.0-alpha"]
    assert review(phase_dir, verdict="GO")["exit"] == 0


def test_a_waived_carried_finding_no_longer_blocks(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[{**FINDING, "break_glass": True}])
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    assert [e["spec"] for e in plan(phase_dir)["carry"]] == ["8.0-alpha"]
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


def test_a_finding_on_a_failed_spec_is_kept_and_re_judged_when_a_sibling_changes(
    tmp_path: Path,
) -> None:
    """A spec whose review said NO-GO does not get a clean bill from a sibling's edit: its finding
    stays on the record, and the spec goes back to the reader to be judged again rather than being
    inherited either way."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[FINDING])
    state = json.loads((phase_dir / STATE_NAME).read_text())
    assert state["specs"]["8.0-alpha"]["verdict"] == "NO-GO"
    assert [f["id"] for f in state["specs"]["8.0-alpha"]["findings"]] == [FINDING["id"]]

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 another req\n")

    assert "8.0-alpha" in [Path(p).name for p in plan(phase_dir)["review"]]


def test_a_spec_reviewed_clean_is_not_recorded_as_the_phases_failure(tmp_path: Path) -> None:
    """The phase verdict used to be stamped on every reviewed spec, so a spec nothing was said
    against was stored as NO-GO and the next bundle announced it that way — misinforming the reader
    about the work it was told not to look at. The findings do the blocking; this is the label."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[FINDING])

    state = json.loads((phase_dir / STATE_NAME).read_text())["specs"]
    assert state["8.1-beta"]["verdict"] == "GO", "the sibling's failure is not this spec's verdict"
    assert state["8.0-alpha"]["verdict"] == "NO-GO"


def test_a_failure_that_names_no_spec_is_recorded_against_every_spec_reviewed(
    tmp_path: Path,
) -> None:
    """Localising a verdict per spec must not invent a clean bill: a rejection whose finding resolves
    to no spec belongs to the phase, so nothing it reviewed may be labelled clean."""
    phase_dir = phase(tmp_path)
    review(phase_dir, verdict="NO-GO", findings=[{**FINDING, "spec_id": ""}])

    state = json.loads((phase_dir / STATE_NAME).read_text())["specs"]
    assert {s["verdict"] for s in state.values()} == {"NO-GO"}
