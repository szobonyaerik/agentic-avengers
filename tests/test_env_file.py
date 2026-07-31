"""Tests for .env loading.

Two dangerous directions, both pinned hard:

1. **Shadowing real configuration.** A CI secret or an exported shell variable must always beat the
   file, or a stale committed default silently overrides the environment CI was given.
2. **Executing the file.** `.env` is parsed, never sourced. A value that looks like a command
   substitution is a literal string — sourcing it would run arbitrary code from a file people paste
   secrets into.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from env_file import (  # noqa: E402
    export_lines,
    find_env_file,
    load_into,
    parse,
)


# --- parsing ------------------------------------------------------------------------------------


def test_parses_simple_pairs() -> None:
    assert parse("A=1\nB=two\n") == {"A": "1", "B": "two"}


def test_ignores_comments_and_blank_lines() -> None:
    assert parse("# a comment\n\nA=1\n   \n# another\nB=2\n") == {"A": "1", "B": "2"}


def test_strips_export_prefix() -> None:
    assert parse("export A=1\nexport  B=2\n") == {"A": "1", "B": "2"}


def test_strips_matching_quotes() -> None:
    assert parse("A='single'\nB=\"double\"\n") == {"A": "single", "B": "double"}


def test_keeps_inner_quotes_and_equals() -> None:
    assert parse('A="a=b"\nB=say "hi"\n') == {"A": "a=b", "B": 'say "hi"'}


def test_trims_surrounding_whitespace() -> None:
    assert parse("  A = 1  \n") == {"A": "1"}


def test_allows_empty_values() -> None:
    assert parse("A=\n") == {"A": ""}


def test_ignores_malformed_lines() -> None:
    assert parse("NOT_A_PAIR\nA=1\n=novalue\n") == {"A": "1"}


def test_ignores_invalid_identifiers() -> None:
    assert parse("9BAD=1\nBAD-NAME=1\nGOOD_1=ok\n") == {"GOOD_1": "ok"}


def test_a_trailing_comment_is_not_stripped_from_a_value() -> None:
    """API keys can contain '#'; stripping after it would silently truncate a secret."""
    assert parse("KEY=abc#def\n") == {"KEY": "abc#def"}


@pytest.mark.parametrize(
    "line",
    ["A=$(touch pwned)", "A=`touch pwned`", "A=$(( 1+1 ))", "A=${OTHER}"],
)
def test_command_substitution_is_literal(line: str) -> None:
    """The file is parsed, never sourced — these must survive as plain text."""
    value = parse(line + "\n")["A"]
    assert value == line.split("=", 1)[1]


# --- discovery ----------------------------------------------------------------------------------


def test_finds_env_in_the_start_directory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n")
    assert find_env_file(tmp_path) == tmp_path / ".env"


def test_walks_up_to_a_parent(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_env_file(nested) == tmp_path / ".env"


def test_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_env_file(tmp_path) is None


def test_nearest_env_wins(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=outer\n")
    nested = tmp_path / "inner"
    nested.mkdir()
    (nested / ".env").write_text("A=inner\n")
    assert find_env_file(nested) == nested / ".env"


# --- precedence ---------------------------------------------------------------------------------


def test_real_environment_wins(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("KEY=from-file\n")
    env = {"KEY": "from-shell"}
    load_into(env, root=tmp_path)
    assert env["KEY"] == "from-shell"


def test_missing_keys_are_filled_from_the_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("KEY=from-file\nOTHER=x\n")
    env = {"KEY": "from-shell"}
    load_into(env, root=tmp_path)
    assert env["OTHER"] == "x"


def test_an_empty_shell_value_is_still_a_value(tmp_path: Path) -> None:
    """Explicitly exporting KEY= means 'unset it', and the file must not resurrect it."""
    (tmp_path / ".env").write_text("KEY=from-file\n")
    env = {"KEY": ""}
    load_into(env, root=tmp_path)
    assert env["KEY"] == ""


def test_no_env_file_is_a_no_op(tmp_path: Path) -> None:
    env = {"KEY": "x"}
    load_into(env, root=tmp_path)
    assert env == {"KEY": "x"}


def test_unreadable_env_file_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / ".env").mkdir()  # a directory named .env
    env: dict[str, str] = {}
    load_into(env, root=tmp_path)
    assert env == {}


# --- shell export -------------------------------------------------------------------------------


def test_export_lines_quote_dangerous_values() -> None:
    line = export_lines({"A": "$(touch pwned)"}, existing={})[0]
    assert line.startswith("export A=")
    assert "$(touch" not in line.replace("'$(touch pwned)'", "")


def test_export_lines_skip_already_set_variables() -> None:
    assert export_lines({"A": "1"}, existing={"A": "shell"}) == []


def test_shell_loader_exports_without_executing(tmp_path: Path) -> None:
    """End-to-end: load_env.sh must set the variable and must not run the substitution."""
    (tmp_path / ".env").write_text("SAFE=ok\nDANGER=$(touch pwned)\n")
    script = f'. "{ROOT}/scripts/load_env.sh"; echo "$SAFE|$DANGER"'
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_PROJECT_DIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok|$(touch pwned)"
    assert not (tmp_path / "pwned").exists(), "the .env file was executed"


def test_shell_loader_does_not_override_the_shell(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("KEY=from-file\n")
    script = f'. "{ROOT}/scripts/load_env.sh"; echo "$KEY"'
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "KEY": "from-shell",
        },
        check=False,
    )
    assert result.stdout.strip() == "from-shell"


def test_shell_loader_is_silent_without_an_env_file(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", "-c", f'. "{ROOT}/scripts/load_env.sh"; echo done'],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_PROJECT_DIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "done"
    assert result.stderr.strip() == ""
