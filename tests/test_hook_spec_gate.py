"""The one spec gate, end to end: two mechanical checks, observe, triage, and a script's verdict.

This replaces tests/test_hook_fidelity.py and tests/test_hook_spec_review.py, whose subjects ran two
model rubrics over the same document at the same moment — a spec once passed one and failed the other
on byte-identical text.

What is pinned here is what the collapse must not lose, and what it must newly guarantee:

  * the mechanical checks run BEFORE any paid call, so an over-cap spec costs nothing and is told to
    SPLIT rather than rejected;
  * a block carries the gate's own findings to the author (an unexplained rejection is what produced
    blind rewriting, and one 25k spec reached 51k that way);
  * notes never block, and they land where the implementer reads them;
  * an unchanged body does not re-run a paid gate, and an unchanged BLOCKED body replays its block;
  * everything ambiguous fails closed.

No model is called: `scripts/` is copied and `gate_runner.py` is replaced by a stub, so the real hook
runs and only the paid call is swapped out.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gate_runner import RUNNER_ABI  # noqa: E402

SPEC = """---
feature: demo
phase: 1-demo
spec: 1.1-a
status: {status}
spec_gate: pending
review_status: pending
---

# Spec

## Requirements
{requirements}

## Acceptance criteria
- R1.1.1 — passes when: the caller sees a greeting; fails when: the caller is unauthenticated
"""

#: A stub gate_runner driven entirely by the environment. It answers the OBSERVE pass with
#: $STUB_OBSERVATIONS and the TRIAGE pass with $STUB_CLASSIFICATIONS, branching on --json-key, so one
#: stub covers both calls of a two-call hook. It identifies itself because the hook refuses a runner
#: that cannot (scripts/gate_runner_guard.sh) — a deliberate double says what it is; a scaffold does
#: not.
STUB_RUNNER = """import hashlib
import json
import os
import sys

if "--identify" in sys.argv:
    print("{abi} " + hashlib.sha256(open(sys.argv[0], "rb").read()).hexdigest())
    sys.exit(0)

key = sys.argv[sys.argv.index("--json-key") + 1] if "--json-key" in sys.argv else "verdict"
out = sys.argv[sys.argv.index("--emit-json") + 1]
target = sys.argv[sys.argv.index("--target") + 1]
open(sys.argv[0] + ".calls", "a").write(key + "\\n")
open(sys.argv[0] + ".targets", "a").write(open(target).read() + "\\n=== END ===\\n")

if os.environ.get("STUB_FAIL_" + key.upper()):
    sys.stderr.write("cause=provider-unreachable the stub was told to fail\\n")
    sys.exit(2)

payload = os.environ.get(
    "STUB_OBSERVATIONS" if key == "observations" else "STUB_CLASSIFICATIONS", ""
)
json.dump(json.loads(payload) if payload else {{key: []}}, open(out, "w"))
"""

ONE_REQUIREMENT = "- R1.1.1 — `binding: e2e` — the caller sees a greeting\n"

OBSERVATION = json.dumps({"observations": [
    {"id": "o1", "area": "edge-case", "spec_ref": "R1.1.1",
     "statement": "No criterion covers a replayed request."},
]})
BLOCKING = json.dumps({"classifications": [
    {"id": "o1", "category": "missing-requirement",
     "why": "Idempotency is in Scope and no requirement states it."},
]})
NOTE_ONLY = json.dumps({"classifications": [
    {"id": "o1", "category": "note", "why": "Worth knowing; nothing is blocked."},
]})
INVENTED = json.dumps({"classifications": [
    {"id": "o1", "category": "too-vague", "why": "it feels thin"},
]})


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "scripts" / "gate_runner.py").write_text(STUB_RUNNER.format(abi=RUNNER_ABI))
    return tmp_path


def write_spec(project: Path, *, status: str = "draft",
               requirements: str = ONE_REQUIREMENT) -> Path:
    spec = project / "docs" / "spec.md"
    spec.write_text(SPEC.format(status=status, requirements=requirements))
    return spec


def run_hook(project: Path, spec: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_spec_gate.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % spec,
        capture_output=True, text=True, check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            **env,
        },
    )


def calls(project: Path) -> list[str]:
    """Which passes the stub was asked for — how many paid calls actually happened."""
    log = project / "scripts" / "gate_runner.py.calls"
    return log.read_text().split() if log.exists() else []


# ── one gate, two passes, in that order ──────────────────────────────────────


def test_an_approved_spec_runs_observe_then_triage_and_stamps_once(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"]
    assert "spec_gate: approved" in spec.read_text()


def test_the_two_gates_it_replaced_leave_no_second_stamp(project: Path) -> None:
    """The collapse is only real if one gate writes one thing. `fidelity_verdict` is gone."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert "fidelity_verdict" not in spec.read_text()


# ── the closed set decides, and the script decides it ────────────────────────


def test_a_blocking_classification_blocks_and_shows_its_findings(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING)
    assert result.returncode == 2
    assert "missing-requirement" in result.stderr
    assert "No criterion covers a replayed request." in result.stderr
    assert "route back to avenger-spec-writer" in result.stderr
    assert "spec_gate: blocked" in spec.read_text()


def test_notes_never_block_and_land_in_the_known_open_list(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert result.returncode == 0, result.stderr
    notes = spec.parent / "spec-notes.md"
    assert notes.is_file()
    assert "No criterion covers a replayed request." in notes.read_text()
    assert "readers: implementer @ once" in notes.read_text()


def test_a_run_with_no_notes_leaves_no_stale_sidecar(project: Path) -> None:
    """A document no stage reads does not get written, and a stale one says something the gate no
    longer says."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert (spec.parent / "spec-notes.md").is_file()
    spec.write_text(spec.read_text().replace("greeting", "greeting message"))
    run_hook(project, spec, STUB_OBSERVATIONS='{"observations": []}',
             STUB_CLASSIFICATIONS='{"classifications": []}')
    assert not (spec.parent / "spec-notes.md").exists()


def test_an_invented_category_fails_closed_rather_than_approving(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=INVENTED)
    assert result.returncode == 2
    assert "unknown-category" in result.stderr
    assert "spec_gate: approved" not in spec.read_text()


# ── the mechanical checks run first, and cost nothing ────────────────────────


def test_an_over_cap_spec_is_told_to_split_before_any_paid_call(project: Path) -> None:
    """A rejection for size is one more thing to grow around, so this is not a gate verdict at all —
    and it must not spend a model call to say so."""
    over = "".join(f"- R1.1.{i} — `binding: e2e` — behavior {i}\n" for i in range(1, 14))
    spec = write_spec(project, requirements=over)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert result.returncode == 2
    assert "SPLIT TRIGGER" in result.stderr
    assert "SPLIT this spec — not to shorten it" in result.stderr
    assert calls(project) == [], "the cap must be decided before the gate is paid for"


def test_a_spec_at_the_cap_still_reaches_the_gate(project: Path) -> None:
    at_cap = "".join(f"- R1.1.{i} — `binding: e2e` — behavior {i}\n" for i in range(1, 13))
    spec = write_spec(project, requirements=at_cap)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"]


# ── the paid gate is not re-run on an unchanged body ─────────────────────────


def test_a_frontmatter_only_edit_does_not_re_run_the_gate(project: Path) -> None:
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    spec.write_text(spec.read_text().replace("status: draft", "status: done"))
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"], "the gate ran a second time"


def test_an_unchanged_blocked_body_replays_its_block(project: Path) -> None:
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING)
    before = list(calls(project))
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert result.returncode == 2
    assert "unchanged since it was judged" in result.stderr
    assert calls(project) == before, "a replayed block must not re-roll a fresh verdict"


def test_a_replayed_block_still_honours_break_glass(project: Path) -> None:
    """A bypass silently dropped is the same defect as one silently taken."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION,
                      STUB_CLASSIFICATIONS=BLOCKING, GATE_BYPASS="captain says ship it")
    assert result.returncode == 0, result.stderr
    assert (project / "gate-overrides.log").is_file()


# ── fail closed on either pass ───────────────────────────────────────────────


@pytest.mark.parametrize("failing", ["OBSERVATIONS", "CLASSIFICATIONS"])
def test_a_pass_that_does_not_answer_stops_the_turn(project: Path, failing: str) -> None:
    spec = write_spec(project)
    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION,
                      STUB_CLASSIFICATIONS=NOTE_ONLY, **{f"STUB_FAIL_{failing}": "1"})
    assert result.returncode == 2
    assert "provider-unreachable" in result.stderr
    assert "spec_gate: approved" not in spec.read_text()


def test_the_triage_pass_is_given_the_observations_and_the_spec(project: Path) -> None:
    """It classifies what the first pass reported; it does not review the spec again."""
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    targets = (project / "scripts" / "gate_runner.py.targets").read_text()
    triage_input = targets.split("=== END ===")[1]
    assert "## OBSERVATIONS TO CLASSIFY" in triage_input
    assert "o1" in triage_input
    assert "## SPEC THEY WERE MADE AGAINST" in triage_input


# ── the human sign-off is a separate question ────────────────────────────────


def test_interactive_mode_leaves_review_status_for_the_human(project: Path) -> None:
    spec = write_spec(project)
    run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert "review_status: pending" in spec.read_text()


def test_auto_mode_carries_the_sign_off_because_nobody_is_there(project: Path) -> None:
    spec = write_spec(project)
    run_hook(project, spec, SPEC_REVIEW_MODE="auto",
             STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert "review_status: approved" in spec.read_text()


def test_a_write_that_is_not_a_spec_is_ignored(project: Path) -> None:
    other = project / "docs" / "notes.md"
    other.write_text("# not a spec\n")
    result = run_hook(project, other)
    assert result.returncode == 0
    assert calls(project) == []


# ── the measurement the collapse must not lose ───────────────────────────────
#
# This gate replaced two hooks that had just been instrumented, and losing instrumentation in a
# refactor is how a pipeline stops being able to answer whether it improved. Driven end to end
# through the real hook against a double writer, because "the call is in the file" is not the claim
# — the claim is that a spec write leaves numbers behind.


@pytest.fixture
def measured(project: Path, tmp_path: Path):
    """The hook's environment with a double metrics writer wired in. Yields (spec, read_record)."""
    from metrics_support import DOUBLE

    store = tmp_path / "store"
    store.mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    spec_dir = project / "docs" / "features" / "demo" / "phases" / "1-demo" / "specs" / "1.1-a"
    spec_dir.mkdir(parents=True)
    spec = spec_dir / "spec.md"
    spec.write_text(SPEC.format(status="draft", requirements=ONE_REQUIREMENT))
    env = {
        "AVENGER_METRICS_CMD": str(double),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp_path / "diagnostics.log"),
        "DOUBLE_LOG": str(tmp_path / "calls.log"),
        "DOUBLE_STORE": str(store),
    }
    return spec, env, (lambda: json.loads((store / "phase-01.json").read_text(encoding="utf-8")))


def test_a_spec_write_leaves_its_phase_its_round_and_its_gate_calls(measured) -> None:
    spec, env, record_of = measured
    project = spec.parents[5].parent.parent   # …/docs/features/demo/phases/… -> project root

    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION,
                      STUB_CLASSIFICATIONS=NOTE_ONLY, **env)

    assert result.returncode == 0, result.stderr
    record = record_of()
    assert record["opened"], "a spec write is where the phase record opens"
    assert record["specs"][0]["bytes_by_round"], "the round the ratchet would show up in"
    assert record["specs"][0]["requirements"] == 1
    # The two provider calls record themselves inside the runner, which this test replaces with a
    # stub; what belongs to the HOOK is the deterministic decide step, and it is a stage of its own
    # because the verdict is derived by a script rather than asked of a model.
    stages = {call["stage"] for call in record["gate_calls"]}
    assert "spec-gate-decide" in stages
    decided = next(c for c in record["gate_calls"] if c["stage"] == "spec-gate-decide")
    assert "observations=1 blocking=0 notes=1" in decided["note"]


def test_the_cheapest_rejection_is_still_measured(measured) -> None:
    """The requirement cap runs before any paid call, so an over-cap spec costs nothing — and would
    be the one case that went unmeasured entirely if `phase-open` sat behind it."""
    spec, env, record_of = measured
    project = spec.parents[5].parent.parent
    spec.write_text(SPEC.format(
        status="draft",
        requirements="".join(f"- R1.1.{n} — `binding: none` — thing {n}\n" for n in range(1, 14)),
    ))

    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION,
                      STUB_CLASSIFICATIONS=NOTE_ONLY, **env)

    assert result.returncode == 2 and "SPLIT" in result.stderr
    assert calls(project) == [], "the cap must cost no provider call"
    assert record_of()["opened"], "and must still leave the phase measured"


def test_a_writer_that_refuses_everything_does_not_fail_the_gate(measured) -> None:
    """Measurement is never a gate. A phase must not stop because a number went unrecorded."""
    spec, env, _ = measured
    project = spec.parents[5].parent.parent

    result = run_hook(project, spec, STUB_OBSERVATIONS=OBSERVATION,
                      STUB_CLASSIFICATIONS=NOTE_ONLY, DOUBLE_EXIT="3", **env)

    assert result.returncode == 0, result.stderr
    assert "spec_gate: approved" in spec.read_text()


def test_a_killed_pass_is_recorded_under_the_pass_that_was_killed(measured) -> None:
    """The runner being killed cannot record its own death, so the hook records it — and this gate
    makes TWO calls, so "which one" is the whole fact. Telling a killed hook apart from a model that
    answered NO-GO is what once read for a day as a model size ceiling."""
    import signal
    import time

    spec, env, record_of = measured
    project = spec.parents[5].parent.parent
    runner = project / "scripts" / "gate_runner.py"
    marker = project / "spawned"
    runner.write_text(
        runner.read_text().replace(
            'open(sys.argv[0] + ".calls", "a").write(key + "\\n")',
            'open(sys.argv[0] + ".calls", "a").write(key + "\\n")\n'
            f'open({str(marker)!r}, "w").write("x")\n'
            'import time as _t; _t.sleep(120)',
        )
    )

    hook = subprocess.Popen(  # noqa: S603
        ["bash", str(project / "scripts" / "hook_spec_gate.sh")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(project),
             "CLAUDE_PROJECT_DIR": str(project),
             "STUB_OBSERVATIONS": OBSERVATION, "STUB_CLASSIFICATIONS": NOTE_ONLY, **env},
    )
    hook.stdin.write('{"tool_input": {"file_path": "%s"}}' % spec)
    hook.stdin.close()
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), "the observe pass never started"
    hook.send_signal(signal.SIGTERM)
    err = hook.stderr.read()
    hook.wait(timeout=30)

    assert hook.returncode == 2
    assert "HOOK KILLED" in err and "NOT a gate verdict" in err
    killed = [c for c in record_of()["gate_calls"] if c["verdict"] == "killed"]
    assert [c["stage"] for c in killed] == ["spec-gate-observe"]
