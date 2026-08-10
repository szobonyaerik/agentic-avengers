"""Every way the gate can fail, driven through the real runner, each naming itself.

This is the end-to-end half of the failure taxonomy: `tests/test_gate_errors.py` pins the classifier,
this pins what an operator actually sees when the gate is invoked and the provider misbehaves. The
`opencode` binary is replaced on PATH by a stub per failure mode — no model is called, and the
runner does not know the difference.

The timeout case is also the money case: the stub leaves a grandchild behind, and the test asserts
the reported duration is the measured one and that nothing survives the kill.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "gate_runner.py"

sys.path.insert(0, str(ROOT / "scripts"))

pytestmark = pytest.mark.subprocess(
    "the subject is the runner's process handling and its CLI-facing failure output"
)

STUBS = {
    # A provider that refuses for billing reasons. The shape that was read as a model rejection.
    "payment": '#!/bin/sh\necho \'{"error":{"code":402,"message":"Insufficient credits"}}\' >&2\nexit 1\n',
    # A provider that cannot be reached at all.
    "unreachable": "#!/bin/sh\necho 'curl: (7) Failed to connect to openrouter.ai port 443: "
                   "Connection refused' >&2\nexit 1\n",
    # A provider that answers, but with nothing a verdict can be read out of.
    "chatty": "#!/bin/sh\necho 'I had a think about it and it seems fine to me.'\n",
    # A provider that wedges, leaving a grandchild holding the pipe.
    "wedged": '#!/bin/sh\nsleep 120 &\necho "$!" > "$PIDFILE"\nsleep 120\n',
    # A provider that answers properly, so the success path is covered by the same harness.
    "good": '#!/bin/sh\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n',
}


@pytest.fixture
def gate(tmp_path: Path):
    """Run the real gate runner with a stubbed `opencode` on PATH."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "rubric.md").write_text("Return JSON with a verdict.")
    (tmp_path / "spec.md").write_text("---\nfeature: demo\n---\n\n- R1.1.1 a requirement\n")

    def run(stub: str, author_family: str = "anthropic", **env_over) -> subprocess.CompletedProcess:
        binary = tmp_path / "bin" / "opencode"
        binary.write_text(STUBS[stub])
        binary.chmod(0o755)
        env = {
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            # An empty project dir, so no repo .env leaks into the run.
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "PIDFILE": str(tmp_path / "grandchild.pid"),
            **env_over,
        }
        return subprocess.run(
            [sys.executable, str(RUNNER), "--rubric", str(tmp_path / "rubric.md"),
             "--target", str(tmp_path / "spec.md"), "--author-family", author_family],
            capture_output=True, text=True, env=env, check=False,
        )

    run.tmp = tmp_path  # type: ignore[attr-defined]
    return run


def test_a_billing_refusal_names_billing_and_shows_the_provider(gate) -> None:
    """Reading this as 'the gate model rejected the spec' cost about a day."""
    result = gate("payment")
    assert result.returncode == 2
    assert "cause=provider-payment-required" in result.stderr
    assert "Insufficient credits" in result.stderr, "the provider's own words must survive"


def test_an_unreachable_provider_is_not_confused_with_a_rejection(gate) -> None:
    result = gate("unreachable")
    assert result.returncode == 2
    assert "cause=provider-unreachable" in result.stderr
    assert "Connection refused" in result.stderr


def test_a_reply_with_no_verdict_is_its_own_cause(gate) -> None:
    result = gate("chatty")
    assert result.returncode == 2
    assert "cause=no-verdict" in result.stderr
    assert "I had a think about it" in result.stderr


def test_a_missing_provider_cli_is_its_own_cause(gate) -> None:
    result = gate("good", PATH="/nonexistent")
    assert result.returncode == 2
    assert "cause=provider-not-found" in result.stderr


def test_a_same_family_model_is_still_refused_by_name(gate) -> None:
    result = gate("good", author_family="deepseek")
    assert result.returncode == 2
    assert "cause=cross-family" in result.stderr


def test_a_timeout_names_itself_reports_measured_time_and_leaves_nothing_running(gate) -> None:
    """The three properties that were all wrong at once: the cause was unnamed, the duration was the
    configured constant rather than the run's, and the work carried on billing after the report."""
    start = time.monotonic()
    result = gate("wedged", GATE_CALL_TIMEOUT="2")
    wall = time.monotonic() - start

    assert result.returncode == 2
    assert "cause=timeout" in result.stderr
    assert "wall clock" in result.stderr

    reported = float(result.stderr.split("after ")[-1].split("s of wall clock")[0])
    assert reported == pytest.approx(wall, abs=1.5), "the reported duration is not the measured one"
    assert reported < 12, f"a 2s budget must not report {reported}s"

    grandchild = int((gate.tmp / "grandchild.pid").read_text().strip())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, 9)
        pytest.fail(f"grandchild {grandchild} outlived the timeout — still running, still spending")


def test_the_success_path_is_unchanged_by_all_of_this(gate) -> None:
    result = gate("good")
    assert result.returncode == 0, result.stderr
    assert "OK (GO)" in result.stdout
