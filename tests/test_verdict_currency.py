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
    (tmp_path / "tests" / "test_create.py").write_text(
        "def test_create():\n    assert True\n"
    )
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
    (repo / "src" / "create_task.py").write_text(
        "def create():\n    return 2  # review gate fix\n"
    )
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


def test_the_features_own_e2e_suite_is_not_a_post_verification_change(
    repo: Path,
) -> None:
    """`tests/e2e/<feature>/` is written once after the last phase is green, BY DESIGN, and is
    excluded from the phase verifier. Holding the feature for it would block its own next stage."""
    e2e = repo / "tests" / "e2e" / "demo"
    e2e.mkdir(parents=True)
    (e2e / "test_journey.py").write_text("def test_journey():\n    assert True\n")
    commit(repo, "e2e author")

    assert verdict_currency.check(repo, feature(repo)) is None


def test_the_pipelines_own_artifacts_are_not_a_post_verification_change(
    repo: Path,
) -> None:
    """The handover, the ledgers and the verdict itself land after verification by design."""
    (feature(repo) / "phases" / "9-tasks" / "handover.md").write_text(
        "---\nfeature: demo\n---\n"
    )
    commit(repo, "phase 9 handover")

    assert verdict_currency.check(repo, feature(repo)) is None


def test_a_feature_with_no_committed_verdict_is_not_a_finding(
    tmp_path: Path, capsys
) -> None:
    """Nothing to be stale against. That is a feature not yet verified, not a stale one."""
    git(tmp_path, "init", "-q")
    fdir = tmp_path / "docs" / "features" / "demo"
    (fdir / "phases" / "1-a").mkdir(parents=True)
    (tmp_path / "src.py").write_text("x = 1\n")
    commit(tmp_path, "work")

    assert verdict_currency.check(tmp_path, fdir) is None
    assert "NOT checked" in capsys.readouterr().err


def test_an_unknowable_scope_enforces_nothing_and_says_so(
    tmp_path: Path, capsys
) -> None:
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
    (phase10 / "verdict.json").write_text(
        json.dumps({"verdict": "pass", "findings": []})
    )
    commit(repo, "phase 10 verdict")

    assert verdict_currency.check(repo, feature(repo)) is None


def test_the_cli_exits_1_on_a_finding_and_0_when_current(repo: Path) -> None:
    assert (
        verdict_currency.main(["check", str(feature(repo))]) == verdict_currency.CURRENT
    )
    (repo / "src" / "create_task.py").write_text("def create():\n    return 2\n")
    commit(repo, "review gate fix")
    assert verdict_currency.main(["check", str(feature(repo))]) == verdict_currency.OWED


# ── an UNCOMMITTED verdict anchors on the head its evidence recorded (issue #97, folded #122) ──


def record_run(root: Path, phase: Path) -> None:
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(ROOT / "scripts" / "verifier_evidence.py"),
            "record",
            str(phase),
            "--kind",
            "suite",
            "--",
            "/bin/echo",
            "1 passed",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def open_repo(tmp_path: Path) -> Path:
    """A phase mid-close: source committed, verification recorded at HEAD, verdict on disk and NOT
    committed - the state the handover hook sees, where the old anchor did not exist."""
    git(tmp_path, "init", "-q")
    phase = tmp_path / "docs" / "features" / "demo" / "phases" / "3-frames"
    (phase / "specs" / "3.1-a").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "frames.py").write_text("def frames():\n    return 1\n")
    (tmp_path / "tests" / "demo" / "3-frames").mkdir(parents=True)
    (tmp_path / "tests" / "demo" / "3-frames" / "test_f.py").write_text(
        "def test_f():\n    assert True\n"
    )
    commit(tmp_path, "phase 3 implementation")
    record_run(tmp_path, phase)
    (phase / "verdict.json").write_text(json.dumps({"verdict": "pass", "findings": []}))
    return tmp_path


def commit_paths(root: Path, message: str, *paths: str) -> None:
    """A fix round commits the SOURCE it touched and never the phase's open verdict or its evidence -
    `git add -A` here would land the verdict in the fix commit and make the anchor the fix itself."""
    git(root, "add", "--", *paths)
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)


def test_an_open_verdict_over_a_tree_nobody_moved_is_current(open_repo: Path) -> None:
    assert verdict_currency.check(open_repo, feature(open_repo)) is None


def test_a_fix_round_that_moves_the_head_while_a_verdict_is_open_owes_an_amendment(
    open_repo: Path,
) -> None:
    """The folded instance (faithful-rep phase 3): a fix round committed while the run was open, and
    only a human reading two heads side by side noticed. RED before the fix: with no committed
    verdict this check said `NOT checked` and stepped aside."""
    (open_repo / "src" / "frames.py").write_text(
        "def frames():\n    return 2  # fix round\n"
    )
    commit_paths(open_repo, "review gate: fix frames", "src/frames.py")

    finding = verdict_currency.check(open_repo, feature(open_repo))

    assert finding is not None
    assert "src/frames.py" in finding
    assert "amendment" in finding


def test_an_amendment_opened_for_the_fix_round_discharges_it(open_repo: Path) -> None:
    (open_repo / "src" / "frames.py").write_text("def frames():\n    return 2\n")
    commit_paths(open_repo, "review gate: fix frames", "src/frames.py")
    (feature(open_repo) / "phases" / "3-frames" / "amendments.json").write_text("{}")
    assert verdict_currency.check(open_repo, feature(open_repo)) is None


def test_a_recorded_head_the_repository_does_not_know_anchors_nothing(
    open_repo: Path, capsys
) -> None:
    phase = feature(open_repo) / "phases" / "3-frames"
    record_path = phase / "verification-evidence.json"
    data = json.loads(record_path.read_text())
    for run in data["runs"]:
        run["head"] = "f" * 40
    record_path.write_text(json.dumps(data))
    assert verdict_currency.check(open_repo, feature(open_repo)) is None
    assert "not a commit this repository knows" in capsys.readouterr().err


def test_a_committed_verdict_still_anchors_on_its_own_commit_not_its_evidence_head(
    open_repo: Path,
) -> None:
    """The phase's own commit lands AFTER its verification and carries the verified source. Anchoring
    a committed verdict on its evidence head would owe every phase an amendment for being committed."""
    commit(open_repo, "phase 3 verdict")
    assert verdict_currency.check(open_repo, feature(open_repo)) is None


def test_a_run_with_no_verdict_yet_anchors_nothing(tmp_path: Path, capsys) -> None:
    git(tmp_path, "init", "-q")
    phase = tmp_path / "docs" / "features" / "demo" / "phases" / "1-a"
    phase.mkdir(parents=True)
    (tmp_path / "src.py").write_text("x = 1\n")
    commit(tmp_path, "work")
    record_run(tmp_path, phase)
    assert verdict_currency.check(tmp_path, feature(tmp_path)) is None
    assert "NOT checked" in capsys.readouterr().err
