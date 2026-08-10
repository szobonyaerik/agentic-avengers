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

from gate_timeouts import (  # noqa: E402
    HOOK_HEADROOM_S,
    call_timeout,
    gate_hooks,
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
    names = {name for _, name, _ in gate_hooks(HOOKS_JSON, SCRIPTS)}
    assert names == {"hook_fidelity.sh", "hook_spec_review.sh"}


def test_a_hook_that_never_calls_the_gate_is_not_forced_to_wait_for_one() -> None:
    """hook_verifier.sh only names verifier_review.sh in a message; it does not run it."""
    assert not reaches_gate_runner(SCRIPTS / "hook_verifier.sh", SCRIPTS)


def test_the_gate_hooks_are_derived_from_what_they_call_not_from_a_list() -> None:
    """A hook that starts calling the gate is covered without anyone updating this module."""
    assert reaches_gate_runner(SCRIPTS / "hook_fidelity.sh", SCRIPTS)
    assert reaches_gate_runner(SCRIPTS / "verifier_review.sh", SCRIPTS)


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
    """The message has to name the inversion; 'the hook failed' is what cost the day."""
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/hook_fidelity.sh"',
         "timeout": 120},
    ]}]}}))
    found = violations(hooks, SCRIPTS)
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


def test_a_sound_pair_reports_nothing(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/hook_fidelity.sh"',
         "timeout": required_hook_timeout()},
    ]}]}}))
    assert violations(hooks, SCRIPTS) == []
