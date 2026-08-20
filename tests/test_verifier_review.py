"""Tests for the verifier-review bundler's fail-closed behaviour.

`verifier_review.sh` used to truncate an over-limit review set and append a note asking the model to
report the review as partial, then return the model's verdict as its exit code — so a truncated
review could pass a phase silently. These pin the refusal, and pin that it happens BEFORE any model
call, so an over-limit set costs a stop rather than tokens plus a false pass.
"""

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verifier_review.sh"

sys.path.insert(0, str(ROOT / "scripts"))

#: A stand-in for the `opencode` CLI: captures the prompt it was handed and answers with a verdict
#: substantive enough to clear the post-call check, so the bundle itself can be asserted on.
STUB_OPENCODE = """#!/bin/sh
for last; do :; done
printf '%s' "$last" > "$VERIFIER_TEST_CAPTURE"
echo '{"verdict":"GO","report":"Reviewed test_a.py (expected values are independent) and test_b.py \
(asserts through the seam).","route_back":"","findings":[]}'
"""

pytestmark = pytest.mark.subprocess(
    "the subject is a bash script that shells out to the gate runner"
)


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
    phase_dir: Path, files: list[Path], tmp_path: Path, **env_over
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
        **env_over,
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


def stale_verdict(phase_dir: Path) -> Path:
    """Run 1's verdict, for the OLD code, sitting where the Verifier agent will merge it."""
    out = phase_dir / ".verifier-review.json"
    out.write_text(json.dumps({"verdict": "GO", "report": "old run", "findings": []}))
    return out


def test_a_previous_runs_verdict_does_not_survive_an_over_limit_refusal(tmp_path: Path) -> None:
    """Run 1 writes a verdict; findings route the phase back; the implementer adds tests; the
    now-larger set is over the cap. Run 2 must leave nothing behind to be merged as its pass."""
    phase_dir = phase(tmp_path)
    stale = stale_verdict(phase_dir)
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * 5000)

    proc = run(phase_dir, [big], VERIFIER_SRC_LIMIT="1000")

    assert proc.returncode == 2
    assert not stale.exists(), "a stale verdict survived a refusal and would be merged as a pass"


def test_a_previous_runs_verdict_does_not_survive_the_no_files_refusal(tmp_path: Path) -> None:
    """Same shape, different exit: the agent re-invokes with an empty review-set expansion."""
    phase_dir = phase(tmp_path)
    stale = stale_verdict(phase_dir)

    proc = run(phase_dir, [])

    assert proc.returncode == 2
    assert not stale.exists(), "a stale verdict survived a refusal and would be merged as a pass"


def test_a_previous_runs_verdict_does_not_survive_a_failed_model_call(tmp_path: Path) -> None:
    """A provider outage writes no verdict, so absence is the only thing separating this run from
    run 1's."""
    phase_dir = phase(tmp_path)
    stale = stale_verdict(phase_dir)
    src = tmp_path / "test_a.py"
    src.write_text("def test_a():\n    assert True\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    broken = bindir / "opencode"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(broken.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    proc = run(
        phase_dir,
        [src],
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        GATE_PROVIDER="opencode",
    )

    assert proc.returncode == 2
    assert not stale.exists(), "a stale verdict survived a failed model call"


def test_no_exit_path_precedes_the_stale_verdict_removal() -> None:
    """Enumerated mechanically rather than by reading, because reading missed it twice. The only
    exits allowed above the removal are those on which `$OUT` is not a computable path at all."""
    lines = SCRIPT.read_text().splitlines()
    removal = next(i for i, ln in enumerate(lines) if ln.strip() == 'rm -f "$OUT"')
    exits_before = [
        i for i, ln in enumerate(lines[:removal])
        if re.search(r"\bexit \d", ln) and not ln.lstrip().startswith("#")
    ]

    assert len(exits_before) == 2, [lines[i] for i in exits_before]
    assert 'cd "$ROOT"' in lines[exits_before[0]]
    assert "usage:" in lines[exits_before[1] - 1]


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


# ── bundle scope: what the model is actually sent on a re-run ───────────────────────────────────
# The bundle used to re-send every spec and every mapping on every attempt; one measured ~832k
# tokens, and one phase had to be split into four chunks to fit a context at all. These assert
# against the PROMPT the stub captured, because "the scope shrank" is only true if the text left.


def two_spec_phase(tmp_path: Path) -> Path:
    """A phase with two specs, so one can change while the other does not."""
    phase_dir = tmp_path / "phases" / "8-demo"
    for name, req in (("8.0-alpha", "R8.0.1 alpha requirement"), ("8.1-beta", "R8.1.1 beta requirement")):
        spec = phase_dir / "specs" / name
        spec.mkdir(parents=True)
        (spec / "spec.md").write_text(f"---\nfeature: demo\n---\n\n- {req}\n")
        (spec / "test-mapping.md").write_text(f"| test | req |\n|---|---|\n| test_a | {req[:6]} |\n")
    return phase_dir


def test_the_first_run_sends_every_spec(tmp_path: Path) -> None:
    phase_dir = two_spec_phase(tmp_path)
    src = tmp_path / "test_a.py"
    src.write_text("def test_a():\n    assert True\n")

    proc, prompt = run_with_stub_model(phase_dir, [src], tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "alpha requirement" in prompt
    assert "beta requirement" in prompt


def test_a_re_run_does_not_re_send_an_unchanged_spec(tmp_path: Path) -> None:
    """The defect, measured where it costs: attempt two paid for attempt one all over again."""
    phase_dir = two_spec_phase(tmp_path)
    src = tmp_path / "test_a.py"
    src.write_text("def test_a():\n    assert True\n")
    run_with_stub_model(phase_dir, [src], tmp_path)

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 beta addition\n")

    proc, prompt = run_with_stub_model(phase_dir, [src], tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "beta addition" in prompt, "the changed spec must still be reviewed"
    assert "alpha requirement" not in prompt, "the unchanged spec was re-sent anyway"


def test_a_carried_spec_is_named_in_the_bundle_and_on_stderr(tmp_path: Path) -> None:
    """A scoped review whose scope is invisible cannot be audited, and the model cannot know what
    it is NOT being asked about."""
    phase_dir = two_spec_phase(tmp_path)
    src = tmp_path / "test_a.py"
    src.write_text("def test_a():\n    assert True\n")
    run_with_stub_model(phase_dir, [src], tmp_path)

    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 beta addition\n")

    proc, prompt = run_with_stub_model(phase_dir, [src], tmp_path)

    assert "CARRIED FORWARD" in prompt
    assert "8.0-alpha" in prompt
    assert "carried forward" in proc.stderr


def test_the_carried_notice_uses_none_of_the_words_that_fail_the_substance_check(
    tmp_path: Path,
) -> None:
    """`verifier_review_check.py` fails closed on a report that calls itself partial or truncated.
    Telling the model some specs are out of scope must not put those words in front of it — a model
    that echoes the bundle's own phrasing would fail a review it actually completed."""
    phase_dir = two_spec_phase(tmp_path)
    src = tmp_path / "test_a.py"
    src.write_text("def test_a():\n    assert True\n")
    run_with_stub_model(phase_dir, [src], tmp_path)
    changed = phase_dir / "specs" / "8.1-beta" / "spec.md"
    changed.write_text(changed.read_text() + "\n- R8.1.2 beta addition\n")

    _, prompt = run_with_stub_model(phase_dir, [src], tmp_path)

    from verifier_review_check import PARTIAL_MARKERS

    notice = prompt.split("=== SPECS CARRIED FORWARD")[1].split("=== SPEC REQUIREMENTS")[0].lower()
    assert not [m for m in PARTIAL_MARKERS if m in notice]
    assert "complete set you were asked to review" in notice


# ── defect attribution: non-blocking, but never silent ──────────────────────────────────────────
# This is the pipeline's highest-volume defect-attribution path. It must never fail the phase, and it
# must never fail invisibly either: a run that dropped every verifier-attributed defect used to look
# exactly like a run that found none.


#: A model that reports one finding, so the run has a defect to attribute in the first place.
STUB_OPENCODE_FINDING = """#!/bin/sh
for last; do :; done
printf '%s' "$last" > "$VERIFIER_TEST_CAPTURE"
echo '{"verdict":"NO-GO","report":"Reviewed test_a.py: it asserts through the seam but pins the \
implementation rather than the behaviour.","route_back":"implementer","findings":[{"kind":"gamed \
test","spec_id":"1.1","target":"test_a.py","instruction":"assert on the observable behaviour"}]}'
"""

#: A metrics writer that answers every call EXCEPT the defect write, which is the one this asserts
#: on. A writer that refused everything would fail the gate runner's own emission too, and both
#: processes say the same words on stderr — so the assertion could not tell which call was heard.
REFUSES_DEFECTS = """#!/bin/sh
if [ "$1" = "show" ]; then echo '{}'; exit 0; fi
for arg in "$@"; do
  if [ "$arg" = "defects" ]; then
    echo 'refusing the defects write on purpose' >&2
    exit 1
  fi
done
exit 0
"""


def metrics_env(tmp_path: Path, body: str) -> dict:
    """Environment pointing the sink at a stub writer, with its diagnostics log out of the repo."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    writer = tmp_path / "fm-pipeline-metrics.sh"
    writer.write_text(body)
    writer.chmod(writer.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "AVENGER_METRICS_CMD": str(writer),
        "AVENGER_METRICS_OFF": "",
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp_path / "metrics.log"),
    }


def run_with_finding(tmp_path: Path, **env_over) -> tuple[subprocess.CompletedProcess, Path]:
    """A review that produces one finding, so `verifier-findings` has something to write."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    phase_dir = phase(tmp_path)
    src = tmp_path / "test_a.py"
    src.write_text("def test_a():\n    assert True\n")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "opencode"
    stub.write_text(STUB_OPENCODE_FINDING)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    proc = run(
        phase_dir,
        [src],
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        GATE_PROVIDER="opencode",
        VERIFIER_GATE_MODEL="google/gemini-3.1-pro-preview",
        AUTHOR_FAMILY="anthropic",
        VERIFIER_TEST_CAPTURE=str(tmp_path / "prompt.txt"),
        **env_over,
    )
    return proc, phase_dir


def test_a_failed_defect_attribution_is_visible_on_stderr(tmp_path: Path) -> None:
    proc, _ = run_with_finding(tmp_path, **metrics_env(tmp_path, REFUSES_DEFECTS))

    assert "refusing the defects write on purpose" in proc.stderr, proc.stderr


def test_a_failed_defect_attribution_still_does_not_fail_the_review(tmp_path: Path) -> None:
    """Removing the silence must not have turned measurement into a gate."""
    ok, broken_root = tmp_path / "ok", tmp_path / "broken"
    healthy, _ = run_with_finding(ok, **metrics_env(ok, "#!/bin/sh\nexit 0\n"))
    proc, phase_dir = run_with_finding(broken_root, **metrics_env(broken_root, REFUSES_DEFECTS))

    assert proc.returncode == healthy.returncode, proc.stderr
    assert (phase_dir / ".verifier-review.json").exists()
