"""A gate timeout has to stop the work, not just stop waiting for it.

`subprocess.run(cmd, capture_output=True, timeout=300)` kills the process it started and nothing
below it. The gate's child spawns its own workers, and those workers kept running — kept calling the
provider, kept spending — after the gate had reported the call dead. Runs REPORTING a 300s timeout
were observed against 569s, 3818s and 4276s of real activity. The gate was not wrong to give up; it
was wrong that giving up stopped anything.

Every test here drives a stub that leaves a grandchild behind, because that is the shape that leaked.
`test_a_timeout_leaves_no_descendant_running` is the one that goes red the moment `run_bounded` is
swapped back for `subprocess.run`.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from proc_group import ChildResult, run_bounded  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the subject is process-group teardown; an in-process double cannot leak a real grandchild"
)

#: A child that spawns a long-lived grandchild holding the same stdout, then blocks itself. Exactly
#: what a wedged provider CLI looks like from here.
STUB = """#!/bin/sh
sleep 120 &
echo "$!" > "$PIDFILE"
sleep 120
"""

#: Same, but the direct child exits at once — the grandchild alone holds the pipe open, which is what
#: makes a naive drain block long past the timeout it already reported.
STUB_DETACHED = """#!/bin/sh
sleep 120 &
echo "$!" > "$PIDFILE"
exit 0
"""


def hanging_child(tmp_path: Path, body: str = STUB) -> tuple[list[str], Path]:
    script = tmp_path / "wedged.sh"
    script.write_text(body)
    script.chmod(0o755)
    return [str(script)], tmp_path / "grandchild.pid"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_gone(pid: int, seconds: float = 3.0) -> bool:
    """Descendants die asynchronously; give the signal a moment to land before judging."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def reap(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def reap_pidfile(pidfile: Path) -> None:
    """Kill the grandchild this stub recorded, if it got as far as recording one.

    Teardown, not the subject. Under a loaded machine the 2s budget can expire before the child's
    shell has written its pid, and a `finally` that raised FileNotFoundError there replaced the
    real result with a cleanup error — a flake that reads as a proc_group defect and is not one.
    """
    try:
        reap(int(pidfile.read_text().strip()))
    except (OSError, ValueError):
        pass


# ── the leak ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [STUB, STUB_DETACHED])
def test_a_timeout_leaves_no_descendant_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """The money defect. Killing the direct child alone left the grandchild billing for an hour."""
    cmd, pidfile = hanging_child(tmp_path, body)
    monkeypatch.setenv("PIDFILE", str(pidfile))

    result = run_bounded(cmd, timeout=2)

    assert result.timed_out
    grandchild = int(pidfile.read_text().strip())
    try:
        assert wait_gone(grandchild), (
            f"grandchild {grandchild} survived the timeout — the gate reported the call dead while "
            "it was still running and still spending"
        )
    finally:
        reap(grandchild)


def test_the_reported_duration_is_measured_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old failure quoted the constant ('timed out after 300 seconds'), which is a fact about
    the config. A measured elapsed cannot disagree with what happened."""
    cmd, pidfile = hanging_child(tmp_path)
    monkeypatch.setenv("PIDFILE", str(pidfile))

    start = time.monotonic()
    result = run_bounded(cmd, timeout=2)
    wall = time.monotonic() - start

    try:
        assert result.timed_out
        assert result.elapsed == pytest.approx(wall, abs=0.5), "reported duration is not wall clock"
        assert 2 <= result.elapsed < 12, f"{result.elapsed}s is not a 2s budget plus teardown"
    finally:
        reap_pidfile(pidfile)


def test_a_timeout_returns_a_result_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller turns this into a `timeout`-caused GateError; an exception here would lose the
    measured elapsed on the way out."""
    cmd, pidfile = hanging_child(tmp_path)
    monkeypatch.setenv("PIDFILE", str(pidfile))
    result = run_bounded(cmd, timeout=2)
    try:
        assert isinstance(result, ChildResult)
        assert result.timed_out is True
    finally:
        reap_pidfile(pidfile)


# ── the other way a call ends early ──────────────────────────────────────────


DRIVER = """import sys
sys.path.insert(0, {scripts!r})
from proc_group import run_bounded
run_bounded([{stub!r}], timeout=120)
"""


def test_being_killed_ourselves_also_takes_the_group_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout is not the only way the call ends early — the hook wrapping it gets killed too, and
    that used to leak exactly as a timeout did. Same leak, different trigger."""
    cmd, pidfile = hanging_child(tmp_path)
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER.format(scripts=str(ROOT / "scripts"), stub=cmd[0]))

    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        env={**os.environ, "PIDFILE": str(pidfile)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pidfile.exists(), "the child never started; the test is not exercising a mid-call kill"

    proc.terminate()
    proc.wait(timeout=10)

    grandchild = int(pidfile.read_text().strip())
    try:
        assert wait_gone(grandchild), (
            f"grandchild {grandchild} outlived the killed gate — still running, still spending"
        )
    finally:
        reap(grandchild)


# ── the ordinary path still works ────────────────────────────────────────────


def test_output_and_exit_code_survive_the_new_plumbing() -> None:
    result = run_bounded(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout=10)
    assert not result.timed_out
    assert result.returncode == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.elapsed >= 0


def test_a_missing_binary_still_raises_so_the_caller_can_name_it_differently() -> None:
    with pytest.raises(FileNotFoundError):
        run_bounded(["definitely-not-a-real-binary-9f2a"], timeout=5)
