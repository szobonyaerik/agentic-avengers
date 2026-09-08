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
    write_spec,
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


def test_an_unreadable_record_does_not_re_stamp_a_closed_phase(
    stub_sink, monkeypatch, capsys
):
    """A failed READ is not an empty record, and the close decides two things on that answer.

    `sink.show` returns None for a record that does not exist yet AND for one the writer refused or
    answered non-JSON for. Read as `{}`, a transient refusal defeats the converge guard and asks a
    SEALED record to change - the one write firstmate's producer contract forbids.

    Red when the distinction goes: the second close issues writes against a record it already
    closed.
    """
    project, store, log = stub_sink
    git_init(project)
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    assert metrics.record_phase_close(str(phase_dir)) is True
    closed = stored(store, "05")["closed"]
    before = len(read_calls(log))

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    assert metrics.record_phase_close(str(phase_dir)) is False

    monkeypatch.delenv("DOUBLE_REFUSE")
    assert stored(store, "05")["closed"] == closed
    assert [
        call for call in read_calls(log)[before:] if call[:1] in (["set"], ["add"])
    ] == [], "an unreadable record must not be written to at all"


def test_an_unreadable_record_is_not_reported_as_a_phase_nobody_opened(
    stub_sink, monkeypatch, capsys
):
    """The never-opened note names a cause and prescribes its remedy, so it must be true.

    `elapsed_minutes` is left null and SAID when nothing stamped the phase open - a spec authored
    through a heredoc. A failed read produces the same missing `opened`, and reported under that
    note it is a confident wrong diagnosis on the one field this issue exists to repair.

    Red when the distinction goes: a phase that WAS opened is told its specs bypassed the hook.
    """
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    capsys.readouterr()

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    assert metrics.record_phase_close(str(phase_dir)) is False

    err = capsys.readouterr().err
    assert "elapsed_minutes NOT recorded" not in err
    assert "could not be READ" in err


def test_emission_switched_off_is_not_reported_as_an_unreadable_record(
    stub_sink, monkeypatch, capsys
):
    """The fourth state `sink.show` folds into one `None`, and the only one that is not a failure.

    `AVENGER_METRICS_OFF=1`, no `fm-pipeline-metrics.sh` anywhere, and a writer this process already
    abandoned all answer `None` to every call - the first two are the documented normal state of a
    standalone install with no firstmate home. Nothing was read there because nothing was asked, so
    a read-failure diagnostic sends its reader to repair a writer they deliberately do not have, and
    prescribes a next close stamp that will report exactly the same thing.

    Red when the state collapses back into "could not be READ": every landing commit says the record
    could not be read, on a machine that is recording nothing on purpose.
    """
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    git_land(project, "land")
    capsys.readouterr()

    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    assert metrics.record_phase_close(str(phase_dir)) is False

    err = capsys.readouterr().err
    assert "could not be READ" not in err
    assert "elapsed_minutes NOT recorded" not in err
    assert metrics.sink.PREFIX not in err


def test_the_four_record_states_are_told_apart_at_one_place(stub_sink, monkeypatch):
    """Every state named once, by the two accessors every caller in the module reads through.

    `sink.show` answers a bare `None` to all four, and each was patched into a caller's own `None`
    check one review round at a time. This drives the four against a real writer: present, absent,
    unreadable, and not configured.

    Absence and unreadability are the pair a READ cannot separate - only opening the record can,
    and that is a write - so `read_record` reports what it found and `open_record`, which every
    writer uses, resolves it. Both are driven here, because the split is what keeps a reporter from
    creating a record and a writer from reading a refusal as an empty one.

    Red when a state stops being distinguished: two of the four collapse onto one answer and the
    callers below go back to guessing which.
    """
    project, _, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)

    absent = metrics.read_record("05")
    assert absent.state == metrics.RECORD_ABSENT
    assert absent.record == {}, (
        "a pure read reports absence and creates nothing to report"
    )

    opened = metrics.open_record("05")
    assert opened.state == metrics.RECORD_ABSENT
    assert opened.usable and opened.record.get("phase") == "05"

    metrics.record_phase_open(str(phase_dir))
    present = metrics.read_record("05")
    assert present.state == metrics.RECORD_PRESENT
    assert present.usable and present.record.get("opened") is not None

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    unreadable = metrics.open_record("05")
    assert unreadable.state == metrics.RECORD_UNREADABLE
    assert not unreadable.usable and unreadable.record == {}

    monkeypatch.delenv("DOUBLE_REFUSE")
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    off = metrics.read_record("05")
    assert off.state == metrics.RECORD_NOT_CONFIGURED
    assert not off.usable and off.record == {}


def test_an_unreadable_record_never_seeds_over_an_observed_skill_load(
    stub_sink, monkeypatch
):
    """A seed is written only when the record says the load was not already observed.

    `record_skill_load(loaded=False)` reads the record to check that, and read as `{}` an unreadable
    answer says "no load observed" - so the seed overwrites the observation, and a skill the stage
    really loaded is audited as a gap. Same `None`, same class, one collection over.

    Red when the read stops being told apart: the observed load is overwritten by the seed.
    """
    project, store, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    assert (
        metrics.record_skill_load(
            "05", stage="avenger-verifier", skill="tdd", evidence="Read", loaded=True
        )
        is True
    )

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    assert (
        metrics.record_skill_load(
            "05", stage="avenger-verifier", skill="tdd", evidence="", loaded=False
        )
        is False
    )

    monkeypatch.delenv("DOUBLE_REFUSE")
    loads = stored(store, "05")["skill_loads"]
    assert [entry["loaded"] for entry in loads] == [True]


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


def test_two_counterexamples_sharing_a_long_prefix_are_two_defects(stub_sink):
    """Identity is over the WHOLE counterexample, because the emission floor counts whole strings.

    A truncated id upserted both onto one entry (`sink.add` converges by id) while
    `emission_gate.check_defects` counted two, so the handover gate reported a GAP whose own printed
    remedy - re-running this emitter - could never raise the count: a blocking check with no
    available remedy.

    Red when the defect returns: one defect is recorded against two counterexamples and the floor
    reports GAP.
    """
    project, store, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    shared = "tests/demo/5-config/test_breaker.py::test_replay_" + "x" * 240
    path = breaker_record(phase_dir, "found", [shared + "_one", shared + "_two"])

    assert metrics.record_breaker_findings(str(phase_dir), str(path)) == 2
    assert metrics.record_breaker_findings(str(phase_dir), str(path)) == 2  # converges

    defects = stored(store, "05")["defects"]
    assert len(defects) == 2
    assert len({d["id"] for d in defects}) == 2
    assert emission_gate.check_defects(str(phase_dir))[0] == emission_gate.CLEAN


def test_two_counterexamples_differing_only_in_whitespace_agree_with_the_floor(
    stub_sink,
):
    """Counterexample identity has ONE owner, so the emitter and the floor cannot disagree.

    The emitter hashed the whitespace-NORMALIZED text; `emission_gate` counted the raw strings. Two
    counterexamples differing only by internal whitespace were therefore one entry to the emitter
    and two to the floor, so `check_defects` reported a GAP and prescribed re-running the emitter -
    which by construction could never write a second entry. A blocking handover gate with no
    available remedy, the same wedge a truncated id produced once, one normalization further along.

    Red when the definition splits again: the recorded count and the floor's count diverge and the
    gate reports GAP on a phase that emitted everything its Breaker landed.
    """
    project, store, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    shared = "tests/demo/5-config/test_breaker.py::test_replay " + "x" * 120
    path = breaker_record(phase_dir, "found", [shared + " tail", shared + "  tail"])

    metrics.record_breaker_findings(str(phase_dir), str(path))
    recorded = stored(store, "05")["defects"]
    landed = emission_gate.described_counterexamples(phase_dir)

    assert len(recorded) == len(landed), (
        "the entries the emitter landed and the counterexamples the floor counts are one set"
    )
    assert emission_gate.check_defects(str(phase_dir))[0] == emission_gate.CLEAN


def test_a_breaker_defect_id_stays_bounded(stub_sink):
    """Bounded as well as whole: dropping the truncation would let an arbitrarily long
    counterexample become the record's identity field."""
    project, store, _ = stub_sink
    phase_dir = phase(project)
    path = breaker_record(phase_dir, "found", ["y" * 5000])
    assert metrics.record_breaker_findings(str(phase_dir), str(path)) == 1
    assert len(stored(store, "05")["defects"][0]["id"]) < 200


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


def test_a_failed_read_never_narrows_a_specs_round_history(stub_sink, monkeypatch):
    """`bytes_by_round` is rewritten from what it already holds, so a failed read must not write.

    `sink.show` answers None on a non-zero exit AND on output that is not JSON, not only when the
    writer is dead - a writer emitting one stray line on stdout answers None to every read while
    every WRITE still lands, which is the state driven here. Read as `{}`, the series starts empty,
    the cache short-circuit is skipped because it is falsy, and the upsert replaces every earlier
    round of that spec with this one - the growth series this exists to expose, deleted by the
    emitter that measures it.

    Red when the read goes back to a bare `None`: five rounds become one and the call reports
    success.
    """
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    metrics.record_spec_round(str(spec), verdict="approved")
    spec.write_text(
        "---\nfeature: demo\n---\n" + "- R8.1.1 one\n" * 40, encoding="utf-8"
    )
    assert metrics.record_spec_round(str(spec), verdict="approved") == 2
    before = stored(store, "08")["specs"][0]["bytes_by_round"]

    spec.write_text(
        "---\nfeature: demo\n---\n" + "- R8.1.1 one\n" * 80, encoding="utf-8"
    )
    real_show, real_ensure = metrics.sink.show, metrics.sink.ensure
    monkeypatch.setattr(metrics.sink, "show", lambda phase_: None)
    monkeypatch.setattr(metrics.sink, "ensure", lambda phase_: True)
    assert metrics.record_spec_round(str(spec), verdict="approved") is None

    monkeypatch.setattr(metrics.sink, "show", real_show)
    monkeypatch.setattr(metrics.sink, "ensure", real_ensure)
    assert stored(store, "08")["specs"][0]["bytes_by_round"] == before
    assert metrics.record_spec_round(str(spec), verdict="approved") == 3, (
        "the body was not cached, so the next verdict still records the round"
    )


def test_a_failed_read_after_the_write_does_not_stamp_spec_rounds_at_one(
    stub_sink, monkeypatch
):
    """The round lands; the count taken afterwards must not be invented from a failed read.

    `_spec_rounds` answers its `default=1` for a record it was handed nothing for, and `_stamp`
    then names `spec_rounds` on the provenance row as pipeline-written. A spec on its third round
    is recorded as having taken one, with no error anywhere, on the field the ratchet hypothesis is
    judged by.

    Red when the read goes back to a bare `None`: `spec_rounds` is stamped 1 against a
    `bytes_by_round` of three.
    """
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    metrics.record_spec_round(str(spec), verdict="approved")
    spec.write_text(
        "---\nfeature: demo\n---\n" + "- R8.1.1 one\n" * 40, encoding="utf-8"
    )
    metrics.record_spec_round(str(spec), verdict="approved")

    written = {"specs": False}
    real_add, real_show = metrics.sink.add, metrics.sink.show

    def add(phase_, collection, *args, **kwargs):
        landed = real_add(phase_, collection, *args, **kwargs)
        written["specs"] = written["specs"] or collection == "specs"
        return landed

    monkeypatch.setattr(metrics.sink, "add", add)
    monkeypatch.setattr(
        metrics.sink, "show", lambda p: None if written["specs"] else real_show(p)
    )

    spec.write_text(
        "---\nfeature: demo\n---\n" + "- R8.1.1 one\n" * 80, encoding="utf-8"
    )
    assert metrics.record_spec_round(str(spec), verdict="approved") == 3

    record = stored(store, "08")
    assert len(record["specs"][0]["bytes_by_round"]) == 3
    assert record["spec_rounds"] != 1, (
        "a count the read could not answer is left alone, never stamped at the default"
    )


def test_a_failed_read_does_not_file_a_gate_call_over_an_earlier_one(
    stub_sink, monkeypatch
):
    """A gate call's id carries its attempt, and `sink.add` converges by id.

    Read as `{}`, `_attempt` finds no concluded attempt and files the call under 1 - so a call from
    a later attempt upserts ONTO the first attempt's entry. One gate call overwritten rather than
    added, in the collection every failure cause is read out of.

    Red when the read goes back to a bare `None`: the round-1 entry is replaced.
    """
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, 1)
    metrics.record_phase_open(str(phase_dir))
    assert metrics.record_verification_attempts(str(phase_dir)) == 1

    monkeypatch.setenv("AVENGER_METRICS_PHASE", "05")
    monkeypatch.setenv("AVENGER_METRICS_STAGE", "verifier")
    assert metrics.record_gate_call(model="first", latency_ms=900, verdict="pass")
    before = [c for c in stored(store, "05")["gate_calls"] if c["stage"] == "verifier"]
    assert [c["attempt"] for c in before] == [2]

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    assert (
        metrics.record_gate_call(model="second", latency_ms=100, verdict="pass")
        is False
    )

    monkeypatch.delenv("DOUBLE_REFUSE")
    after = [c for c in stored(store, "05")["gate_calls"] if c["stage"] == "verifier"]
    assert after == before, (
        "an unreadable record must not overwrite a recorded gate call"
    )


def test_the_provenance_report_does_not_call_metrics_off_an_unreadable_record(
    stub_sink, monkeypatch, capsys
):
    """The last command still deciding what a bare `None` meant, one over from the close stamp.

    With no writer configured nothing was read because nothing was asked, so "no record readable"
    names a cause that does not apply and prescribes a retry that answers identically forever.

    Red when the state collapses: `provenance` reports an unreadable record on a machine that is
    recording nothing on purpose.
    """
    project, _, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    capsys.readouterr()

    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    assert metrics.main(["provenance", "05"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no record readable" not in captured.err
    assert "no metrics writer is configured" in captured.err


def test_the_provenance_report_creates_no_record_for_a_phase_that_has_none(
    stub_sink, capsys
):
    """A report READS. Resolving absence by opening the record made it a writer.

    `sink.ensure` runs `init <phase> --project <name> --from <previous>`, so asking what the
    pipeline stamped for a phase that never ran created a record for it - seeded from an earlier
    phase's ledger, with a null `elapsed_minutes`. That is the exact population issue #120 counts,
    manufactured by the instrument built to detect it, and the message the command printed was
    false as of the moment it printed it.

    Red when a reader picks the opening accessor back up: `phase-09.json` exists after a command
    that only asked a question.
    """
    project, store, log = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    capsys.readouterr()

    assert metrics.main(["provenance", "09"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no record for '09' yet" in captured.err
    assert not (store / "phase-09.json").exists(), (
        "a report must not create the record it reports on"
    )
    assert [call for call in read_calls(log) if call[:1] == ["init"]] == [
        ["init", "05", "--project", "unit-test"]
    ], "the only record opened is the one phase-open opened"


def test_the_provenance_report_still_reads_a_record_that_exists(stub_sink, capsys):
    """The green case the split must not cost: a phase with a record still reports on it."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    capsys.readouterr()

    assert metrics.main(["provenance", "05"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert "opened" in report["scalars_by_pipeline"]


def test_a_ref_that_names_no_phase_is_a_usage_error_not_a_record_state(
    stub_sink, capsys
):
    """An unresolvable ref never reached the record, so it is not one of the four read states.

    Reported as an unreadable record it sends its reader to repair a writer that is working
    perfectly, when the remedy is the argument - the same mis-diagnosis the states were named to
    remove, one layer further out.

    Red when the ref borrows a record state again: the exit code says success and the message
    blames the writer.
    """
    stub_sink
    assert metrics.main(["provenance", "docs/features/demo"]) == metrics.USAGE_ERROR

    err = capsys.readouterr().err
    assert "does not resolve to a phase" in err
    assert "remedy is the ARGUMENT" in err
    assert "no record readable" not in err
    assert metrics.DEFECT_BAD_PHASE_REF not in err, (
        "a report is not a lost defect, and the defect markers are read by stages"
    )


def test_a_dropped_gate_call_says_so_rather_than_reading_as_a_run_that_made_none(
    stub_sink, monkeypatch, capsys
):
    """`gate_calls[]` is where a call that reached no verdict belongs, so losing one must be said.

    A writer emitting one stray line on stdout answers None to every read while `add` keeps
    working, and reads fail exactly while the writer misbehaves - which is when those failure
    causes matter most. Dropping the row is right, because filing it under a record that says
    nothing puts it at attempt 1, where `sink.add` converges by id and it REPLACES the first
    attempt's row. Silence is what is not.

    Red when the drop goes quiet: a run that lost every gate call reads like one that made none.
    """
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = phase(project)
    verdict(phase_dir, 1)
    metrics.record_phase_open(str(phase_dir))
    monkeypatch.setenv("AVENGER_METRICS_PHASE", "05")
    monkeypatch.setenv("AVENGER_METRICS_STAGE", "verifier")
    before = stored(store, "05")["gate_calls"]
    capsys.readouterr()

    monkeypatch.setenv("DOUBLE_REFUSE", "show")
    assert metrics.record_gate_call(model="m", latency_ms=10, verdict="pass") is False

    err = capsys.readouterr().err
    monkeypatch.delenv("DOUBLE_REFUSE")
    assert "gate call not recorded" in err
    assert stored(store, "05")["gate_calls"] == before


def test_a_gate_call_stays_silent_when_emission_is_deliberately_off(
    stub_sink, monkeypatch, capsys
):
    """The other half of the same rule: a configured choice is not a failure and must not speak."""
    project, _, _ = stub_sink
    phase_dir = phase(project)
    metrics.record_phase_open(str(phase_dir))
    monkeypatch.setenv("AVENGER_METRICS_PHASE", "05")
    capsys.readouterr()

    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    assert metrics.record_gate_call(model="m", latency_ms=10, verdict="pass") is False

    assert "gate call not recorded" not in capsys.readouterr().err


# ── 8. one landing rule, and one argument per declared root ────────────────────────────────────────


def test_both_landing_projections_answer_from_one_decision(stub_sink):
    """`record_phase_close` decides with the reason and `emission_gate.landed_phases` with the
    verdict, so the two must never be able to disagree: landed is exactly "no reason", and every
    other verdict - including the one git could not answer - carries one.

    Red when the defect returns: a second implementation of the predicate drifts from the first, and
    the gate calls a phase LANDED that the close stamp refuses, prescribing a `phase-close` that by
    construction records nothing.
    """
    project, _, _ = stub_sink
    git_init(project)
    (project / "README.md").write_text("demo\n", encoding="utf-8")
    git_land(project, "root")  # git answers "what changed" only once HEAD exists
    phase_dir = phase(project)

    seen = set()

    def agree(label: str) -> bool | None:
        """Both projections, read at THIS moment - the state is what the working tree is now."""
        verdict_ = metrics.phase_landed(phase_dir)
        why = metrics.not_landed_reason(phase_dir)
        assert (verdict_ is True) == (why is None), (
            f"{label}: {verdict_!r} against {why!r}"
        )
        seen.add(verdict_)
        return verdict_

    spec(phase_dir)
    assert agree("uncommitted") is False
    git_land(project, "specs")
    assert agree("clean, no committed card") is False
    (phase_dir / metrics.CLOSING_DOCUMENT).write_text("# card\n", encoding="utf-8")
    assert agree("card written, uncommitted") is False
    git_land(project, "land")
    assert agree("landed") is True

    assert {True, False} <= seen


def test_a_phase_git_cannot_answer_for_is_not_landed_and_says_why(stub_sink):
    """The third verdict is not a lighter version of the second: unknown must never read as landed,
    and it carries a reason like every refusal does."""
    project, _, _ = stub_sink
    phase_dir = phase(project)  # no repository at all
    assert metrics.phase_landed(phase_dir) is None
    assert metrics.not_landed_reason(phase_dir) is not None


def test_a_declared_root_containing_a_space_runs_rather_than_reading_red(
    tmp_path: Path,
):
    """The hook's own fallback, driven end to end. `SUBPROC_CHECK_PATHS` is operator-set, so a root
    with whitespace is reachable; split at the call it became two nonexistent paths, pytest exited
    on a usage error, and this hook read that as a RED suite - blocking the phase with a message
    about failing tests over a quoting fault.

    Red when the defect returns: the hook fails and names the split paths.
    """
    import shutil

    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    root = tmp_path / "my suite"
    (root / "e2e").mkdir(parents=True)
    (root / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "e2e" / "test_journey.py").write_text(
        "def test_journey():\n    assert False\n", encoding="utf-8"
    )
    git_init(tmp_path)

    spec_dir = tmp_path / "docs/features/demo/phases/1-demo/specs/1.1-a"
    spec_dir.mkdir(parents=True)
    header = "---\nfeature: demo\nphase: 1-demo\nstatus: %s\nspec_gate: approved\n"
    body = "review_status: approved\n---\n\n## Acceptance criteria\n\n- R1.1.1 (binding: none) do\n"
    (spec_dir / "spec.md").write_text(header % "in-progress" + body, encoding="utf-8")
    (spec_dir / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
        "| R1.1.1 | my suite/test_ok.py::test_ok | integration | drives the seam |\n",
        encoding="utf-8",
    )
    git_land(tmp_path, "specs")
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
            "SUBPROC_CHECK_PATHS": "my suite",
            "AVENGER_METRICS_OFF": "1",
        },
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    # The root reached pytest whole - never as `my` and `suite` - and its own e2e stayed excluded.
    assert "file or directory not found" not in combined, combined
    assert "test_journey" not in combined, combined


# ── 9. the declaration's readers, all of them ──────────────────────────────────────────────────────


def scratch_gate_project(root: Path) -> None:
    """A project whose tests are NOT at `tests/`, with a RED test in the declared root's own e2e."""
    import shutil

    shutil.copytree(ROOT / "scripts", root / "scripts")
    (root / "suite" / "e2e").mkdir(parents=True)
    (root / "suite" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (root / "suite" / "e2e" / "test_journey.py").write_text(
        "def test_journey():\n    assert False\n", encoding="utf-8"
    )


def test_the_gate_floor_runs_the_declared_roots_and_excludes_their_e2e(tmp_path: Path):
    """The gate floor is a reader of the declaration too, and it kept the hardcode the hook lost.

    Driven through the real `gate_ci.sh` pre-commit branch on a project whose tests are at `suite/`:
    the declared root runs and its OWN `e2e/` is excluded, which is what the branch's own comment
    says it does.

    Red when the defect returns: `--ignore=tests/e2e` matches nothing on disk, `suite/e2e` is
    collected, its failing journey turns the gate red, and the floor fails a suite it is designed
    never to run.
    """
    scratch_gate_project(tmp_path)
    git_init(tmp_path)
    git_land(tmp_path, "root")

    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/gate_ci.sh")],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "SUBPROC_CHECK_PATHS": "suite",
            "AVENGER_METRICS_OFF": "1",
        },
    )
    combined = result.stdout + result.stderr
    assert "test_journey" not in combined, combined
    assert "1 passed" in combined, combined


def test_both_gates_assemble_the_roots_through_one_file(tmp_path: Path):
    """The property behind "one reader": the floor and the phase gate ask the same file, so they
    cannot run different populations. Asserted by driving the shared assembly the way both do."""
    (tmp_path / "suite" / "e2e").mkdir(parents=True)
    (tmp_path / "extra").mkdir()
    script = (
        f'. "{ROOT}/scripts/test_root_args.sh"\n'
        f'test_root_pytest_args "{ROOT}/scripts" || echo "FELL-BACK"\n'
        'printf "%s\\n" "${TEST_ROOT_ARGS[@]}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env={**os.environ, "SUBPROC_CHECK_PATHS": "suite" + os.pathsep + "extra"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:4] == [
        "--ignore=suite/e2e",
        "suite",
        "--ignore=extra/e2e",
        "extra",
    ], result.stdout
    assert "FELL-BACK" not in result.stdout


def fall_back_scope(tmp_path: Path, scripts: str, **env: str) -> str:
    """Drive the real assembly and return what it SAID about falling back."""
    script = (
        f'. "{ROOT}/scripts/test_root_args.sh"\n'
        f'test_root_pytest_args "{scripts}" || echo "FELL-BACK: $TEST_ROOT_SCOPE"\n'
        'printf "%s\\n" "${TEST_ROOT_ARGS[@]}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env={**os.environ, **env},
    )
    assert "FELL-BACK" in result.stdout, result.stdout
    assert "--ignore=tests/e2e" in result.stdout, (
        "the whole-tree fallback is the right scope for both causes and must not change"
    )
    return result.stdout


def test_a_declaration_that_names_no_root_on_disk_says_that(tmp_path: Path):
    """A silent fallback to a different population is the defect being removed, so the fallback
    reports itself and the caller prints it.

    This is the state the single old message described correctly: the query answered, and none of
    the roots it named exists here.
    """
    said = fall_back_scope(tmp_path, f"{ROOT}/scripts", SUBPROC_CHECK_PATHS="nowhere")
    assert "no root it names exists on disk" in said
    assert "could not be READ" not in said


def test_a_declaration_that_could_not_be_read_is_not_reported_as_absent(tmp_path: Path):
    """The other state reaching the same fallback, which the one message asserted was the first.

    No `python3`, an unreadable or crashing `subprocess_check.py` in a vendored install - the
    declaration was never read, so naming a missing root states something nobody established and
    sends the reader to inspect a project layout that may be perfectly fine.

    Red when the two collapse back into one message: a query that never ran reports the filesystem
    as the cause, and the real cause is discarded with the query's stderr.
    """
    broken = tmp_path / "scripts"
    broken.mkdir()
    (broken / "subprocess_check.py").write_text(
        "raise SystemExit('the declaration reader is broken here')\n", encoding="utf-8"
    )

    said = fall_back_scope(tmp_path, str(broken))

    assert "could not be READ" in said
    assert "the declaration reader is broken here" in said, (
        "the query's own cause is reported, not discarded"
    )
    assert "no root it names exists on disk" not in said


def test_print_roots_answers_the_query_in_process_without_scanning(
    tmp_path: Path, capsys
):
    """`--print-roots` was registered on the parser and read only by the `__main__` intercept, so an
    in-process `main(["--print-roots"])` performed a full scan and returned a gate verdict - a
    silently wrong answer rather than an error.

    Red when the defect returns: the scan runs (its scope line reaches stderr) and the roots are
    never printed.
    """
    import subprocess_check

    (tmp_path / "suite").mkdir()
    (tmp_path / "suite" / "test_spawn.py").write_text(
        "import subprocess\n\n\ndef test_spawn():\n    subprocess.run(['true'])\n",
        encoding="utf-8",
    )
    os.environ["SUBPROC_CHECK_PATHS"] = "suite"
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        capsys.readouterr()
        code = subprocess_check.main(["--print-roots"])
        said = capsys.readouterr()
    finally:
        os.chdir(cwd)
        del os.environ["SUBPROC_CHECK_PATHS"]

    assert code == subprocess_check.CLEAN
    assert said.out.split() == ["suite"]
    # The undeclared spawner above would have been reported by a scan. Nothing scanned.
    assert "subprocess check scope" not in said.err
    assert "test_spawn" not in said.err
