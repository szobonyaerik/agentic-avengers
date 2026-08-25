"""The lint gate can see FORMAT drift - proven by driving it red on drifted input.

## The defect this pins

The gate ran `ruff check .` and nothing else. `ruff check` judges rules; it does not judge
formatting, so drift passed it untouched. Two consecutive clickup-agents phases reported "ruff
clean" and neither statement was evidence about formatting: nothing in the gate could have failed
on it.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** Every case below constructs a file that `ruff
check` accepts and `ruff format` would rewrite, and asserts the gate goes RED on it. The gate is
then handed the formatted file and asserted green, so the assertion is about the guard and not
about the fixture.

## Why the format dimension is diff-scoped

Same applicability boundary as every other check added after the tree it runs on (CLAUDE.md 3a):
this repository has 93 files that predate the rule, and a gate that failed the build over them
would be a wedge rather than a gate. What the change touches is enforced; the rest is counted and
named. `--all` is the audit somebody runs deliberately.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lint_gate  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the gate's whole job is to run ruff; a mocked ruff would prove the mock formats correctly"
)

CLI = [sys.executable, str(ROOT / "scripts" / "lint_gate.py")]

#: `ruff check` has nothing to say about either of these. Only `ruff format` does.
DRIFTED = "x = {'a':1,   'b':2}\nprint( x )\n"
CLEAN = 'x = {"a": 1, "b": 2}\nprint(x)\n'

#: A rule violation, to prove the rules dimension still binds after the format one is added.
RULE_BREAK = "import os\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one committed, clean file - so `changed_paths` can answer."""
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=project, check=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    (project / "settled.py").write_text(CLEAN, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=project, check=True)
    return project


def gate(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*CLI, *args], cwd=repo, capture_output=True, text=True, check=False
    )


# --- the format dimension exists at all ----------------------------------------------------------


def test_ruff_check_alone_cannot_see_the_drift_this_gate_is_about(repo: Path) -> None:
    """The premise, asserted rather than assumed: the old gate command passes this file."""
    (repo / "drifted.py").write_text(DRIFTED, encoding="utf-8")
    rules_only = subprocess.run(
        ["ruff", "check", "."], cwd=repo, capture_output=True, text=True
    )
    assert rules_only.returncode == 0, rules_only.stdout


def test_drift_in_scope_fails_the_gate(repo: Path) -> None:
    (repo / "drifted.py").write_text(DRIFTED, encoding="utf-8")
    proc = gate(repo)
    assert proc.returncode == lint_gate.FOUND, proc.stdout + proc.stderr
    assert "drifted.py" in proc.stdout + proc.stderr


def test_the_same_file_formatted_passes_the_gate(repo: Path) -> None:
    """The counterweight: the gate must fail drift without failing correct code."""
    (repo / "drifted.py").write_text(CLEAN, encoding="utf-8")
    proc = gate(repo)
    assert proc.returncode == lint_gate.OK, proc.stdout + proc.stderr


def test_a_rule_violation_still_fails_after_the_format_dimension_is_added(
    repo: Path,
) -> None:
    (repo / "rules.py").write_text(RULE_BREAK, encoding="utf-8")
    proc = gate(repo)
    assert proc.returncode == lint_gate.FOUND, proc.stdout + proc.stderr
    assert "F401" in proc.stdout + proc.stderr


# --- the applicability boundary ------------------------------------------------------------------


def test_drift_this_change_did_not_touch_is_counted_and_named_never_blocked(
    repo: Path,
) -> None:
    """A repository full of pre-rule files adopts the gate instead of being held hostage by it."""
    (repo / "ancient.py").write_text(DRIFTED, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "pre-rule"], cwd=repo, check=True)

    proc = gate(repo)
    assert proc.returncode == lint_gate.OK, proc.stdout + proc.stderr
    assert "ancient.py" in proc.stdout + proc.stderr
    assert "counted" in (proc.stdout + proc.stderr).lower()


def test_all_audits_the_whole_tree(repo: Path) -> None:
    (repo / "ancient.py").write_text(DRIFTED, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "pre-rule"], cwd=repo, check=True)

    proc = gate(repo, "--all")
    assert proc.returncode == lint_gate.FOUND, proc.stdout + proc.stderr
    assert "ancient.py" in proc.stdout + proc.stderr


def test_an_unknowable_scope_enforces_nothing_and_says_so(tmp_path: Path) -> None:
    """Not a git repository: `changed_paths` cannot answer, so the format dimension enforces
    nothing rather than falling back to enforcing everything - and never silently."""
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "drifted.py").write_text(DRIFTED, encoding="utf-8")
    proc = subprocess.run(
        [*CLI], cwd=loose, capture_output=True, text=True, check=False
    )
    assert proc.returncode == lint_gate.OK, proc.stdout + proc.stderr
    assert "could not" in (proc.stdout + proc.stderr).lower()


# --- the gate a caller actually runs --------------------------------------------------------------


def test_the_configured_lint_gate_is_this_one() -> None:
    """The check lives where the gate is DECIDED. `.no-mistakes.yaml` is the one place this
    repository's lint command is written down, and a future edit back to a bare `ruff check`
    reintroduces exactly the gap this file pins."""
    config = (ROOT / ".no-mistakes.yaml").read_text(encoding="utf-8")
    line = next(ln for ln in config.splitlines() if ln.strip().startswith("lint:"))
    assert "lint_gate.py" in line, line


def test_ruff_is_what_this_gate_runs() -> None:
    assert shutil.which("ruff"), (
        "the gate shells out to ruff; without it there is no gate"
    )


# --- ruff's output is a version-dependent contract -------------------------------------------------


def _stub_ruff(tmp_path: Path, format_stdout: str, format_exit: int) -> Path:
    """A `ruff` on PATH whose FORMAT answer is fixed.

    The real ruff on this machine is one version, and the thing under test is what this gate does
    with the OTHER version's output shape - so the contract has to be supplied rather than
    installed. Nothing here asserts anything about formatting: only about the gate reading, or
    failing to read, an answer it is handed.
    """
    bindir = tmp_path / "stub-bin"
    bindir.mkdir()
    stub = bindir / "ruff"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "format" ]; then\n'
        f"  cat <<'OUT'\n{format_stdout}\nOUT\n"
        f"  exit {format_exit}\n"
        "fi\n"
        "echo 'All checks passed!'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _gate_with(bindir: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    return subprocess.run(
        [*CLI, *args], cwd=repo, capture_output=True, text=True, check=False, env=env
    )


#: ruff >= 0.16 stopped emitting `Would reformat: <path>` and names the file in a diagnostic
#: instead. Read for the old shape alone, the drift arrived here as an empty list - i.e. as clean.
NEW_SHAPE = """unformatted: File would be reformatted
 --> drifted.py:1:10
  |
  - x = {'a':1,   'b':2}
1 + x = {"a": 1, "b": 2}
  |

1 file would be reformatted"""


def test_drift_named_in_the_newer_ruff_shape_still_fails_the_gate(
    repo: Path, tmp_path: Path
) -> None:
    (repo / "drifted.py").write_text(DRIFTED, encoding="utf-8")
    proc = _gate_with(_stub_ruff(tmp_path, NEW_SHAPE, 1), repo)
    assert proc.returncode == lint_gate.FOUND, proc.stdout + proc.stderr
    assert "drifted.py is not formatted" in proc.stdout + proc.stderr


def test_an_unreadable_ruff_answer_is_an_error_never_a_clean_pass(
    repo: Path, tmp_path: Path
) -> None:
    """Drift ruff reports in a shape this gate cannot parse must not arrive as `no drift`."""
    unreadable = _stub_ruff(tmp_path, "1 file would be reformatted", 1)
    proc = _gate_with(unreadable, repo)
    assert proc.returncode == lint_gate.ERROR, proc.stdout + proc.stderr
    said = proc.stdout + proc.stderr
    assert "verified NOTHING" in said
    assert "clean (rules + format)" not in said


def test_ruff_failing_to_run_at_all_is_an_error(repo: Path, tmp_path: Path) -> None:
    proc = _gate_with(_stub_ruff(tmp_path, "ruff failed: bad config", 2), repo)
    assert proc.returncode == lint_gate.ERROR, proc.stdout + proc.stderr
    assert "clean (rules + format)" not in proc.stdout + proc.stderr


# --- the rule set is this repository's, not the installed ruff's -----------------------------------


def test_the_rule_set_is_declared_rather_than_inherited_from_ruff_defaults() -> None:
    """Run through ruff itself, at this repository's root, so the assertion is about the config
    that is IN FORCE rather than about the text of a file. Unsorted imports (I001) are outside the
    declared set and must stay outside it whatever the installed ruff defaults to; the unused
    import (F401) proves the same invocation does still lint."""
    proc = subprocess.run(
        [
            "ruff",
            "check",
            "--no-cache",
            "--output-format",
            "concise",
            "--stdin-filename",
            str(ROOT / "declared_rule_set_probe.py"),
            "-",
        ],
        input="import os\nimport abc\n",
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    said = proc.stdout + proc.stderr
    assert "F401" in said, said
    assert "I001" not in said, said
