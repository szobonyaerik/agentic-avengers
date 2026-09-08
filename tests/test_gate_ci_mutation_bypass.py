"""`GATE_BYPASS` may waive a mutation verdict, never a checkout nobody established as clean.

`gate_ci.sh` is a real caller of `cosmic-ray exec` in a live working copy (the Verifier runs it with
`--full`), so its break-glass carries the same exposure the hook's did: an unscoped override turned
a tree still holding a mutant into a green run. The two blocks that decide it are sliced out and run
against a `bypass_log.sh` stub, the same seam `tests/test_gate_ci_mutation_base.py` uses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_CI = ROOT / "scripts" / "gate_ci.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is bash's own evaluation of gate_ci.sh's break-glass, which only a shell performs"
)


def _bypass_source() -> str:
    """The refusal block plus the break-glass block it guards, in that order."""
    lines = GATE_CI.read_text(encoding="utf-8").splitlines()
    start = next(
        i
        for i, ln in enumerate(lines)
        if ln.startswith('if [ "$fail" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ] && ')
    )
    closes = [i for i, ln in enumerate(lines[start:], start) if ln == "fi"]
    return "\n".join(lines[start : closes[1] + 1])


def _run(tmp_path: Path, tree_unclean: int) -> subprocess.CompletedProcess[str]:
    stub_dir = tmp_path / "scripts"
    stub_dir.mkdir(parents=True, exist_ok=True)
    logged = tmp_path / "logged"
    stub = stub_dir / "bypass_log.sh"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{logged}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    body = (
        "set -uo pipefail\n"
        f'SCRIPT_DIR="{stub_dir}"; ROOT="{tmp_path}"\n'
        'fail=1; failed_gates=" mutation:tree-corrupted"\n'
        f"TREE_UNCLEAN={tree_unclean}\n"
        f"{_bypass_source()}\n"
        'echo "REACHED_END"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "GATE_BYPASS": "skipping flaky mutation gate",
        },
    )
    proc.logged = logged.read_text(encoding="utf-8") if logged.exists() else ""
    return proc


def test_a_bypass_still_waives_a_failure_over_a_clean_checkout(tmp_path):
    """The break-glass is not being removed: a failed gate over an intact tree is still waivable."""
    proc = _run(tmp_path, tree_unclean=0)

    assert proc.returncode == 0, proc.stderr
    assert "mutation:tree-corrupted" in proc.logged, (
        "an honoured override is a logged override"
    )


def test_a_bypass_cannot_waive_a_checkout_that_is_not_established_clean(tmp_path):
    """`mutation_exec_guarded` exit 5 is a mutant still applied, or a restore that never finished.

    Waiving it converts issue #95's exact damage - cosmic-ray source nobody authored, left in a live
    working copy the Verifier runs this in - into a green run.
    """
    proc = _run(tmp_path, tree_unclean=1)

    assert proc.returncode == 1, proc.stderr
    assert "NOT BYPASSED" in proc.stderr
    assert "Inspect the working tree by hand" in proc.stderr
    assert proc.logged == "", (
        "a refused override leaves no record: a line in that log asserts a gate WAS waived"
    )
