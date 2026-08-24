"""The close stamp is emitted by something that EXECUTES, at the moment the phase lands.

Issue #46 moved `closed` from implementation-finish to landing, correctly. Nothing then emitted it
at landing: `commands/avenger-run.md` §5 asked the orchestrator to run `phase-close` after the
commit and stated outright that no hook could see that commit land. One can - a `PostToolUse` hook
on `Bash` runs after the commit did - and the difference is the whole point, because an instruction
an agent has to remember is exactly what stopped happening for two phases running.

These drive the real hook. A test that called `record_phase_close` directly would prove the emitter
works, which was never in doubt; what was missing was anything that CALLS it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook fired by a real `git commit`; running either any other "
    "way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from metrics_support import DOUBLE  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "store").mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def env(project: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(project),
        "CLAUDE_PROJECT_DIR": str(project),
        "AVENGER_METRICS_CMD": str(project / "fm-pipeline-metrics.sh"),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(project / "metrics.log"),
        "DOUBLE_LOG": str(project / "calls.log"),
        "DOUBLE_STORE": str(project / "store"),
        **extra,
    }


def phase(project: Path, number: int = 12, slug: str = "poll") -> Path:
    path = project / "docs" / "features" / "demo" / "phases" / f"{number}-{slug}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "verdict.json").write_text(json.dumps({"verdict": "pass", "attempt": 1}), "utf-8")
    (path / "handover.md").write_text("# handover\n", encoding="utf-8")
    return path


def open_record(project: Path, phase_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(project / "scripts" / "pipeline_metrics.py"), "phase-open",
         str(phase_dir)],
        cwd=project, check=True, capture_output=True, env=env(project),
    )


def land(project: Path, message: str = "feat(demo): phase 12-poll verified") -> None:
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=project, check=True)


def run_hook(project: Path, command: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_phase_close.sh")],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True, text=True, check=False, env=env(project, **extra),
    )


def record(project: Path, phase: str = "12") -> dict:
    return json.loads((project / "store" / f"phase-{phase}.json").read_text(encoding="utf-8"))


def test_the_commit_that_lands_a_phase_stamps_its_close(project: Path) -> None:
    """Phase 12 landed on 2026-08-21 at 11:15:08Z and its record still carried `closed: null`,
    `elapsed_minutes: null` and `tests_after: null` hours later, until they were entered by hand."""
    phase_dir = phase(project)
    open_record(project, phase_dir)
    land(project)

    result = run_hook(project, "git commit -q -m 'feat(demo): phase 12-poll verified'")

    assert result.returncode == 0, result.stderr
    assert record(project)["closed"] is not None
    assert record(project)["elapsed_minutes"] is not None


def test_a_phase_that_is_not_in_the_commit_is_not_stamped(project: Path) -> None:
    """The stamp follows the landing, so it is scoped to what the commit actually touched."""
    landed = phase(project, 12, "poll")
    open_record(project, landed)
    other = phase(project, 13, "sync")
    open_record(project, other)
    subprocess.run(["git", "add", "docs/features/demo/phases/12-poll"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "phase 12 lands"], cwd=project, check=True)

    run_hook(project, "git commit -m 'phase 12 lands'")

    assert record(project, "12")["closed"] is not None
    assert record(project, "13")["closed"] is None


def test_a_bash_call_that_is_not_a_commit_stamps_nothing(project: Path) -> None:
    phase_dir = phase(project)
    open_record(project, phase_dir)
    land(project)

    assert run_hook(project, "git status --short").returncode == 0
    assert record(project)["closed"] is None


def test_a_second_commit_touching_a_closed_phase_does_not_re_stamp_it(project: Path) -> None:
    """A closed record seals its measurements, so converging matters: this hook fires on every
    commit, and a phase's directory is routinely touched again after it closed."""
    phase_dir = phase(project)
    open_record(project, phase_dir)
    land(project)
    run_hook(project, "git commit -m 'phase 12 lands'")
    closed = record(project)["closed"]

    (phase_dir / "handover.md").write_text("# handover\n\na later correction\n", encoding="utf-8")
    land(project, "docs(demo): correct the phase 12 card")
    run_hook(project, "git commit -m 'docs(demo): correct the phase 12 card'")

    assert record(project)["closed"] == closed


def test_the_stamp_can_never_fail_the_commit_that_triggered_it(project: Path) -> None:
    """Measurement is not a gate. The commit has already happened by the time this runs, so a
    non-zero exit here would stop a turn over a record nobody can write."""
    phase_dir = phase(project)
    open_record(project, phase_dir)
    land(project)

    assert run_hook(project, "git commit -m x", DOUBLE_EXIT="3").returncode == 0
    assert run_hook(project, "git commit -m x", AVENGER_METRICS_OFF="1").returncode == 0


def test_a_repository_with_no_commit_yet_is_not_an_error(project: Path) -> None:
    phase(project)
    assert run_hook(project, "git commit -m 'nothing here'").returncode == 0
