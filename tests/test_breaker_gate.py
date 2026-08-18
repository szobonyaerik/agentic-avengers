"""The Breaker gate - a phase that declares `criticality: critical` does not close without a record.

All four phase-8 specs and all four phase-9 specs of one measured feature declared
`criticality: critical`, which is what routes the Breaker (commands/avenger-run.md §4). It was owed
twice and ran neither time, and there was zero trace of it anywhere in the feature's docs or tests -
a stage that emits nothing is indistinguishable from a stage that never ran. Phase 8's credential
leaks were found BY the Breaker constructing inputs nothing else would; whatever it would have found
in phases 8 and 9 was never looked for, because nothing noticed the omission.

These tests break the gap before proving it closed: `due()` must refuse a critical phase with no
record, and a record that names nothing (a vacuous "clean" or "found") is refused exactly like a
missing one - a file existing is not proof anything was probed.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# `check()`'s diff scoping is a claim about what git actually reports, so the real binary is the
# subject under test, not something to fake. Same rule tests/test_carried_items.py runs on.
pytestmark = pytest.mark.subprocess(
    "the diff scoping is a claim about what git actually reports; a faked diff tests the fake"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import applicability  # noqa: E402
import breaker_gate  # noqa: E402


@pytest.fixture(autouse=True)
def _project_dir(tmp_path, monkeypatch):
    """bypass_log.sh writes gate-overrides.log under $CLAUDE_PROJECT_DIR — never this repository."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

SPEC = """---
feature: demo
phase: {phase}
spec: {spec}
criticality: {criticality}
---

# Spec
"""


def write_spec(root: Path, phase: str, spec: str, *, criticality: str = "standard") -> Path:
    spec_dir = root / "docs" / "features" / "demo" / "phases" / phase / "specs" / spec
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / "spec.md"
    path.write_text(SPEC.format(phase=phase, spec=spec, criticality=criticality))
    return path


def phase_dir(root: Path, phase: str = "1-core") -> Path:
    return root / "docs" / "features" / "demo" / "phases" / phase


def write_breaker(root: Path, data: dict, phase: str = "1-core") -> None:
    record = {"readers": list(breaker_gate.READERS), **data}
    (phase_dir(root, phase) / "breaker.json").write_text(json.dumps(record))


# --- owed() -------------------------------------------------------------------------------------


def test_owed_when_a_spec_declares_criticality_critical(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    assert breaker_gate.owed(phase_dir(tmp_path)) is True


def test_not_owed_when_every_spec_is_standard(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="standard")
    assert breaker_gate.owed(phase_dir(tmp_path)) is False


def test_owed_when_any_one_of_several_specs_is_critical(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="standard")
    write_spec(tmp_path, "1-core", "1.2-b", criticality="critical")
    assert breaker_gate.owed(phase_dir(tmp_path)) is True


def test_not_owed_with_no_specs_at_all(tmp_path: Path) -> None:
    assert breaker_gate.owed(phase_dir(tmp_path)) is False


# --- due(): reproduce the gap, then close it -----------------------------------------------------


def test_due_refuses_a_critical_phase_with_no_record_at_all(tmp_path: Path) -> None:
    """This is issue #45, reproduced directly: the phase owes a Breaker run and left no trace it
    happened. Without the fix `due()` returns None here — the exact silent gap that shipped twice."""
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "breaker.json" in reason
    assert "1-core" in reason


def test_due_is_clear_once_a_clean_record_names_what_it_attacked(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "clean", "attacked": ["malformed payloads"]})
    assert breaker_gate.due(phase_dir(tmp_path)) is None


def test_due_is_clear_once_a_found_record_names_a_counterexample(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "found", "counterexamples": ["tests/demo/1-core/x.py"]})
    assert breaker_gate.due(phase_dir(tmp_path)) is None


def test_due_refuses_a_clean_verdict_naming_nothing_attacked(tmp_path: Path) -> None:
    """"A clean Breaker report with no attempts described is not acceptable" was already the agent's
    own instruction (agents/avenger-breaker.md) - this is what makes it checkable rather than a
    sentence nobody enforces."""
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "clean", "attacked": []})
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "names nothing attacked" in reason


def test_due_refuses_a_found_verdict_naming_no_counterexample(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "found", "counterexamples": []})
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "no counterexample" in reason


def test_due_refuses_an_unreadable_verdict(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "definitely-fine"})
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "no readable verdict" in reason


def test_due_refuses_a_record_that_declares_no_readers(tmp_path: Path) -> None:
    """A record this gate accepts and `doc_read_path.py` refuses is two gates disagreeing about
    what a valid record is - the phase closes on it, and the very next commit fails on the same
    file. Asked here, where the remedy still exists, rather than a commit later."""
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    (phase_dir(tmp_path) / "breaker.json").write_text(
        json.dumps({"verdict": "clean", "attacked": ["replay"]})
    )
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "declares no `readers`" in reason


def test_due_refuses_a_record_whose_readers_list_is_empty(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    (phase_dir(tmp_path) / "breaker.json").write_text(
        json.dumps({"verdict": "clean", "attacked": ["replay"], "readers": []})
    )
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "declares no `readers`" in reason


def test_a_record_this_gate_accepts_is_one_the_read_path_check_accepts(tmp_path: Path) -> None:
    """The two gates are pinned to ONE answer: whatever clears the phase close must also clear the
    artifact check that reads the same file. `doc_read_path` takes its declared readers from this
    module's constant, so the entry and the enforcement cannot drift apart."""
    import doc_read_path

    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "clean", "attacked": ["replay"]})
    assert breaker_gate.due(phase_dir(tmp_path)) is None

    spec = doc_read_path.spec_for(breaker_gate.FILENAME)
    assert spec is not None, "breaker.json is not on the read-path table"
    assert spec["readers"] == breaker_gate.READERS
    assert doc_read_path._artifact_problems(breaker_gate.record_path(phase_dir(tmp_path)), spec) == []


def test_due_refuses_malformed_json(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    (phase_dir(tmp_path) / "breaker.json").write_text("{not json")
    reason = breaker_gate.due(phase_dir(tmp_path))
    assert reason is not None
    assert "could not be read as JSON" in reason


def test_due_is_clear_for_a_standard_phase_with_no_record(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="standard")
    assert breaker_gate.due(phase_dir(tmp_path)) is None


# --- the disclosed exception --------------------------------------------------------------------


def test_a_recorded_exception_clears_an_owed_and_unmet_obligation(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    applicability.record_exception(
        phase_dir(tmp_path), "breaker", "1-core", "no reachable critical path", "captain"
    )
    assert breaker_gate.due(phase_dir(tmp_path)) is None


def test_an_exception_for_a_different_rule_does_not_clear_it(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    applicability.record_exception(phase_dir(tmp_path), "verdict", "1-core", "unrelated", "captain")
    assert breaker_gate.due(phase_dir(tmp_path)) is not None


# --- the CI sweep, and why it is scoped -----------------------------------------------------------


def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S603,S607
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)  # noqa: S603,S607
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)  # noqa: S603,S607
    (tmp_path / ".keep").write_text("", encoding="utf-8")
    commit_all(tmp_path)  # `git diff HEAD` has no answer in a repo with no commits
    return tmp_path


def commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S603,S607
    subprocess.run(["git", "commit", "-qm", "x"], cwd=root, check=True)  # noqa: S603,S607


def test_check_holds_an_untracked_critical_phase_with_no_record(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    write_spec(root, "1-core", "1.1-a", criticality="critical")
    assert breaker_gate.check(root), "an owed Breaker run in an untracked phase must be enforced"


def test_check_only_counts_a_phase_the_diff_does_not_touch(tmp_path: Path) -> None:
    """This obligation lands on a phase directory tree every consumer repo already has on disk, so a
    full audit would fail CI over phases that closed before this rule existed."""
    root = git_repo(tmp_path)
    write_spec(root, "1-core", "1.1-a", criticality="critical")
    commit_all(root)

    assert breaker_gate.check(root) == []
    assert breaker_gate.check(root, enforce_all=True), "--all is the audit, and it audits"


def test_check_enforces_nothing_when_git_cannot_say_what_changed(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    assert breaker_gate.check(tmp_path) == []


def test_check_over_a_tree_with_no_specs_is_clean_and_says_so(tmp_path: Path, capsys) -> None:
    assert breaker_gate.check(tmp_path) == []
    assert "nothing to check" in capsys.readouterr().err


# --- it is enforced, not asked for ----------------------------------------------------------------


def test_the_in_session_hook_holds_the_obligation() -> None:
    """A rule only CI applies arrives after the phase has already closed."""
    text = (ROOT / "scripts" / "hook_verifier.sh").read_text(encoding="utf-8")
    assert "breaker_gate.py" in text
    assert 'breaker_gate.py" due' in text


def test_ci_holds_the_obligation_too() -> None:
    text = (ROOT / "scripts" / "gate_ci.sh").read_text(encoding="utf-8")
    assert "breaker_gate.py" in text


# --- the CLI -----------------------------------------------------------------------------------


def test_cli_due_exits_1_and_names_the_phase(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    assert breaker_gate.main(["due", str(phase_dir(tmp_path))]) == 1


def test_cli_due_exits_0_once_satisfied(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    write_breaker(tmp_path, {"verdict": "clean", "attacked": ["boundary values"]})
    assert breaker_gate.main(["due", str(phase_dir(tmp_path))]) == 0


def test_cli_owed_exits_1_when_owed(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="critical")
    assert breaker_gate.main(["owed", str(phase_dir(tmp_path))]) == 1


def test_cli_owed_exits_0_when_not_owed(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality="standard")
    assert breaker_gate.main(["owed", str(phase_dir(tmp_path))]) == 0
