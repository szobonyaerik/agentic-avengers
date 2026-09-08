"""Tests for the fail-open bridge to firstmate's metrics record.

This module has exactly one job that outranks recording anything: it must never be able to fail a
phase. A metrics bug that blocked delivery would be a self-inflicted outage in the thing meant to
make delivery cheaper, so every failure mode a real run can produce — no writer, an unwritable
record, a refusal, a hang, a writer that explodes — is asserted to come back as a quiet False.

The second invariant asserted here is silence on stdout. Several callers are hooks whose stdout is
a JSON protocol; one stray diagnostic line there corrupts a hook's contract.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from metrics_support import DOUBLE, read_calls, real_sink, stub_sink  # noqa: F401 — fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import metrics_sink as sink  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the sink's whole contract is what it does to a real writer process, including one that hangs"
)


def test_no_writer_disables_emission_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AVENGER_METRICS_CMD", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "log"))
    sink._announced = False

    assert sink.enabled() is False
    assert sink.add("07", "gate_calls", id="x") is False
    assert "records no pipeline metrics" in capsys.readouterr().err


def test_off_switch_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "log"))
    sink._announced = False

    assert sink.enabled() is False
    assert capsys.readouterr().err == ""


def test_writes_go_through_and_are_encoded_by_type(stub_sink):  # noqa: F811
    _, store, log = stub_sink

    assert (
        sink.add("07", "gate_calls", id="g", latency_ms=1200, failure_cause=None)
        is True
    )

    add = [call for call in read_calls(log) if call[0] == "add"][-1]
    assert add[:3] == ["add", "07", "gate_calls"]
    assert "id=g" in add  # a string is passed verbatim
    assert "latency_ms:=1200" in add  # a number is raw JSON
    assert "failure_cause:=null" in add  # and so is an explicit absence
    assert (store / "phase-07.json").exists()


def test_unwritable_record_does_not_raise(stub_sink, monkeypatch):  # noqa: F811
    """The definition-of-done property: make the record unwritable, the phase carries on."""
    _, store, _ = stub_sink
    assert sink.ensure("07") is True
    record = store / "phase-07.json"
    record.chmod(0o400)
    try:
        assert sink.set_fields("07", spec_rounds=3) is False
        assert sink.add("07", "defects", id="D1") is False
    finally:
        record.chmod(0o600)


def test_writer_refusal_is_reported_not_raised(stub_sink, monkeypatch, capsys):  # noqa: F811
    assert sink.ensure("07") is True
    monkeypatch.setenv("DOUBLE_REFUSE", "set")

    assert sink.set_fields("07", spec_rounds=1) is False
    assert "refused" in capsys.readouterr().err


def test_a_hanging_writer_is_bounded_and_then_abandoned(tmp_path, monkeypatch, capsys):
    """An unwritable record makes firstmate's CLI BLOCK, not fail. One strike is enough evidence.

    Without this a stage recording a dozen facts pays the full timeout a dozen times, inside a hook
    budget that exists for the gate. The property under test is the wall clock, not just the verdict.
    """
    hang = tmp_path / "fm-pipeline-metrics.sh"
    hang.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    hang.chmod(0o755)
    monkeypatch.setenv("AVENGER_METRICS_CMD", str(hang))
    monkeypatch.setenv("AVENGER_METRICS_TIMEOUT", "1")
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "log"))
    monkeypatch.setattr(sink, "_writer_unusable", False)

    started = time.monotonic()
    assert sink.run("show", "07") is None
    assert [sink.add("07", "defects", id=f"D{n}") for n in range(10)] == [False] * 10
    assert time.monotonic() - started < 5  # one timeout paid, not eleven

    errors = capsys.readouterr().err
    assert "no further metrics are recorded" in errors
    # A configured writer that hung is not a missing one; saying so sends the reader to their PATH.
    assert "is not on PATH" not in errors


def test_a_writer_that_is_not_executable_is_not_a_writer(tmp_path, monkeypatch):
    inert = tmp_path / "fm-pipeline-metrics.sh"
    inert.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    inert.chmod(0o600)
    monkeypatch.setenv("AVENGER_METRICS_CMD", str(inert))

    assert sink.cli() is None


def test_a_named_but_unexecutable_writer_is_not_reported_as_an_unset_one(
    tmp_path, monkeypatch, capsys
):
    """`defect` sends its reader HERE for the cause, so a cause that is false costs a retry loop.

    An operator who copied firstmate's script without the exec bit has already applied the remedy
    "set AVENGER_METRICS_CMD", so being handed it again leaves them nothing to do but re-run.
    """
    inert = tmp_path / "fm-pipeline-metrics.sh"
    inert.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    inert.chmod(0o600)
    monkeypatch.setenv("AVENGER_METRICS_CMD", str(inert))
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "log"))
    monkeypatch.setattr(sink, "_announced", False)
    monkeypatch.setattr(sink, "_writer_unusable", False)

    assert sink.enabled() is False

    errors = capsys.readouterr().err
    assert str(inert) in errors
    assert "not an executable file" in errors
    assert "AVENGER_METRICS_CMD is unset" not in errors
    assert "is not on PATH" not in errors


def test_nothing_is_ever_written_to_stdout(stub_sink, monkeypatch):  # noqa: F811
    """A hook's stdout is a JSON protocol; one diagnostic line there corrupts it."""
    monkeypatch.setenv("DOUBLE_EXIT", "3")
    program = (
        "import sys; sys.path.insert(0, %r); import metrics_sink as s;"
        "s.note('a diagnostic'); s.enabled(); s.set_fields('07', spec_rounds=1)"
        % str(ROOT / "scripts")
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout == ""
    assert "a diagnostic" in result.stderr


def test_diagnostics_reach_the_log_file(tmp_path, monkeypatch):
    log = tmp_path / "metrics.log"
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(log))

    sink.note("something could not be written")

    assert "something could not be written" in log.read_text(encoding="utf-8")


def test_an_unwritable_log_is_not_a_second_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AVENGER_METRICS_LOG", str(tmp_path / "missing-dir" / "metrics.log")
    )

    sink.note("nowhere to put this")  # must not raise


def test_a_record_that_already_exists_is_not_reopened(stub_sink):  # noqa: F811
    _, _, log = stub_sink
    assert sink.ensure("07") is True
    before = len([call for call in read_calls(log) if call[0] == "init"])

    assert sink.ensure("07") is True

    assert len([call for call in read_calls(log) if call[0] == "init"]) == before


def test_a_phase_opens_from_this_projects_predecessor(stub_sink):  # noqa: F811
    """Opening without `--from` would make phase 09 read as its project's first record."""
    _, _, log = stub_sink
    assert sink.ensure("07") is True

    assert sink.ensure("09") is True

    init = [call for call in read_calls(log) if call[0] == "init" and call[1] == "09"][
        -1
    ]
    assert "--from" in init and init[init.index("--from") + 1] == "07"


def _project_with_env_writer(tmp_path):
    """A project whose `.env` names a double writer, and nothing else pointing at one.

    This is the shape issue #66 is about: the operator configured the writer once, in the project's
    `.env`, the way every other pipeline setting is configured — and the stage that emits the defect
    runs in a shell that never inherited the export.
    """
    project = tmp_path / "project"
    project.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    log = tmp_path / "calls.log"
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    (project / ".env").write_text(f"AVENGER_METRICS_CMD={double}\n", encoding="utf-8")
    return project, store, log


def test_the_writer_is_resolved_from_the_project_env_file(tmp_path, monkeypatch):
    """A stage's `defect` call must find the writer the project configured, not only an export.

    `AVENGER_METRICS_CMD` reaches every hook through `load_env.sh`, but `pipeline_metrics.py defect`
    is the one command a STAGE runs directly, from a subagent shell that carries no export — so it
    resolved nothing and the defect was lost silently. Resolution belongs to the sink, which is the
    one point every caller passes through.
    """
    project, store, log = _project_with_env_writer(tmp_path)

    env = dict(os.environ)
    env.pop("AVENGER_METRICS_CMD", None)
    env.pop("AVENGER_METRICS_OFF", None)
    env.update(
        CLAUDE_PROJECT_DIR=str(project),
        # A real PATH so the double's own `env python3` shebang resolves, but one with no
        # `fm-pipeline-metrics.sh` on it: the `.env` is the only thing naming a writer.
        PATH=str(Path(sys.executable).parent),
        DOUBLE_LOG=str(log),
        DOUBLE_STORE=str(store),
        AVENGER_METRICS_PROJECT="unit-test",
        AVENGER_METRICS_LOG=str(tmp_path / "diagnostics.log"),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "pipeline_metrics.py"),
            "defect",
            "--phase-ref",
            "07",
            "--id",
            "D1",
            "--summary",
            "a leak",
            "--found-by",
            "verifier",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "was NOT written" not in result.stderr
    recorded = read_calls(log)
    assert [call for call in recorded if call[:3] == ["add", "07", "defects"]]


def test_an_exported_writer_still_wins_over_the_env_file(tmp_path, monkeypatch):
    """The real environment always wins — a committed default must never shadow what CI was given."""
    project, _, _ = _project_with_env_writer(tmp_path)
    exported = tmp_path / "exported-writer.sh"
    exported.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    exported.chmod(0o755)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("AVENGER_METRICS_CMD", str(exported))

    assert sink.cli() == str(exported)


# --- a write the seal turned away is queued, never destroyed (issue #98) --------------------------
#
# firstmate seals a closed record and refuses, exit 3, any write that would move a measurement. The
# refusal is right. This side used to answer it by logging a line and dropping the entry, so a phase
# closed too early lost five spec-gate rounds permanently. Now the exact argv waits in a spool and
# lands the moment the record is reopened.


def _spool(tmp_path) -> Path:
    return tmp_path / "project" / sink.SPOOL_NAME


def _close(store: Path, phase: str) -> None:
    import json

    path = store / f"phase-{int(phase):02d}.json"
    record = json.loads(path.read_text())
    record["closed"] = "2026-09-06T11:00:00Z"
    path.write_text(json.dumps(record))


def test_a_write_the_seal_refused_is_queued_verbatim_and_reported_as_not_landed(
    stub_sink,  # noqa: F811
    monkeypatch,
    capsys,
):
    _, store, log = stub_sink
    assert sink.ensure("07") is True
    _close(store, "07")
    monkeypatch.setenv("DOUBLE_SEAL", "1")

    assert (
        sink.add("07", "gate_calls", id="g1", latency_ms=1200) is False
    )  # it did NOT land

    (entry,) = sink.queued("07")
    assert entry["argv"] == ["add", "07", "gate_calls", "id=g1", "latency_ms:=1200"]
    err = capsys.readouterr().err
    assert "QUEUED" in err
    assert "--reopen closed:=null" in err
    import json

    assert json.loads((store / "phase-07.json").read_text())["gate_calls"] == []


def test_the_queue_drains_into_the_reopened_record_in_order(stub_sink, monkeypatch):  # noqa: F811
    import json

    _, store, log = stub_sink
    assert sink.ensure("07") is True
    _close(store, "07")
    monkeypatch.setenv("DOUBLE_SEAL", "1")
    assert sink.add("07", "gate_calls", id="g1") is False
    assert sink.add("07", "gate_calls", id="g2") is False
    assert [e["argv"][3] for e in sink.queued("07")] == ["id=g1", "id=g2"]

    assert sink.run("set", "07", "--reopen", "closed:=null")[0] == 0

    # The next ordinary write replays the queue first, then lands itself.
    assert sink.add("07", "gate_calls", id="g3") is True
    ids = [
        e["id"] for e in json.loads((store / "phase-07.json").read_text())["gate_calls"]
    ]
    assert ids == ["g1", "g2", "g3"]
    assert sink.queued("07") == []
    assert not _spool(log.parent).exists()


def test_a_still_sealed_record_keeps_the_queue_and_says_how_many_wait(
    stub_sink,  # noqa: F811
    monkeypatch,
):
    _, store, _ = stub_sink
    assert sink.ensure("07") is True
    _close(store, "07")
    monkeypatch.setenv("DOUBLE_SEAL", "1")
    assert sink.add("07", "gate_calls", id="g1") is False

    landed, remaining = sink.drain("07")
    assert (landed, remaining) == (0, 1)
    assert len(sink.queued("07")) == 1


def test_a_refusal_that_is_not_the_seal_is_not_queued(stub_sink, monkeypatch, capsys):  # noqa: F811
    """A key the writer does not know fails identically on replay; a queue that can never drain is
    the loss with extra steps. Only the seal - a refusal reopening cures - is queued."""
    assert sink.ensure("07") is True
    monkeypatch.setenv("DOUBLE_REFUSE", "add")
    assert sink.add("07", "gate_calls", id="g1") is False
    assert sink.queued("07") == []
    assert "QUEUED" not in capsys.readouterr().err


def test_the_spool_is_per_phase(stub_sink, monkeypatch):  # noqa: F811
    _, store, _ = stub_sink
    assert sink.ensure("07") is True and sink.ensure("08") is True
    _close(store, "07")
    monkeypatch.setenv("DOUBLE_SEAL", "1")
    assert sink.add("07", "gate_calls", id="g1") is False
    assert (
        sink.add("08", "gate_calls", id="h1") is True
    )  # phase 08 is open and unaffected
    assert [e["phase"] for e in sink.queued()] == ["07"]


def test_the_operator_command_lists_and_drains(stub_sink, monkeypatch, capsys):  # noqa: F811
    import json

    import pipeline_metrics as metrics

    _, store, _ = stub_sink
    assert sink.ensure("07") is True
    _close(store, "07")
    monkeypatch.setenv("DOUBLE_SEAL", "1")
    assert sink.add("07", "gate_calls", id="g1") is False

    assert metrics.main(["spool"]) == 0
    assert "id=g1" in capsys.readouterr().err
    assert (
        metrics.main(["spool", "--drain"]) == 1
    )  # still sealed: still refused, and the exit says so
    assert sink.run("set", "07", "--reopen", "closed:=null")[0] == 0
    assert metrics.main(["spool", "--drain"]) == 0
    assert [
        e["id"] for e in json.loads((store / "phase-07.json").read_text())["gate_calls"]
    ] == ["g1"]
    assert sink.queued() == []


def test_the_real_writers_seal_is_the_refusal_this_queues(real_sink):  # noqa: F811
    """The claim the double cannot make: firstmate's actual seal refusal is recognised as one, and
    a record reopened with firstmate's own --reopen receives the queued write."""
    import json

    project, home = real_sink
    assert sink.ensure("07") is True
    assert (
        sink.set_fields(
            "07", opened="2026-09-06T10:00:00Z", closed="2026-09-06T11:00:00Z"
        )
        is True
    )
    row = dict(
        id="s1-a1-spec-gate",
        stage="spec-gate",
        spec="1.1",
        attempt=1,
        model="m/x",
        model_family="f",
        latency_ms=1200,
        verdict="GO",
        failure_cause=None,
        note="n",
    )
    assert sink.add("07", "gate_calls", **row) is False
    assert len(sink.queued("07")) == 1
    assert sink.show("07")["gate_calls"] == []

    assert sink.run("set", "07", "--reopen", "closed:=null")[0] == 0
    assert sink.drain("07") == (1, 0)
    assert [e["id"] for e in sink.show("07")["gate_calls"]] == ["s1-a1-spec-gate"]
    del json
