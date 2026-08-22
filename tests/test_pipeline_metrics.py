"""Tests for the facts the pipeline emits about itself, at the points it observes them.

Three properties are load-bearing and each has its own group below.

**Emitted during the run.** Nothing here reconstructs anything at the end, so every emission point
is driven the way its caller drives it and the record is read straight off disk afterwards.

**Never able to fail a phase, except `defect`.** The CLI is asserted to exit 0 with the record
unwritable, which is the shape a real failure takes: a hook runs `pipeline_metrics.py …` and a
non-zero exit there would stop the turn. `defect` is the deliberate exception and has its own group
below: a stage runs it directly, off any hook's `|| true`, so an emission it could not write is
asserted to exit non-zero and say so on stderr — the breaks-the-recorder cases are what prove that
guard goes red rather than green.

**Attributed to the fact, not to the caller.** A spec round is idempotent by content, so any number
of callers may report the same write; a seeded skill requirement never overwrites an observed load,
whatever order the two hooks run in.

`real_sink` tests are the ones that matter most: they prove the emitted records VALIDATE against
firstmate's own schema, which is a claim the double cannot make.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import (  # noqa: F401
    git_init,
    git_land,
    read_calls,
    real_sink,
    stored,
    stub_sink,
    write_spec,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_errors  # noqa: E402
import gate_timeouts  # noqa: E402
import pipeline_metrics as metrics  # noqa: E402
import plugin_release  # noqa: E402
import proc_group  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "every emission point writes through a real writer process, which is the whole mechanism"
)

CLI = [sys.executable, str(ROOT / "scripts" / "pipeline_metrics.py")]


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Drive the CLI the way a hook does, inheriting the fixture's environment."""
    return subprocess.run([*CLI, *args], capture_output=True, text=True, check=False)  # noqa: S603


# --- the failure taxonomy stays distinguishable ---------------------------------------------------


def test_every_gate_failure_cause_maps_to_a_recorded_outcome():
    """A cause added upstream must not land in `other` and lose the distinction PR 1 created."""
    assert set(metrics.CAUSE_MAP) == set(gate_errors.CAUSES)


def test_every_shipped_rubric_names_the_stage_that_judges_against_it():
    """The same exhaustiveness, for stages rather than causes.

    Without it a rubric lands with its calls recorded under a name derived from its filename, which
    is silently plausible and silently wrong — and the two spec-gate passes are the case that
    matters: collapsing observe and triage into one stage would hide how many observations became
    blockers, which is the number the whole redesign is judged by.
    """
    shipped = {p.name for p in (ROOT / "prompts").glob("*.md")} - {"project-setup.md"}

    assert set(metrics.RUBRIC_STAGE) == shipped
    assert metrics.RUBRIC_STAGE["spec-gate-observe.md"] != metrics.RUBRIC_STAGE[
        "spec-gate-triage.md"
    ]


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        ("timeout", ("killed", "timeout")),
        ("provider-payment-required", ("error", "http-402")),
        ("provider-unreachable", ("error", "provider-unreachable")),
        ("no-verdict", ("error", "unparseable")),
        ("cross-family", ("error", "other")),
        (metrics.HOOK_KILLED, ("killed", "killed-by-harness")),
    ],
)
def test_a_failure_records_which_failure_it_was(cause, expected):
    assert metrics._outcome(None, cause) == expected


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [("GO", "GO"), ("PASS", "GO"), ("review", "REVIEW"), ("NO-GO", "NO-GO"), ("", "NO-GO")],
)
def test_a_reached_verdict_carries_no_failure_cause(verdict, expected):
    assert metrics._outcome(verdict, None) == (expected, None)


# --- knowing where we are -------------------------------------------------------------------------


def test_phase_and_spec_come_out_of_the_artifact_path():
    path = "docs/features/demo/phases/8-auth/specs/8.2-tokens/spec.md"

    assert metrics.resolve_phase(path) == "08"
    assert metrics.resolve_spec(path) == "8.2"


def test_a_path_with_no_phase_resolves_to_none():
    assert metrics.resolve_phase("src/app.py") is None


def test_the_stage_is_read_off_the_rubric_it_judges_against():
    assert metrics.stage_from_rubric("prompts/spec-gate-observe.md") == "spec-gate-observe"
    assert metrics.stage_from_rubric("prompts/spec-gate-triage.md") == "spec-gate-triage"
    assert metrics.stage_from_rubric(None) == "unknown"


def test_the_phase_in_flight_is_the_most_recently_touched_one(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    write_spec(project, 3, "3.0", "- R3.0.1 old\n")
    newer = write_spec(project, 4, "4.0", "- R4.0.1 new\n")
    os.utime(newer, (2_000_000_000, 2_000_000_000))

    assert metrics.current_phase() == "04"


def test_the_observing_stage_is_the_one_live_subagent(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    (project / ".agent-activity.jsonl").write_text(
        json.dumps({"event": "SubagentStart", "agent_type": "x:avenger-verifier"}) + "\n",
        encoding="utf-8",
    )

    assert metrics.observing_stage("") == "avenger-verifier"


def test_an_ambiguous_moment_is_the_main_thread(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    (project / ".agent-activity.jsonl").write_text(
        json.dumps({"event": "SubagentStart", "agent_type": "avenger-verifier"}) + "\n"
        + json.dumps({"event": "SubagentStart", "agent_type": "avenger-breaker"}) + "\n",
        encoding="utf-8",
    )

    assert metrics.observing_stage("") == "main-thread"


# --- spec rounds --------------------------------------------------------------------------------


def test_a_spec_round_records_its_size_and_its_requirements(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n- R8.1.2 two\n- R8.1.1 again\n")

    assert metrics.record_spec_round(str(spec)) == 1

    entry = stored(store, "08")["specs"][0]
    assert entry["id"] == "8.1"
    assert entry["requirements"] == 2          # ids, not mentions
    assert entry["bytes_by_round"] == [len(spec.read_text().split("---\n", 2)[2].encode())]


def test_the_same_body_reported_twice_is_one_round(stub_sink):  # noqa: F811
    """Idempotent by content, so emission can sit at the fact rather than once per caller."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    metrics.record_spec_round(str(spec))
    metrics.record_spec_round(str(spec))

    assert len(stored(store, "08")["specs"][0]["bytes_by_round"]) == 1


def test_a_rewritten_spec_is_a_new_round_and_the_growth_is_visible(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    metrics.record_spec_round(str(spec))

    spec.write_text("---\nfeature: demo\n---\n" + "- R8.1.1 one\n" * 40, encoding="utf-8")
    assert metrics.record_spec_round(str(spec)) == 2

    record = stored(store, "08")
    rounds = record["specs"][0]["bytes_by_round"]
    assert len(rounds) == 2 and rounds[1] > rounds[0]
    assert record["spec_rounds"] == 2


def test_a_frontmatterless_file_records_nothing(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "x")
    spec.write_text("no frontmatter here", encoding="utf-8")

    assert metrics.record_spec_round(str(spec)) is None


def test_a_round_the_record_refused_is_retried_not_swallowed(stub_sink, monkeypatch):  # noqa: F811
    """A body is remembered as counted only once the record took it.

    Caching a body the writer refused would make the next call believe the round was already there,
    and that round would be missing from `bytes_by_round` forever — a hole in the one growth series
    this exists to expose.
    """
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    metrics.record_spec_round(str(spec))

    spec.write_text("---\nfeature: demo\n---\n" + "- R8.1.1 one\n" * 40, encoding="utf-8")
    monkeypatch.setenv("DOUBLE_REFUSE", "add")
    assert metrics.record_spec_round(str(spec)) is None

    monkeypatch.delenv("DOUBLE_REFUSE")
    assert metrics.record_spec_round(str(spec)) == 2

    rounds = stored(store, "08")["specs"][0]["bytes_by_round"]
    assert len(rounds) == 2 and rounds[1] > rounds[0]


# --- gate calls ------------------------------------------------------------------------------------


def test_a_gate_call_records_its_model_latency_and_verdict(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    metrics.record_gate_call(
        model="deepseek/deepseek-chat", model_family="deepseek",
        rubric="prompts/spec-gate-observe.md", target=str(spec),
        latency_ms=106_000, verdict="GO", provider="opencode",
    )

    call = stored(store, "08")["gate_calls"][0]
    assert call["id"] == "8.1-a1-spec-gate-observe"
    assert call["stage"] == "spec-gate-observe" and call["spec"] == "8.1"
    assert call["model_family"] == "deepseek" and call["latency_ms"] == 106_000
    assert call["verdict"] == "GO" and call["failure_cause"] is None


def test_a_killed_gate_is_not_a_verdict(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    metrics.record_gate_call(
        model="m", rubric="prompts/spec-gate-observe.md", target=str(spec),
        latency_ms=143_000, cause="timeout", detail="killed after 143.0s", provider="opencode",
    )

    call = stored(store, "08")["gate_calls"][0]
    assert call["verdict"] == "killed" and call["failure_cause"] == "timeout"
    assert "143.0s" in call["note"]


def test_a_second_round_is_a_second_gate_call_not_an_overwrite(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    metrics.record_spec_round(str(spec))
    metrics.record_gate_call(model="m", rubric="prompts/spec-gate-observe.md", target=str(spec),
                             latency_ms=1, verdict="NO-GO")

    spec.write_text("---\nfeature: demo\n---\n- R8.1.1 rewritten\n", encoding="utf-8")
    metrics.record_spec_round(str(spec))
    metrics.record_gate_call(model="m", rubric="prompts/spec-gate-observe.md", target=str(spec),
                             latency_ms=2, verdict="GO")

    calls = stored(store, "08")["gate_calls"]
    assert [c["id"] for c in calls] == ["8.1-a1-spec-gate-observe", "8.1-a2-spec-gate-observe"]


def test_a_call_outside_any_phase_records_nothing(stub_sink):  # noqa: F811
    assert metrics.record_gate_call(model="m", rubric="r.md", target="/tmp/x.md") is False


def test_the_spec_path_wins_over_a_temp_bundle_target(stub_sink, monkeypatch):  # noqa: F811
    """The spec gate judges a diff bundle in /tmp; only the caller knows which spec that is."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.3", "- R8.3.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_SPEC_PATH", str(spec))

    metrics.record_gate_call(model="m", rubric="prompts/spec-gate-triage.md",
                             target="/tmp/bundle.XXXX", latency_ms=5, verdict="GO")

    call = stored(store, "08")["gate_calls"][0]
    assert call["spec"] == "8.3" and call["stage"] == "spec-gate-triage"


def test_a_stage_with_no_rubric_to_read_still_names_itself(stub_sink):  # noqa: F811
    """The kill trap and the decide step have no rubric. `unknown` would merge them with each other
    and with every other rubric-less call, which is the one thing the stage field must not do — the
    spec gate makes two calls and "which of them was killed" is the whole reason the record exists."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    metrics.record_gate_call(model="m", rubric=None, stage="spec-gate-triage", target=str(spec),
                             cause=metrics.HOOK_KILLED, detail="killed mid-call")

    call = stored(store, "08")["gate_calls"][0]
    assert call["stage"] == "spec-gate-triage"
    assert call["verdict"] == "killed" and call["failure_cause"] == "killed-by-harness"


def test_a_pass_that_reaches_no_verdict_is_not_recorded_as_a_rejection(stub_sink):  # noqa: F811
    """The observe pass answers with `observations` and is told it cannot block. An empty verdict
    would be recorded NO-GO — a rejection it never issued, on every single spec write."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    metrics.record_gate_call(model="m", rubric="prompts/spec-gate-observe.md", target=str(spec),
                             latency_ms=7, verdict=metrics.NO_VERDICT)

    assert stored(store, "08")["gate_calls"][0]["verdict"] != "NO-GO"


def test_the_filters_own_arithmetic_is_in_the_ledger(stub_sink, monkeypatch):  # noqa: F811
    """The number this redesign is judged by. A filter that blocks everything and a filter that
    blocks nothing must be distinguishable without reading a transcript."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_SPEC_PATH", str(spec))

    metrics.record_triage_decision(spec_path=str(spec), observations=9, blocking=2, notes=7,
                                   approved=False)

    call = stored(store, "08")["gate_calls"][0]
    assert call["stage"] == metrics.TRIAGE_DECIDE_STAGE and call["verdict"] == "NO-GO"
    assert "observations=9 blocking=2 notes=7" in call["note"]


def test_the_decide_step_records_from_where_the_verdict_is_derived(stub_sink, monkeypatch):  # noqa: F811
    """Through the real CLI the hook runs, not through the function directly: the emission has to
    survive being reached by `spec_gate_triage.py decide`, which is where the counts exist."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.2", "- R8.2.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_SPEC_PATH", str(spec))
    observations = spec.parent / "obs.json"
    classifications = spec.parent / "cls.json"
    observations.write_text(json.dumps({"observations": [
        {"id": "o1", "statement": "one"}, {"id": "o2", "statement": "two"},
    ]}), encoding="utf-8")
    classifications.write_text(json.dumps({"classifications": [
        {"id": "o1", "category": "note", "why": "fine"},
        {"id": "o2", "category": "contradiction", "why": "cannot both hold"},
    ]}), encoding="utf-8")

    done = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "spec_gate_triage.py"),
         "decide", str(observations), str(classifications)],
        capture_output=True, text=True, check=False,
    )

    assert done.returncode == 1  # BLOCKED, and the decision still reached stdout first
    assert json.loads(done.stdout)["verdict"] == "BLOCKED"
    call = stored(store, "08")["gate_calls"][0]
    assert "observations=2 blocking=1 notes=1" in call["note"]


# --- verification attempts, suite size, phase boundaries --------------------------------------------


def verdict_at(phase_dir: Path, attempt: int, name: str = "verdict.json") -> None:
    """The Verifier's own record, which is where the attempt number lives."""
    phase_dir.mkdir(parents=True, exist_ok=True)
    (phase_dir / name).write_text(
        json.dumps({"phase": phase_dir.name, "attempt": attempt, "verdict": "fail"}),
        encoding="utf-8",
    )


def test_the_attempt_is_derived_from_the_verdict_record_not_counted_per_call(stub_sink):  # noqa: F811
    """The measured defect: 8 recorded — three timed-out review calls plus five diagnostic
    retries — against a `verdict.json` correctly reading `attempt: 1` and a cap of 3 that had never
    fired. Read against that cap the number says the cap failed. Repeated calls must converge."""
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)

    assert metrics.open_verification_attempt(str(phase_dir)) == 1
    assert metrics.open_verification_attempt(str(phase_dir)) == 1
    assert metrics.open_verification_attempt(str(phase_dir)) == 1
    assert stored(store, "08")["verification_attempts"] == 1


def test_the_next_attempt_follows_the_verdict_on_record(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    verdict_at(phase_dir, 2)

    assert metrics.open_verification_attempt(str(phase_dir)) == 3
    assert stored(store, "08")["verification_attempts"] == 3


def test_archived_attempts_count_towards_it(stub_sink):  # noqa: F811
    """The archives are how the cap counts, so they are how the metric counts."""
    project, _, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    verdict_at(phase_dir, 1, "verdict-attempt-1.json")
    verdict_at(phase_dir, 2, "verdict-attempt-2.json")
    verdict_at(phase_dir, 3)

    assert metrics.open_verification_attempt(str(phase_dir)) == 4


def test_an_unreadable_verdict_record_records_nothing_rather_than_a_number(stub_sink):  # noqa: F811
    """Measurement fails open: an unreadable record leaves the stored value alone."""
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    (phase_dir / "verdict.json").write_text("{not json", encoding="utf-8")

    assert metrics.open_verification_attempt(str(phase_dir)) is None
    assert not (store / "phase-08.json").exists()


def test_the_suite_is_counted_the_same_way_at_both_ends(stub_sink):  # noqa: F811
    """`count_tests` counts collected pytest ITEMS, not `def test_` lines — issue #46.

    A parametrized function is one `def` and several collected items: the static count the emitter
    used to write disagreed with every suite run `hook_verifier.sh` itself reports, by exactly that
    gap (917/973 recorded against 1092/1164 observed in the phase that filed #46). One `def` with
    three parametrize cases below proves the field now carries the SAME population pytest itself
    reports, not merely a different but internally-consistent one.
    """
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    git_land(project, "open")

    metrics.record_phase_open(str(phase_dir))
    (tests / "test_b.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('n', [1, 2, 3])\n"
        "def test_two(n):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (phase_dir / "handover.md").write_text("landed", encoding="utf-8")
    git_land(project, "close")
    metrics.record_phase_close(str(phase_dir))

    record = stored(store, "08")
    assert record["tests_before"] == 1
    # 1 pre-existing item + 3 parametrize cases from one `def` — not 2 `def`s.
    assert record["tests_after"] == 4
    assert record["opened"] is not None and record["closed"] is not None
    assert record["elapsed_minutes"] == 0


def test_the_collection_is_bounded_the_way_every_child_on_a_hook_path_is(monkeypatch, tmp_path):
    """It runs inside `hook_spec_gate.sh`, so it goes through `proc_group.run_bounded`.

    `subprocess.run(cmd, capture_output=True, timeout=…)` stops the process it started and nothing
    else: xdist workers keep the inherited pipes open and the drain that follows the kill has no
    bound of its own, which turns the timeout into the unbounded hang that gets the whole hook
    killed — and a killed spec-gate hook reports no verdict at all.
    """
    (tmp_path / "tests").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("SUBPROC_CHECK_PATHS", raising=False)
    seen = {}

    def fake(cmd, timeout, cwd=None):
        seen.update(cmd=cmd, timeout=timeout, cwd=cwd)
        return proc_group.ChildResult(0, "7 tests collected in 0.1s\n", "", 0.1, False)

    monkeypatch.setattr(metrics.proc_group, "run_bounded", fake)

    assert metrics.count_tests() == 7
    assert seen["timeout"] == gate_timeouts.collect_timeout()
    assert seen["cwd"] == str(tmp_path)


def test_a_wedged_collection_counts_nothing_rather_than_taking_the_hook_with_it(
    monkeypatch, tmp_path
):
    """The smaller bound is the one that has to bind: a collection that blows it leaves the field
    absent — the meaning "not counted" already had — instead of the hook being killed mid-gate."""
    (tmp_path / "tests").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(
        metrics.proc_group,
        "run_bounded",
        lambda cmd, timeout, cwd=None: proc_group.ChildResult(
            -9, "300 tests collected in 60s\n", "", 61.0, True
        ),
    )

    assert metrics.count_tests() is None


def test_the_ignored_e2e_directory_follows_the_test_root(monkeypatch, tmp_path):
    """`--ignore=tests/e2e` was a literal, so a project pointing `SUBPROC_CHECK_PATHS` at
    `src/tests` counted `src/tests/e2e` items that the suite run it claims parity with excludes."""
    (tmp_path / "src" / "tests").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("SUBPROC_CHECK_PATHS", "src/tests")
    seen = {}

    def fake(cmd, timeout, cwd=None):
        seen["cmd"] = cmd
        return proc_group.ChildResult(0, "0 tests collected in 0.1s\n", "", 0.1, False)

    monkeypatch.setattr(metrics.proc_group, "run_bounded", fake)
    metrics.count_tests()

    assert f"--ignore={tmp_path / 'src' / 'tests' / 'e2e'}" in seen["cmd"]
    assert str(tmp_path / "src" / "tests") in seen["cmd"]


def test_pytest_is_the_one_on_path_when_there_is_one(monkeypatch):
    """`hook_verifier.sh` runs the binary off `PATH`. Where that pytest is not importable by the
    interpreter running this hook, `-m pytest` collects nothing and the field silently disappears —
    where the static count it replaced always produced a number."""
    monkeypatch.setattr(metrics.shutil, "which", lambda _name: "/usr/local/bin/pytest")
    assert metrics.pytest_argv() == ["/usr/local/bin/pytest"]

    monkeypatch.setattr(metrics.shutil, "which", lambda _name: None)
    assert metrics.pytest_argv() == [sys.executable, "-m", "pytest"]


# --- close is LANDED, not implemented — issue #46 -------------------------------------------------


def test_a_handover_write_alone_does_not_close_the_phase(stub_sink):  # noqa: F811
    """Reproduces the defect: a handover.md WRITE, with nothing committed, used to stamp `closed`.

    This is the guard going red on the exact shape #46 described — an amendment still open, a
    further Verifier finding, a suite hang blocking the commit, nothing pushed. Simulated here as
    "the phase directory is not committed at all", which is every one of those cases from the
    emitter's point of view: it has no commit to point at.
    """
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    (phase_dir / "verdict.json").write_text("{}", encoding="utf-8")
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "open")  # only the open is committed

    (phase_dir / "handover.md").write_text("draft", encoding="utf-8")  # NOT committed
    assert metrics.record_phase_close(str(phase_dir)) is False

    record = stored(store, "08")
    assert record["closed"] is None
    assert record["elapsed_minutes"] is None
    assert record["tests_after"] is None


def test_a_phase_with_no_repo_at_all_does_not_close_either(stub_sink):  # noqa: F811
    """A producer that cannot observe landing must not claim it — including when it cannot ask git."""
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    metrics.record_phase_open(str(phase_dir))

    (phase_dir / "handover.md").write_text("draft", encoding="utf-8")
    assert metrics.record_phase_close(str(phase_dir)) is False
    assert stored(store, "08")["closed"] is None


def test_the_close_lands_once_the_phase_directory_is_actually_committed(stub_sink):  # noqa: F811
    """The green case: committing the phase (`avenger-run.md` §5) is what unblocks the stamp."""
    project, store, _ = stub_sink
    git_init(project)
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    (phase_dir / "verdict.json").write_text("{}", encoding="utf-8")
    metrics.record_phase_open(str(phase_dir))
    git_land(project, "open")

    (phase_dir / "handover.md").write_text("landed", encoding="utf-8")
    git_land(project, "close")
    assert metrics.record_phase_close(str(phase_dir)) is True

    record = stored(store, "08")
    assert record["closed"] is not None


def test_opening_a_phase_twice_keeps_the_first_answer(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    metrics.record_phase_open(phase_dir)
    opened = stored(store, "08")["opened"]

    metrics.record_phase_open(phase_dir)

    assert stored(store, "08")["opened"] == opened


# --- plugin version: issue #65, riding on an existing key rather than a new one --------------------


def test_phase_open_records_which_plugin_copy_executed(stub_sink, monkeypatch):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")

    stale = plugin_release.DriftResult(
        status="stale", executing_version="0.10.2", source_version="0.10.3",
        executing_root=Path("/cache/0.10.2"), source_root=Path("/repo"), detail="drifted",
    )
    monkeypatch.setattr(plugin_release, "check", lambda *a, **k: stale)

    metrics.record_phase_open(phase_dir)

    calls = [c for c in stored(store, "08")["gate_calls"] if c["stage"] == metrics.PLUGIN_VERSION_STAGE]
    assert len(calls) == 1
    assert calls[0]["verdict"] == "NO-GO"
    assert "executing_version=0.10.2" in calls[0]["note"]
    assert "source_version=0.10.3" in calls[0]["note"]


def test_plugin_version_recording_never_adds_a_new_top_level_field(stub_sink):  # noqa: F811
    """firstmate's schema is closed and its producer contract is 'add no key'
    (pipeline-conventions §6d) — a new field is firstmate's decision, not this repo's. This must
    ride on an existing collection, the way `record_triage_decision` already does."""
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")

    metrics.record_phase_open(phase_dir)

    record = stored(store, "08")
    assert "plugin_version" not in record
    assert any(c["stage"] == metrics.PLUGIN_VERSION_STAGE for c in record["gate_calls"])


def test_plugin_version_recording_is_idempotent_across_repeated_opens(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")

    metrics.record_phase_open(phase_dir)
    metrics.record_phase_open(phase_dir)

    rows = [c for c in stored(store, "08")["gate_calls"] if c["stage"] == metrics.PLUGIN_VERSION_STAGE]
    assert len(rows) == 1


def test_plugin_version_recording_never_fails_the_phase_open(stub_sink, monkeypatch):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")

    def boom(*_a, **_k):
        raise RuntimeError("plugin_release blew up")

    monkeypatch.setattr(plugin_release, "check", boom)

    assert metrics.record_phase_open(phase_dir) is True
    assert stored(store, "08")["opened"] is not None


# --- which stage found each defect -------------------------------------------------------------------


def declared_finding_kinds() -> set[str]:
    """The finding vocabulary the verdict schema is actually told to emit.

    Read from `skills/verifier-triage`, which owns that schema. It used to be read from the
    cross-family reader's prompt; that pass is gone, and the schema is where the vocabulary lived
    all along — the prompt restated it.
    """
    schema = (ROOT / "skills" / "verifier-triage" / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r'"kind"\s*:\s*"([a-z|-]+)"', schema)
    assert match, "skills/verifier-triage no longer declares a `kind` vocabulary"
    return set(match.group(1).split("|"))


def test_every_finding_kind_the_verifier_emits_has_a_recorded_meaning():
    """Keyed on the emitted vocabulary, not on prose triage labels.

    An unmatched kind falls back to `real=True`, so a map keyed on words the Verifier never emits
    records every gamed test and every coverage gap as a genuine product defect — destroying the
    split `found_by` exists for. This asserts against the verdict schema itself, so changing the
    verifier's vocabulary moves the map instead of silently invalidating it.
    """
    assert set(metrics.VERIFIER_KIND_REAL) == declared_finding_kinds()


def test_verifier_findings_become_defects_attributed_to_the_verifier(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    verdict = phase_dir / "verdict.json"
    # The shape `skills/verifier-triage` writes: the verdict's findings, kinds and all.
    verdict.write_text(json.dumps({"verdict": "fail", "attempt": 1, "findings": [
        {"id": "aaa", "kind": "code", "spec_id": "R8.1.1", "target": "src/token.py",
         "severity": "blocker", "instruction": "off-by-one in the token window"},
        {"id": "bbb", "kind": "gamed-test", "spec_id": "R8.1.2",
         "target": "tests/demo/8-auth/8.1-sub/test_token.py", "severity": "major",
         "instruction": "test_window asserts the mock; assert through the public seam"},
        {"id": "ccc", "kind": "coverage-gap", "spec_id": "R8.1.3", "target": "R8.1.3",
         "severity": "blocker", "instruction": "no test exercises an expired token"},
    ]}), encoding="utf-8")

    assert metrics.record_verifier_findings(str(phase_dir), str(verdict)) == 3

    defects = {d["id"]: d for d in stored(store, "08")["defects"]}
    assert defects["verifier-aaa"]["found_by"] == "verifier"
    assert defects["verifier-aaa"]["real"] is True
    assert "off-by-one" in defects["verifier-aaa"]["summary"]   # the finding text, not its target
    assert defects["verifier-bbb"]["real"] is False   # a defect in the tests, not in the product
    assert defects["verifier-ccc"]["real"] is False


def test_mutation_records_what_it_found_without_inventing_identities(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    score = phase_dir / "score.json"
    score.write_text(json.dumps({"survivors": 4, "tested": 40, "score": 0.9}), encoding="utf-8")

    assert metrics.record_mutation_survivors(str(phase_dir), str(score)) is True

    defect = stored(store, "08")["defects"][0]
    assert defect["found_by"] == "mutation" and defect["real"] is False
    assert "4 of 40" in defect["summary"]


def test_a_clean_mutation_run_records_no_defect(stub_sink):  # noqa: F811
    project, _, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    score = phase_dir / "score.json"
    score.write_text(json.dumps({"survivors": 0, "tested": 40, "score": 1.0}), encoding="utf-8")

    assert metrics.record_mutation_survivors(str(phase_dir), str(score)) is False


# --- skill loads --------------------------------------------------------------------------------------


def test_a_load_is_recorded_with_its_evidence_and_its_requirement(stub_sink):  # noqa: F811
    _, store, _ = stub_sink

    metrics.record_skill_load(
        "08", stage="plan-build-verify:avenger-verifier", skill="verifier-triage",
        evidence="PostToolUse Read: skills/verifier-triage/SKILL.md",
    )

    entry = stored(store, "08")["skill_loads"][0]
    assert entry["id"] == "avenger-verifier:verifier-triage"   # the qualifier is not a second stage
    assert entry["required"] is True and entry["loaded"] is True
    assert "verifier-triage" in entry["evidence"]


def test_a_seed_never_unobserves_a_load(stub_sink):  # noqa: F811
    """Both writers are SubagentStart hooks and the harness may run them in either order."""
    _, store, _ = stub_sink
    metrics.record_skill_load("08", stage="avenger-verifier", skill="tdd", evidence="read it")

    metrics.record_skill_load("08", stage="avenger-verifier", skill="tdd",
                              evidence="", loaded=False)

    entry = stored(store, "08")["skill_loads"][0]
    assert entry["loaded"] is True and entry["evidence"] == "read it"


def test_a_required_skill_with_no_load_is_a_row_not_a_silence(stub_sink):  # noqa: F811
    _, store, _ = stub_sink

    metrics.record_skill_load("08", stage="avenger-verifier", skill="verifier-triage",
                              evidence="", loaded=False)

    entry = stored(store, "08")["skill_loads"][0]
    assert entry["required"] is True and entry["loaded"] is False


# --- the CLI cannot fail a phase -----------------------------------------------------------------------


def test_the_cli_exits_zero_when_the_record_cannot_be_written(stub_sink, monkeypatch):  # noqa: F811
    """A hook runs these; a non-zero exit here would stop the turn over a missing number."""
    project, _, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("DOUBLE_EXIT", "3")

    for args in (
        ("spec-round", str(spec)),
        ("gate-killed", "--stage", "spec-gate-observe", "--spec-path", str(spec)),
        ("verifier-attempt", str(spec.parents[2])),
        ("phase-open", str(spec)),
        ("phase-close", str(spec)),
    ):
        assert run_cli(*args).returncode == 0, args


def test_the_cli_exits_zero_with_no_writer_at_all(stub_sink, monkeypatch):  # noqa: F811
    project, _, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")

    assert run_cli("spec-round", str(spec)).returncode == 0


def test_a_usage_error_is_still_a_usage_error():
    """Fail-open covers emission, not a caller that typed the command wrong."""
    assert run_cli("no-such-command").returncode != 0


# --- `defect` is the deliberate exception: it must be loud when it fails (issue #66) --------------


DEFECT_ARGS = (
    "defect", "--phase-ref", "docs/features/demo/phases/08-slug",
    "--id", "D1", "--summary", "a real one", "--found-by", "execution",
)


def test_a_bare_defect_cli_invocation_records_with_no_human_intervention(stub_sink):  # noqa: F811
    """The happy path: a bare CLI invocation, inheriting only the fixture's environment, records."""
    project, store, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    result = run_cli(*DEFECT_ARGS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stored(store, "08")["defects"][0]["id"] == "D1"


def test_a_defect_that_cannot_be_written_because_the_writer_refuses_fails_loudly(stub_sink, monkeypatch):  # noqa: F811,E501
    """Break the recorder (the writer exits non-zero) and confirm the guard goes red, not green."""
    project, store, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("DOUBLE_EXIT", "3")

    result = run_cli(*DEFECT_ARGS)

    assert result.returncode != 0
    assert "D1" in result.stderr
    assert not (store / "phase-08.json").exists()


def test_a_writer_that_refuses_is_reported_as_retryable(stub_sink, monkeypatch):  # noqa: F811
    """A configured writer that failed the write is a cause the stage can fix, so it is told to."""
    project, _, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("DOUBLE_EXIT", "3")

    result = run_cli(*DEFECT_ARGS)

    assert "re-run this exact command" in result.stderr
    assert "DO NOT re-run" not in result.stderr


def test_a_defect_with_no_writer_configured_fails_loudly(stub_sink, monkeypatch):  # noqa: F811
    """Unset the writer entirely — the "unconfigured" half of the guard, not just "refused"."""
    project, _, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.delenv("AVENGER_METRICS_CMD", raising=False)
    monkeypatch.setenv("PATH", "")  # no fm-pipeline-metrics.sh reachable by any other name either

    result = run_cli(*DEFECT_ARGS)

    assert result.returncode != 0
    assert "D1" in result.stderr


def test_no_writer_configured_is_reported_as_terminal_not_retryable(stub_sink, monkeypatch):  # noqa: F811,E501
    """The remedy is the operator's, not the stage's: "fix the cause and re-run" here is a loop.

    A standalone install with no firstmate home is the documented normal state of this repo, so the
    stage has to be able to tell "your write failed, try again" from "nothing can record here".
    """
    project, _, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.delenv("AVENGER_METRICS_CMD", raising=False)
    monkeypatch.setenv("PATH", "")

    result = run_cli(*DEFECT_ARGS)

    assert "NO METRICS WRITER CONFIGURED" in result.stderr
    assert "DO NOT re-run" in result.stderr
    assert "re-run this exact command" not in result.stderr
    assert "AVENGER_METRICS_OFF=1" in result.stderr   # the other reachable resolution, named


def test_no_failure_marker_contains_another():
    """A stage discriminates on these, and substring matching is how it does it."""
    markers = (metrics.DEFECT_WRITE_FAILED, metrics.DEFECT_NO_WRITER, metrics.DEFECT_BAD_PHASE_REF)
    assert len(set(markers)) == len(markers)
    for marker in markers:
        assert [other for other in markers if marker in other] == [marker]


def test_an_unresolvable_phase_ref_is_the_arguments_fault_not_the_writers(stub_sink):  # noqa: F811
    """A working writer and a --phase-ref that names nothing: neither existing message is true.

    Routed through the write-failed shape, the stage is told the write failed and to re-run this
    exact command — which can only fail identically, because the command is what is wrong.
    """
    project, store, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    result = run_cli(
        "defect", "--phase-ref", "docs/features/demo/nowhere-in-particular",
        "--id", "D1", "--summary", "a real one", "--found-by", "execution",
    )

    assert result.returncode == metrics.USAGE_ERROR
    assert metrics.DEFECT_BAD_PHASE_REF in result.stderr
    assert metrics.DEFECT_WRITE_FAILED not in result.stderr
    assert metrics.DEFECT_NO_WRITER not in result.stderr
    assert "re-run this exact command" not in result.stderr
    assert "DO NOT re-run it" not in result.stderr   # the OTHER shape's instruction, verbatim
    assert "D1" in result.stderr and "nowhere-in-particular" in result.stderr
    assert not (store / "phase-08.json").exists()


def test_an_unresolvable_phase_ref_stays_loud_when_metrics_are_off(stub_sink, monkeypatch):  # noqa: F811,E501
    """`AVENGER_METRICS_OFF=1` is a choice about RECORDING; it does not make a bad argument fine."""
    project, _, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")

    result = run_cli(
        "defect", "--phase-ref", "docs/features/demo/nowhere-in-particular",
        "--id", "D1", "--summary", "a real one", "--found-by", "execution",
    )

    assert result.returncode == metrics.USAGE_ERROR
    assert metrics.DEFECT_BAD_PHASE_REF in result.stderr


def test_a_named_but_unexecutable_writer_is_retryable_with_a_true_cause(stub_sink, monkeypatch, tmp_path):  # noqa: F811,E501
    """The one state where "configured" and "resolvable" disagree — and the loop it used to cause.

    `configured()` says yes, so the stage is told to fix the cause and re-run; the cause line it is
    sent to must therefore name the exec bit, not an unset variable the operator has already set.
    """
    project, _, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    inert = tmp_path / "inert-fm-pipeline-metrics.sh"
    inert.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    inert.chmod(0o600)
    monkeypatch.setenv("AVENGER_METRICS_CMD", str(inert))

    result = run_cli(*DEFECT_ARGS)

    assert result.returncode != 0
    assert metrics.DEFECT_WRITE_FAILED in result.stderr
    assert "not an executable file" in result.stderr
    assert "AVENGER_METRICS_CMD is unset" not in result.stderr


def test_a_defect_stays_silent_when_metrics_are_deliberately_off(stub_sink, monkeypatch):  # noqa: F811,E501
    """`AVENGER_METRICS_OFF=1` is a configured choice, not a failure — it must not turn loud."""
    project, _, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")

    result = run_cli(*DEFECT_ARGS)

    assert result.returncode == 0
    assert result.stderr == ""


# --- driven through the real gate runner, which is where every gate call passes -------------------------

#: A provider that answers, and one that refuses for billing reasons — the shape once read as a
#: model rejection, and the reason `failure_cause` exists at all.
PROVIDERS = {
    "good": '#!/bin/sh\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n',
    "payment": '#!/bin/sh\necho \'{"error":{"code":402,"message":"Insufficient credits"}}\' >&2\n'
               'exit 1\n',
}


def run_gate(project: Path, spec: Path, provider: str, stdout=None) -> subprocess.CompletedProcess:
    """The real runner, with `opencode` stubbed on PATH — no model is called.

    `stdout` sends the gate's own output to a file instead of a pipe, which is how the ordering
    between what the gate says and what it then measures can be observed at all.
    """
    binary = project / "bin" / "opencode"
    binary.parent.mkdir(exist_ok=True)
    binary.write_text(PROVIDERS[provider], encoding="utf-8")
    binary.chmod(0o755)
    rubric = project / "spec-gate-observe.md"
    rubric.write_text("Return JSON with a verdict.", encoding="utf-8")
    streams = ({"capture_output": True} if stdout is None
               else {"stdout": stdout, "stderr": subprocess.DEVNULL})
    return subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "gate_runner.py"),
         "--rubric", str(rubric), "--target", str(spec), "--author-family", "anthropic",
         "--model", "deepseek/deepseek-chat"],
        text=True, check=False,
        # The stub answers in milliseconds — what gate_plausibility.py refuses as a call that
        # cannot have happened. Switched off here so these tests pin the metrics emission rather
        # than the floor; tests/test_gate_plausibility.py drives the floor itself end to end.
        env={**os.environ, "PATH": f"{binary.parent}:{os.environ['PATH']}", "HOME": str(project),
             "GATE_MIN_LATENCY_MS": "0"},
        **streams,
    )


def test_the_runner_records_the_call_it_just_made(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    assert run_gate(project, spec, "good").returncode == 0

    call = stored(store, "08")["gate_calls"][0]
    assert call["stage"] == "spec-gate-observe" and call["spec"] == "8.1"
    assert call["model"] == "deepseek/deepseek-chat" and call["model_family"] == "deepseek"
    assert call["verdict"] == "GO" and call["failure_cause"] is None
    assert isinstance(call["latency_ms"], int) and call["latency_ms"] >= 0


def test_the_runner_records_a_failure_with_the_cause_it_named(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    result = run_gate(project, spec, "payment")

    assert result.returncode == 2 and "cause=provider-payment-required" in result.stderr
    call = stored(store, "08")["gate_calls"][0]
    assert call["verdict"] == "error" and call["failure_cause"] == "http-402"


def test_a_gate_call_that_cannot_be_recorded_still_returns_its_verdict(stub_sink, monkeypatch):  # noqa: F811
    """The property that outranks measuring anything: the gate's answer is unaffected."""
    project, _, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    monkeypatch.setenv("DOUBLE_EXIT", "3")

    result = run_gate(project, spec, "good")

    assert result.returncode == 0 and result.stdout.strip() == "OK (GO)"


def test_the_gate_answers_before_it_measures_itself(stub_sink, monkeypatch, tmp_path):  # noqa: F811
    """Delivery first, measurement second — the same invariant, in wall clock rather than exit code.

    A blocked writer costs a full metrics timeout per process, and a hook's budget is the call
    timeout plus a fixed headroom. Measuring ahead of the answer lets a hung writer push a
    near-budget gate past the harness's limit, so the thing that failed the phase is the
    measurement. Both the gate's stdout and the writer's own trace land in one file here, so the
    order they happened in is the order of its lines.
    """
    project, _, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    ordered = tmp_path / "ordered.log"
    monkeypatch.setenv("DOUBLE_LOG", str(ordered))

    with open(ordered, "w", encoding="utf-8") as fh:
        assert run_gate(project, spec, "good", stdout=fh).returncode == 0

    lines = ordered.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "OK (GO)"
    assert any("gate_calls" in line for line in lines[1:])


# --- the records this emits validate against firstmate's own schema -------------------------------------


def test_a_populated_record_validates(real_sink):  # noqa: F811
    """The claim the double cannot make: what this pipeline writes is a valid v1 record."""
    project, home = real_sink
    git_init(project)
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n- R8.1.2 two\n")
    phase_dir = str(spec.parents[2])
    (project / "tests").mkdir()
    (project / "tests" / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    git_land(project, "open")

    metrics.record_phase_open(phase_dir)
    metrics.record_spec_round(str(spec))
    metrics.record_gate_call(model="deepseek/deepseek-chat", model_family="deepseek",
                             rubric="prompts/spec-gate-observe.md", target=str(spec),
                             latency_ms=106_000, verdict="GO", provider="opencode")
    metrics.record_gate_call(model="deepseek/deepseek-chat", model_family="deepseek",
                             rubric="prompts/spec-gate-triage.md", target=str(spec),
                             latency_ms=143_000, cause="timeout", detail="killed",
                             provider="opencode")
    metrics.open_verification_attempt(phase_dir)
    metrics.record_skill_load("08", stage="avenger-verifier", skill="verifier-triage",
                              evidence="PostToolUse Read")
    metrics.record_skill_load("08", stage="avenger-verifier", skill="tdd",
                              evidence="", loaded=False)
    metrics.record_defect("08", identifier="D1", summary="a real one", found_by="execution",
                          real=True, stage_reached="verification", severity="security")
    git_land(project, "close")
    metrics.record_phase_close(phase_dir)

    result = subprocess.run(  # noqa: S603
        [os.environ["AVENGER_METRICS_CMD"], "validate", "08"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    record = stored(home, "08")
    assert record["specs"][0]["requirements"] == 2
    assert record["spec_rounds"] == 1 and record["verification_attempts"] == 1
    assert record["tests_before"] == 1 and record["tests_after"] == 1
    gate_calls = [c for c in record["gate_calls"] if c["stage"] != metrics.PLUGIN_VERSION_STAGE]
    assert {c["verdict"] for c in gate_calls} == {"GO", "killed"}
    assert record["defects"][0]["found_by"] == "execution"


def test_the_plugin_version_row_survives_the_real_writers_closed_verdict_enum(real_sink, monkeypatch):  # noqa: F811,E501
    """The claim the double cannot make, for the row issue #65 exists to write.

    `gate_calls[].verdict` is a closed enum firstmate owns, and its writer refuses a row `validate`
    would refuse. A drift status carried through verbatim ("STALE") is not in that enum, so the row
    was dropped, the refusal was swallowed by the fail-open path, and the executing version was
    recorded nowhere — invisible to every `stub_sink` test, because the double enforces no schema.
    """
    project, home = real_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    stale = plugin_release.DriftResult(
        status="stale", executing_version="0.10.2", source_version="0.10.3",
        executing_root=Path("/cache/0.10.2"), source_root=Path("/repo"), detail="drifted",
    )
    monkeypatch.setattr(plugin_release, "check", lambda *a, **k: stale)

    metrics.record_phase_open(str(spec.parents[2]))

    rows = [c for c in stored(home, "08")["gate_calls"]
            if c["stage"] == metrics.PLUGIN_VERSION_STAGE]
    assert len(rows) == 1
    assert rows[0]["verdict"] == "NO-GO"
    assert "status=stale" in rows[0]["note"] and "executing_version=0.10.2" in rows[0]["note"]

    result = subprocess.run(  # noqa: S603
        [os.environ["AVENGER_METRICS_CMD"], "validate", "08"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_drift_status_maps_onto_the_verdict_enum_firstmate_owns():
    """A status added to `plugin_release` must not silently reintroduce an out-of-enum verdict."""
    allowed = {"GO", "REVIEW", "NO-GO", "error", "killed"}
    assert set(metrics.PLUGIN_VERSION_VERDICTS.values()) <= allowed
    assert set(metrics.PLUGIN_VERSION_VERDICTS) == {"fresh", "stale", "unknown"}


def test_a_defect_found_by_other_carries_its_note(real_sink):  # noqa: F811
    """`found_by: other` without a note is refused by the validator, so it is never emitted bare."""
    _, home = real_sink

    metrics.record_defect("08", identifier="D9", summary="odd one", found_by="other",
                          real=True, stage_reached="review", severity="cosmetic")

    result = subprocess.run(  # noqa: S603
        [os.environ["AVENGER_METRICS_CMD"], "validate", "08"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert stored(home, "08")["defects"][0]["found_by_note"]


# --- recorded_by: the pipeline says the PIPELINE wrote it down ------------------------------------


def test_every_route_that_records_a_defect_stamps_the_pipeline_as_the_recorder(stub_sink):  # noqa: F811,E501
    """All three routes, because a route left unstamped reads as a record predating the field.

    `recorded_by` has three answers and absence is one of them: it means the record is older than
    the field, and a reader must not resolve it to either value. So a defect this pipeline emitted
    but did not stamp is not "unlabelled", it is labelled as something else entirely — and it is
    exactly the phases whose defects a person transcribed by hand that absence is reserved for.
    """
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    verdict = phase_dir / "verdict.json"
    verdict.write_text(json.dumps({"verdict": "fail", "attempt": 1, "findings": [
        {"id": "aaa", "kind": "code", "target": "src/token.py", "instruction": "off-by-one"},
    ]}), encoding="utf-8")
    score = phase_dir / "score.json"
    score.write_text(json.dumps({"survivors": 4, "tested": 40, "score": 0.9}), encoding="utf-8")

    metrics.record_defect("08", identifier="D1", summary="caught by hand-run seam",
                          found_by="execution", real=True, stage_reached="verification",
                          severity="security")
    metrics.record_verifier_findings(str(phase_dir), str(verdict))
    metrics.record_mutation_survivors(str(phase_dir), str(score))

    defects = stored(store, "08")["defects"]
    assert {d["id"] for d in defects} == {"D1", "verifier-aaa", "mutation-survivors"}
    assert [d["recorded_by"] for d in defects] == ["stage"] * 3


def test_no_defect_reaches_the_record_except_through_the_stamping_point():
    """The guard against a FOURTH route: one `defects` write, so a new one cannot skip the stamp.

    Stamping every current route says nothing about the next one. `record_defect` is the single
    write, and this is what keeps it single — a new emission point either goes through it and is
    stamped, or turns this red.
    """
    source = (Path(__file__).resolve().parents[1] / "scripts" / "pipeline_metrics.py").read_text(
        encoding="utf-8"
    )
    assert source.count('"defects"') == 1
    assert 'sink.add(phase, "defects", _optional=DEFECT_OPTIONAL_FIELDS, **fields)' in source


def test_recorded_by_is_never_derived_from_what_caught_the_defect(stub_sink):  # noqa: F811
    """Two questions, two fields. `found_by` varies; the recorder does not."""
    _, store, _ = stub_sink

    for index, found_by in enumerate(("breaker", "mutation", "ci", "human-review")):
        metrics.record_defect("08", identifier=f"D{index}", summary=found_by, found_by=found_by,
                              real=True, stage_reached="implementation", severity="correctness")

    defects = stored(store, "08")["defects"]
    assert {d["found_by"] for d in defects} == {"breaker", "mutation", "ci", "human-review"}
    assert {d["recorded_by"] for d in defects} == {"stage"}


def test_a_firstmate_too_old_for_the_field_loses_the_field_and_keeps_the_defect(
    stub_sink, monkeypatch, capsys,  # noqa: F811
):
    """Version skew costs the measurement, never the entry.

    firstmate's key surface is CLOSED — a writer that predates `recorded_by` refuses the whole entry
    over it, taking `found_by` down with it, and `found_by` is the one field in the record that
    cannot be recovered after the run. So the write is retried without the key the caller named as
    droppable, the defect lands, and the loss is said out loud on stderr.
    """
    _, store, log = stub_sink
    monkeypatch.setenv("DOUBLE_REFUSE_KEY", "recorded_by")

    assert metrics.record_defect("08", identifier="D1", summary="a real one",
                                 found_by="execution", real=True, stage_reached="verification",
                                 severity="security") is True

    defect = stored(store, "08")["defects"][0]
    assert "recorded_by" not in defect          # absent, not guessed at, and never `null`
    assert defect["found_by"] == "execution" and defect["summary"] == "a real one"

    writes = [call for call in read_calls(log) if call[:1] == ["add"]]
    assert len(writes) == 2                     # the stamped write, then one retry without the key
    assert any(field.startswith("recorded_by=") for field in writes[0])
    assert not any(field.startswith("recorded_by=") for field in writes[1])

    captured = capsys.readouterr()
    assert captured.out == ""                   # every caller's stdout is somebody's protocol
    assert "recorded_by" in captured.err        # a lost measurement is never a silent one


def test_a_refusal_that_is_not_about_the_new_field_still_fails(stub_sink, monkeypatch, capsys):  # noqa: F811,E501
    """The downgrade is one retry of a named key, not a blanket "try again without the hard parts".

    A writer refusing every `add` refuses the retry too, so the emission fails exactly as it did
    before the field existed — `defect`'s loud exit still has something to be loud about.
    """
    _, _, log = stub_sink
    monkeypatch.setenv("DOUBLE_REFUSE", "add")

    assert metrics.record_defect("08", identifier="D1", summary="a real one",
                                 found_by="execution", real=True, stage_reached="verification",
                                 severity="security") is False

    assert capsys.readouterr().out == ""
    assert len([call for call in read_calls(log) if call[:1] == ["add"]]) == 2


def test_the_real_writer_validates_a_defect_this_pipeline_stamped(real_sink):  # noqa: F811
    """The claim the double cannot make: `recorded_by: stage` is a value firstmate's enum accepts."""
    _, home = real_sink

    assert metrics.record_defect("08", identifier="D1", summary="a real one",
                                 found_by="execution", real=True, stage_reached="verification",
                                 severity="security") is True

    result = subprocess.run(  # noqa: S603
        [os.environ["AVENGER_METRICS_CMD"], "validate", "08"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert stored(home, "08")["defects"][0]["recorded_by"] == "stage"


# --- the mutation gate leaves a record when it could NOT run (agents#16, class of #69) ------------
#
# `MUTATION_POLICY=advisory` was set for two whole phases while the gate never once ran: no
# cosmic-ray.toml at the repo root and the tool not installed. The hook said so on stderr and exited
# 0 - correctly, because advisory never blocks - but nothing durable distinguished "the gate did not
# run" from "the gate ran and found nothing". A settled hypothesis was then read as evidence that
# `MUTATION_POLICY=advisory` is the lever, when the measured lever was hand-built drills the
# implementer substituted after the gate produced nothing.
#
# Advisory must keep NOT blocking - that is a deliberate decision, not the defect. What changes is
# that the absence is now recorded where a later reader looks.


def test_a_mutation_gate_that_could_not_run_records_that_it_did_not(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    metrics.record_phase_open(phase_dir)

    assert metrics.record_mutation_unavailable(
        phase_dir, "cosmic-ray.toml missing at repo root"
    ) is True

    (row,) = [
        c for c in stored(store, "08")["gate_calls"]
        if c["stage"] == metrics.MUTATION_STAGE
    ]
    assert row["verdict"] == metrics.NO_VERDICT, "a gate that did not run reached no verdict"
    assert row["failure_cause"] == metrics.MUTATION_UNAVAILABLE_CAUSE
    assert "cosmic-ray.toml missing" in row["note"]


def test_it_rides_an_existing_collection_and_adds_no_field(stub_sink):  # noqa: F811
    """firstmate's schema is closed; a new key is their decision, not this repo's."""
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    metrics.record_phase_open(phase_dir)

    metrics.record_mutation_unavailable(phase_dir, "cosmic-ray not installed")

    record = stored(store, "08")
    assert "mutation" not in record and "mutation_unavailable" not in record


def test_repeated_reports_converge_on_one_row(stub_sink):  # noqa: F811
    """The producer contract: repetition converges. A hook fires per handover write."""
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    metrics.record_phase_open(phase_dir)

    metrics.record_mutation_unavailable(phase_dir, "cosmic-ray not installed")
    metrics.record_mutation_unavailable(phase_dir, "cosmic-ray not installed")

    rows = [c for c in stored(store, "08")["gate_calls"] if c["stage"] == metrics.MUTATION_STAGE]
    assert len(rows) == 1


def test_recording_the_absence_never_fails_the_phase(stub_sink, monkeypatch):  # noqa: F811
    """Measurement may never fail what it measures - and least of all an advisory gate."""
    project, _, _ = stub_sink
    monkeypatch.setattr(metrics.sink, "add", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))

    assert metrics.record_mutation_unavailable(
        str(project / "docs/features/demo/phases/8-auth"), "anything"
    ) is False


def test_the_cli_reports_it_and_always_exits_zero(stub_sink):  # noqa: F811
    """Called from `hook_mutation.sh`, where nothing may block on a metrics write."""
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    metrics.record_phase_open(phase_dir)

    result = run_cli("mutation-unavailable", phase_dir, "cosmic-ray exec errored")

    assert result.returncode == 0
    rows = [c for c in stored(store, "08")["gate_calls"] if c["stage"] == metrics.MUTATION_STAGE]
    assert rows and "exec errored" in rows[0]["note"]


# ── a stage that CONCLUDES a defect emits it, at the moment it concludes ─────────────────────────


def test_findings_from_a_superseded_attempt_are_recorded_too(stub_sink):  # noqa: F811
    """Phase 12 recorded ONE defect against at least five it produced, four of them found by the
    Verifier by executing code on attempt 1.

    The reason is this shape exactly: `skills/verifier-triage` archives a superseded attempt to
    `verdict-attempt-<n>.json` and leaves only its number in `verdict.json`, so the attempt that
    actually concluded the defects is not the file the close-time emission read. A passing verdict
    carries no findings at all, so the phase closed reporting none.
    """
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/12-poll"
    phase_dir.mkdir(parents=True)
    (phase_dir / "verdict-attempt-1.json").write_text(json.dumps({"attempt": 1, "verdict": "fail",
        "findings": [
            {"id": "aaa", "kind": "code", "instruction": "poll before sleep opens a second session"},
            {"id": "bbb", "kind": "code", "instruction": "InvalidToken raised outside the catch"},
        ]}), encoding="utf-8")
    verdict = phase_dir / "verdict.json"
    verdict.write_text(json.dumps({"attempt": 2, "verdict": "pass", "findings": []}), "utf-8")

    assert metrics.record_verifier_findings(str(phase_dir), str(verdict)) == 2

    defects = {d["id"]: d for d in stored(store, "12")["defects"]}
    assert set(defects) == {"verifier-aaa", "verifier-bbb"}
    assert all(d["found_by"] == "verifier" for d in defects.values())


def test_re_emitting_the_same_findings_converges(stub_sink):  # noqa: F811
    """The emission fires per verdict write AND once at the handover, so it repeats by design."""
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/12-poll"
    phase_dir.mkdir(parents=True)
    verdict = phase_dir / "verdict.json"
    verdict.write_text(json.dumps({"attempt": 1, "verdict": "fail", "findings": [
        {"id": "aaa", "kind": "code", "instruction": "one"}]}), encoding="utf-8")

    metrics.record_verifier_findings(str(phase_dir), str(verdict))
    metrics.record_verifier_findings(str(phase_dir), str(verdict))

    assert len(stored(store, "12")["defects"]) == 1


def test_closing_a_phase_twice_keeps_the_first_answer(stub_sink):  # noqa: F811
    """A closed record SEALS its measurements, so a second stamp is drift, not a repeat.

    The emission points fire more than once by design — `hook_phase_close.sh` on every commit that
    touches the phase, and the orchestrator's own `phase-close` — and firstmate's producer contract
    is that repetition CONVERGES. Re-stamping would ask a sealed record to change a value it already
    holds, which the real writer refuses outright.
    """
    project, store, log = stub_sink
    git_init(project)
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    (phase_dir / "verdict.json").write_text("{}", encoding="utf-8")
    metrics.record_phase_open(str(phase_dir))
    (phase_dir / "handover.md").write_text("landed", encoding="utf-8")
    git_land(project, "land")
    assert metrics.record_phase_close(str(phase_dir)) is True
    closed = stored(store, "08")["closed"]
    before = len([call for call in read_calls(log) if call[:1] == ["set"]])

    assert metrics.record_phase_close(str(phase_dir)) is True

    assert stored(store, "08")["closed"] == closed
    assert len([call for call in read_calls(log) if call[:1] == ["set"]]) == before, (
        "a phase already closed issues no second write at all"
    )
