"""The gate used to be trusted by path: whatever sat there formed the verdict.

A scaffold `gate_runner.py` on a temp path once printed a bare `GO` having checked nothing. Nothing
in the pipeline noticed — its pass was byte-identical to a real one — and it was disbelieved only
because an unrelated JSON-shape requirement failed first. That is luck, not a gate.

`require_gate_runner` makes the runner say what it is and checks the answer against the file that
gave it. What it bounds is ACCIDENTS: a scaffold, a truncated copy, a half-vendored install. A
deliberate double that identifies itself honestly still passes, which is what the test doubles in
this suite are; `GATE_RUNNER_SHA256` is the pin for when that is not enough.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "gate_runner_guard.sh"
REAL_RUNNER = ROOT / "scripts" / "gate_runner.py"

sys.path.insert(0, str(ROOT / "scripts"))

from gate_runner import RUNNER_ABI  # noqa: E402

pytestmark = pytest.mark.subprocess("the subject is a bash guard function")

#: The scaffold that actually happened: it answers every invocation with a pass.
SCAFFOLD = "#!/usr/bin/env python3\nprint('GO')\n"

#: An honest double: it says what it is, and its digest is of itself.
HONEST_DOUBLE = (
    "#!/usr/bin/env python3\n"
    "import hashlib, sys\n"
    "if '--identify' in sys.argv:\n"
    f"    print('{RUNNER_ABI} ' + hashlib.sha256(open(sys.argv[0],'rb').read()).hexdigest())\n"
    "    sys.exit(0)\n"
    "print('GO')\n"
)

#: A stub that claims the ABI but names someone else's digest — a copied identity line.
LIAR = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "if '--identify' in sys.argv:\n"
    f"    print('{RUNNER_ABI} ' + '0' * 64)\n"
    "    sys.exit(0)\n"
    "print('GO')\n"
)


def guard(runner: Path, **env_over) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. "{GUARD}"; require_gate_runner "{runner}"'],
        capture_output=True, text=True, env={**os.environ, **env_over}, check=False,
    )


def test_the_shipped_runner_is_accepted() -> None:
    result = guard(REAL_RUNNER)
    assert result.returncode == 0, result.stderr


def test_a_scaffold_that_prints_go_is_refused(tmp_path: Path) -> None:
    """The defect, exactly as it occurred."""
    fake = tmp_path / "gate_runner.py"
    fake.write_text(SCAFFOLD)
    result = guard(fake)
    assert result.returncode == 2
    assert "cause=runner-untrusted" in result.stderr


def test_an_identity_copied_from_somewhere_else_is_refused(tmp_path: Path) -> None:
    """Claiming the ABI is not enough: the digest must be of the file that claimed it."""
    fake = tmp_path / "gate_runner.py"
    fake.write_text(LIAR)
    result = guard(fake)
    assert result.returncode == 2
    assert "cause=runner-untrusted" in result.stderr


def test_a_missing_runner_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    result = guard(tmp_path / "absent.py")
    assert result.returncode == 2
    assert "no gate runner at" in result.stderr


def test_a_deliberate_double_that_identifies_itself_is_accepted(tmp_path: Path) -> None:
    """Stated so the limit is on the record rather than assumed: this guard bounds accidents."""
    double = tmp_path / "gate_runner.py"
    double.write_text(HONEST_DOUBLE)
    assert guard(double).returncode == 0


def test_a_pinned_digest_refuses_even_an_honest_double(tmp_path: Path) -> None:
    """The real pin, for a deployment that wants one runner and no substitutes."""
    double = tmp_path / "gate_runner.py"
    double.write_text(HONEST_DOUBLE)
    result = guard(double, GATE_RUNNER_SHA256="f" * 64)
    assert result.returncode == 2
    assert "pinned digest" in result.stderr


def test_the_pin_accepts_the_runner_it_names() -> None:
    import hashlib

    digest = hashlib.sha256(REAL_RUNNER.read_bytes()).hexdigest()
    assert guard(REAL_RUNNER, GATE_RUNNER_SHA256=digest).returncode == 0


def test_the_runner_reports_a_digest_of_its_own_current_content() -> None:
    """`--identify` must be computed, not stored: a stored digest survives an edit."""
    import hashlib

    out = subprocess.run(
        [sys.executable, str(REAL_RUNNER), "--identify"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == f"{RUNNER_ABI} {hashlib.sha256(REAL_RUNNER.read_bytes()).hexdigest()}"
