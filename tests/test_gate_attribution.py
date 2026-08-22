"""A verdict names the model and the transport that produced it - on the verdict, not in a story.

## The defect this pins

Phase 13's first spec gate ran on `anthropic/claude-3-haiku` over the OpenRouter transport, and that
fact survives **only because the worker typed it into a status line**. Firstmate then had to rule on
whether that gate stood - a cross-family question - with prose as the sole evidence of what had
judged it. Nothing on the artifact said.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** Each case below removes the attribution and
asserts the recorded verdict says so explicitly, rather than reading as an ordinary one: an
unrecorded attribution is a distinct, visible state, never an absent key a later reader has to
interpret.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "gate_runner.py"
sys.path.insert(0, str(ROOT / "scripts"))

import spec_gate_cache as cache  # noqa: E402
from gate_runner import ATTRIBUTION_MARKER  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the marker is something the real runner writes to a real stderr; a mocked runner would prove "
    "the mock writes it"
)

GO = '#!/bin/sh\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n'
NO_GO = (
    '#!/bin/sh\necho \'{"verdict":"NO-GO","report":"thin","route_back":"writer"}\'\n'
)
UNREACHABLE = (
    "#!/bin/sh\necho 'curl: (7) Failed to connect to openrouter.ai port 443: "
    "Connection refused' >&2\nexit 1\n"
)


@pytest.fixture
def gate(tmp_path: Path):
    """The real runner, with `opencode` stubbed on PATH so no model is called."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "rubric.md").write_text("Return JSON with a verdict.")
    (tmp_path / "spec.md").write_text(
        "---\nfeature: demo\n---\n\n- R1.1.1 a requirement\n"
    )

    def run(
        stub: str, model: str = "deepseek/deepseek-chat"
    ) -> subprocess.CompletedProcess:
        binary = tmp_path / "bin" / "opencode"
        binary.write_text(stub)
        binary.chmod(0o755)
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--rubric",
                str(tmp_path / "rubric.md"),
                "--target",
                str(tmp_path / "spec.md"),
                "--author-family",
                "anthropic",
                "--model",
                model,
            ],
            capture_output=True,
            text=True,
            check=False,
            env={
                "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
                "HOME": str(tmp_path),
                "CLAUDE_PROJECT_DIR": str(tmp_path),
                "GATE_MIN_LATENCY_MS": "0",
            },
        )

    return run


def marker(stderr: str) -> str | None:
    for line in stderr.splitlines():
        if line.startswith(ATTRIBUTION_MARKER + ":"):
            return line
    return None


# --- the runner says what judged --------------------------------------------------------------


def test_a_reached_verdict_names_its_model_and_transport(gate) -> None:
    line = marker(gate(GO).stderr)
    assert line, "a verdict with no attribution is the state this exists to remove"
    assert "model=deepseek/deepseek-chat" in line
    assert "provider=opencode" in line
    assert "family=deepseek" in line


def test_a_rejection_is_attributed_exactly_like_a_pass(gate) -> None:
    """A NO-GO is a verdict, and 'which model said no' is the question a route-back turns on."""
    result = gate(NO_GO)
    assert result.returncode == 2
    assert "=== GATE: NO-GO ===" in result.stderr
    assert marker(result.stderr), result.stderr


def test_a_call_that_reached_no_provider_attributes_nothing(gate) -> None:
    """There is no verdict to attribute. The failure already names the provider and the cause, and
    a marker here would claim a model formed a judgement that was never asked for one."""
    result = gate(UNREACHABLE)
    assert result.returncode == 2
    assert marker(result.stderr) is None, result.stderr


# --- the verdict record carries it ----------------------------------------------------------------


def spec(text: str = "") -> str:
    return f"---\nfeature: demo\nspec_gate: pending\n{text}---\n\n# Spec\n\nbody\n"


def test_the_stamp_records_what_judged_beside_the_verdict() -> None:
    stamped = cache.stamp(spec(), "gate", "APPROVED", "observe=x/y@openrouter")
    frontmatter, _body = cache.split_spec(stamped)
    assert "gate_gated_by: observe=x/y@openrouter" in frontmatter
    assert cache.stored_attribution(frontmatter, "gate") == "observe=x/y@openrouter"


def test_a_block_is_attributed_too() -> None:
    frontmatter, _ = cache.split_spec(
        cache.stamp(spec(), "gate", "BLOCKED", "observe=x/y@or")
    )
    assert cache.stored_attribution(frontmatter, "gate") == "observe=x/y@or"


def test_an_unrecorded_attribution_is_a_named_state_not_an_absent_key() -> None:
    """RED against the shape this replaces: a verdict whose provenance is simply missing, which a
    later reader has to go and ask a human about."""
    frontmatter, _ = cache.split_spec(cache.stamp(spec(), "gate", "APPROVED"))
    assert cache.stored_attribution(frontmatter, "gate") == cache.UNRECORDED
    assert f"gate_gated_by: {cache.UNRECORDED}" in frontmatter


def test_a_spec_stamped_before_this_rule_reads_as_unrecorded_rather_than_as_a_claim() -> (
    None
):
    old = spec("gate_gated_hash: " + "a" * 64 + "\ngate_gated_verdict: APPROVED\n")
    frontmatter, _ = cache.split_spec(old)
    assert cache.stored_attribution(frontmatter, "gate") == cache.UNRECORDED


def test_restamping_replaces_the_attribution_rather_than_appending_a_second() -> None:
    once = cache.stamp(spec(), "gate", "APPROVED", "observe=a/b@opencode")
    twice = cache.stamp(once, "gate", "BLOCKED", "observe=c/d@openrouter")
    frontmatter, _ = cache.split_spec(twice)
    assert frontmatter.count("gate_gated_by:") == 1
    assert cache.stored_attribution(frontmatter, "gate") == "observe=c/d@openrouter"
