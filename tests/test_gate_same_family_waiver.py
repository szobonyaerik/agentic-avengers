"""A same-family gate is refused unless it is WAIVED EXPLICITLY - and a waived one says so.

The state this pins was a wedge. `hook_spec_gate.sh` hands the runner an author family that cannot
be cleared from outside the plugin, so a Claude gate model trips `assert_cross_family` and fails
closed. The only route through was to declare a FALSE `--author-family`: the author genuinely is
Anthropic, so there was no truthful value to write, and a phase worker was right to refuse.

Both directions are proven here, per issue 69 - a guard proven only by passing is not proven:

  * with no waiver, a same-family call is still refused by name (`cause=cross-family`);
  * with an explicit waiver it proceeds, AND every record of the call carries the waiver.

The second half is the load-bearing one. A run that silently dropped the assertion would be worse
than the wedge it replaced: it would turn a loud refusal into a quiet false assurance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "gate_runner.py"
sys.path.insert(0, str(ROOT / "scripts"))

import gate_runner  # noqa: E402
from gate_errors import GateError  # noqa: E402
from gate_runner import SAME_FAMILY_MARKER, resolve_gate_family, waiver_reason  # noqa: E402

#: A Claude gate model judging Claude-authored work - the configuration the captain waived.
SAME_FAMILY = "anthropic/claude-opus-4"
CROSS_FAMILY = "google/gemini-3.1-pro-preview"
REASON = "captain waived cross-family independence for phase 12: no second vendor is reachable"


# ── the resolver: what a waiver does, and everything it does not ─────────────


def test_a_same_family_call_is_refused_when_no_waiver_was_given() -> None:
    """The default path, unchanged. Nobody who has not asked for a waiver gets a weaker gate."""
    with pytest.raises(GateError) as raised:
        resolve_gate_family(SAME_FAMILY, "anthropic")
    assert raised.value.cause == "cross-family"


def test_an_explicit_waiver_lets_the_same_family_call_through_and_names_itself() -> None:
    family, waived = resolve_gate_family(SAME_FAMILY, "anthropic", waiver=REASON)
    assert family == "anthropic"
    assert waived == REASON, "the reason is carried, not merely consumed"


def test_an_empty_or_blank_waiver_is_not_a_waiver() -> None:
    """`GATE_SAME_FAMILY_WAIVER=` and `--same-family-waiver ''` must not read as a waiver: a waiver
    is an explicit act that states why, and a blank one states nothing."""
    for blank in ("", "   ", "\n\t ", None):
        with pytest.raises(GateError) as raised:
            resolve_gate_family(SAME_FAMILY, "anthropic", waiver=blank)
        assert raised.value.cause == "cross-family"


def test_a_cross_family_call_is_unaffected_by_a_waiver_lying_around() -> None:
    """A waiver left in the environment must never make an independent verdict look waived."""
    family, waived = resolve_gate_family(CROSS_FAMILY, "anthropic", waiver=REASON)
    assert family == "google"
    assert waived is None


def test_an_unknown_vendor_is_not_waivable() -> None:
    """The waiver waives `cross-family` and nothing else. A family that could not be resolved is not
    a family somebody CHOSE to share, so the refusal that says so still stands."""
    with pytest.raises(GateError) as raised:
        resolve_gate_family("acme/not-a-real-model", "anthropic", waiver=REASON)
    assert raised.value.cause == "unknown-vendor"


def test_a_multi_line_reason_is_normalised_to_one_line() -> None:
    """The reason is prose, so it arrives from a file (CLAUDE.md section 6). Every record that
    carries it is one line per entry, and a raw newline would split one record into two."""
    assert waiver_reason("captain waived this\nfor phase 12\n") == "captain waived this for phase 12"


# ── the same two directions, driven through the real CLI ─────────────────────

STUB_GO = '#!/bin/sh\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n'

pytestmark = pytest.mark.subprocess(
    "half of this pins what an operator sees from the runner's CLI, which cannot be reached in-process"
)


@pytest.fixture
def gate(tmp_path: Path):
    """The real runner against a stubbed `opencode`, so no model is called."""
    (tmp_path / "bin").mkdir()
    binary = tmp_path / "bin" / "opencode"
    binary.write_text(STUB_GO)
    binary.chmod(0o755)
    (tmp_path / "rubric.md").write_text("Return JSON with a verdict.")
    (tmp_path / "spec.md").write_text("---\nfeature: demo\n---\n\n- R1.1.1 a requirement\n")

    def run(**env_over) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),   # empty project dir: no repo .env leaks in
            # The stub answers in milliseconds; the latency floor has its own end-to-end tests.
            "GATE_MIN_LATENCY_MS": "0",
            **env_over,
        }
        return subprocess.run(
            [sys.executable, str(RUNNER), "--rubric", str(tmp_path / "rubric.md"),
             "--target", str(tmp_path / "spec.md"), "--author-family", "anthropic",
             "--model", SAME_FAMILY],
            capture_output=True, text=True, env=env, check=False,
        )

    return run


def test_the_cli_still_refuses_a_same_family_gate_with_no_waiver(gate) -> None:
    result = gate()
    assert result.returncode == 2
    assert "cause=cross-family" in result.stderr


def test_the_cli_proceeds_under_an_explicit_waiver_and_announces_it(gate) -> None:
    result = gate(GATE_SAME_FAMILY_WAIVER=REASON)
    assert result.returncode == 0, result.stderr
    assert SAME_FAMILY_MARKER in result.stderr
    assert REASON in result.stderr
    assert "NOT an independent cross-family judgement" in result.stderr
    assert "anthropic" in result.stderr, "the author family is reported truthfully, not cleared"


def test_the_waiver_is_announced_before_the_verdict_is_delivered(gate) -> None:
    """A verdict on stdout with the disclosure arriving later would let a caller reading only the
    answer consume a same-family GO as an ordinary one."""
    result = gate(GATE_SAME_FAMILY_WAIVER=REASON)
    assert "OK (GO)" in result.stdout
    assert SAME_FAMILY_MARKER in result.stderr


# ── and the record the call leaves behind ────────────────────────────────────


def test_the_recorded_call_carries_the_waiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """`note` is an existing field of firstmate's closed gate_calls schema, so the waiver is legible
    in the metrics record without adding a key to it. Recorded on the PASS, which is the outcome
    somebody would otherwise read as an independent judgement."""
    recorded: list[dict] = []
    monkeypatch.setattr(gate_runner, "record_gate_call", lambda **fields: recorded.append(fields))
    monkeypatch.setattr(gate_runner, "call_opencode",
                        lambda *a, **k: json.dumps({"verdict": "GO", "report": "fine"}))
    monkeypatch.setenv("GATE_MIN_LATENCY_MS", "0")
    monkeypatch.setattr(sys, "argv", [
        "gate_runner.py", "--selftest", "--model", SAME_FAMILY,
        "--author-family", "anthropic", "--same-family-waiver", REASON,
    ])

    with pytest.raises(SystemExit) as exit_code:
        gate_runner.main()

    assert exit_code.value.code == 0
    assert recorded, "a paid call is always recorded"
    assert f"same-family waiver: {REASON}" in recorded[-1]["note"]
    assert recorded[-1]["verdict"] == "GO"


def test_an_unwaived_recorded_call_says_nothing_about_a_waiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror: an ordinary independent call must not acquire the note."""
    recorded: list[dict] = []
    monkeypatch.setattr(gate_runner, "record_gate_call", lambda **fields: recorded.append(fields))
    monkeypatch.setattr(gate_runner, "call_opencode",
                        lambda *a, **k: json.dumps({"verdict": "GO", "report": "fine"}))
    monkeypatch.setenv("GATE_MIN_LATENCY_MS", "0")
    monkeypatch.delenv("GATE_SAME_FAMILY_WAIVER", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "gate_runner.py", "--selftest", "--model", CROSS_FAMILY, "--author-family", "anthropic",
    ])

    with pytest.raises(SystemExit) as exit_code:
        gate_runner.main()

    assert exit_code.value.code == 0
    assert "waiver" not in (recorded[-1].get("note") or "")
