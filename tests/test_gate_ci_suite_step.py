"""The CI floor's test step reports the OUTCOME of the suite, and pins what ran it (issue #98).

Folded from agents#31, two checks that could not see their own subject:

* an intermittent suite hang exited 0 with no summary line, and a bare `pytest -q; pc=$?` read that
  exit code as a pass. `scripts/suite_outcome.py` already refuses a run with no runner summary; the
  CI step simply did not go through it.
* three phase-1 tests failed under a bare `python3` and passed under the venv interpreter, so WHICH
  `pytest` PATH resolved was load-bearing and nothing pinned it. The step now runs `python3 -m pytest`
  under the same interpreter every other check in `gate_ci.sh` uses, and announces its path.

`gate_ci.sh` runs its whole floor on execution, so the step is sliced out and driven the way the file
drives it (the seam `tests/test_gate_ci_mutation_base.py` uses). A fake `pytest` MODULE on PYTHONPATH
stands in for the suite - that is what `python3 -m pytest` resolves - and a decoy `pytest` EXECUTABLE
on PATH records whether anything still reached for it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_CI = ROOT / "scripts" / "gate_ci.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is bash's own evaluation of gate_ci.sh's test step, which only a shell performs"
)


def _step_source() -> str:
    lines = GATE_CI.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("suite_step() {"))
    close_at = next(i for i, ln in enumerate(lines[start:], start) if ln == "}")
    return "\n".join(lines[start : close_at + 1])


def _fake_suite(tmp_path: Path, stdout: str, exit_code: int) -> Path:
    """A `pytest` module `python3 -m pytest` will find first, saying and exiting what it is told."""
    pkg = tmp_path / "pypath" / "pytest"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import os, sys\n"
        f"open({str(tmp_path / 'module-ran')!r}, 'w').write(' '.join(sys.argv[1:]))\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return pkg.parent


def _decoy(tmp_path: Path) -> Path:
    """A `pytest` executable on PATH that reports green - and records that it was ever called."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    decoy = bin_dir / "pytest"
    decoy.write_text(
        f"#!/bin/sh\ntouch {str(tmp_path / 'decoy-ran')!r}\necho '99 passed in 0.01s'\nexit 0\n",
        encoding="utf-8",
    )
    decoy.chmod(0o755)
    return bin_dir


def _run(
    tmp_path: Path, stdout: str, exit_code: int, full: int = 0
) -> subprocess.CompletedProcess:
    pypath = _fake_suite(tmp_path, stdout, exit_code)
    bin_dir = _decoy(tmp_path)
    body = (
        "set -uo pipefail\n"
        f'SCRIPT_DIR="{ROOT / "scripts"}"\n'
        'record_fail() { echo "RECORD_FAIL:$1"; }\n'
        f"{_step_source()}\n"
        f"suite_step {full}\n"
    )
    python_dir = str(Path(sys.executable).parent)
    return subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env={
            # The decoy first, then the interpreter running this test (as `python3`), then the rest.
            "PATH": f"{bin_dir}:{python_dir}:{os.environ['PATH']}",
            "PYTHONPATH": str(pypath),
            "HOME": str(tmp_path),
            "SUITE_BUDGET_S": "60",
        },
    )


def test_exit_zero_with_no_summary_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """THE folded instance: a hang that exits 0 and never says how many tests ran."""
    proc = _run(tmp_path, stdout="", exit_code=0)
    assert "RECORD_FAIL:tests:incomplete" in proc.stdout, proc.stdout + proc.stderr
    assert "did NOT COMPLETE" in proc.stderr


def test_a_green_completed_run_passes(tmp_path: Path) -> None:
    proc = _run(tmp_path, stdout="3 passed in 0.10s\n", exit_code=0)
    assert "RECORD_FAIL" not in proc.stdout, proc.stdout + proc.stderr


def test_a_red_completed_run_is_a_red_suite_not_an_incomplete_one(
    tmp_path: Path,
) -> None:
    proc = _run(tmp_path, stdout="1 failed, 2 passed in 0.10s\n", exit_code=1)
    assert "RECORD_FAIL:tests\n" in proc.stdout, proc.stdout + proc.stderr
    assert "incomplete" not in proc.stdout


def test_the_suite_runs_under_the_pinned_interpreter_never_whatever_pytest_path_finds(
    tmp_path: Path,
) -> None:
    """A decoy `pytest` on PATH reports 99 passed. If anything reached for it, a wrong interpreter
    could pass the step with a suite that never ran under the one the checks use."""
    proc = _run(tmp_path, stdout="3 passed in 0.10s\n", exit_code=0)
    assert (tmp_path / "module-ran").exists(), "python3 -m pytest did not run the suite"
    assert not (tmp_path / "decoy-ran").exists(), "the PATH `pytest` was reached for"
    assert "RECORD_FAIL" not in proc.stdout


def test_the_step_announces_which_interpreter_ran(tmp_path: Path) -> None:
    proc = _run(tmp_path, stdout="3 passed in 0.10s\n", exit_code=0)
    announced = next(ln for ln in proc.stdout.splitlines() if ln.startswith("• tests:"))
    assert "interpreter: " in announced
    named = announced.split("interpreter: ", 1)[1].strip()
    assert Path(named).exists(), f"announced interpreter {named!r} is not a real path"
    assert "python3 -m pytest" in announced


def test_e2e_is_excluded_on_the_fast_floor_and_included_under_full(
    tmp_path: Path,
) -> None:
    fast, full = tmp_path / "fast", tmp_path / "full"
    fast.mkdir()
    full.mkdir()
    _run(fast, stdout="3 passed in 0.10s\n", exit_code=0, full=0)
    assert "--ignore=tests/e2e" in (fast / "module-ran").read_text()
    _run(full, stdout="3 passed in 0.10s\n", exit_code=0, full=1)
    assert "--ignore=tests/e2e" not in (full / "module-ran").read_text()


# --- the suite outcome crosses the step boundary on a published name (issue #98 review round) ------


def _mutation_scope_guard_line() -> str:
    """The one line in the mutation gate that reads the suite's exit code.

    The mutation gate is top-level code, not a function, so there is no callable to slice. What is
    under test is the single expression that used to read a name the suite step had made local.
    """
    for line in GATE_CI.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("if [") and "no tests collected" not in stripped:
            if "SUITE_PC" in stripped or '"$pc"' in stripped:
                if "-eq 5" in stripped or '= "5"' in stripped:
                    return stripped
    raise AssertionError(
        "the mutation gate no longer reads the suite's exit code at all"
    )


def test_the_mutation_gate_survives_a_suite_step_that_published_nothing():
    """`suite_step()` declares its exit code `local`, so the mutation gate cannot see that name.

    Reading it directly aborted the WHOLE script under `set -uo pipefail` with `pc: unbound
    variable` - invisible in this repository, where cosmic-ray's `module-path` does not resolve and
    the branch is never taken, and fatal in any consumer whose does. The gate must reach a decision
    with the suite's outcome unpublished rather than kill the run.
    """
    body = (
        "set -uo pipefail\n"
        "suite_step() { local pc; pc=5; }\n"
        "suite_step\n"
        f"{_mutation_scope_guard_line()} echo SKIPPED; else echo RAN; fi\n"
        "echo REACHED_THE_END\n"
    )
    proc = subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, check=False
    )
    assert "unbound variable" not in proc.stderr, proc.stderr
    assert "REACHED_THE_END" in proc.stdout, (proc.stdout, proc.stderr)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
