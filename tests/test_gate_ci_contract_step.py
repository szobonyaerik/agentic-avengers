"""`gate_ci.sh`'s frontmatter-contract announcement, in its three distinguishable states.

The step prints BEFORE `check --contract-only` runs, so it may name its SUBJECT and never a
verdict: worded as an outcome it announced that the table and the writer instructions agree and
then failed two lines later.

The other half is the probe's own exit code. `doc_read_path.py canonical` answers 1 for
not-canonical and nothing else; 2 is a usage error, 127 is a missing `python3`, and a traceback out
of a module it imports is neither. Reading any non-zero as not-canonical announced a vendored
install with an upstream remedy inside the pipeline's own repository, which is a cause nobody
verified. Three states, and they must not collapse into each other:

  0        -> the subject line, claiming no outcome
  1        -> NOT CHECKED, not the canonical repository, remedy upstream
  anything -> the probe COULD NOT ANSWER, and which tree this is was not determined

`gate_ci.sh` runs its whole floor on execution, so it cannot be sourced to reach one function. The
slice below is that one function's definition, EXECUTED against a stubbed probe, which is the same
approach `test_gate_ci_cross_family.py` takes and for the same reason.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_CI = ROOT / "scripts" / "gate_ci.sh"


def _announce_source() -> str:
    lines = GATE_CI.read_text(encoding="utf-8").splitlines()
    open_at = next(
        i for i, ln in enumerate(lines) if ln.startswith("contract_step_announce () {")
    )
    close_at = next(i for i, ln in enumerate(lines[open_at:], open_at) if ln == "}")
    return "\n".join(lines[open_at : close_at + 1])


def _stub(tmp_path: Path, exit_code: int) -> Path:
    """A `python3` on PATH that answers the probe with one exit code and nothing else."""
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python3").write_text(f"#!/bin/sh\nexit {exit_code}\n")
    (shim / "python3").chmod(0o755)
    return shim


@pytest.mark.subprocess(
    "the subject IS a shell function's exit-code mapping, which only bash can evaluate"
)
def _run(tmp_path: Path, exit_code: int) -> str:
    shim = _stub(tmp_path, exit_code)
    script = (
        "set -uo pipefail\n"
        f'SCRIPT_DIR="{ROOT / "scripts"}"\n'
        f'ROOT="{ROOT}"\n'
        f"{_announce_source()}\n"
        "contract_step_announce\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{shim}{os.pathsep}/usr/bin:/bin", "HOME": str(Path.home())},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_canonical_probe_announces_the_subject_and_claims_no_outcome(
    tmp_path: Path,
) -> None:
    said = _run(tmp_path, 0)
    assert "the table, the templates and the writer instructions" in said, said
    assert "agree" not in said, said
    assert "NOT CHECKED" not in said, said


def test_exit_one_is_the_only_thing_read_as_not_canonical(tmp_path: Path) -> None:
    said = _run(tmp_path, 1)
    assert "NOT CHECKED" in said, said
    assert "not the canonical pipeline repository" in said, said


@pytest.mark.parametrize("exit_code", [2, 127])
def test_any_other_exit_is_a_probe_that_could_not_answer(
    tmp_path: Path, exit_code: int
) -> None:
    """The regression: a usage error or a missing interpreter announced a vendored install.

    Both are the probe failing rather than answering, and the remedy for neither lives upstream.
    """
    said = _run(tmp_path, exit_code)
    assert "COULD NOT ANSWER" in said, said
    assert "not the canonical pipeline repository" not in said, said
    assert "remedy upstream" not in said, said
