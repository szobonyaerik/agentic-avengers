"""Tests for the verifier-review bundler's fail-closed behaviour.

`verifier_review.sh` used to truncate an over-limit review set and append a note asking the model to
report the review as partial, then return the model's verdict as its exit code — so a truncated
review could pass a phase silently. These pin the refusal, and pin that it happens BEFORE any model
call, so an over-limit set costs a stop rather than tokens plus a false pass.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verifier_review.sh"


def phase(tmp_path: Path) -> Path:
    """A phase dir with one spec and mapping, enough for the bundler to run."""
    spec = tmp_path / "phases" / "1-x" / "specs" / "1.1-y"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("---\nfeature: demo\n---\n\n- R1.1.1 does a thing\n")
    (spec / "test-mapping.md").write_text("| test | req |\n|---|---|\n| test_a | R1.1.1 |\n")
    return tmp_path / "phases" / "1-x"


def run(phase_dir: Path, files: list[Path], **env_over) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        # A test-run stub, so the bundler never shells out to a real pytest.
        "TEST_CMD": "echo 'stub: 1 passed'",
        # Guarantees a hard failure if the model is ever reached — these cases must not get there.
        "OPENROUTER_API_KEY": "",
        **env_over,
    }
    return subprocess.run(
        ["bash", str(SCRIPT), str(phase_dir), *[str(f) for f in files]],
        capture_output=True,
        text=True,
        env=env,
    )


def test_an_over_limit_review_set_is_refused(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * 5000)

    proc = run(phase_dir, [big], VERIFIER_SRC_LIMIT="1000")

    assert proc.returncode == 2
    assert "over VERIFIER_SRC_LIMIT" in proc.stderr
    assert "fail closed" in proc.stderr


def test_the_refusal_names_both_ways_out(tmp_path: Path) -> None:
    """A stop the operator cannot act on just gets bypassed."""
    phase_dir = phase(tmp_path)
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * 5000)

    proc = run(phase_dir, [big], VERIFIER_SRC_LIMIT="1000")

    assert "VERIFIER_SRC_LIMIT=" in proc.stderr   # raise the cap
    assert "split the review set" in proc.stderr  # or chunk it
    assert "Do not drop files" in proc.stderr     # and not by shrinking the set


def test_no_verdict_is_written_when_the_set_is_over_limit(tmp_path: Path) -> None:
    """Fail closed means no artifact — a stale .verifier-review.json would be merged as a pass."""
    phase_dir = phase(tmp_path)
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * 5000)

    run(phase_dir, [big], VERIFIER_SRC_LIMIT="1000")

    assert not (phase_dir / ".verifier-review.json").exists()


def test_the_limit_is_checked_before_the_model_is_called(tmp_path: Path) -> None:
    """With no API key a model call fails loudly; the over-limit path must not reach it."""
    phase_dir = phase(tmp_path)
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * 5000)

    proc = run(phase_dir, [big], VERIFIER_SRC_LIMIT="1000")

    assert proc.returncode == 2
    combined = (proc.stderr + proc.stdout).lower()
    assert "api" not in combined and "unreachable" not in combined, combined


def test_passing_no_files_is_refused(tmp_path: Path) -> None:
    """A review of zero tests is not a clean review."""
    proc = run(phase(tmp_path), [])
    assert proc.returncode != 0


def test_the_default_limit_clears_a_real_phase_review_set() -> None:
    """A real bounded set measured 158k chars; the default must not make truncation routine."""
    text = SCRIPT.read_text()
    line = next(ln for ln in text.splitlines() if "VERIFIER_SRC_LIMIT:-" in ln)
    default = int(line.split(":-")[1].split("}")[0])
    assert default >= 200_000, f"default {default} would refuse an ordinary phase review set"
