"""A suite run that never finished must not read as a passing one - every way, proven red.

## The defect this pins

clickup-agents phase 12: a defect made the suite fail intermittently on one run and HANG to its
30-second watchdog on the next. The first run after the code landed was clean at 1298 tests with the
defect already present, and nothing mechanically told a hung suite from a passing one - it was
caught only because the implementer happened to run the suite twice.

An exit code alone cannot say it. A watchdog kill drains whatever the child had written and the
recorder stored `timed_out` and never read it; a suite that dies before it reports anything leaves an
output with no summary line in it, which is the one thing a completed run always emits.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** Each check below is driven RED - the watchdog
fired, the summary absent - and then repaired and driven GREEN, so the assertion is about the guard.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import suite_outcome as so  # noqa: E402

CLI = [sys.executable, str(ROOT / "scripts" / "suite_outcome.py")]

PYTEST_SUMMARY = "....\n1298 passed in 42.10s\n"
HUNG = "....\ntests/test_a.py::test_one\n"


# --- the decision --------------------------------------------------------------------------------


def test_a_completed_green_run_has_no_problems():
    assert so.problems(exit_code=0, timed_out=False, output=PYTEST_SUMMARY) == []


def test_the_watchdog_firing_is_a_problem_even_on_a_zero_exit_code():
    """The canonical shape: the group is killed, the drained output still carries a summary from
    whatever ran, and the recorded exit code is whatever the corpse reported."""
    found = so.problems(exit_code=0, timed_out=True, output=PYTEST_SUMMARY)
    assert found, "a run killed by its watchdog is not a completed suite run"
    assert "watchdog" in " ".join(found).lower()


def test_output_with_no_summary_is_a_problem():
    found = so.problems(exit_code=0, timed_out=False, output=HUNG)
    assert found, "output with no summary line is not evidence a suite finished"
    assert "summary" in " ".join(found).lower()


def test_empty_output_is_a_problem():
    assert so.problems(exit_code=0, timed_out=False, output="") != []


@pytest.mark.parametrize(
    "line",
    [
        "1298 passed in 42.10s",
        "3 failed, 5 passed in 1.20s",
        "no tests ran in 0.01s",
        "Ran 12 tests in 0.003s",
        "test result: ok. 7 passed; 0 failed",
        "Tests:       12 passed, 12 total",
        "ok  \tgithub.com/x/y\t0.012s",
    ],
)
def test_the_default_patterns_know_the_common_runners(line):
    assert so.summary_line(f"noise\n{line}\nmore noise\n") is not None


def test_a_project_can_declare_its_own_summary(monkeypatch):
    monkeypatch.setenv(so.PATTERN_ENV, r"SUITE COMPLETE")
    assert so.problems(exit_code=0, timed_out=False, output="SUITE COMPLETE\n") == []
    # and the declaration REPLACES the defaults rather than adding to them
    assert so.problems(exit_code=0, timed_out=False, output=PYTEST_SUMMARY) != []


def test_an_unusable_declared_pattern_fails_closed(monkeypatch):
    monkeypatch.setenv(so.PATTERN_ENV, "(unclosed")
    with pytest.raises(so.SuiteOutcomeError):
        so.problems(exit_code=0, timed_out=False, output=PYTEST_SUMMARY)


# --- the runner ----------------------------------------------------------------------------------


@pytest.mark.subprocess(
    "the runner's whole job is to bound a real child and read what it produced"
)
def test_run_passes_a_completed_green_run_straight_through(tmp_path: Path):
    script = tmp_path / "runner.py"
    script.write_text("print('1298 passed in 42.10s')\n", encoding="utf-8")
    proc = subprocess.run(
        CLI + ["run", "--", sys.executable, str(script)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert "1298 passed" in proc.stdout


@pytest.mark.subprocess(
    "a watchdog kill cannot be simulated without a real child to kill"
)
def test_run_refuses_a_child_that_runs_past_its_budget(tmp_path: Path):
    script = tmp_path / "hang.py"
    script.write_text(
        "import time\nprint('collected 1298 items', flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        CLI + ["run", "--budget", "1", "--", sys.executable, str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == so.INCOMPLETE, (proc.returncode, proc.stderr)
    assert "watchdog" in proc.stderr.lower()


@pytest.mark.subprocess("the point is what a real child wrote before it stopped")
def test_run_refuses_a_child_that_exits_clean_with_no_summary(tmp_path: Path):
    script = tmp_path / "quiet.py"
    script.write_text("print('collected 1298 items')\n", encoding="utf-8")
    proc = subprocess.run(
        CLI + ["run", "--", sys.executable, str(script)], capture_output=True, text=True
    )
    assert proc.returncode == so.INCOMPLETE, (proc.returncode, proc.stderr)
    assert "summary" in proc.stderr.lower()


@pytest.mark.subprocess("a red suite is a real child exiting non-zero")
def test_run_hands_back_the_childs_own_exit_code_when_it_completed(tmp_path: Path):
    script = tmp_path / "red.py"
    script.write_text(
        "import sys\nprint('3 failed, 5 passed in 1.20s')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        CLI + ["run", "--", sys.executable, str(script)], capture_output=True, text=True
    )
    assert proc.returncode == 1, proc.stderr


@pytest.mark.subprocess(
    "a command that cannot start is a different failure and is named as one"
)
def test_run_names_a_command_that_cannot_start():
    proc = subprocess.run(
        CLI + ["run", "--", "definitely-not-a-real-binary-xyz"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == so.ERROR
    assert "could not be started" in proc.stderr.lower()
