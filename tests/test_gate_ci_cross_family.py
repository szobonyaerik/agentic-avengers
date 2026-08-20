"""`gate_ci.sh`'s cross-family assertion, in its three distinguishable states.

The assertion is a STATIC AUDIT that runs under `gate_ci.sh --full`, in CI and in a disposable
worktree — no gate call happens there and neither environment carries the project `.env`. So an
unset `$GATE_MODEL` is its NORMAL state outside a developer's machine, and refusing the build over
one prescribes a remedy that is not available where the failure occurs: a wedge, not a gate. That
is distinct from `gate_runner.py`, which refuses an unset model at CALL time (issue #48), where a
model genuinely decides something and the remedy is at hand.

Three states, and they must not collapse into each other:

  1. set, different family  -> exit 0, reports what it compared
  2. set, implementer's own family, or an unknown vendor -> non-zero (someone CHOSE that value)
  3. unset                  -> exit 0, and says on stderr that the check was not performed

State 2 is the one that matters: it is what stops state 3 from being a silent removal of the
invariant. Per issue 69 — a guard proven only by passing is not proven — the not-checked case was
shown red against the refusing version before this fix landed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_CI = ROOT / "scripts" / "gate_ci.sh"


def _cross_family_source() -> str:
    """Slice the assertion's own helpers out of `gate_ci.sh`.

    `gate_ci.sh` runs its whole floor on execution — the suite, mutation, every artifact check — so
    it cannot be sourced to reach one function. The slice is the three definitions the assertion is
    made of, evaluated against real `agents/` and the real vendor table.
    """
    lines = GATE_CI.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("model_token ()"))
    open_at = next(i for i, ln in enumerate(lines) if ln.startswith("cross_family_check () {"))
    close_at = next(i for i, ln in enumerate(lines[open_at:], open_at) if ln == "}")
    return "\n".join(lines[start : close_at + 1])


def _run(gate_model: str | None) -> subprocess.CompletedProcess[str]:
    script = (
        'set -uo pipefail\n'
        f'SCRIPT_DIR="{ROOT / "scripts"}"\n'
        f'ROOT="{ROOT}"\n'
        f"{_cross_family_source()}\n"
        "cross_family_check\n"
    )
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(Path.home())}
    if gate_model is not None:
        env["GATE_MODEL"] = gate_model
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
    )


def _implementer_model() -> str:
    """The family the implementers actually declare, read the way the assertion reads it."""
    for name in ("avenger-backend-architect.md", "avenger-frontend-developer.md"):
        path = ROOT / "agents" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("model:"):
                return line.split(":", 1)[1].strip().split()[0]
    pytest.skip("no implementer agent definition to read a model from")


def test_unset_gate_model_is_not_checked_and_never_fails() -> None:
    """State 3. CI has no `.env`, so an unset model must not fail the build — and must not look
    like a clean comparison either."""
    result = _run(None)
    assert result.returncode == 0, result.stderr
    assert "nothing checked" in result.stderr
    # The remedy is named, per the standing rule that a refusal (or a skip) says what would
    # satisfy it.
    assert "GATE_MODEL" in result.stderr
    # And it must not read as a performed comparison.
    assert "the spec gate judges on" not in result.stdout


def test_same_family_gate_model_still_fails() -> None:
    """State 2. The invariant still bites wherever it CAN be evaluated — this is what makes the
    not-checked state a scope reduction rather than a deletion."""
    result = _run(_implementer_model())
    assert result.returncode != 0
    assert "implementer's own family" in result.stderr


def test_unknown_vendor_gate_model_still_fails() -> None:
    """State 2, other half. Someone chose the value and it resolves to no known vendor."""
    result = _run("acme/not-a-real-model")
    assert result.returncode != 0
    assert "unknown GATE_MODEL family" in result.stderr


def test_cross_family_gate_model_passes_and_reports_the_comparison() -> None:
    """State 1. Unchanged by this fix: a real comparison prints what it compared."""
    result = _run("google/gemini-3.1-pro-preview")
    assert result.returncode == 0, result.stderr
    assert "the spec gate judges on" in result.stdout
    assert "google" in result.stdout
