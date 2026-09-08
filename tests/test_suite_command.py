"""What the project DECLARES its test command to be, against what actually ran (issue #119).

The reproduction is the first test in this file and it is the whole point. A project declares
`test: "pytest --cov"`, the step that was supposed to run it logs `no test command configured` and
an agent improvises a different invocation, and the verdict passes with nobody comparing the two.
That is not merely wasteful: `clickup-agents` has three phase-1 tests that fail under a bare
`python3` and pass under the venv interpreter, so WHICH invocation ran decides the answer.

The pipeline's own half of that defect is the phase verification: `verifier_evidence.py` records
the argv of every command the Verifier ran, and nothing asked whether the `suite` run was the one
the project declared.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import suite_command  # noqa: E402
from verifier_precheck import check_phase  # noqa: E402

SPEC = """---
feature: demo
phase: 1-core
spec: 1.1-a
spec_gate: approved
---

# Spec

## Requirements
- R1.1.1 (binding: none) — the thing.

## Acceptance criteria
- R1.1.1 — passes when: …; fails when: …
"""

#: The line the improvised run leaves behind. Quoted from the grid-bot-platform worker's own report
#: of the step, 2026-09-06 — it is a log line rather than a failure, which is exactly why nothing
#: noticed.
IMPROVISED_LOG = (
    "no test command configured, asking agent to run tests...\n"
    "1298 passed, 4 skipped in 61.20s\n"
)


def build(
    tmp_path: Path,
    *,
    declared: str | None = "pytest --cov",
    argv: list[str] | None = None,
    verdict: str = "pass",
) -> Path:
    """A feature tree with one phase: a declared command, a passing verdict, and a recorded run."""
    phase = tmp_path / "docs" / "features" / "demo" / "phases" / "1-core"
    (phase / "specs" / "1.1-a").mkdir(parents=True)
    (phase / "specs" / "1.1-a" / "spec.md").write_text(SPEC, encoding="utf-8")
    (phase / "specs" / "1.1-a" / "test-mapping.md").write_text(
        "| Requirement | Test | level |\n|---|---|---|\n", encoding="utf-8"
    )
    if declared is not None:
        (tmp_path / ".no-mistakes.yaml").write_text(
            f'agent: claude\n\ncommands:\n  lint: "ruff check ."\n  test: "{declared}"\n',
            encoding="utf-8",
        )
    (phase / "verdict.json").write_text(
        json.dumps({"verdict": verdict, "findings": [], "attempt": 1}), encoding="utf-8"
    )
    if argv is not None:
        logs = phase / "evidence"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "01-suite.log").write_text(IMPROVISED_LOG, encoding="utf-8")
        (phase / "verification-evidence.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "phase": "1-core",
                    "runs": [
                        {
                            "seq": 1,
                            "kind": "suite",
                            "argv": argv,
                            "exit_code": 0,
                            "elapsed_ms": 61200,
                            "log": "evidence/01-suite.log",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    return phase


# --- the reproduction -----------------------------------------------------------------------------


def test_improvised_run_under_a_declared_command_is_refused(tmp_path: Path) -> None:
    """THE REPRODUCTION. Declared `pytest --cov`, ran something else, verdict says pass.

    Red before issue #119's fix: `check_phase` returned nothing at all about the mismatch, so the
    phase was clean and the improvised run was indistinguishable in the record from the declared
    one passing.
    """
    phase = build(tmp_path, argv=["pytest", "-q", "tests/demo/1-core"])
    findings = [f for f in check_phase(phase) if "test command" in f]
    assert findings, (
        "the mismatch between the declared and the run command was not examined"
    )
    assert "pytest --cov" in findings[0]
    assert "pytest -q tests/demo/1-core" in findings[0]


def test_the_improvised_run_left_its_own_log_line(tmp_path: Path) -> None:
    """The symptom the fixture carries, so the reproduction is the real shape and not a stand-in."""
    phase = build(tmp_path, argv=["pytest", "-q", "tests/demo/1-core"])
    log = (phase / "evidence" / "01-suite.log").read_text(encoding="utf-8")
    assert "no test command configured" in log


# --- what must remain possible --------------------------------------------------------------------


def test_a_project_declaring_no_command_is_untouched(tmp_path: Path) -> None:
    phase = build(tmp_path, declared=None, argv=["pytest", "-q", "tests/demo/1-core"])
    assert [f for f in check_phase(phase) if "test command" in f] == []


def test_a_declared_command_actually_run_is_clean(tmp_path: Path) -> None:
    phase = build(tmp_path, argv=["pytest", "--cov", "tests/demo/1-core"])
    assert [f for f in check_phase(phase) if "test command" in f] == []


def test_a_verdict_that_does_not_pass_is_not_refused(tmp_path: Path) -> None:
    """The rule is about a PASS. A phase still routing back is not making the claim."""
    phase = build(tmp_path, argv=["pytest", "-q", "x"], verdict="fail")
    assert [f for f in check_phase(phase) if "test command" in f] == []


def test_an_unreadable_run_is_unknown_and_refused_only_under_a_declaration(
    tmp_path: Path,
) -> None:
    """No transcript at all: the command cannot be read, so it is `unknown`, never a guess."""
    declared = build(tmp_path, argv=None)
    findings = [f for f in check_phase(declared) if "test command" in f]
    assert findings and suite_command.UNKNOWN in findings[0]


def test_unknown_without_a_declaration_is_still_untouched(tmp_path: Path) -> None:
    phase = build(tmp_path, declared=None, argv=None)
    assert [f for f in check_phase(phase) if "test command" in f] == []


# --- the declaration reader -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('test: "pytest --cov"\n', "pytest --cov"),
        ("test: 'pytest --cov'\n", "pytest --cov"),
        (
            "commands:\n  test: bash scripts/gate_ci.sh --full\n",
            "bash scripts/gate_ci.sh --full",
        ),
        ("commands:\n  test: pytest  # the suite\n", "pytest"),
        ("lint: ruff\n", None),
        ("# test: pytest\n", None),
    ],
)
def test_declared_reads_the_test_key(
    tmp_path: Path, body: str, expected: str | None
) -> None:
    (tmp_path / ".no-mistakes.yaml").write_text(body, encoding="utf-8")
    assert suite_command.declared(tmp_path).command == expected


def test_two_disagreeing_declarations_are_undecidable_not_absent(
    tmp_path: Path,
) -> None:
    (tmp_path / ".no-mistakes.yaml").write_text(
        'test: "pytest --cov"\ncommands:\n  test: "pytest -q"\n', encoding="utf-8"
    )
    result = suite_command.declared(tmp_path)
    assert result.command is None and result.problem is not None


def test_an_unreadable_config_is_a_problem_not_a_missing_declaration(
    tmp_path: Path,
) -> None:
    (tmp_path / ".no-mistakes.yaml").write_bytes(b"test: \xff\xfe\n")
    result = suite_command.declared(tmp_path)
    assert result.command is None and result.problem is not None


def test_no_config_at_all_declares_nothing_and_is_not_a_problem(tmp_path: Path) -> None:
    result = suite_command.declared(tmp_path)
    assert result.command is None and result.problem is None


# --- matching -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "declared", "expected"),
    [
        (["pytest", "--cov"], "pytest --cov", True),
        (["pytest", "--cov", "tests/x"], "pytest --cov", True),
        (["pytest", "-q", "tests/x"], "pytest --cov", False),
        (["pytest"], "pytest --cov", False),
        (["python3", "-m", "pytest", "--cov"], "pytest --cov", False),
    ],
)
def test_runs_declared_is_a_prefix_rule(
    argv: list[str], declared: str, expected: bool
) -> None:
    assert suite_command.runs_declared(argv, declared) is expected
