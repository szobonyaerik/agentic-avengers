"""A gate verdict that came back faster than the call can be made is refused, not consumed.

## The defect this pins

Phase 10 of one measured feature records a **4 ms GO** from a model gate. Four milliseconds is less
time than the provider CLI takes to start, let alone a network round trip to a hosted model. A sweep
of all 178 gate calls across phases 08-11 found it the only implausible latency attached to a
**passing** verdict - exactly the shape a gate that never ran would take. It was consumed as a pass.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** Every test below first builds the state that must
be refused and asserts the refusal, then defeats the enforcement - by moving the floor, which is the
documented escape hatch - and asserts the SAME input is then accepted. That second half is what
proves the floor is load-bearing rather than incidental: without it, a test that only ever saw
rejections could not tell a working floor from a runner that rejects everything.

The provider is a shell stub on PATH, so no model is called and the runner cannot tell the
difference - which is itself the point: a stub that answers in 4 ms is the artifact under test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "gate_runner.py"
sys.path.insert(0, str(ROOT / "scripts"))

import gate_plausibility as gp  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the subject is what the real runner does with a real provider process that answers instantly, "
    "which is the shape the 4 ms GO had"
)

#: A provider that answers correctly and as fast as a shell can. Nothing about the reply is wrong;
#: the only thing wrong with it is that it could not have taken as long as a real call.
INSTANT_GO = '#!/bin/sh\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n'

#: The floor the end-to-end refusals run at, and why it is not the shipped default. A stub answers in
#: single-digit milliseconds on an idle machine and in hundreds under a loaded CI run, so pinning the
#: refusal to the 250 ms default would make these tests a race against the machine - and a gate test
#: that fails at random teaches people to re-run it. The floor is therefore set to something no
#: subprocess can clear, which makes the END-TO-END behaviour deterministic; that the SHIPPED default
#: refuses the measured 4 ms is asserted directly against the predicate below, where no clock is
#: involved. Between them the two halves cover what one flaky test tried to.
UNCLEARABLE_MS = "60000"

#: The same, but NO-GO. A refusal returned in 4 ms did not run either, and the rule says so.
INSTANT_NOGO = '#!/bin/sh\necho \'{"verdict":"NO-GO","report":"nope","route_back":"impl"}\'\n'

#: A provider that takes long enough to be a real call under the default floor.
SLOW_GO = ('#!/bin/sh\nsleep 0.4\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n')


@pytest.fixture
def gate(tmp_path: Path):
    """The real runner, with `opencode` stubbed on PATH."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "rubric.md").write_text("Return JSON with a verdict.", encoding="utf-8")
    (tmp_path / "spec.md").write_text("---\nfeature: demo\n---\n\n- R1.1.1 a\n", encoding="utf-8")

    def run(stub: str, author_family: str = "anthropic", **env_over) -> subprocess.CompletedProcess:
        binary = tmp_path / "bin" / "opencode"
        binary.write_text(stub, encoding="utf-8")
        binary.chmod(0o755)
        env = {
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),   # empty, so no repo .env leaks in
            **env_over,
        }
        return subprocess.run(  # noqa: S603
            [sys.executable, str(RUNNER), "--rubric", str(tmp_path / "rubric.md"),
             "--target", str(tmp_path / "spec.md"), "--author-family", author_family,
             "--model", "deepseek/deepseek-chat"],
            capture_output=True, text=True, env=env, check=False,
        )

    return run


# ── the refusal, end to end ──────────────────────────────────────────────────


def test_an_instant_go_is_refused_and_the_same_reply_passes_once_the_floor_is_defeated(gate) -> None:
    """RED then GREEN on one byte-identical provider reply. The floor is the only variable."""
    refused = gate(INSTANT_GO, GATE_MIN_LATENCY_MS=UNCLEARABLE_MS)
    assert refused.returncode == 2, "a verdict faster than the floor must not be consumable"
    assert "cause=implausible-latency" in refused.stderr
    assert "presumed" in refused.stderr and "NOT to have run" in refused.stderr

    # Defeat the enforcement: the documented escape hatch, and nothing else about the run changes.
    allowed = gate(INSTANT_GO, GATE_MIN_LATENCY_MS="0")
    assert allowed.returncode == 0, "with the floor off the identical reply passes — so the floor is what refused it"
    assert "OK (GO)" in allowed.stdout


def test_an_instant_no_go_is_refused_too_and_says_so_differently(gate) -> None:
    """A NO-GO in 4 ms did not run either, and 'the gate did not run' is a different stop from
    'the work is wrong'. Both exit 2, so only the cause tells an operator which to fix."""
    refused = gate(INSTANT_NOGO, GATE_MIN_LATENCY_MS=UNCLEARABLE_MS)
    assert refused.returncode == 2
    assert "cause=implausible-latency" in refused.stderr
    assert "=== GATE: NO-GO ===" not in refused.stderr, (
        "reported as a gate rejection, an operator fixes the spec for a call that never happened"
    )

    graded = gate(INSTANT_NOGO, GATE_MIN_LATENCY_MS="0")
    assert graded.returncode == 2
    assert "=== GATE: NO-GO ===" in graded.stderr, "with the floor off this is an ordinary rejection"


def test_a_call_slow_enough_to_be_real_passes_under_the_default_floor(gate) -> None:
    """The floor must not refuse real calls: this is the false-positive half, and a floor that
    rejected everything would pass every test above.

    This one DOES run at the shipped default, and its timing margin only ever grows: the stub sleeps
    0.4s against a 250 ms floor, and a loaded machine makes the child slower, never faster."""
    result = gate(SLOW_GO)
    assert result.returncode == 0, result.stderr
    assert "cause=implausible-latency" not in result.stderr


def test_a_refusal_that_never_reached_a_provider_is_not_reported_as_implausible(gate) -> None:
    """`gate_runner` records a near-zero latency for a cross-family refusal ON PURPOSE — it says the
    call never happened. Reporting that as an implausible latency would rename a correct, specific
    stop into a vague one, and send the operator at the provider instead of at the model id."""
    result = gate(INSTANT_GO, author_family="deepseek", GATE_MIN_LATENCY_MS=UNCLEARABLE_MS)
    assert result.returncode == 2
    assert "cause=cross-family" in result.stderr
    assert "cause=implausible-latency" not in result.stderr


def test_the_refusal_names_what_would_satisfy_it(gate) -> None:
    """A rule whose remedy is unavailable is a wedge. The remedy is in the message, by name."""
    result = gate(INSTANT_GO, GATE_MIN_LATENCY_MS=UNCLEARABLE_MS)
    assert "GATE_MIN_LATENCY_MS" in result.stderr
    assert "GATE_MIN_LATENCY_MS=0" in result.stderr


# ── the predicate itself ─────────────────────────────────────────────────────


def test_the_measured_defect_is_refused_and_a_real_latency_is_not() -> None:
    """The literal number from the record, against the SHIPPED default, with no clock involved.

    This is the half the end-to-end tests above deliberately do not carry: they prove the runner
    acts on the floor, this proves the floor that ships is the one that refuses the 4 ms GO."""
    assert gp.implausible(4, floor=gp.PROVIDER_FLOOR_MS) is not None
    assert gp.implausible(gp.PROVIDER_FLOOR_MS, floor=gp.PROVIDER_FLOOR_MS) is None
    assert gp.implausible(30_000, floor=gp.PROVIDER_FLOOR_MS) is None


def test_disabling_the_floor_says_so_rather_than_doing_nothing_quietly(capsys) -> None:
    """A plausibility check silently doing nothing is the failure it exists to remove."""
    assert gp.implausible(4, floor=0) is None
    assert "DISABLED" in capsys.readouterr().err


def test_an_unmeasured_latency_is_not_implausible() -> None:
    """The check is about a number that contradicts itself, never about the absence of one — a
    caller that does not measure must not have its gate blocked over a missing measurement."""
    assert gp.implausible(None, floor=gp.PROVIDER_FLOOR_MS) is None


def test_a_malformed_floor_is_refused_rather_than_read_as_the_default(monkeypatch) -> None:
    """Read as 'use the default', an operator who tried to relax the floor gets the strict one with
    no indication why, and one who tried to raise it gets no protection at all."""
    monkeypatch.setenv(gp.FLOOR_ENV, "fast")
    with pytest.raises(gp.FloorMisconfigured):
        gp.provider_floor_ms()
    monkeypatch.setenv(gp.FLOOR_ENV, "-1")
    with pytest.raises(gp.FloorMisconfigured):
        gp.provider_floor_ms()
    monkeypatch.setenv(gp.FLOOR_ENV, "50")
    assert gp.provider_floor_ms() == 50
    monkeypatch.delenv(gp.FLOOR_ENV)
    assert gp.provider_floor_ms() == gp.PROVIDER_FLOOR_MS


def test_a_malformed_floor_reaches_the_operator_as_a_configuration_failure(gate) -> None:
    """End to end: the runner must name it `config`, not `internal` — a defect in the gate and a
    typo in a .env send the operator to different places."""
    result = gate(SLOW_GO, GATE_MIN_LATENCY_MS="soon")
    assert result.returncode == 2
    assert "cause=config" in result.stderr
    assert "GATE_MIN_LATENCY_MS" in result.stderr


def test_the_cause_is_in_the_taxonomy_and_in_the_metrics_map() -> None:
    """A cause outside `gate_errors.CAUSES` raises at construction; one missing from the metrics
    map lands in `other` and loses the separation `failure_cause` exists for."""
    import gate_errors
    import pipeline_metrics

    assert "implausible-latency" in gate_errors.CAUSES
    assert "implausible-latency" in pipeline_metrics.CAUSE_MAP
