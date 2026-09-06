"""What must hold around `cosmic-ray exec` (issue #95): the tree it leaves, and whether it can finish.

The hook's kill path left a mutant applied in the working tree, where it was committed as authored
code. `scripts/mutation_exec_guard.py` decides the three things the hook now asks at the point each is
decided: what the in-scope files held before exec, whether they hold it afterwards (and puts them back
from that snapshot when they do not), and whether exec can finish inside the seconds the hook has
left. The snapshot is the restore source on purpose - the pipeline verifies UNCOMMITTED work, so
`git checkout` would restore the wrong state.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mutation_exec_guard as guard  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the snapshot records `git status --porcelain`, so the fixtures are real repositories"
)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path):
    """A repository with a committed module, an uncommitted edit to it, and a session naming it."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    tracked = root / "pkg" / "app.py"
    tracked.write_text("def a():\n    return 1\n", encoding="utf-8")
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("add", "-A"),
        ("commit", "-q", "-m", "root"),
    ):
        git(root, *args)
    # The implementer's uncommitted work: this, not HEAD, is what a restore must bring back.
    tracked.write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8"
    )
    untracked = root / "pkg" / "new.py"
    untracked.write_text("def c():\n    return 3\n", encoding="utf-8")

    from cosmic_ray.work_db import WorkDB, use_db
    from cosmic_ray.work_item import MutationSpec, WorkItem

    session = tmp_path / "session.sqlite"
    with use_db(str(session), WorkDB.Mode.create) as db:
        for job, path, line in (
            ("m0", tracked, 2),
            ("m1", tracked, 6),
            ("m2", untracked, 2),
        ):
            db.add_work_item(
                WorkItem.single(
                    job,
                    MutationSpec(
                        module_path=str(path.relative_to(root)),
                        operator_name="core/NumberReplacer",
                        occurrence=0,
                        start_pos=(line, 4),
                        end_pos=(line, 12),
                    ),
                )
            )
    return root, session, tracked, untracked


class TestSnapshotAndRestore:
    def test_an_unchanged_tree_is_intact(self, repo, tmp_path):
        root, session, tracked, untracked = repo
        snap = tmp_path / "snap"

        assert guard.snapshot(root, session, snap) == guard.OK
        assert guard.restore(root, snap) == guard.OK

    def test_a_changed_file_is_put_back_to_the_uncommitted_state_and_reported(
        self, repo, tmp_path, capsys
    ):
        root, session, tracked, untracked = repo
        before = tracked.read_text(encoding="utf-8")
        snap = tmp_path / "snap"
        guard.snapshot(root, session, snap)

        tracked.write_text(
            before.replace("return 1", "return 2"), encoding="utf-8"
        )  # a mutant
        code = guard.restore(root, snap)

        assert code == guard.DIFFERED, (
            "a tree that had to be restored is a tree that differed"
        )
        assert tracked.read_text(encoding="utf-8") == before, (
            "restored to the snapshot - the implementer's uncommitted `b()` survives, HEAD is not "
            "what comes back"
        )
        err = capsys.readouterr().err
        assert "RESTORED" in err and str(tracked) in err
        assert "TREE DIFFERED" in err

    def test_an_untracked_in_scope_file_is_covered_too(self, repo, tmp_path):
        """Issue #97's shape: a phase whose contribution is new files. They are in the session, so
        they are in the snapshot; git's view of them is irrelevant to the hash."""
        root, session, tracked, untracked = repo
        before = untracked.read_text(encoding="utf-8")
        snap = tmp_path / "snap"
        guard.snapshot(root, session, snap)

        untracked.write_text("def c():\n    return 4\n", encoding="utf-8")

        assert guard.restore(root, snap) == guard.DIFFERED
        assert untracked.read_text(encoding="utf-8") == before

    def test_a_deleted_in_scope_file_is_recreated(self, repo, tmp_path):
        root, session, tracked, untracked = repo
        before = untracked.read_bytes()
        snap = tmp_path / "snap"
        guard.snapshot(root, session, snap)

        untracked.unlink()

        assert guard.restore(root, snap) == guard.DIFFERED
        assert untracked.read_bytes() == before

    def test_a_missing_snapshot_is_an_error_never_intact(self, repo, tmp_path):
        root, session, tracked, untracked = repo

        assert guard.restore(root, tmp_path / "nowhere") == guard.ERROR

    def test_a_file_the_session_names_but_the_tree_lacks_refuses_the_snapshot(
        self, repo, tmp_path
    ):
        """No snapshot, no exec: a set that could not be recorded cannot be compared afterwards."""
        root, session, tracked, untracked = repo
        untracked.unlink()

        assert guard.snapshot(root, session, tmp_path / "snap") == guard.ERROR

    def test_the_snapshot_names_only_files_the_session_can_write(self, repo, tmp_path):
        root, session, tracked, untracked = repo
        (root / "pkg" / "bystander.py").write_text("x = 1\n", encoding="utf-8")
        snap = tmp_path / "snap"
        guard.snapshot(root, session, snap)

        files = {Path(p) for p in guard.session_files(session, root)}
        assert files == {tracked.resolve(), untracked.resolve()}


class TestBudget:
    def test_the_estimate_is_baseline_times_pending(self):
        assert guard.estimate_seconds(pending=42, baseline_s=7.2) == 303
        assert guard.estimate_seconds(pending=0, baseline_s=7.2) == 0

    def test_an_exec_that_fits_proceeds(self, repo, capsys):
        root, session, tracked, untracked = repo

        assert guard.budget(session, baseline_s=1.0, budget_s=10) == guard.OK
        assert "3 pending mutant(s)" in capsys.readouterr().out

    def test_an_exec_that_would_overrun_is_refused_and_says_the_remedy(
        self, repo, capsys
    ):
        root, session, tracked, untracked = repo

        assert guard.budget(session, baseline_s=3.0, budget_s=8) == guard.DIFFERED
        err = capsys.readouterr().err
        assert "OVER BUDGET" in err
        assert "MUTATION_HOOK_BUDGET_S" in err

    def test_mutants_already_answered_are_not_pending(self, repo):
        """After scoping, skipped mutants carry a result. They cost no test run and are not counted."""
        root, session, tracked, untracked = repo
        from cosmic_ray.work_db import WorkDB, use_db
        from cosmic_ray.work_item import WorkerOutcome, WorkResult

        with use_db(str(session), WorkDB.Mode.open) as db:
            db.set_result(
                "m2",
                WorkResult(output="filtered", worker_outcome=WorkerOutcome.SKIPPED),
            )

        assert guard.pending_mutants(session) == 2

    def test_an_unreadable_session_is_an_error(self, tmp_path):
        bogus = tmp_path / "session.sqlite"
        bogus.write_text("not a database", encoding="utf-8")

        assert guard.budget(bogus, baseline_s=1.0, budget_s=100) == guard.ERROR


class TestCli:
    def test_restore_exit_code_is_the_verdict(self, repo, tmp_path):
        root, session, tracked, untracked = repo
        snap = tmp_path / "snap"
        script = ROOT / "scripts" / "mutation_exec_guard.py"

        taken = subprocess.run(
            [
                sys.executable,
                str(script),
                "snapshot",
                "--root",
                str(root),
                "--session",
                str(session),
                str(snap),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert taken.returncode == 0, taken.stderr
        tracked.write_text("def a():\n    return 9\n", encoding="utf-8")
        put_back = subprocess.run(
            [sys.executable, str(script), "restore", "--root", str(root), str(snap)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert put_back.returncode == 1
        assert "RESTORED" in put_back.stderr
        intact = subprocess.run(
            [sys.executable, str(script), "restore", "--root", str(root), str(snap)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert intact.returncode == 0
        assert "SCOPE OF A CLEAN RESULT" in intact.stderr, (
            "a clean result states its scope on stderr (issue #97)"
        )
