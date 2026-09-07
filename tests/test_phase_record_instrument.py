"""The phase record measures the moment it claims to, or it records nothing - issue #120.

Six instances, one class: a stamp taken at the wrong moment, or a fact a stage observed and never
emitted. Every test here is written against a shape measured on a real record:

* phase 5 of one feature was stamped `closed` at its SPEC commit, `elapsed_minutes: 10`, two hours
  before it landed - and because a close SEALS the record, every defect the phase then produced
  was refused by the writer;
* every record the pipeline wrote after the cross-family review script was deleted carried
  `verification_attempts: null` while the phase's own verdict read `attempt: 4`;
* the Breaker's counterexamples and the spec gate's blockers reached no emission at all;
* nothing in the record said which of its numbers the pipeline wrote and which a person typed.

Each guard is proven by going red: revert the fix and the test named beside it fails.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from metrics_support import (  # noqa: F401
    DOUBLE,
    git_init,
    git_land,
    read_calls,
    real_sink,
    stored,
    stub_sink,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import emission_gate  # noqa: E402
import pipeline_metrics as metrics  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "every emission point writes through a real writer process and asks the real git binary "
    "whether the phase landed; a double of either would prove the double"
)


def phase(project: Path, number: int = 5, slug: str = "config") -> Path:
    path = project / "docs" / "features" / "demo" / "phases" / f"{number}-{slug}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def spec(phase_dir: Path, spec_id: str = "5.1") -> Path:
    path = phase_dir / "specs" / f"{spec_id}-a" / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nfeature: demo\nstatus: draft\n---\n- R5.1.1 do\n", encoding="utf-8"
    )
    return path


def verdict(
    phase_dir: Path, attempt: int, result: str = "pass", name: str = "verdict.json"
) -> None:
    (phase_dir / name).write_text(
        json.dumps({"attempt": attempt, "verdict": result, "findings": []}),
        encoding="utf-8",
    )


# ── 1. `closed` is stamped at LANDING, and landing is the committed contract card ───────────────────


def test_a_spec_commit_does_not_close_the_phase(stub_sink):
    """The measured defect. A spec commit leaves the phase directory clean, and "clean" was the
    whole landing test: phase 5 closed at 20:11:08Z, the exact second of its spec commit, with
    `elapsed_minutes: 10` and `tests_after` - before any verdict existed, 2h16m before it landed."""
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    spec(phase_dir)
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "docs(demo): phase 5 specs")

    assert metrics.record_phase_close(str(phase_dir)) is False
    record = stored(store, "05")
    assert record["closed"] is None
    assert record["elapsed_minutes"] is None
    assert record["tests_after"] is None


def test_the_reason_names_the_missing_card(stub_sink):
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    spec(phase_dir)
    git_land(project, "specs")
    why = metrics.not_landed_reason(phase_dir)
    assert why is not None
    assert metrics.CLOSING_DOCUMENT in why
    assert "spec or plan commit is not a close" in why


def test_a_verdict_commit_without_the_card_does_not_close_it_either(stub_sink):
    """A verdict is not landing: the card is written after it, and an amendment can still reopen
    the phase between the two."""
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, 1)
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "verdict")
    assert metrics.record_phase_close(str(phase_dir)) is False
    assert stored(store, "05")["closed"] is None


def test_the_commit_that_carries_the_card_closes_it(stub_sink):
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    spec(phase_dir)
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "specs")
    verdict(phase_dir, 2)
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "feat(demo): phase 5 verified")
    assert metrics.record_phase_close(str(phase_dir)) is True
    record = stored(store, "05")
    assert record["closed"] is not None
    assert record["elapsed_minutes"] is not None


def test_a_card_written_but_not_committed_is_not_landing(stub_sink):
    """Issue #46's own case survives: the card is the Verifier's precondition, not the landing."""
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    spec(phase_dir)
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "open")
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("draft", encoding="utf-8")
    assert metrics.phase_landed(phase_dir) is False
    assert metrics.record_phase_close(str(phase_dir)) is False
    assert stored(store, "05")["closed"] is None


def test_a_never_opened_phase_still_closes_and_says_its_elapsed_time_is_unmeasured(
    stub_sink, capsys
):
    """A spec authored through a heredoc fires no `phase-open`, so `opened` is null. The close is
    still a fact and is stamped; `elapsed_minutes` is genuinely unmeasured and is left null AND
    said, never invented from the first commit that touched the directory."""
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    assert metrics.record_phase_close(str(phase_dir)) is True
    record = stored(store, "05")
    assert record["closed"] is not None
    assert record["elapsed_minutes"] is None
    assert "elapsed_minutes NOT recorded" in capsys.readouterr().err


def test_the_landing_hook_stamps_only_the_commit_that_carries_the_card(tmp_path: Path):
    """The real hook, driven by real commits: a spec commit records nothing, the card's commit
    stamps. Both commits leave the phase directory clean, which is exactly what the hook used to
    read as landing."""
    import shutil

    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "store").mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    git_init(tmp_path)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "AVENGER_METRICS_CMD": str(double),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp_path / "metrics.log"),
        "DOUBLE_LOG": str(tmp_path / "calls.log"),
        "DOUBLE_STORE": str(tmp_path / "store"),
    }
    phase_dir = phase(tmp_path)
    spec(phase_dir)
    subprocess.run(
        [
            sys.executable,
            str(tmp_path / "scripts/pipeline_metrics.py"),
            "phase-open",
            str(phase_dir),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=env,
    )

    def hook(command: str) -> None:
        result = subprocess.run(
            ["bash", str(tmp_path / "scripts/hook_phase_close.sh")],
            input=json.dumps({"tool_input": {"command": command}}),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, result.stderr

    def record() -> dict:
        return json.loads(
            (tmp_path / "store" / "phase-05.json").read_text(encoding="utf-8")
        )

    git_land(tmp_path, "docs(demo): phase 5 specs")
    hook("git commit -q -m 'docs(demo): phase 5 specs'")
    assert record()["closed"] is None, "a spec commit is not the phase landing"

    verdict(phase_dir, 1)
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(tmp_path, "feat(demo): phase 5 verified")
    hook("git commit -q -m 'feat(demo): phase 5 verified'")
    assert record()["closed"] is not None


# ── 2. `verification_attempts` counts attempts on record, and is actually emitted ──────────────────


def test_verification_attempts_is_the_highest_attempt_on_record(stub_sink):
    """Not a per-invocation counter: three calls, one number, and it is the verdict's."""
    project, store, _ = stub_sink
    phase_dir = phase(project)
    verdict(phase_dir, 1, "fail", "verdict-attempt-1.json")
    verdict(phase_dir, 2, "fail", "verdict-attempt-2.json")
    verdict(phase_dir, 3, "pass")
    for _ in range(3):
        assert metrics.record_verification_attempts(str(phase_dir)) == 3
    assert stored(store, "05")["verification_attempts"] == 3


def test_a_phase_with_no_verdict_has_run_no_attempt_and_records_nothing(stub_sink):
    project, store, _ = stub_sink
    phase_dir = phase(project)
    assert metrics.record_verification_attempts(str(phase_dir)) == 0
    assert not (store / "phase-05.json").exists()


def test_an_unreadable_verdict_record_records_nothing_rather_than_a_number(stub_sink):
    project, store, _ = stub_sink
    phase_dir = phase(project)
    (phase_dir / "verdict.json").write_text("{not json", encoding="utf-8")
    assert metrics.record_verification_attempts(str(phase_dir)) is None
    assert not (store / "phase-05.json").exists()


def test_the_verdict_write_hook_emits_the_attempt_count(tmp_path: Path):
    """The emission point that went missing with the deleted review script. Driven through the real
    hook on the real trigger, because an emitter with no caller is what this repairs."""
    import shutil

    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "store").mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    phase_dir = phase(tmp_path, 1, "demo")
    verdict(phase_dir, 1, "fail", "verdict-attempt-1.json")
    verdict(phase_dir, 2, "pass")
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/hook_verifier.sh")],
        input=json.dumps(
            {"tool_input": {"file_path": str(phase_dir / "verdict.json")}}
        ),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "AVENGER_METRICS_CMD": str(double),
            "AVENGER_METRICS_PROJECT": "unit-test",
            "AVENGER_METRICS_LOG": str(tmp_path / "metrics.log"),
            "DOUBLE_LOG": str(tmp_path / "calls.log"),
            "DOUBLE_STORE": str(tmp_path / "store"),
        },
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(
        (tmp_path / "store" / "phase-01.json").read_text(encoding="utf-8")
    )
    assert record["verification_attempts"] == 2


# ── 4. every stage that concludes a defect records it ───────────────────────────────────────────────


def breaker_record(phase_dir: Path, verdict_: str, items: list[str]) -> Path:
    key = "counterexamples" if verdict_ == "found" else "attacked"
    path = phase_dir / "breaker.json"
    path.write_text(
        json.dumps({"verdict": verdict_, key: items, "readers": ["x"]}), "utf-8"
    )
    return path


def test_a_found_breaker_verdict_records_one_defect_per_counterexample(stub_sink):
    project, store, _ = stub_sink
    phase_dir = phase(project)
    path = breaker_record(
        phase_dir, "found", ["tests/demo/5-config/test_breaker.py::test_replay"]
    )
    assert metrics.record_breaker_findings(str(phase_dir), str(path)) == 1
    assert metrics.record_breaker_findings(str(phase_dir), str(path)) == 1  # converges
    defects = stored(store, "05")["defects"]
    assert len(defects) == 1
    assert defects[0]["found_by"] == "breaker"
    assert defects[0]["real"] is True
    assert defects[0]["recorded_by"] == "stage"
    assert defects[0]["id"].startswith("breaker-")
    # The Breaker runs only after a passing verdict, so a counterexample it lands got past
    # implementation AND verification - firstmate's "last stage the defect passed through
    # undetected". Recording `implementation` credits the Verifier with a catch it never made.
    assert defects[0]["stage_reached"] == "verification"


def test_a_clean_breaker_verdict_records_no_defect(stub_sink):
    project, store, _ = stub_sink
    phase_dir = phase(project)
    path = breaker_record(phase_dir, "clean", ["replay", "malformed payloads"])
    assert metrics.record_breaker_findings(str(phase_dir), str(path)) == 0
    assert not (store / "phase-05.json").exists()


def test_a_breaker_json_write_reaches_the_emission(tmp_path: Path):
    """The hook branch: a `breaker.json` write used to reach no hook at all."""
    import shutil

    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "store").mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    phase_dir = phase(tmp_path, 1, "demo")
    path = breaker_record(phase_dir, "found", ["tests/demo/1-demo/test_b.py::test_x"])
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/hook_verifier.sh")],
        input=json.dumps({"tool_input": {"file_path": str(path)}}),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "AVENGER_METRICS_CMD": str(double),
            "AVENGER_METRICS_PROJECT": "unit-test",
            "AVENGER_METRICS_LOG": str(tmp_path / "metrics.log"),
            "DOUBLE_LOG": str(tmp_path / "calls.log"),
            "DOUBLE_STORE": str(tmp_path / "store"),
        },
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(
        (tmp_path / "store" / "phase-01.json").read_text(encoding="utf-8")
    )
    assert [d["found_by"] for d in record["defects"]] == ["breaker"]


def test_the_emission_gate_holds_the_breaker_floor(stub_sink):
    """A phase does not close carrying fewer breaker defects than its breaker.json lands."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    path = breaker_record(
        phase_dir, "found", ["tests/demo/t.py::a", "tests/demo/t.py::b"]
    )
    code, lines = emission_gate.check_defects(str(phase_dir))
    assert code == emission_gate.GAP
    assert "found_by=breaker" in " ".join(lines)
    metrics.record_breaker_findings(str(phase_dir), str(path))
    assert emission_gate.check_defects(str(phase_dir))[0] == emission_gate.CLEAN


def test_an_unparseable_breaker_record_is_undecidable_not_clean(stub_sink):
    project, _, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    (phase_dir / "breaker.json").write_text("{nope", encoding="utf-8")
    assert emission_gate.check_defects(str(phase_dir))[0] == emission_gate.UNDECIDABLE


def test_spec_gate_blockers_are_recorded_as_spec_gate_defects(stub_sink):
    project, store, _ = stub_sink
    path = spec(phase(project))
    blocking = [
        {
            "id": "o1",
            "category": "missing-requirement",
            "statement": "No criterion covers replay.",
        },
        {
            "id": "o2",
            "category": "contradiction",
            "statement": "R5.1.1 contradicts the overview.",
        },
    ]
    assert metrics.record_spec_gate_findings(str(path), blocking) == 2
    assert (
        metrics.record_spec_gate_findings(str(path), blocking) == 2
    )  # converges by statement
    defects = stored(store, "05")["defects"]
    assert len(defects) == 2
    assert {d["found_by"] for d in defects} == {"spec-gate"}
    assert all(d["real"] is False and d["stage_reached"] == "spec" for d in defects)


def test_the_triage_decide_step_reaches_the_spec_gate_emission(stub_sink, monkeypatch):
    """Driven through the real decide step, where the blocking set is derived."""
    project, store, _ = stub_sink
    path = spec(phase(project))
    obs = project / "observations.json"
    cls = project / "classifications.json"
    obs.write_text(
        json.dumps(
            {
                "observations": [
                    {
                        "id": "o1",
                        "area": "requirements",
                        "statement": "R5.1.1 is two behaviours.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cls.write_text(
        json.dumps(
            {
                "classifications": [
                    {"id": "o1", "category": "missing-requirement", "why": "split"}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AVENGER_METRICS_SPEC_PATH", str(path))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_gate_triage.py"),
            "decide",
            str(obs),
            str(cls),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr  # BLOCKED
    defects = stored(store, "05")["defects"]
    assert [d["found_by"] for d in defects] == ["spec-gate"]


# ── 5. the record says what the pipeline wrote itself ───────────────────────────────────────────────


def test_every_scalar_the_pipeline_stamps_is_named_on_the_provenance_row(stub_sink):
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, 1)
    metrics.record_phase_open(str(phase_dir))
    metrics.record_verification_attempts(str(phase_dir))
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    assert metrics.record_phase_close(str(phase_dir)) is True
    record = stored(store, "05")
    stamped = metrics.stamped_fields(record)
    assert {"opened", "verification_attempts", "closed", "elapsed_minutes"} <= stamped
    report = metrics.provenance_report(record)
    assert report["scalars_not_by_pipeline"] == []
    assert report["fraction_by_pipeline"] == 1.0


def test_a_hand_entered_scalar_is_not_credited_to_the_pipeline(stub_sink):
    project, store, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    # The operator types a close in afterwards, through the writer, with no provenance row update.
    import metrics_sink as sink

    sink.set_fields("05", closed="2026-09-06T00:00:00Z", connection_drops=2)
    report = metrics.provenance_report(stored(store, "05"))
    assert "opened" in report["scalars_by_pipeline"]
    assert set(report["scalars_not_by_pipeline"]) == {"closed", "connection_drops"}
    assert report["fraction_by_pipeline"] < 1.0


def test_the_provenance_row_lands_before_the_close_seals_the_collections(stub_sink):
    """`closed` seals `gate_calls`; a provenance row written after it is refused. So the order of
    writes at close is the guard, and this pins it on the writer's own call log."""
    project, store, log = stub_sink
    git_init(project)
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    assert metrics.record_phase_close(str(phase_dir)) is True
    calls = read_calls(log)
    closing = next(
        i
        for i, c in enumerate(calls)
        if c[:2] == ["set", "05"] and any(a.startswith("closed=") for a in c)
    )
    provenance = [
        i
        for i, c in enumerate(calls)
        if c[:3] == ["add", "05", "gate_calls"]
        and any(metrics.PROVENANCE_STAGE in a for a in c)
    ]
    assert provenance and max(provenance) < closing


def test_a_field_the_row_names_but_a_failed_write_left_null_is_not_counted(stub_sink):
    record = {
        "phase": "05",
        "opened": "2026-09-06T00:00:00Z",
        "closed": None,
        "gate_calls": [
            {
                "stage": metrics.PROVENANCE_STAGE,
                "note": f"{metrics.PROVENANCE_PREFIX} closed,opened",
            }
        ],
        "defects": [],
    }
    report = metrics.provenance_report(record)
    assert report["scalars_by_pipeline"] == ["opened"]
    assert report["scalars_held"] == 1


def test_a_failed_read_never_narrows_the_provenance_row(stub_sink):
    """The row is merged from a read-back and written by UPSERT, so a read that could not answer
    must never be taken for a row holding nothing: merging it against an empty set rewrites the row
    as this stamp's names alone, and every earlier pipeline-written scalar then reports as
    hand-entered - the instrument built to tell the two apart mislabelling its own.

    The reachable shape is a read that fails while the writer still works - a timed-out `show`, one
    reply that is not JSON - so this fails exactly the merge read and lets the rest through.

    Red when the defect returns: the row comes back naming `closed` alone and `opened` moves to
    `scalars_not_by_pipeline`.
    """
    project, store, _ = stub_sink
    import metrics_sink as sink

    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    before = metrics.stamped_fields(stored(store, "05"))
    assert "opened" in before

    real_show = sink.show
    seen: list[str] = []

    def show_failing_once(phase: str):
        seen.append(phase)
        return None if len(seen) == 1 else real_show(phase)

    try:
        sink.show = show_failing_once
        metrics._record_provenance("05", {"closed"})
    finally:
        sink.show = real_show

    record = stored(store, "05")
    assert before <= metrics.stamped_fields(record)
    assert "opened" in metrics.provenance_report(record)["scalars_by_pipeline"]


def test_a_writer_that_cannot_answer_reads_at_all_writes_no_row(stub_sink, monkeypatch):
    """The other end of the same rule: when nothing can say what the row already names, the row is
    left exactly as it was - incomplete for as long as the read fails, never a wrong set."""
    project, store, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    before = metrics.stamped_fields(stored(store, "05"))

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    assert metrics._record_provenance("05", {"closed"}) is False

    monkeypatch.delenv("DOUBLE_REFUSE")
    assert metrics.stamped_fields(stored(store, "05")) == before


def test_a_readable_record_with_no_row_yet_is_the_ordinary_first_stamp(stub_sink):
    """The other side of that distinction: a record that reads fine and simply holds no row is not
    a failed read, and the first stamp still writes."""
    project, store, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    assert "opened" in metrics.stamped_fields(stored(store, "05"))


def test_a_close_that_is_the_records_first_write_still_names_itself(stub_sink):
    """The third state `sink.show` answers None for: the record does not exist YET.

    A phase never opened has no `opened` and so no `elapsed_minutes`, and a project whose suite
    cannot be collected has no `tests_after` - which leaves `closed` as the only field of the first
    stamp. `_stamp` opens the record through `set_fields`, and it has no ordinary field to set here,
    so the record is still absent when the row is merged.

    Red when the defect returns: the record lands with a pipeline-written `closed` and no provenance
    row, and `provenance_report` calls the pipeline's own stamp hand-entered.
    """
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")

    assert metrics.record_phase_close(str(phase_dir)) is True

    record = stored(store, "05")
    assert record["closed"] is not None
    assert record["opened"] is None
    assert "closed" in metrics.stamped_fields(record)
    report = metrics.provenance_report(record)
    assert report["scalars_not_by_pipeline"] == []
    assert report["fraction_by_pipeline"] == 1.0


def test_the_provenance_cli_reports_the_fraction(stub_sink):
    project, _, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/pipeline_metrics.py"),
            "provenance",
            str(phase_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["phase"] == "05"
    assert "opened" in report["scalars_by_pipeline"]


def test_the_real_writer_accepts_the_provenance_row_and_seals_after_it(real_sink):
    """The claim that matters: firstmate's own writer validates the record this produces, takes the
    provenance row, and then seals - so a later stamp is refused while the row is already in."""
    project, home = real_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, 2)
    metrics.record_phase_open(str(phase_dir))
    metrics.record_verification_attempts(str(phase_dir))
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    assert metrics.record_phase_close(str(phase_dir)) is True
    record = stored(home, "05")
    assert record["closed"] is not None
    assert record["verification_attempts"] == 2
    assert {"opened", "closed", "verification_attempts"} <= metrics.stamped_fields(
        record
    )
    validate = subprocess.run(
        [os.environ["AVENGER_METRICS_CMD"], "validate", "05"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )
    assert validate.returncode == 0, validate.stderr
    # Re-emission converges rather than being refused by the seal.
    assert metrics.record_phase_close(str(phase_dir)) is True


# ── 6. tests_before/tests_after count the suite the gate actually runs ──────────────────────────────
#
# `count_tests` stamps `tests_before`/`tests_after` from the project's DECLARED test root minus that
# root's `e2e/`. `hook_verifier.sh`'s full-suite fallback ran a different command: no root at all and
# a hardcoded `--ignore=tests/e2e`. On the default layout the two agree by coincidence (2013 == 2013
# measured on this repository), so the divergence is invisible here and permanent anywhere else -
# measured at 3 against 4 on a project with tests at `suite/`. The declaration now has ONE reader,
# `subprocess_check.test_roots()`, and these prove both callers use it.


def scratch_project(root: Path, *, e2e_fails: bool) -> Path:
    """A project whose tests are NOT at `tests/` - the layout the divergence needs."""
    (root / "suite" / "e2e").mkdir(parents=True, exist_ok=True)
    for index in (1, 2, 3):
        (root / "suite" / f"test_u{index}.py").write_text(
            f"def test_u{index}():\n    assert True\n", encoding="utf-8"
        )
    body = "assert False" if e2e_fails else "assert True"
    (root / "suite" / "e2e" / "test_journey.py").write_text(
        f"def test_journey():\n    {body}\n", encoding="utf-8"
    )
    return root


def test_the_declaration_has_one_reader(monkeypatch):
    """`pipeline_metrics.test_roots` reads `subprocess_check.test_roots()` rather than re-reading the
    environment, and carries EVERY declared root. Two copies of one declaration is what let the three
    readers disagree; keeping only the first is that disagreement one notch narrower."""
    import subprocess_check

    monkeypatch.setenv("SUBPROC_CHECK_PATHS", "suite" + os.pathsep + "extra")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/proj")
    assert subprocess_check.test_roots() == [Path("suite"), Path("extra")]
    assert metrics.test_roots() == [Path("/proj/suite"), Path("/proj/extra")]

    monkeypatch.delenv("SUBPROC_CHECK_PATHS")
    assert subprocess_check.test_roots() == [Path("tests")]
    assert metrics.test_roots() == [Path("/proj/tests")]


def test_print_roots_reports_without_claiming_a_scan(tmp_path: Path):
    """The shell's way in to the same answer. It must NOT emit the guard's clean-scan statement:
    a query borrowing a gate's clean line claims a result nobody obtained."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/subprocess_check.py"), "--print-roots"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env={**os.environ, "SUBPROC_CHECK_PATHS": "suite" + os.pathsep + "extra"},
    )
    assert result.returncode == 0
    assert result.stdout.split() == ["suite", "extra"]
    assert "SCOPE OF A CLEAN RESULT" not in result.stderr


def collected(argv: list[str], cwd: Path) -> int:
    """How many test items pytest would run for `argv`, from its own summary line."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    match = metrics.TESTS_COLLECTED.search(result.stdout)
    assert match, result.stdout + result.stderr
    return int(match.group(1))


def test_the_counted_suite_and_the_gates_suite_are_one_population(
    tmp_path: Path, monkeypatch
):
    """The property, not the tree shape: for a project whose tests are not at `tests/`, the number
    stamped into `tests_before` equals the number the verifier hook's fallback actually collects.

    Before the fix these were 3 and 4 - the gate ran the whole tree, e2e included, while the record
    claimed the declared root.
    """
    scratch_project(tmp_path, e2e_fails=False)
    monkeypatch.setenv("SUBPROC_CHECK_PATHS", "suite")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    counted = metrics.count_tests()

    roots = subprocess.run(
        [sys.executable, str(ROOT / "scripts/subprocess_check.py"), "--print-roots"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "SUBPROC_CHECK_PATHS": "suite"},
    ).stdout.split()
    argv: list[str] = []
    for root in roots:
        argv += [f"--ignore={root}/e2e", root]
    gated = collected(argv, tmp_path)

    assert counted == 3, counted
    assert counted == gated, f"record counts {counted}, the gate runs {gated}"


def test_every_declared_root_is_counted_not_only_the_first(tmp_path: Path, monkeypatch):
    """The same property for a project that declares SEVERAL roots: the number stamped into
    `tests_before` equals the number the verifier hook's own argv collects.

    Red when the defect returns: counting `test_roots()[0]` alone stamps 3 while the gate runs 5.
    """
    scratch_project(tmp_path, e2e_fails=False)
    (tmp_path / "extra" / "e2e").mkdir(parents=True)
    for index in (1, 2):
        (tmp_path / "extra" / f"test_x{index}.py").write_text(
            f"def test_x{index}():\n    assert True\n", encoding="utf-8"
        )
    (tmp_path / "extra" / "e2e" / "test_journey.py").write_text(
        "def test_journey():\n    assert True\n", encoding="utf-8"
    )
    declared = "suite" + os.pathsep + "extra"
    monkeypatch.setenv("SUBPROC_CHECK_PATHS", declared)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    counted = metrics.count_tests()

    roots = subprocess.run(
        [sys.executable, str(ROOT / "scripts/subprocess_check.py"), "--print-roots"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "SUBPROC_CHECK_PATHS": declared},
    ).stdout.split()
    argv: list[str] = []
    for root in roots:
        argv += [f"--ignore={root}/e2e", root]
    gated = collected(argv, tmp_path)

    assert counted == 5, counted
    assert counted == gated, f"record counts {counted}, the gate runs {gated}"


def test_a_declared_root_that_does_not_exist_is_skipped_not_handed_to_pytest(
    tmp_path: Path, monkeypatch
):
    """The hook's own rule, at the counting end: pytest treats a missing path as a usage error, which
    would read as no count at all where the existing roots still have a population to report."""
    scratch_project(tmp_path, e2e_fails=False)
    monkeypatch.setenv("SUBPROC_CHECK_PATHS", "suite" + os.pathsep + "nowhere")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert metrics.count_tests() == 3

    monkeypatch.setenv("SUBPROC_CHECK_PATHS", "nowhere")
    assert metrics.count_tests() is None


def test_the_verifier_hooks_fallback_excludes_the_declared_roots_e2e(tmp_path: Path):
    """End to end through the real hook, on the trigger that runs the suite. A RED test in the
    declared root's own `e2e/` must not be run by the phase suite - §4b excludes feature e2e from
    the phase verifier hook, and before this the exclusion only ever applied to a literal
    `tests/e2e`.

    Red when the defect returns: a hardcoded `--ignore=tests/e2e` over no root collects
    `suite/e2e/test_journey.py`, the suite goes red, and the hook stops on it.
    """
    import shutil

    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    scratch_project(tmp_path, e2e_fails=True)
    git_init(tmp_path)

    # A spec stamped `status: done`, with a real mapping row - the spec-done trigger, and the one
    # branch that runs the phase suite. The phase has NO tests directory of its own, so `TESTPATH`
    # does not resolve and the full-suite fallback is what runs: the path under test.
    spec_dir = tmp_path / "docs/features/demo/phases/1-demo/specs/1.1-a"
    spec_dir.mkdir(parents=True, exist_ok=True)
    header = "---\nfeature: demo\nphase: 1-demo\nstatus: %s\nspec_gate: approved\n"
    body = "review_status: approved\n---\n\n## Acceptance criteria\n\n- R1.1.1 (binding: none) do\n"
    (spec_dir / "spec.md").write_text(header % "in-progress" + body, encoding="utf-8")
    (spec_dir / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
        "| R1.1.1 | suite/test_u1.py::test_u1 | integration | drives the seam |\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "specs"], cwd=tmp_path, check=True, capture_output=True
    )
    (spec_dir / "spec.md").write_text(header % "done" + body, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/hook_verifier.sh")],
        input=json.dumps({"tool_input": {"file_path": str(spec_dir / "spec.md")}}),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "SUBPROC_CHECK_PATHS": "suite",
            "AVENGER_METRICS_OFF": "1",
        },
    )
    combined = result.stdout + result.stderr
    # The failing e2e test must never have been collected by the phase suite.
    assert "test_journey" not in combined, combined
    assert "declared test roots minus e2e" in combined or result.returncode == 0, (
        combined
    )


# ── 7. a measurement never decides a verdict, and a reader states the field's meaning ───────────────


def test_a_failing_spec_gate_emission_does_not_decide_the_gates_verdict(
    stub_sink, monkeypatch
):
    """`spec_gate_triage.py`'s exit code IS the verdict, and `guard_scope.run` re-raises anything
    that is not a SystemExit - so an exception escaping this emission left the process exiting 1,
    the code that script spells BLOCKED. Measurement may never decide a verdict (§6d).

    Red when the defect returns: `main` raises instead of returning the verdict it derived.
    """
    project, _, _ = stub_sink
    import spec_gate_triage

    path = spec(phase(project))
    monkeypatch.setenv("AVENGER_METRICS_SPEC_PATH", str(path))

    def explode(*_args, **_kwargs):
        raise RuntimeError("the writer died mid-entry")

    monkeypatch.setattr(metrics, "record_defect", explode)

    obs = project / "observations.json"
    cls = project / "classifications.json"
    obs.write_text(
        json.dumps(
            {
                "observations": [
                    {
                        "id": "o1",
                        "area": "requirements",
                        "statement": "no replay criterion",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cls.write_text(
        json.dumps(
            {
                "classifications": [
                    {"id": "o1", "category": "missing-requirement", "why": "x"}
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        spec_gate_triage.main(["decide", str(obs), str(cls)])
        == spec_gate_triage.BLOCKED
    )


def test_the_emission_fails_open_at_its_own_definition(stub_sink, monkeypatch):
    """The guard is on the recorder, not around one call site, so the next caller cannot
    reintroduce the hole by forgetting the wrapper."""
    project, _, _ = stub_sink
    path = spec(phase(project))

    def explode(*_args, **_kwargs):
        raise RuntimeError("the writer died mid-entry")

    monkeypatch.setattr(metrics, "record_defect", explode)
    assert (
        metrics.record_spec_gate_findings(
            str(path),
            [{"id": "o1", "category": "contradiction", "statement": "contradicts"}],
        )
        == 0
    )


def test_a_verifier_gate_call_is_filed_under_the_attempt_in_flight(
    stub_sink, monkeypatch
):
    """`verification_attempts` counts attempts already CONCLUDED (it used to be written as the
    attempt in flight), so the reader converts: a call made while an attempt is being decided
    belongs to `concluded + 1`.

    Red when the defect returns: with two attempts concluded the call is filed under 2 - one low,
    and against a cap of 3 that is a number the record cannot be read against.
    """
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, 1, name="verdict-attempt-1.json")
    verdict(phase_dir, 2)
    metrics.record_phase_open(str(phase_dir))
    assert metrics.record_verification_attempts(str(phase_dir)) == 2

    monkeypatch.setenv("AVENGER_METRICS_PHASE", "05")
    monkeypatch.setenv("AVENGER_METRICS_STAGE", "verifier")
    assert (
        metrics.record_gate_call(model="probe", latency_ms=900, verdict="pass") is True
    )

    call = next(
        c for c in stored(store, "05")["gate_calls"] if c.get("stage") == "verifier"
    )
    assert call["attempt"] == 3
