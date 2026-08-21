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

from metrics_support import DOUBLE, read_calls, stub_sink  # noqa: F401 — fixture

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

    assert sink.add("07", "gate_calls", id="g", latency_ms=1200, failure_cause=None) is True

    add = [call for call in read_calls(log) if call[0] == "add"][-1]
    assert add[:3] == ["add", "07", "gate_calls"]
    assert "id=g" in add                      # a string is passed verbatim
    assert "latency_ms:=1200" in add          # a number is raw JSON
    assert "failure_cause:=null" in add       # and so is an explicit absence
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
    assert time.monotonic() - started < 5   # one timeout paid, not eleven

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
        [sys.executable, "-c", program], capture_output=True, text=True, check=False,
    )

    assert result.stdout == ""
    assert "a diagnostic" in result.stderr


def test_diagnostics_reach_the_log_file(tmp_path, monkeypatch):
    log = tmp_path / "metrics.log"
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(log))

    sink.note("something could not be written")

    assert "something could not be written" in log.read_text(encoding="utf-8")


def test_an_unwritable_log_is_not_a_second_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "missing-dir" / "metrics.log"))

    sink.note("nowhere to put this")   # must not raise


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

    init = [call for call in read_calls(log) if call[0] == "init" and call[1] == "09"][-1]
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
        [sys.executable, str(ROOT / "scripts" / "pipeline_metrics.py"), "defect",
         "--phase-ref", "07", "--id", "D1", "--summary", "a leak", "--found-by", "verifier"],
        capture_output=True, text=True, env=env, cwd=str(project), check=False,
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
