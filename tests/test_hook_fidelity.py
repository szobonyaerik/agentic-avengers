"""A rejection has to say what was rejected, and leave a record that it happened.

The fidelity hook stamped the judged body's hash only on GO and REVIEW. A NO-GO printed one line and
dropped the gate's own `report` field entirely, so the spec writer got a refusal with no reasoning
and the pipeline kept no record of which text had been refused. What follows an unexplained
rejection is blind rewriting: one 25k spec reached 51k that way.

Two things are pinned here, and they are separate defects that happened to travel together:

  * the report reaches the author on rejection (it was being read off stderr through a process
    substitution the hook could outrun, so it arrived empty when it arrived at all);
  * the hash is recorded WITH its verdict on every verdict, so an unchanged body that was rejected
    replays its rejection instead of sliding through the "unchanged, skip" path.

No model is called: `scripts/` is copied and `gate_runner.py` is replaced by a stub, so the real hook
runs and only the paid call is swapped out.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "scripts"))

from gate_runner import RUNNER_ABI  # noqa: E402

SPEC = """---
feature: demo
phase: 1-demo
status: {status}
fidelity_verdict: pending
---

## Requirements
- R1.1.1 — `binding: e2e` — {requirement}
"""

#: A stub gate_runner whose verdict and report are set by the environment, so one stub covers both
#: branches. It identifies itself because the hook refuses a runner that cannot (see
#: scripts/gate_runner_guard.sh) — a deliberate double says what it is; a scaffold does not.
STUB_RUNNER = """import hashlib
import json
import os
import sys

if "--identify" in sys.argv:
    print("{abi} " + hashlib.sha256(open(sys.argv[0], "rb").read()).hexdigest())
    sys.exit(0)
verdict = os.environ.get("STUB_VERDICT", "GO")
report = os.environ.get("STUB_REPORT", "")
if "--emit-json" in sys.argv:
    with open(sys.argv[sys.argv.index("--emit-json") + 1], "w") as fh:
        json.dump({{"verdict": verdict, "report": report}}, fh)
open(sys.argv[0] + ".calls", "a").write(verdict + "\\n")
print(verdict)
"""

REPORT = "R1.1.1 has no acceptance criterion an end user could observe."


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "scripts" / "gate_runner.py").write_text(STUB_RUNNER.format(abi=RUNNER_ABI))
    return tmp_path


def write_spec(project: Path, *, status: str = "pending",
               requirement: str = "the user sees a greeting") -> Path:
    spec = project / "docs" / "spec.md"
    spec.write_text(SPEC.format(status=status, requirement=requirement))
    return spec


def run_hook(project: Path, spec: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_fidelity.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % spec,
        capture_output=True, text=True, check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            **env,
        },
    )


def calls(project: Path) -> list[str]:
    """Every verdict the stub was asked for — how many times the paid gate actually ran."""
    log = project / "scripts" / "gate_runner.py.calls"
    return log.read_text().split() if log.exists() else []


# ── a rejection carries its reasoning ────────────────────────────────────────


def test_a_no_go_shows_the_gate_s_own_report(project: Path) -> None:
    """The defect: the report existed, was paid for, and never reached the author."""
    result = run_hook(project, write_spec(project), STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    assert result.returncode == 2
    assert REPORT in result.stderr


def test_a_no_go_still_routes_back(project: Path) -> None:
    result = run_hook(project, write_spec(project), STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    assert "route back to avenger-spec-writer" in result.stderr


def test_a_pass_is_unchanged(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(project, spec, STUB_VERDICT="GO")
    assert result.returncode == 0, result.stderr
    assert "fidelity_verdict: GO" in spec.read_text()


# ── the hash is recorded with its verdict, on every verdict ──────────────────


def test_a_rejection_records_the_body_it_rejected(project: Path) -> None:
    spec = write_spec(project)
    run_hook(project, spec, STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    text = spec.read_text()
    assert "fidelity_gated_hash:" in text, "a rejection with no record of what was rejected"
    assert "fidelity_gated_verdict: NO-GO" in text


def test_an_unchanged_rejected_body_replays_the_rejection(project: Path) -> None:
    """Recording the hash without the verdict would make the next frontmatter-only edit look
    unchanged-and-therefore-fine, turning a rejection into a pass on the way past."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    spec.write_text(spec.read_text().replace("status: pending", "status: done"))

    result = run_hook(project, spec, STUB_VERDICT="GO")

    assert result.returncode == 2, "an unchanged rejected body must not slide through"
    assert calls(project) == ["NO-GO"], "and it must not cost a second paid call to say so"
    assert REPORT in result.stderr, "the replay carries the reasoning too"


def test_break_glass_is_honoured_on_a_replayed_rejection(project: Path) -> None:
    """A replayed rejection blocks the turn exactly like a fresh one, so it has to answer to
    break-glass exactly like a fresh one. It did not: the override was a ONE-SHOT — the stored NO-GO
    came back on the very next write of the same spec with GATE_BYPASS set and ignored. A bypass
    silently dropped breaks "never silent" the same way a bypass silently taken does."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    spec.write_text(spec.read_text().replace("status: pending", "status: done"))

    result = run_hook(
        project, spec, STUB_VERDICT="NO-GO",
        GATE_BYPASS="captain-authorised: the requirement is deliberately unobservable",
    )

    assert result.returncode == 0, result.stderr
    log = (project / "gate-overrides.log").read_text()
    assert "gate:fidelity" in log
    assert "deliberately unobservable" in log, "the override must be logged, not just honoured"
    assert calls(project) == ["NO-GO"], "and it must not cost a second paid call"


def test_a_replayed_rejection_without_break_glass_still_blocks(project: Path) -> None:
    """The bar itself is untouched: only an explicit, logged override gets past."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    spec.write_text(spec.read_text().replace("status: pending", "status: done"))

    assert run_hook(project, spec, STUB_VERDICT="NO-GO").returncode == 2
    assert not (project / "gate-overrides.log").exists()


def test_an_unchanged_approved_body_still_skips_silently(project: Path) -> None:
    """The behaviour the cache exists for is untouched: a pass is not re-rolled."""
    spec = write_spec(project)
    assert run_hook(project, spec, STUB_VERDICT="GO").returncode == 0
    spec.write_text(spec.read_text().replace("status: pending", "status: done"))

    assert run_hook(project, spec, STUB_VERDICT="NO-GO").returncode == 0
    assert calls(project) == ["GO"]


def test_an_edited_body_is_gated_again(project: Path) -> None:
    spec = write_spec(project)
    run_hook(project, spec, STUB_VERDICT="NO-GO", STUB_REPORT=REPORT)
    write_spec(project, requirement="the user sees a greeting AND a name")

    assert run_hook(project, spec, STUB_VERDICT="GO").returncode == 0
    assert calls(project) == ["NO-GO", "GO"]


# ── a killed hook is not a verdict ───────────────────────────────────────────


SLOW_RUNNER = """import hashlib
import sys
import time

if "--identify" in sys.argv:
    print("{abi} " + hashlib.sha256(open(sys.argv[0], "rb").read()).hexdigest())
    sys.exit(0)
open(sys.argv[0] + ".started", "w").write("1")
time.sleep(30)
print("GO")
"""


def test_a_hook_killed_mid_call_says_so_instead_of_leaving_nothing(project: Path) -> None:
    """The failure that read as a model size ceiling for a day: a hook killed by the harness left
    no verdict, no report and no cause, and the run took the silence for an objection."""
    (project / "scripts" / "gate_runner.py").write_text(SLOW_RUNNER.format(abi=RUNNER_ABI))
    spec = write_spec(project)
    proc = subprocess.Popen(
        ["bash", str(project / "scripts" / "hook_fidelity.sh")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(project), "CLAUDE_PROJECT_DIR": str(project)},
    )
    proc.stdin.write('{"tool_input": {"file_path": "%s"}}' % spec)
    proc.stdin.flush()
    proc.stdin.close()
    proc.stdin = None  # already closed; communicate() must not try to flush it again

    started = project / "scripts" / "gate_runner.py.started"
    deadline = time.monotonic() + 10
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started.exists(), "the gate never started; the test is not exercising a mid-call kill"

    proc.terminate()
    _, stderr = proc.communicate(timeout=10)

    assert proc.returncode == 2
    assert "HOOK KILLED" in stderr
    assert "NOT a gate verdict" in stderr
    assert "fidelity_verdict: pending" in spec.read_text(), "a kill must not stamp a verdict"


def test_a_write_that_is_not_a_spec_is_ignored(project: Path) -> None:
    other = project / "docs" / "notes.md"
    other.write_text("not a spec")
    assert run_hook(project, other).returncode == 0
    assert calls(project) == []
