"""The handover gate, and the one thing in it that used to report a limit while enforcing none.

`scripts/verifier_attempts.py` decides the 3-attempt cap, and its own tests cover that decision. What
is pinned HERE is that the decision is acted on: the hook called it as `... || true`, printed the cap
notice, and then routed the phase back anyway, so a fourth attempt proceeded with nothing stopping
it — while `CLAUDE.md` and `skills/pipeline-conventions` both say it "stops the loop", and H4 is
measured on `verification_attempts`.

A cap whose test passes against the un-capped code is not a cap, so these drive the real hook.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PASSING = {
    "verdict": "pass",
    "attempt": 3,
    "findings": [],
    "test_quality": {"reviewed": True, "scope": {"test_files": ["tests/demo/1-a/test_x.py"]}},
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "docs" / "features" / "demo" / "phases" / "1-demo").mkdir(parents=True)
    return tmp_path


def phase_dir(project: Path) -> Path:
    return project / "docs" / "features" / "demo" / "phases" / "1-demo"


def attempts(project: Path, series: list[tuple[int, int, str]]) -> None:
    """Write the phase's verdict history: the archives, then the live verdict as the last entry."""
    for number, findings, result in series[:-1]:
        (phase_dir(project) / f"verdict-attempt-{number}.json").write_text(json.dumps({
            "attempt": number, "verdict": result,
            "findings": [{"id": f"a{number}-{i}"} for i in range(findings)],
        }))
    number, findings, result = series[-1]
    verdict = {
        "attempt": number, "verdict": result,
        "findings": [{"id": f"f{i}", "status": "open"} for i in range(findings)],
        "routed": [{"to": "implementer", "reason": "code issue", "finding_id": "f0"}],
    }
    if result == "pass":
        verdict["findings"] = []
        verdict["test_quality"] = PASSING["test_quality"]
    (phase_dir(project) / "verdict.json").write_text(json.dumps(verdict))


def run_hook(project: Path, **env: str) -> subprocess.CompletedProcess:
    handover = phase_dir(project) / "handover.md"
    handover.write_text("# handover\n")
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_verifier.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % handover,
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(project),
             "CLAUDE_PROJECT_DIR": str(project), **env},
    )


# ── the cap binds, rather than being announced ───────────────────────────────


def test_a_fourth_attempt_is_refused_at_handover(project: Path) -> None:
    """The failure this replaced: the notice printed, then `fail` routed back regardless, and
    attempt 5 followed. Enforcement was an instruction to the model — the exact class this branch
    replaces everywhere else."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail"), (4, 3, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "a further attempt is" in result.stderr and "refused" in result.stderr
    assert "route back per its findings" not in result.stderr, (
        "at the cap the phase does not go round again; it is carried, waived or escalated"
    )


def test_the_cap_names_the_three_honest_ways_out(project: Path) -> None:
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])

    err = run_hook(project).stderr

    assert "KNOWN-OPEN" in err
    assert "waive" in err
    assert "escalate" in err


def test_under_the_cap_the_phase_still_routes_back_normally(project: Path) -> None:
    """The cap must not swallow the ordinary route-back it sits in front of."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "route back per its findings" in result.stderr
    assert "refused" not in result.stderr


def test_a_phase_that_passes_cleanly_on_its_last_allowed_attempt_still_passes(project: Path) -> None:
    """The cap is on the LOOP, not on the phase. Turning a successful third attempt into a stop
    would be weakening the gate in the other direction."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 0, "pass")])

    result = run_hook(project)

    assert result.returncode == 0, result.stderr


def test_the_cap_is_escapable_and_audited_rather_than_a_hard_wedge(project: Path) -> None:
    """Consistent with every other blocking check here: break-glass through the same `fail()` path,
    logged and visible, never silent."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])

    result = run_hook(project, GATE_BYPASS="captain accepts the remaining findings as known-open")

    assert result.returncode == 0, result.stderr
    assert (project / "gate-overrides.log").is_file()
