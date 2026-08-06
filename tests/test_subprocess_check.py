"""Tests for the subprocess check.

The defect this check exists for is a test that spawns a nested run of the whole suite: four of them
survived spec review, fidelity checking, cross-family review and verification across five phases,
because every one of those stages reads for correctness and none of them can see a subprocess.

So the dangerous direction here is a MISS, and most of these pin "must still be flagged" cases —
including the aliasing forms an author reaches for without meaning to evade anything
(`import subprocess as sp`, `from subprocess import run`).
"""

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


def violations(source: str) -> list[str]:
    """The rendered reason of each violation found in one test module."""
    return [v.reason for v in scan_source(source, Path("tests/test_x.py"))]


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
