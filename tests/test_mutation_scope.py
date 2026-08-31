"""Tests for the mutation gate's working-tree scope.

## The defect

Issue #97's second instance, and the sharpest one: the mutation gate diff-scoped through
`cr-filter-git`, whose whole mechanism is `git diff --relative -U0 <branch> .`. `git diff` reports
nothing about an UNTRACKED file, and the pipeline verifies uncommitted work — so a phase whose
contribution is new files had every mutant marked skipped, `tested` came out zero, and the scorer
returned GO. Nothing anywhere said the gate had not run.

So the dangerous direction here is a scope that is too NARROW, and most of these pin "must still be
in scope" cases — a brand-new file above all. The other direction is pinned too: a file the change
never touched must stay out, or the gate mutates the whole package on every run.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mutation_scope  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "git is the authority for what the working tree changed; a stubbed git would only test the stub"
)


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
    ):
        git(root, *args)
    (root / "pkg").mkdir()
    (root / "pkg" / "existing.py").write_text(
        "def a():\n    return 1\n", encoding="utf-8"
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "root")
    return root


class TestChangedLines:
    def test_a_brand_new_untracked_file_is_entirely_in_scope(self, repo):
        """The instance itself. `git diff` says nothing about a file git has never seen."""
        new = repo / "pkg" / "added.py"
        new.write_text("def b():\n    return 2\n", encoding="utf-8")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert scope[new.resolve()] == {1, 2}

    def test_the_query_the_old_filter_made_cannot_see_a_new_file(self, repo):
        """Pins the MECHANISM, so the reason this module exists is measured rather than asserted.

        `cr-filter-git` scopes with exactly `git diff --relative -U0 <branch> .`. Run against a
        brand-new file that query is empty, so every mutant in it was marked skipped and the scorer
        reported GO. This asserts both halves in one place: the old query is blind, this one is not.
        """
        new = repo / "pkg" / "added.py"
        new.write_text("def b():\n    return 2\n", encoding="utf-8")

        old_query = git(repo, "diff", "--relative", "-U0", "HEAD", ".")

        assert "added.py" not in old_query
        scope = mutation_scope.changed_lines(repo)
        assert scope is not None and new.resolve() in scope

    def test_a_new_file_that_is_staged_but_not_committed_is_in_scope(self, repo):
        new = repo / "pkg" / "staged.py"
        new.write_text("def c():\n    return 3\n", encoding="utf-8")
        git(repo, "add", "pkg/staged.py")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert scope[new.resolve()] == {1, 2}

    def test_an_uncommitted_edit_to_a_tracked_file_is_in_scope(self, repo):
        target = repo / "pkg" / "existing.py"
        target.write_text("def a():\n    return 99\n", encoding="utf-8")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert 2 in scope[target.resolve()]

    @pytest.mark.parametrize("setting", ["diff.mnemonicPrefix", "diff.noprefix"])
    def test_the_operators_git_config_cannot_narrow_the_scope(self, repo, setting):
        """`git diff` is porcelain and its header prefix is configurable.

        Under `diff.mnemonicPrefix=true` a tracked file arrives as `+++ w/<path>`, so a parser
        anchored on `+++ b/` dropped every hunk. With one untracked file keeping the scope
        non-empty, the gate exited OK having measured a fraction of the change - a wrong result
        with no error, from the module written to stop exactly that.
        """
        git(repo, "config", setting, "true")
        target = repo / "pkg" / "existing.py"
        target.write_text("def a():\n    return 99\n", encoding="utf-8")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert 2 in scope[target.resolve()]

    def test_added_content_that_looks_like_a_diff_header_cannot_narrow_the_scope(
        self, repo
    ):
        """Under `-U0` an added line beginning `++ ` renders as `+++ <rest>`.

        Parsed as a file header that repointed the scope at a path which does not exist, and every
        LATER hunk of the real file was attributed to it and dropped. Another changed line kept the
        scope non-empty, so the gate exited OK over a fraction of the change - the silent-narrowing
        class this module exists to stop, reproduced inside it.
        """
        target = repo / "pkg" / "existing.py"
        target.write_text(
            "".join(f"x{i} = {i}\n" for i in range(1, 12)), encoding="utf-8"
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "wide")
        rows = target.read_text(encoding="utf-8").splitlines()
        rows[2] = "++ hack"
        rows[9] = "x10 = 999"
        target.write_text("\n".join(rows) + "\n", encoding="utf-8")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert scope[target.resolve()] == {3, 10}
        assert not [path for path in scope if path.name == "hack"]

    def test_an_untracked_path_git_has_to_quote_is_still_in_scope(self, repo):
        """`core.quotePath` is ON by default, and the untracked half is what this module exists for.

        Without `-z`, `git ls-files --others` returns `"pkg/caf\\303\\251.py"` verbatim, the join
        resolves to a path that does not exist, `read_text` raises OSError and the swallow labelled
        it "a binary or unreadable file holds no mutants either way". A brand-new source file left
        the scope with no error and no message, while another changed file kept `kept > 0` so the
        gate reported a normal pass over a scope missing a whole file.
        """
        new = repo / "pkg" / "caf\u00e9.py"
        new.write_text("def b():\n    return 2\n", encoding="utf-8")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert scope[new.resolve()] == {1, 2}

    def test_a_name_with_pathspec_wildcards_does_not_absorb_another_file(self, repo):
        """The per-file query passes a git PATHSPEC, which is fnmatch-matched.

        `app/[slug]/page.tsx` is an ordinary frontend filename, and unquoted it matched OTHER files
        and folded their new-side line numbers into this one's set - a scope that is not the truth,
        putting mutants on unchanged lines.
        """
        bracket = repo / "pkg" / "a[bc].py"
        plain = repo / "pkg" / "ab.py"
        for target in (bracket, plain):
            target.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "both")
        bracket.write_text("x = 1\ny = 99\nz = 3\n", encoding="utf-8")
        plain.write_text("x = 42\ny = 2\nz = 3\n", encoding="utf-8")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert scope[bracket.resolve()] == {2}
        assert scope[plain.resolve()] == {1}

    def test_an_untracked_path_that_cannot_be_opened_is_unknowable_not_empty(
        self, repo
    ):
        """A path git just reported that will not open is a scope this module could not compute."""
        broken = repo / "pkg" / "gone.py"
        broken.symlink_to(repo / "pkg" / "does-not-exist.py")

        assert mutation_scope.changed_lines(repo) is None

    def test_content_that_is_not_utf8_is_skipped_out_loud(self, repo, capsys):
        """Binary holds no mutants a source operator generates - skippable, but never in silence."""
        blob = repo / "pkg" / "image.py"
        blob.write_bytes(b"\xff\xfe\x00\x01binary")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert blob.resolve() not in scope
        assert "not UTF-8 text" in capsys.readouterr().err

    def test_a_deleted_file_lands_in_no_other_files_scope(self, repo):
        """`+++ /dev/null` names no file; attributing its hunks to the previous one would be worse."""
        git(repo, "rm", "-q", "pkg/existing.py")

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert (repo / "pkg" / "existing.py").resolve() not in scope

    def test_an_untouched_file_is_not_in_scope(self, repo):
        (repo / "pkg" / "added.py").write_text(
            "def b():\n    return 2\n", encoding="utf-8"
        )

        scope = mutation_scope.changed_lines(repo)

        assert scope is not None
        assert (repo / "pkg" / "existing.py").resolve() not in scope

    def test_a_base_widens_the_scope_to_the_branch(self, repo):
        """In CI nothing is uncommitted, so the working tree alone reports an empty change."""
        git(repo, "checkout", "-q", "-b", "feature")
        (repo / "pkg" / "committed.py").write_text(
            "def d():\n    return 4\n", encoding="utf-8"
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "feature work")

        assert mutation_scope.changed_lines(repo) == {}

        scope = mutation_scope.changed_lines(repo, base="main")
        assert scope is not None
        assert (repo / "pkg" / "committed.py").resolve() in scope

    def test_no_git_answer_is_none_and_never_an_empty_scope(self, tmp_path):
        """None is 'unknowable'. Read as 'nothing changed' it would skip every mutant silently."""
        assert mutation_scope.changed_lines(tmp_path) is None

    def test_an_unresolvable_base_is_unknowable(self, repo):
        assert mutation_scope.changed_lines(repo, base="no-such-ref") is None


class TestSelection:
    def test_a_mutant_on_a_changed_line_is_kept(self, repo):
        target = (repo / "pkg" / "added.py").resolve()
        scope = {target: {2, 3}}
        assert mutation_scope.in_scope(target, 2, 2, scope)

    def test_a_mutant_spanning_into_a_changed_line_is_kept(self, repo):
        target = (repo / "pkg" / "added.py").resolve()
        assert mutation_scope.in_scope(target, 1, 3, {target: {2}})

    def test_a_mutant_outside_every_changed_line_is_dropped(self, repo):
        target = (repo / "pkg" / "added.py").resolve()
        assert not mutation_scope.in_scope(target, 7, 8, {target: {2, 3}})

    def test_a_mutant_in_an_untouched_file_is_dropped(self, repo):
        target = (repo / "pkg" / "added.py").resolve()
        other = (repo / "pkg" / "existing.py").resolve()
        assert not mutation_scope.in_scope(other, 1, 1, {target: {1}})


class TestVerdict:
    def test_nothing_in_scope_is_not_a_pass(self):
        """The whole instance in one assertion: an empty scope must never read as a clean gate."""
        code, message = mutation_scope.verdict(kept=0, skipped=12, total=12)
        assert code == mutation_scope.NOTHING_IN_SCOPE
        assert code != mutation_scope.OK
        assert "did not run" in message

    def test_an_empty_session_is_not_a_pass(self):
        code, _ = mutation_scope.verdict(kept=0, skipped=0, total=0)
        assert code == mutation_scope.NOTHING_IN_SCOPE

    def test_at_least_one_mutant_in_scope_is_ok(self):
        code, message = mutation_scope.verdict(kept=3, skipped=9, total=12)
        assert code == mutation_scope.OK
        assert "3" in message


class TestFilterSession:
    """Against a REAL cosmic-ray session, because the filter's whole job is what the DB holds."""

    def session_with(self, path: Path, items: list[tuple[str, Path, int]]):
        from cosmic_ray.work_db import WorkDB, use_db
        from cosmic_ray.work_item import MutationSpec, WorkItem

        with use_db(str(path), WorkDB.Mode.create) as db:
            for job_id, module_path, line in items:
                db.add_work_item(
                    WorkItem.single(
                        job_id,
                        MutationSpec(
                            module_path=module_path,
                            operator_name="core/NumberReplacer",
                            occurrence=0,
                            start_pos=(line, 4),
                            end_pos=(line, 8),
                        ),
                    )
                )

    def test_a_mutant_in_a_brand_new_file_survives_the_filter(self, repo, tmp_path):
        """The instance, end to end: the old filter skipped this one and the gate reported GO."""
        new = repo / "pkg" / "added.py"
        new.write_text("def b():\n    return 2\n", encoding="utf-8")
        session = tmp_path / "s.sqlite"
        self.session_with(session, [("new", new.resolve(), 2)])

        code = mutation_scope.main([str(session), "--root", str(repo)])

        assert code == mutation_scope.OK

    def test_a_mutant_in_an_untouched_file_is_marked_skipped(self, repo, tmp_path):
        from cosmic_ray.work_db import WorkDB, use_db
        from cosmic_ray.work_item import WorkerOutcome

        new = repo / "pkg" / "added.py"
        new.write_text("def b():\n    return 2\n", encoding="utf-8")
        session = tmp_path / "s.sqlite"
        self.session_with(
            session,
            [
                ("keep", new.resolve(), 2),
                ("drop", (repo / "pkg" / "existing.py").resolve(), 2),
            ],
        )

        assert (
            mutation_scope.main([str(session), "--root", str(repo)])
            == mutation_scope.OK
        )

        with use_db(str(session), WorkDB.Mode.open) as db:
            outcomes = {job: result.worker_outcome for job, result in db.results}
        assert outcomes == {"drop": WorkerOutcome.SKIPPED}

    def test_a_session_with_nothing_in_scope_exits_loudly(self, repo, tmp_path, capsys):
        """The acceptance criterion: it can no longer report a null score in silence."""
        session = tmp_path / "s.sqlite"
        self.session_with(
            session, [("drop", (repo / "pkg" / "existing.py").resolve(), 2)]
        )

        code = mutation_scope.main([str(session), "--root", str(repo)])

        assert code == mutation_scope.NOTHING_IN_SCOPE
        assert "did not run" in capsys.readouterr().err

    def test_an_unknowable_scope_is_an_error_and_never_an_empty_one(
        self, tmp_path, capsys
    ):
        session = tmp_path / "s.sqlite"
        self.session_with(session, [("x", tmp_path / "a.py", 1)])

        code = mutation_scope.main([str(session), "--root", str(tmp_path)])

        assert code == mutation_scope.ERROR
        assert "UNKNOWABLE" in capsys.readouterr().err
