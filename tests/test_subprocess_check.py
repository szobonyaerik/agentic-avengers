"""Tests for the subprocess check.

The defect this check exists for is a test that spawns a nested run of the whole suite: four of them
survived spec review, fidelity checking and verification across five phases,
because every one of those stages reads for correctness and none of them can see a subprocess.

So the dangerous direction here is a MISS, and most of these pin "must still be flagged" cases —
including the aliasing forms an author reaches for without meaning to evade anything
(`import subprocess as sp`, `from subprocess import run`).

The scope cases below pin the OTHER dangerous direction, which this check met on its first real
repository: it refused every spec write of a phase over 17 undeclared spawners in locked phase-1 and
phase-7 tests that the phase had never opened. Both directions matter — a spawner in a file this
change touched must still block, and one in a file it did not must not.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from subprocess_check import (  # noqa: E402
    CLEAN,
    ERROR,
    VIOLATIONS,
    main,
    scan_source,
)

SPAWNER = "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n"


@pytest.fixture(autouse=True)
def _no_inherited_project_dir(monkeypatch):
    """The diff scope is rooted at $CLAUDE_PROJECT_DIR, which a session running these tests exports.

    Left inherited, every scope case would silently answer about THIS repository instead of its own
    fixture — a green suite measuring the wrong tree.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def violations(source: str) -> list[str]:
    """The rendered reason of each violation found in one test module."""
    return [v.reason for v in scan_source(source, Path("tests/test_x.py"))]


@pytest.mark.subprocess(
    "the diff scope is whatever git reports, and a stubbed git would only ever test the stub"
)
def git_repo(root: Path) -> Path:
    """A real repository, because git is the authority for what this change touched."""
    for args in (
        ("init", "-q"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("commit", "-q", "--allow-empty", "-m", "root"),
    ):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)
    return root


@pytest.mark.subprocess("commits the fixture so a later edit is what the diff reports")
def git_commit(root: Path) -> None:
    for args in (("add", "-A"), ("commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


class TestDetection:
    def test_subprocess_run_is_flagged(self):
        assert violations("import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n")

    @pytest.mark.parametrize(
        "call", ["run", "Popen", "check_output", "check_call", "call"]
    )
    def test_every_banned_subprocess_entry_point(self, call):
        source = f"import subprocess\n\ndef test_a():\n    subprocess.{call}(['ls'])\n"
        assert violations(source)

    @pytest.mark.parametrize("call", ["create_subprocess_exec", "create_subprocess_shell"])
    def test_asyncio_spawners(self, call):
        source = f"import asyncio\n\nasync def test_a():\n    await asyncio.{call}('ls')\n"
        assert violations(source)

    def test_module_alias_does_not_hide_the_call(self):
        assert violations("import subprocess as sp\n\ndef test_a():\n    sp.run(['ls'])\n")

    def test_from_import_does_not_hide_the_call(self):
        assert violations("from subprocess import run\n\ndef test_a():\n    run(['ls'])\n")

    def test_from_import_alias_does_not_hide_the_call(self):
        assert violations(
            "from subprocess import run as go\n\ndef test_a():\n    go(['ls'])\n"
        )

    def test_a_helper_outside_a_test_function_is_still_flagged(self):
        """The nested-pytest case lived in a module-level helper, not in the test body."""
        assert violations("import subprocess\n\ndef _run():\n    subprocess.run(['ls'])\n")

    def test_unrelated_call_named_run_is_not_flagged(self):
        assert not violations("def test_a():\n    runner.run(['ls'])\n")

    def test_a_bare_name_that_was_never_imported_from_subprocess(self):
        assert not violations("def run(x):\n    pass\n\ndef test_a():\n    run(1)\n")

    def test_reports_the_line_number(self):
        source = "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n"
        assert scan_source(source, Path("tests/test_x.py"))[0].line == 4


class TestMarker:
    def test_marked_test_with_a_justification_is_allowed(self):
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.subprocess("shells out to the real git binary")\n'
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_reason_keyword_counts_as_the_justification(self):
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.subprocess(reason="drives the installed CLI end to end")\n'
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_marker_without_a_justification_is_a_violation(self):
        """The marker alone is a rubber stamp; the sentence is what a reviewer reads."""
        source = (
            "import pytest, subprocess\n\n"
            "@pytest.mark.subprocess\n"
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        found = violations(source)
        assert found and "justification" in found[0]

    def test_empty_justification_is_a_violation(self):
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.subprocess("   ")\n'
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        assert violations(source)

    def test_module_level_pytestmark_covers_the_whole_file(self):
        """A file of hook tests spawns in every test; one declaration is the honest shape."""
        source = (
            "import pytest, subprocess\n\n"
            'pytestmark = pytest.mark.subprocess("every test drives the hook as a real process")\n\n'
            "def _helper():\n    subprocess.run(['ls'])\n\n"
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_module_level_pytestmark_list_form(self):
        source = (
            "import pytest, subprocess\n\n"
            "pytestmark = [pytest.mark.slow, "
            'pytest.mark.subprocess("drives the real binary")]\n\n'
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_a_different_marker_does_not_grant_the_exemption(self):
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.slow("takes a while")\n'
            "def test_a():\n    subprocess.run(['ls'])\n"
        )
        assert violations(source)

    def test_a_class_decorator_declares_its_methods(self):
        """`@pytest.mark.subprocess` above a test class is idiomatic pytest, not a violation."""
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.subprocess("drives the real CLI binary")\n'
            "class TestCli:\n"
            "    def test_a(self):\n        subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_a_class_decorator_without_a_justification_is_a_violation(self):
        source = (
            "import pytest, subprocess\n\n"
            "@pytest.mark.subprocess\n"
            "class TestCli:\n"
            "    def test_a(self):\n        subprocess.run(['ls'])\n"
        )
        found = violations(source)
        assert found and "justification" in found[0]

    def test_in_class_pytestmark_declares_its_methods(self):
        source = (
            "import pytest, subprocess\n\n"
            "class TestCli:\n"
            '    pytestmark = pytest.mark.subprocess("every method drives the real binary")\n\n'
            "    def test_a(self):\n        subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_in_class_pytestmark_without_a_justification_is_a_violation(self):
        source = (
            "import pytest, subprocess\n\n"
            "class TestCli:\n"
            "    pytestmark = pytest.mark.subprocess\n\n"
            "    def test_a(self):\n        subprocess.run(['ls'])\n"
        )
        found = violations(source)
        assert found and "justification" in found[0]

    def test_a_class_marker_does_not_leak_to_a_sibling_class(self):
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.subprocess("drives the real binary")\n'
            "class TestOne:\n"
            "    def test_a(self):\n        subprocess.run(['ls'])\n\n"
            "class TestTwo:\n"
            "    def test_b(self):\n        subprocess.run(['ls'])\n"
        )
        found = scan_source(source, Path("tests/test_x.py"))
        assert len(found) == 1
        assert found[0].line == 10  # TestTwo's call

    def test_the_nearest_declaration_wins_over_the_outer_one(self):
        """A method's own marker answers for it; the class's answers only for the rest."""
        source = (
            "import pytest, subprocess\n\n"
            "@pytest.mark.subprocess\n"
            "class TestCli:\n"
            '    @pytest.mark.subprocess("this one drives the real binary")\n'
            "    def test_a(self):\n        subprocess.run(['ls'])\n\n"
            "    def test_b(self):\n        subprocess.run(['ls'])\n"
        )
        found = scan_source(source, Path("tests/test_x.py"))
        assert len(found) == 1
        assert found[0].line == 10  # test_b, falling back to the unjustified class marker

    def test_a_class_marker_overrides_an_unjustified_module_marker(self):
        source = (
            "import pytest, subprocess\n\n"
            "pytestmark = pytest.mark.subprocess\n\n"
            '@pytest.mark.subprocess("drives the real binary")\n'
            "class TestCli:\n"
            "    def test_a(self):\n        subprocess.run(['ls'])\n"
        )
        assert not violations(source)

    def test_the_marker_does_not_leak_to_a_sibling_test(self):
        source = (
            "import pytest, subprocess\n\n"
            '@pytest.mark.subprocess("drives the real binary")\n'
            "def test_a():\n    subprocess.run(['ls'])\n\n"
            "def test_b():\n    subprocess.run(['ls'])\n"
        )
        found = scan_source(source, Path("tests/test_x.py"))
        assert len(found) == 1
        assert found[0].line == 8  # test_b's call, not test_a's


class TestCli:
    def write(self, root: Path, relative: str, source: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_clean_tree_passes(self, tmp_path, capsys):
        self.write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        assert main([str(tmp_path / "tests")]) == CLEAN

    def test_violation_exits_one_and_names_the_file(self, tmp_path, capsys):
        self.write(
            tmp_path, "tests/test_a.py", "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n"
        )
        assert main([str(tmp_path / "tests")]) == VIOLATIONS
        assert "test_a.py" in capsys.readouterr().err

    def test_a_missing_path_is_clean_not_an_error(self, tmp_path):
        """A project with no tests/ yet is not a failure — it is a project with no tests yet."""
        assert main([str(tmp_path / "tests")]) == CLEAN

    def test_a_missing_path_says_so_on_stderr(self, tmp_path, capsys):
        """Scanning nothing and printing nothing is indistinguishable from a gate that passed."""
        assert main([str(tmp_path / "tests")]) == CLEAN
        err = capsys.readouterr().err
        assert "no such test root" in err
        assert "SUBPROC_CHECK_PATHS" in err

    def test_the_env_override_picks_the_test_root(self, tmp_path, monkeypatch):
        """A project whose tests live at packages/api/tests/ must be able to point the gate there."""
        git_repo(tmp_path)
        self.write(
            tmp_path,
            "packages/api/tests/test_a.py",
            "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SUBPROC_CHECK_PATHS", "packages/api/tests")
        assert main([]) == VIOLATIONS

    def test_an_explicit_path_wins_over_the_env_override(self, tmp_path, monkeypatch):
        self.write(
            tmp_path,
            "packages/api/tests/test_a.py",
            "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n",
        )
        self.write(tmp_path, "tests/test_b.py", "def test_b():\n    assert True\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SUBPROC_CHECK_PATHS", "packages/api/tests")
        assert main([str(tmp_path / "tests")]) == CLEAN

    def test_without_the_override_the_default_root_is_tests(self, tmp_path, monkeypatch):
        git_repo(tmp_path)
        self.write(
            tmp_path, "tests/test_a.py", "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SUBPROC_CHECK_PATHS", raising=False)
        assert main([]) == VIOLATIONS

    def test_unparseable_python_fails_closed(self, tmp_path):
        """A file the checker cannot read is a file it cannot clear."""
        self.write(tmp_path, "tests/test_a.py", "def test_a(:\n")
        assert main([str(tmp_path / "tests")]) == ERROR

    def test_non_python_files_are_ignored(self, tmp_path):
        self.write(tmp_path, "tests/fixture.txt", "subprocess.run(['ls'])\n")
        assert main([str(tmp_path / "tests")]) == CLEAN

    def test_scans_recursively(self, tmp_path):
        self.write(
            tmp_path,
            "tests/feature/1-phase/test_a.py",
            "import subprocess\n\ndef test_a():\n    subprocess.run(['ls'])\n",
        )
        assert main([str(tmp_path / "tests")]) == VIOLATIONS


class TestScope:
    """The applicability boundary: this change answers for the files it touched, and no others.

    Both directions are dangerous and both are pinned. Under-scoping is the hostage failure that
    refused every spec write of one measured phase over 17 spawners in locked phases nobody had
    opened; over-scoping would silently delete the only cost gate the pipeline has.
    """

    def write(self, root: Path, relative: str, source: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def test_a_spawner_in_an_untouched_file_does_not_block(self, tmp_path, monkeypatch, capsys):
        """The measured defect: 17 spawners in locked phases refused every spec write."""
        git_repo(tmp_path)
        self.write(tmp_path, "tests/locked/test_old.py", SPAWNER)
        git_commit(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert main([]) == CLEAN
        assert "NOT enforced" in capsys.readouterr().err

    def test_a_spawner_in_a_touched_file_still_blocks(self, tmp_path, monkeypatch):
        """The reason the check exists. Scoping must not become "never enforce"."""
        git_repo(tmp_path)
        self.write(tmp_path, "tests/locked/test_old.py", SPAWNER)   # untracked = touched
        monkeypatch.chdir(tmp_path)
        assert main([]) == VIOLATIONS

    def test_all_enforces_the_whole_tree(self, tmp_path, monkeypatch):
        git_repo(tmp_path)
        self.write(tmp_path, "tests/locked/test_old.py", SPAWNER)
        git_commit(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert main(["--all"]) == VIOLATIONS

    def test_an_unknowable_scope_enforces_nothing_and_says_so(self, tmp_path, monkeypatch, capsys):
        """Not a git repository: falling back to enforcing everything is the hostage failure."""
        self.write(tmp_path, "tests/test_a.py", SPAWNER)
        monkeypatch.chdir(tmp_path)
        assert main([]) == CLEAN
        err = capsys.readouterr().err
        assert "unknowable" in err and "--all" in err

    def test_an_unreadable_untouched_file_does_not_fail_the_gate(self, tmp_path, monkeypatch):
        """A syntax error in a locked phase's test is fail-closed for whoever changes THAT file."""
        git_repo(tmp_path)
        self.write(tmp_path, "tests/locked/test_broken.py", "def test_a(:\n")
        git_commit(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert main([]) == CLEAN

    def test_an_unreadable_touched_file_still_fails_closed(self, tmp_path, monkeypatch):
        git_repo(tmp_path)
        self.write(tmp_path, "tests/locked/test_broken.py", "def test_a(:\n")
        monkeypatch.chdir(tmp_path)
        assert main([]) == ERROR

    def test_paths_named_on_the_command_line_are_enforced_whole(self, tmp_path, monkeypatch):
        """An explicit path is a caller asking for that path, not for the diff."""
        git_repo(tmp_path)
        self.write(tmp_path, "tests/locked/test_old.py", SPAWNER)
        git_commit(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert main([str(tmp_path / "tests")]) == VIOLATIONS

    def test_the_mode_is_always_printed(self, tmp_path, monkeypatch, capsys):
        """A silent fallback is how a check comes to mean something other than what it says."""
        git_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        main([])
        assert "scope" in capsys.readouterr().err
