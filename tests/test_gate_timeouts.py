"""The nested-timeout relation is asserted here, against the config that actually ships.

`hooks/hooks.json` gave the gate hooks 120s while the provider call inside them was given 300s, so
the harness killed the hook 180s before the gate could answer. A killed hook leaves no verdict, no
report and no cause, and the run read that absence as a rejection. The duration split was clean —
spec 8.0 answered in 106s and passed, spec 8.1 took 143s and "failed" — and it was read for a day
as a size ceiling in the gate model.

`test_the_shipped_hooks_can_outlive_the_call_they_wrap` is the one that would have caught it, and it
reads the numbers out of `hooks.json` rather than restating them, so lowering either side moves it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_timeouts  # noqa: E402
from gate_errors import GateError  # noqa: E402
from gate_timeouts import (  # noqa: E402
    DEFAULT_CALL_TIMEOUT_S,
    HOOK_HEADROOM_S,
    METRICS_SPAWN,
    call_timeout,
    collect_processes,
    collect_timeout,
    gate_calls,
    gate_hooks,
    main,
    metrics_processes,
    metrics_worst_case_s,
    reaches_gate_runner,
    references,
    required_hook_timeout,
    violations,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"


# ── the shipped configuration ────────────────────────────────────────────────


def test_the_shipped_hooks_can_outlive_the_call_they_wrap() -> None:
    """The defect, stated as the property it broke. Red at 120s, green at 420s."""
    assert violations(HOOKS_JSON, SCRIPTS) == []


def test_every_hook_that_can_reach_the_gate_is_checked() -> None:
    """The relation is worthless if the set it applies to is empty or wrong."""
    names = {name for _, name, _, _ in gate_hooks(HOOKS_JSON, SCRIPTS)}
    assert names == {"hook_spec_gate.sh"}


def test_a_hook_that_never_calls_the_gate_is_not_forced_to_wait_for_one() -> None:
    """hook_verifier.sh runs mechanical checks and the suite; it reaches no provider call."""
    assert not reaches_gate_runner(SCRIPTS / "hook_verifier.sh", SCRIPTS)


def test_the_gate_hooks_are_derived_from_what_they_call_not_from_a_list() -> None:
    """A hook that starts calling the gate is covered without anyone updating this module."""
    assert reaches_gate_runner(SCRIPTS / "hook_spec_gate.sh", SCRIPTS)
    # The negative half, which is what makes the positive one mean something: a script in the same
    # directory that does NOT reach the runner must not be swept in by the walk.
    assert not reaches_gate_runner(SCRIPTS / "verifier_evidence.py", SCRIPTS)


def test_a_hook_that_calls_the_gate_twice_needs_a_budget_for_two_calls() -> None:
    """The spec gate observes, then triages. A budget sized for one call reinstates the original
    defect exactly: the harness kills the hook mid-second-call and a killed hook reports nothing.

    It routes both passes through one `run_pass` helper, which is the better shape and which a
    naive count of invocation *sites* would read as a single call — so the counter expands the
    helper rather than the hook being written worse to suit the counter."""
    assert gate_calls(SCRIPTS / "hook_spec_gate.sh", SCRIPTS) == 2
    assert required_hook_timeout(calls=2) == 2 * call_timeout() + HOOK_HEADROOM_S

    shipped = {name: (timeout, calls) for _, name, timeout, calls in gate_hooks(HOOKS_JSON, SCRIPTS)}
    timeout, calls = shipped["hook_spec_gate.sh"]
    assert timeout >= required_hook_timeout(calls=calls)


def test_a_helper_called_twice_counts_twice(tmp_path: Path) -> None:
    """The property, away from the shipped file, so it survives a refactor of either."""
    hook = tmp_path / "hook_two.sh"
    hook.write_text(
        "#!/usr/bin/env bash\n"
        "run_pass () {\n"
        '  python3 "$SD/gate_runner.py" --rubric "$1"\n'
        "}\n"
        'run_pass a\n'
        'if ! run_pass b; then exit 2; fi\n'
    )
    assert gate_calls(hook, SCRIPTS) == 2


REFERENCE_FORMS = {
    "plugin root": '  bash "${CLAUDE_PLUGIN_ROOT}/scripts/gate_runner.py"\n',
    "$SD": '  python3 "$SD/gate_runner.py"\n',
    "$SCRIPT_DIR": '  python3 "$SCRIPT_DIR/gate_runner.py"\n',
}


@pytest.mark.parametrize("form", sorted(REFERENCE_FORMS))
def test_every_spelling_of_a_script_reference_is_followed(form: str, tmp_path: Path) -> None:
    """The derivation is only as wide as the forms it recognises. `$SCRIPT_DIR/` (gate_ci.sh's
    spelling) was known to tests/test_install_manifest.py and not here, so a hook reaching the gate
    through one would not have been recognised as a gate hook at all: no violation reported, verify
    exits 0, and the inverted pair this module exists to catch stays live. The two scanners audit
    the same repo and must recognise the same three forms."""
    hook = tmp_path / "hook_x.sh"
    hook.write_text(f"#!/usr/bin/env bash\n{REFERENCE_FORMS[form]}")

    assert references(hook) == {"gate_runner.py"}, f"{form} reference not followed"
    assert reaches_gate_runner(hook, SCRIPTS)


def test_this_module_and_the_install_audit_recognise_the_same_forms() -> None:
    """Two regexes for one question drift, and the one that drifts first is the one nobody reads."""
    import test_install_manifest as manifest

    probe = " ".join(f'"{ref}"' for ref in REFERENCE_FORMS.values())
    assert {a or b or c for a, b, c in manifest.REFERENCE.findall(probe)} == {"gate_runner.py"}


# ── the relation itself ──────────────────────────────────────────────────────


def test_the_required_budget_leaves_room_for_the_work_around_the_call() -> None:
    assert required_hook_timeout() == call_timeout() + HOOK_HEADROOM_S


def test_an_inverted_pair_is_reported_with_both_numbers(tmp_path: Path) -> None:
    """The message has to name the inversion; 'the hook failed' is what cost the day.

    The original 120s-vs-300s pair, reproduced on a single-call hook of this test's own so the
    arithmetic stays the one the defect had — the shipped gate hook now makes two calls and its
    numbers are checked separately."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hook_one.sh").write_text(
        '#!/usr/bin/env bash\npython3 "$SD/gate_runner.py" --rubric r --target t\n'
    )
    (scripts / "gate_runner.py").write_text("# stand-in for resolution only\n")
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/hook_one.sh"',
         "timeout": 120},
    ]}]}}))
    found = violations(hooks, scripts)
    assert len(found) == 1
    assert "120s" in found[0] and "300s" in found[0]
    assert "kills the hook 180s before the gate can answer" in found[0]


def test_raising_the_call_budget_without_the_hook_budget_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GATE_CALL_TIMEOUT is the inner number; the relation is checked against whatever it is,
    so a raised call budget cannot silently re-create the inversion."""
    monkeypatch.setenv("GATE_CALL_TIMEOUT", "600")
    assert violations(HOOKS_JSON, SCRIPTS), "420s cannot outlive a 600s call"


# ── the budget itself is read, never guessed ─────────────────────────────────


@pytest.mark.parametrize("bad", ["600s", "10m", "five minutes", "5,000"])
def test_an_unparseable_call_budget_is_refused_and_quoted(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to fall back to 300 in silence: the operator believed the budget they wrote, the
    relation below validated the hook against 300 and passed, and the call was killed at 300s with
    nothing saying why — the same shape as the inversion this module exists to catch."""
    monkeypatch.setenv("GATE_CALL_TIMEOUT", bad)
    with pytest.raises(GateError) as raised:
        call_timeout()
    assert raised.value.cause == "config"
    assert bad.strip() in raised.value.detail, "the offending value has to be in the message"


@pytest.mark.parametrize("bad", ["0", "-30"])
def test_a_non_positive_call_budget_is_refused(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A budget of zero kills every gate before it can answer; falling back to 300 hides that the
    configured value was never in effect."""
    monkeypatch.setenv("GATE_CALL_TIMEOUT", bad)
    with pytest.raises(GateError, match="positive"):
        call_timeout()


def test_verify_stops_on_a_bad_budget_rather_than_checking_against_the_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both gate hooks run `verify` before they call the gate, so this is where a misread budget is
    caught. Exit 0 here would mean the hook proceeded with a budget nobody set."""
    monkeypatch.setenv("GATE_CALL_TIMEOUT", "600s")
    assert main(["verify", str(HOOKS_JSON)]) == 2
    assert "'600s'" in capsys.readouterr().err


def test_an_unset_budget_is_still_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_CALL_TIMEOUT", raising=False)
    assert call_timeout() == DEFAULT_CALL_TIMEOUT_S


# ── measurement spends the same headroom, so it is checked against it ────────


def test_measurement_fits_inside_the_headroom_it_spends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unwritable record makes firstmate's CLI block rather than fail, and the sink's breaker is
    per-process, so every process on a hook's path can pay the per-call bound once. The hook budget
    has to outlive the provider call PLUS all of that."""
    monkeypatch.delenv("AVENGER_METRICS_TIMEOUT", raising=False)
    for _, name, _, _ in gate_hooks(HOOKS_JSON, SCRIPTS):
        assert metrics_worst_case_s(SCRIPTS / name, SCRIPTS) <= HOOK_HEADROOM_S, name


def test_the_spec_gate_path_counts_every_process_that_can_pay_the_timeout() -> None:
    """The worst case today: phase-open, spec-round, the kill trap's own record — plus the gate
    runner and the triage decider, which record in-process rather than by spawning the CLI."""
    hook = SCRIPTS / "hook_spec_gate.sh"
    spawned = len(METRICS_SPAWN.findall(hook.read_text(encoding="utf-8")))

    assert spawned >= 3
    assert metrics_processes(hook, SCRIPTS) == spawned + 2


def test_a_new_emission_point_moves_the_count_by_itself(tmp_path: Path) -> None:
    """Derived, not listed — the same guarantee as the gate-hook walk. A number typed in here would
    stay right until the first hook that added a fact, which is the only time it matters."""
    hook = tmp_path / "hook_x.sh"
    hook.write_text('#!/usr/bin/env bash\npython3 "$SD/gate_runner.py"\n', encoding="utf-8")
    before = metrics_processes(hook, SCRIPTS)

    hook.write_text(
        '#!/usr/bin/env bash\npython3 "$SD/gate_runner.py"\n'
        'python3 "$SD/pipeline_metrics.py" phase-open "$FILE"\n',
        encoding="utf-8",
    )

    assert before == 1
    assert metrics_processes(hook, SCRIPTS) == before + 1


def test_the_suite_collection_is_charged_to_the_hook_that_can_spawn_it() -> None:
    """`phase-open` sizes the suite with `pytest --collect-only` — a child whose natural duration
    is a property of somebody else's test tree, sitting on the spec gate's path. `spec-round` and
    the kill traps spawn the CLI and collect nothing, so they are not charged a collection."""
    hook = SCRIPTS / "hook_spec_gate.sh"

    assert collect_processes(hook, SCRIPTS) == 1
    assert collect_processes(hook, SCRIPTS) < metrics_processes(hook, SCRIPTS)


def test_a_new_collection_site_moves_the_count_by_itself(tmp_path: Path) -> None:
    """Derived, never listed — the same guarantee as the emission-point walk."""
    hook = tmp_path / "hook_y.sh"
    hook.write_text('#!/usr/bin/env bash\npython3 "$SD/gate_runner.py"\n', encoding="utf-8")
    before = collect_processes(hook, SCRIPTS)

    hook.write_text(
        '#!/usr/bin/env bash\npython3 "$SD/gate_runner.py"\n'
        'python3 "$SD/pipeline_metrics.py" phase-close "$PHASE"\n',
        encoding="utf-8",
    )

    assert before == 0
    assert collect_processes(hook, SCRIPTS) == 1


def test_a_collection_budget_that_eats_the_headroom_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound on the collection is checked against the headroom it spends, not believed. Raising
    it past what the hook carries is loud rather than a hook killed mid-gate with nothing to show —
    the exact `120s hook around a 300s call` shape one layer in."""
    monkeypatch.delenv("AVENGER_METRICS_TIMEOUT", raising=False)
    monkeypatch.setattr(gate_timeouts, "COLLECT_TIMEOUT_S", HOOK_HEADROOM_S)

    found = violations(HOOKS_JSON, SCRIPTS)

    assert any("HEADROOM EXHAUSTED" in line and "hook_spec_gate.sh" in line for line in found)
    assert all("suite collections" in line for line in found if "HEADROOM EXHAUSTED" in line)


def test_the_shipped_collection_bound_fits_beside_the_writers_it_shares_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves of measurement come out of one headroom, so they are checked together."""
    monkeypatch.delenv("AVENGER_METRICS_TIMEOUT", raising=False)
    for _, name, _, _ in gate_hooks(HOOKS_JSON, SCRIPTS):
        assert metrics_worst_case_s(SCRIPTS / name, SCRIPTS) <= HOOK_HEADROOM_S, name
    assert collect_timeout() > 0


def test_a_module_that_reads_the_budget_but_records_nothing_is_not_counted() -> None:
    """`gate_timeouts.py` imports the sink to read its bound. Counting an import rather than an
    emission would inflate every hook that checks its own timeouts."""
    assert metrics_processes(SCRIPTS / "gate_timeouts.py", SCRIPTS) == 0


def test_a_metrics_budget_that_eats_the_headroom_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-call bound stays configurable — raising it past what the headroom carries is loud
    rather than a hook killed mid-gate with nothing to show for it."""
    monkeypatch.setenv("AVENGER_METRICS_TIMEOUT", "60")

    found = violations(HOOKS_JSON, SCRIPTS)

    assert any("HEADROOM EXHAUSTED" in line and "hook_spec_gate.sh" in line for line in found)
    assert all("60s AVENGER_METRICS_TIMEOUT" in line
               for line in found if "HEADROOM EXHAUSTED" in line)


def test_verify_fails_when_measurement_would_eat_the_hook_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AVENGER_METRICS_TIMEOUT", "60")

    assert main(["verify", str(HOOKS_JSON)]) == 2
    assert "HEADROOM EXHAUSTED" in capsys.readouterr().err


def test_a_sound_pair_reports_nothing(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/hook_spec_gate.sh"',
         "timeout": required_hook_timeout(calls=2)},
    ]}]}}))
    assert violations(hooks, SCRIPTS) == []
