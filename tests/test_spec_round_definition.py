"""ONE definition of a spec round, enforced where the round is decided: at the gate.

## The defect this pins

`spec_rounds` was counted at the WRITER - `hook_spec_gate.sh` measured the body the moment it got
past the cache check, before either paid call. Phase 12 then used three counting conventions in one
phase, including a spec whose rounds were structurally invisible because it was authored through a
shell heredoc and fired no gate at all. Phase 13 added two more: a re-gate after an amendment, and
an attempt that could not run when a provider balance ran out. **A gate that never ran is not a
round by any definition currently written down, and nothing said so** - so `spec_rounds` could not
be compared between phases, which quietly weakens every comparison the improvement method depends
on.

## The definition

**A spec round is one COMPLETED gate evaluation of a spec body: a run of the spec gate that reached
a verdict.** An approval and a block are both rounds - a block is a completed evaluation. Three
things are not:

* a gate that never ran (nothing was evaluated, and a spec written outside the gate's trigger has
  zero rounds, which is correct rather than a third convention);
* a gate that ran and could not reach a verdict - a provider that refused for billing, an
  unreachable one, a killed hook. Those stay visible in `gate_calls[]` with their `failure_cause`,
  which is where a failed call belongs;
* a replayed verdict over an unchanged body. Nothing was evaluated; the stored verdict was reread.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** The decisive case drives the real hook with a
provider that refuses, and asserts NO round is recorded - which the unfixed hook recorded before it
ever made the call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import stub_sink  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_metrics as metrics  # noqa: E402
from gate_runner import ATTRIBUTION_MARKER, RUNNER_ABI, SAME_FAMILY_MARKER  # noqa: E402
from test_hook_spec_gate import (  # noqa: E402
    BLOCKING,
    NOTE_ONLY,
    OBSERVATION,
    SPEC,
    STUB_RUNNER,
    ONE_REQUIREMENT,
)

PHASE = "1-demo"


def spec_at(project: Path, body: str = "# Spec\n\nThe body.\n") -> Path:
    path = (
        project
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / PHASE
        / "specs"
        / "1.1-a"
        / "spec.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nfeature: demo\nphase: {PHASE}\nspec: 1.1-a\nspec_gate: pending\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def rounds(store: Path) -> list[int]:
    records = sorted(store.glob("phase-*.json"))
    if not records:
        return []
    data = json.loads(records[0].read_text(encoding="utf-8"))
    specs = data.get("specs") or []
    return list(specs[0].get("bytes_by_round") or []) if specs else []


# --- the definition binds at the module, not at the caller ---------------------------------------


def test_a_completed_approval_is_a_round(stub_sink) -> None:  # noqa: F811
    project, store, _log = stub_sink
    assert metrics.record_spec_round(str(spec_at(project)), verdict="approved") == 1
    assert len(rounds(store)) == 1


def test_a_block_is_a_round_too(stub_sink) -> None:  # noqa: F811
    project, store, _log = stub_sink
    assert metrics.record_spec_round(str(spec_at(project)), verdict="blocked") == 1
    assert len(rounds(store)) == 1


def test_no_verdict_records_nothing_and_says_why(stub_sink) -> None:  # noqa: F811
    """The rule lives HERE, so a future caller that measures the writer again cannot reintroduce
    the gap by being wired one line earlier."""
    project, store, _log = stub_sink
    with pytest.raises(metrics.SpecRoundUndecided):
        metrics.record_spec_round(str(spec_at(project)))
    assert rounds(store) == []


def test_a_verdict_outside_the_closed_set_is_refused(stub_sink) -> None:  # noqa: F811
    project, store, _log = stub_sink
    with pytest.raises(metrics.SpecRoundUndecided):
        metrics.record_spec_round(str(spec_at(project)), verdict="probably-fine")
    assert rounds(store) == []


def test_the_same_body_judged_twice_is_still_one_round(stub_sink) -> None:  # noqa: F811
    project, store, _log = stub_sink
    path = str(spec_at(project))
    metrics.record_spec_round(path, verdict="approved")
    metrics.record_spec_round(path, verdict="approved")
    assert len(rounds(store)) == 1


def test_a_changed_body_judged_again_is_a_second_round(stub_sink) -> None:  # noqa: F811
    project, store, _log = stub_sink
    metrics.record_spec_round(str(spec_at(project)), verdict="blocked")
    metrics.record_spec_round(
        str(spec_at(project, "# Spec\n\nA longer body now.\n")), verdict="approved"
    )
    assert len(rounds(store)) == 2


def test_a_gate_call_belongs_to_the_round_it_is_part_of(stub_sink) -> None:  # noqa: F811
    """The round is now recorded AFTER the calls it belongs to, so the attempt a call reports has
    to be the round IN FLIGHT - not the number of rounds already closed."""
    project, store, _log = stub_sink
    path = spec_at(project)
    metrics.record_spec_round(str(path), verdict="blocked")
    spec_at(project, "# Spec\n\nRewritten after the block.\n")

    os.environ["AVENGER_METRICS_SPEC_PATH"] = str(path)
    try:
        metrics.record_gate_call(
            model="x/y",
            rubric="prompts/spec-gate-observe.md",
            verdict="GO",
            latency_ms=900,
        )
    finally:
        del os.environ["AVENGER_METRICS_SPEC_PATH"]

    data = json.loads(sorted(store.glob("phase-*.json"))[0].read_text(encoding="utf-8"))
    assert [call["attempt"] for call in data["gate_calls"]] == [2]


# --- and it binds through the real hook -----------------------------------------------------------


@pytest.fixture
def gated(tmp_path: Path):
    """The real spec-gate hook, with a stubbed runner and the metrics double behind it."""
    project = tmp_path / "project"
    project.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    shutil.copytree(ROOT / "scripts", project / "scripts")
    shutil.copytree(ROOT / "prompts", project / "prompts")
    (project / "scripts" / "gate_runner.py").write_text(
        STUB_RUNNER.format(
            abi=RUNNER_ABI, marker=SAME_FAMILY_MARKER, attribution=ATTRIBUTION_MARKER
        )
    )
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(
        (ROOT / "tests" / "metrics_support.py")
        .read_text(encoding="utf-8")
        .split("DOUBLE = r'''")[1]
        .split("'''")[0],
        encoding="utf-8",
    )
    double.chmod(0o755)

    spec = (
        project
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / PHASE
        / "specs"
        / "1.1-a"
        / "spec.md"
    )
    spec.parent.mkdir(parents=True)
    spec.write_text(SPEC.format(status="draft", requirements=ONE_REQUIREMENT))

    def run(**env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(project / "scripts" / "hook_spec_gate.sh")],
            input='{"tool_input": {"file_path": "%s"}}' % spec,
            capture_output=True,
            text=True,
            check=False,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(project),
                "CLAUDE_PROJECT_DIR": str(project),
                "AVENGER_METRICS_CMD": str(double),
                "AVENGER_METRICS_PROJECT": "unit-test",
                "AVENGER_METRICS_LOG": str(tmp_path / "diagnostics.log"),
                "DOUBLE_LOG": str(tmp_path / "calls.log"),
                "DOUBLE_STORE": str(store),
                "GATE_MODEL": "x/observer",
                **env,
            },
        )

    run.store = store  # type: ignore[attr-defined]
    run.spec = spec  # type: ignore[attr-defined]
    return run


@pytest.mark.subprocess("the subject is the real bash hook and the argv it emits")
def test_an_approved_run_records_exactly_one_round(gated) -> None:
    result = gated(STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert "APPROVED" in result.stderr, result.stderr
    assert len(rounds(gated.store)) == 1


@pytest.mark.subprocess("the subject is the real bash hook and the argv it emits")
def test_a_blocked_run_records_a_round(gated) -> None:
    result = gated(STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING)
    assert "BLOCKED" in result.stderr, result.stderr
    assert len(rounds(gated.store)) == 1


@pytest.mark.subprocess("the subject is the real bash hook and the argv it emits")
def test_a_gate_that_could_not_run_is_not_a_round(gated) -> None:
    """THE regression. The unfixed hook measured the body before it called anything, so a provider
    that refused for billing - phase 13's actual case - still counted as a round."""
    result = gated(STUB_OBSERVATIONS=OBSERVATION, STUB_FAIL_OBSERVATIONS="1")
    assert result.returncode == 2, result.stderr
    assert rounds(gated.store) == []


@pytest.mark.subprocess("the subject is the real bash hook and the argv it emits")
def test_a_replayed_verdict_over_an_unchanged_body_is_not_a_second_round(gated) -> None:
    """Nothing was evaluated: the stored verdict was reread."""
    gated(STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert len(rounds(gated.store)) == 1
    gated(STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY)
    assert len(rounds(gated.store)) == 1


@pytest.mark.subprocess("the CLI's own argument handling is what a hook invokes")
def test_the_cli_refuses_to_record_a_round_with_no_verdict(tmp_path: Path) -> None:
    """A usage error, not a swallowed emission: every other metrics command exits 0 on a failed
    WRITE, and this is a caller that did not say what it was recording."""
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\n---\n\nbody\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "pipeline_metrics.py"),
            "spec-round",
            str(spec),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--verdict" in result.stderr
