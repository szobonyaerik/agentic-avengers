"""Tests for the trigger that makes an amendment mandatory after a fix pass (issue #51).

The mechanism (`scripts/amendments.py`) already existed. What was missing is the rule that makes it
necessary: a fix pass changed verified production code, touched no phase artifact, and the passing
verdict went on asserting a tree that no longer existed. Nothing noticed and nothing said so.

The forbidden remedy is asserted here too, because it is the obvious one: nothing in this module may
suggest rewriting `verdict.json`, which would restate a verification nobody performed.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verdict_currency  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the subject is what git says changed after a commit; a double would prove the double"
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one verified phase: source, tests, and a passing verdict, all committed."""
    git(tmp_path, "init", "-q")
    phase = tmp_path / "docs" / "features" / "demo" / "phases" / "9-tasks"
    phase.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "create_task.py").write_text("def create():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_create.py").write_text("def test_create():\n    assert True\n")
    commit(tmp_path, "phase 9 implementation")
    (phase / "verdict.json").write_text(json.dumps({"verdict": "pass", "findings": []}))
    commit(tmp_path, "phase 9 verdict")
    return tmp_path


def feature(repo: Path) -> Path:
    return repo / "docs" / "features" / "demo"


def test_a_verified_tree_nobody_touched_is_current(repo: Path) -> None:
    assert verdict_currency.check(repo, feature(repo)) is None


def test_a_fix_pass_over_verified_code_owes_an_amendment(repo: Path) -> None:
    """The measurement: five fixes applied to verified, closed production code, no phase artifact
    touched, and `verdict.json` still asserting the file was byte-identical."""
    (repo / "src" / "create_task.py").write_text("def create():\n    return 2  # review gate fix\n")
    commit(repo, "review gate: fix create_task")

    finding = verdict_currency.check(repo, feature(repo))

    assert finding is not None
    assert "src/create_task.py" in finding
    assert "amendment" in finding
    # The dangerous remedy must never be offered.
    assert "rewrite verdict.json" in finding and "Do NOT" in finding


def test_new_test_files_from_a_fix_pass_count_too(repo: Path) -> None:
    """The same fix pass left the recorded suite totals stale by two new test files."""
    (repo / "tests" / "test_fix.py").write_text("def test_fix():\n    assert True\n")
    commit(repo, "review gate: two new tests")

    assert verdict_currency.check(repo, feature(repo)) is not None


def test_an_opened_amendment_discharges_it(repo: Path) -> None:
    """The remedy the finding prescribes must actually clear it — a rule whose remedy is
    unavailable is a wedge, not a gate."""
    (repo / "src" / "create_task.py").write_text("def create():\n    return 2\n")
    commit(repo, "review gate: fix create_task")
    phase = feature(repo) / "phases" / "9-tasks"
    (phase / "amendments.json").write_text(json.dumps({"amendments": [{"id": "A1"}]}))

    assert verdict_currency.check(repo, feature(repo)) is None


def test_the_features_own_e2e_suite_is_not_a_post_verification_change(repo: Path) -> None:
    """`tests/e2e/<feature>/` is written once after the last phase is green, BY DESIGN, and is
    excluded from the phase verifier. Holding the feature for it would block its own next stage."""
    e2e = repo / "tests" / "e2e" / "demo"
    e2e.mkdir(parents=True)
    (e2e / "test_journey.py").write_text("def test_journey():\n    assert True\n")
    commit(repo, "e2e author")

    assert verdict_currency.check(repo, feature(repo)) is None


def test_the_pipelines_own_artifacts_are_not_a_post_verification_change(repo: Path) -> None:
    """The handover, the ledgers and the verdict itself land after verification by design."""
    (feature(repo) / "phases" / "9-tasks" / "handover.md").write_text("---\nfeature: demo\n---\n")
    commit(repo, "phase 9 handover")

    assert verdict_currency.check(repo, feature(repo)) is None


def test_a_feature_with_no_committed_verdict_is_not_a_finding(tmp_path: Path, capsys) -> None:
    """Nothing to be stale against. That is a feature not yet verified, not a stale one."""
    git(tmp_path, "init", "-q")
    fdir = tmp_path / "docs" / "features" / "demo"
    (fdir / "phases" / "1-a").mkdir(parents=True)
    (tmp_path / "src.py").write_text("x = 1\n")
    commit(tmp_path, "work")

    assert verdict_currency.check(tmp_path, fdir) is None
    assert "NOT checked" in capsys.readouterr().err


def test_an_unknowable_scope_enforces_nothing_and_says_so(tmp_path: Path, capsys) -> None:
    """Not a git repository: the same fail-open every other check on this boundary uses."""
    fdir = tmp_path / "docs" / "features" / "demo"
    (fdir / "phases" / "1-a").mkdir(parents=True)

    assert verdict_currency.check(tmp_path, fdir) is None
    assert "NOT checked" in capsys.readouterr().err


def test_ordinary_phase_work_before_the_newest_verdict_is_not_held(repo: Path) -> None:
    """An implementer changing source while an EARLIER phase's verdict stands is ordinary work.

    The anchor is the NEWEST verdict in the feature, so phase 10's commits are only measured once
    phase 10's own verdict has landed — by which time they precede it.
    """
    (repo / "src" / "create_task.py").write_text("def create():\n    return 3\n")
    commit(repo, "phase 10 implementation")
    phase10 = feature(repo) / "phases" / "10-next"
    phase10.mkdir()
    (phase10 / "verdict.json").write_text(json.dumps({"verdict": "pass", "findings": []}))
    commit(repo, "phase 10 verdict")

    assert verdict_currency.check(repo, feature(repo)) is None


def test_the_cli_exits_1_on_a_finding_and_0_when_current(repo: Path) -> None:
    assert verdict_currency.main(["check", str(feature(repo))]) == verdict_currency.CURRENT
    (repo / "src" / "create_task.py").write_text("def create():\n    return 2\n")
    commit(repo, "review gate fix")
    assert verdict_currency.main(["check", str(feature(repo))]) == verdict_currency.OWED
