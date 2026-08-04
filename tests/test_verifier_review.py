"""Tests for the verifier-review bundler's fail-closed behaviour.

`verifier_review.sh` used to truncate an over-limit review set and append a note asking the model to
report the review as partial, then return the model's verdict as its exit code — so a truncated
review could pass a phase silently. These pin the refusal, and pin that it happens BEFORE any model
call, so an over-limit set costs a stop rather than tokens plus a false pass.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verifier_review.sh"

#: A stand-in for the `opencode` CLI: captures the prompt it was handed and answers with a verdict
#: substantive enough to clear the post-call check, so the bundle itself can be asserted on.
STUB_OPENCODE = """#!/bin/sh
for last; do :; done
printf '%s' "$last" > "$VERIFIER_TEST_CAPTURE"
echo '{"verdict":"GO","report":"Reviewed test_a.py (expected values are independent) and test_b.py \
(asserts through the seam).","route_back":"","findings":[]}'
"""


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


def run_with_stub_model(
    phase_dir: Path, files: list[Path], tmp_path: Path
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the bundler against a stubbed model CLI, returning the process and the prompt it sent."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "opencode"
    stub.write_text(STUB_OPENCODE)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    capture = tmp_path / "prompt.txt"
    proc = run(
        phase_dir,
        files,
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        GATE_PROVIDER="opencode",
        VERIFIER_GATE_MODEL="google/gemini-3.1-pro-preview",
        AUTHOR_FAMILY="anthropic",
        VERIFIER_TEST_CAPTURE=str(capture),
    )
    return proc, capture.read_text() if capture.exists() else ""


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


def test_a_previous_runs_verdict_does_not_survive_an_over_limit_refusal(tmp_path: Path) -> None:
    """The exact sequence: run 1 reviews the phase and writes a verdict; findings route the phase
    back; the implementer adds tests; the now-larger set is over the cap. Run 2 must not leave run
    1's verdict — for the OLD code — on disk, because the Verifier agent merges it as this run's
    pass."""
    phase_dir = phase(tmp_path)
    stale = phase_dir / ".verifier-review.json"
    stale.write_text(json.dumps({"verdict": "GO", "report": "old run", "findings": []}))
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * 5000)

    proc = run(phase_dir, [big], VERIFIER_SRC_LIMIT="1000")

    assert proc.returncode == 2
    assert not stale.exists(), "a stale verdict survived a refusal and would be merged as a pass"


def test_each_review_set_file_header_starts_on_its_own_line(tmp_path: Path) -> None:
    """Command substitution strips trailing newlines, which glued each `--- <path> ---` header onto
    the previous file's last line — corrupting the attribution the substance check depends on."""
    phase_dir = phase(tmp_path)
    a = tmp_path / "test_a.py"
    a.write_text("def test_a():\n    assert compute() == 15\n")
    b = tmp_path / "test_b.py"
    b.write_text("def test_b():\n    assert other() == 3\n")

    proc, prompt = run_with_stub_model(phase_dir, [a, b], tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert f"\n--- {b} ---\n" in prompt, prompt[-600:]
    assert "assert compute() == 15--- " not in prompt


def test_the_whole_review_set_reaches_the_model_intact(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    a = tmp_path / "test_a.py"
    a.write_text("def test_a():\n    assert compute() == 15\n")
    b = tmp_path / "test_b.py"
    b.write_text("def test_b():\n    assert other() == 3\n")

    _, prompt = run_with_stub_model(phase_dir, [a, b], tmp_path)

    assert "assert compute() == 15" in prompt
    assert "assert other() == 3" in prompt
