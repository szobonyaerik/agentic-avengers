"""Tests for the facts the pipeline emits about itself, at the points it observes them.

Three properties are load-bearing and each has its own group below.

**Emitted during the run.** Nothing here reconstructs anything at the end, so every emission point
is driven the way its caller drives it and the record is read straight off disk afterwards.

**Never able to fail a phase.** The CLI is asserted to exit 0 with the record unwritable, which is
the shape a real failure takes: a hook runs `pipeline_metrics.py …` and a non-zero exit there would
stop the turn.

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

from metrics_support import read_calls, real_sink, stored, stub_sink, write_spec  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_errors  # noqa: E402
import pipeline_metrics as metrics  # noqa: E402

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
    assert metrics.stage_from_rubric("prompts/fidelity-rubric.md") == "fidelity"
    assert metrics.stage_from_rubric("prompts/verifier-review.md") == "verifier"
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
        rubric="prompts/fidelity-rubric.md", target=str(spec),
        latency_ms=106_000, verdict="GO", provider="opencode",
    )

    call = stored(store, "08")["gate_calls"][0]
    assert call["id"] == "8.1-a1-fidelity"
    assert call["stage"] == "fidelity" and call["spec"] == "8.1"
    assert call["model_family"] == "deepseek" and call["latency_ms"] == 106_000
    assert call["verdict"] == "GO" and call["failure_cause"] is None


def test_a_killed_gate_is_not_a_verdict(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    metrics.record_gate_call(
        model="m", rubric="prompts/fidelity-rubric.md", target=str(spec),
        latency_ms=143_000, cause="timeout", detail="killed after 143.0s", provider="opencode",
    )

    call = stored(store, "08")["gate_calls"][0]
    assert call["verdict"] == "killed" and call["failure_cause"] == "timeout"
    assert "143.0s" in call["note"]


def test_a_second_round_is_a_second_gate_call_not_an_overwrite(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    metrics.record_spec_round(str(spec))
    metrics.record_gate_call(model="m", rubric="prompts/fidelity-rubric.md", target=str(spec),
                             latency_ms=1, verdict="NO-GO")

    spec.write_text("---\nfeature: demo\n---\n- R8.1.1 rewritten\n", encoding="utf-8")
    metrics.record_spec_round(str(spec))
    metrics.record_gate_call(model="m", rubric="prompts/fidelity-rubric.md", target=str(spec),
                             latency_ms=2, verdict="GO")

    calls = stored(store, "08")["gate_calls"]
    assert [c["id"] for c in calls] == ["8.1-a1-fidelity", "8.1-a2-fidelity"]


def test_a_call_outside_any_phase_records_nothing(stub_sink):  # noqa: F811
    assert metrics.record_gate_call(model="m", rubric="r.md", target="/tmp/x.md") is False


def test_the_spec_path_wins_over_a_temp_bundle_target(stub_sink, monkeypatch):  # noqa: F811
    """spec-review judges a diff bundle in /tmp; only the caller knows which spec that is."""
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.3", "- R8.3.1 one\n")
    monkeypatch.setenv("AVENGER_METRICS_SPEC_PATH", str(spec))

    metrics.record_gate_call(model="m", rubric="prompts/spec-review-rubric.md",
                             target="/tmp/bundle.XXXX", latency_ms=5, verdict="GO")

    call = stored(store, "08")["gate_calls"][0]
    assert call["spec"] == "8.3" and call["stage"] == "spec-review"


# --- verification attempts, suite size, phase boundaries --------------------------------------------


def test_each_verification_attempt_is_counted(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")

    assert metrics.open_verification_attempt(phase_dir) == 1
    assert metrics.open_verification_attempt(phase_dir) == 2
    assert stored(store, "08")["verification_attempts"] == 2


def test_the_suite_is_counted_the_same_way_at_both_ends(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")

    metrics.record_phase_open(phase_dir)
    (tests / "test_b.py").write_text(
        "async def test_two():\n    pass\ndef test_three():\n    pass\n", encoding="utf-8"
    )
    metrics.record_phase_close(phase_dir)

    record = stored(store, "08")
    assert record["tests_before"] == 1 and record["tests_after"] == 3
    assert record["opened"] is not None and record["closed"] is not None
    assert record["elapsed_minutes"] == 0


def test_opening_a_phase_twice_keeps_the_first_answer(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = str(project / "docs/features/demo/phases/8-auth")
    metrics.record_phase_open(phase_dir)
    opened = stored(store, "08")["opened"]

    metrics.record_phase_open(phase_dir)

    assert stored(store, "08")["opened"] == opened


# --- which stage found each defect -------------------------------------------------------------------


def declared_finding_kinds() -> set[str]:
    """The finding vocabulary the cross-family reader is actually told to emit."""
    prompt = (ROOT / "prompts" / "verifier-review.md").read_text(encoding="utf-8")
    match = re.search(r'"kind"\s*:\s*"([a-z|-]+)"', prompt)
    assert match, "prompts/verifier-review.md no longer declares a `kind` vocabulary"
    return set(match.group(1).split("|"))


def test_every_finding_kind_the_verifier_emits_has_a_recorded_meaning():
    """Keyed on the emitted vocabulary, not on prose triage labels.

    An unmatched kind falls back to `real=True`, so a map keyed on words the reader never emits
    records every gamed test and every coverage gap as a genuine product defect — destroying the
    split `found_by` exists for. This asserts against the schema in the prompt itself, so changing
    the verifier's vocabulary moves the map instead of silently invalidating it.
    """
    assert set(metrics.VERIFIER_KIND_REAL) == declared_finding_kinds()


def test_verifier_findings_become_defects_attributed_to_the_verifier(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    phase_dir = project / "docs/features/demo/phases/8-auth"
    phase_dir.mkdir(parents=True)
    verdict = phase_dir / ".verifier-review.json"
    # The shape `verifier_review.sh` actually hands over: the verdict.json findings of
    # skills/verifier-triage, kinds and all.
    verdict.write_text(json.dumps({"verdict": "NO-GO", "route_back": "Implementer", "findings": [
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
        ("gate-killed", "--stage", "fidelity", "--spec-path", str(spec)),
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
    rubric = project / "fidelity-rubric.md"
    rubric.write_text("Return JSON with a verdict.", encoding="utf-8")
    streams = ({"capture_output": True} if stdout is None
               else {"stdout": stdout, "stderr": subprocess.DEVNULL})
    return subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "gate_runner.py"),
         "--rubric", str(rubric), "--target", str(spec), "--author-family", "anthropic",
         "--model", "deepseek/deepseek-chat"],
        text=True, check=False,
        env={**os.environ, "PATH": f"{binary.parent}:{os.environ['PATH']}", "HOME": str(project)},
        **streams,
    )


def test_the_runner_records_the_call_it_just_made(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n")

    assert run_gate(project, spec, "good").returncode == 0

    call = stored(store, "08")["gate_calls"][0]
    assert call["stage"] == "fidelity" and call["spec"] == "8.1"
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
    spec = write_spec(project, 8, "8.1", "- R8.1.1 one\n- R8.1.2 two\n")
    phase_dir = str(spec.parents[2])
    (project / "tests").mkdir()
    (project / "tests" / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")

    metrics.record_phase_open(phase_dir)
    metrics.record_spec_round(str(spec))
    metrics.record_gate_call(model="deepseek/deepseek-chat", model_family="deepseek",
                             rubric="prompts/fidelity-rubric.md", target=str(spec),
                             latency_ms=106_000, verdict="GO", provider="opencode")
    metrics.record_gate_call(model="deepseek/deepseek-chat", model_family="deepseek",
                             rubric="prompts/spec-review-rubric.md", target=str(spec),
                             latency_ms=143_000, cause="timeout", detail="killed",
                             provider="opencode")
    metrics.open_verification_attempt(phase_dir)
    metrics.record_skill_load("08", stage="avenger-verifier", skill="verifier-triage",
                              evidence="PostToolUse Read")
    metrics.record_skill_load("08", stage="avenger-verifier", skill="tdd",
                              evidence="", loaded=False)
    metrics.record_defect("08", identifier="D1", summary="a real one", found_by="execution",
                          real=True, stage_reached="verification", severity="security")
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
    assert {c["verdict"] for c in record["gate_calls"]} == {"GO", "killed"}
    assert record["defects"][0]["found_by"] == "execution"


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
