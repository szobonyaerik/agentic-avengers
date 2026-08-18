"""A stamp is not a completion signal (issue #68) unless something makes it one.

`status: done` is written by the same implementer whose work it claims is finished, and used to
keep working afterward — `test-mapping.md` was still empty and the phase's mutation gate had not
run. This module is the mechanism: it decides whether a spec's own mapping is non-empty, and it can
revert a premature `done` back to `in-progress` so the false stamp does not survive on disk.

It also decides what that rule may BIND: only a stamp that is NEW relative to the file's committed
HEAD version. A spec already stamped `done` there has shipped, and rewriting it destroys the one
evidence `applicability.spec_shipped` reads.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from spec_done_guard import (  # noqa: E402
    ERROR,
    NOT_DONE,
    OK,
    OUT_OF_SCOPE,
    main,
    mapping_complete,
    revert,
    stamp_is_new,
)


pytestmark = pytest.mark.subprocess(
    "the applicability boundary is what git says is COMMITTED, so a real repository is the subject "
    "under test; a stubbed git would test a reimplementation of the boundary"
)


def write_spec(path: Path, status: str = "done") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nfeature: demo\nstatus: {status}\n---\n\n# Spec\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def repo_with_spec(root: Path, status: str) -> Path:
    """A git repository whose committed HEAD carries the spec at `status`."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    spec = root / "spec.md"
    write_spec(spec, status=status)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "spec")
    return spec


# ── mapping completeness ─────────────────────────────────────────────────────


def test_a_missing_mapping_file_is_not_complete(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec)

    assert mapping_complete(spec) is False


def test_a_mapping_with_only_the_header_row_is_not_complete(tmp_path: Path) -> None:
    """The template ships a header and a separator; neither is a recorded test."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
    )

    assert mapping_complete(spec) is False


def test_a_mapping_with_a_real_row_is_complete(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
        "| R1.1.1 | test_x.py::test_it | integration | ... |\n"
    )

    assert mapping_complete(spec) is True


def test_a_real_row_whose_text_contains_a_dash_run_still_counts(tmp_path: Path) -> None:
    """Rows used to be found by "starts with | and does not contain ---", which drops a recorded
    test whose own justification happens to contain a dash run — reverting a correct stamp."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
        "| R1.1.1 | test_x.py::test_it | narrow | no reachable seam --- see spec |\n"
    )

    assert mapping_complete(spec) is True


def test_pipe_lines_before_the_separator_are_never_counted_as_rows(
    tmp_path: Path,
) -> None:
    """A multi-line header is still a header. Rows are what follows the separator, not what
    follows the first pipe line in the file."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test |\n| id | path |\n|---|---|\n"
    )

    assert mapping_complete(spec) is False


# ── the applicability boundary: only a NEW stamp binds ────────────────────


def test_a_stamp_that_was_not_done_at_head_is_new(tmp_path: Path) -> None:
    spec = repo_with_spec(tmp_path / "repo", status="in-progress")
    write_spec(spec, status="done")

    assert stamp_is_new(spec) is True


def test_a_stamp_already_done_at_head_is_not_new(tmp_path: Path) -> None:
    """The wedge this boundary exists to stop: a shipped spec edited later still reads
    `status: done`, and reverting it destroys the only `shipped` evidence the pipeline has."""
    spec = repo_with_spec(tmp_path / "repo", status="done")
    spec.write_text(spec.read_text() + "\n## Acceptance criteria\n\nDone.\n")

    assert stamp_is_new(spec) is False


def test_a_spec_with_no_committed_version_is_new(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo_with_spec(repo, status="in-progress")
    fresh = repo / "specs" / "1.2-b" / "spec.md"
    write_spec(fresh, status="done")

    assert stamp_is_new(fresh) is True


def test_a_spec_outside_any_repository_leaves_the_scope_unknowable(
    tmp_path: Path,
) -> None:
    """None is not False. Git cannot say what is committed, so nothing may be enforced — the same
    direction every other check on this boundary takes rather than enforcing everything."""
    spec = tmp_path / "loose" / "spec.md"
    write_spec(spec, status="done")

    assert stamp_is_new(spec) is None


# ── revert ────────────────────────────────────────────────────────────────


def test_reverting_a_done_stamp_flips_it_to_in_progress(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec, status="done")

    changed = revert(spec)

    assert changed is True
    assert "status: in-progress" in spec.read_text()
    assert "status: done" not in spec.read_text()


def test_reverting_a_spec_already_in_progress_is_a_no_op(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec, status="in-progress")

    changed = revert(spec)

    assert changed is False
    assert "status: in-progress" in spec.read_text()


def test_revert_leaves_any_status_that_is_not_done_untouched(tmp_path: Path) -> None:
    """The phase's whole suite runs between the hook's grep and this call, so another lane may have
    written a different value in the meantime. Only `done` is this function's to rewrite."""
    spec = tmp_path / "spec.md"
    write_spec(spec, status="blocked")

    changed = revert(spec)

    assert changed is False
    assert "status: blocked" in spec.read_text()


def test_revert_refuses_a_spec_with_no_frontmatter(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# no frontmatter here\n")

    try:
        revert(spec)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "frontmatter" in str(exc)


def test_revert_preserves_the_rest_of_the_document(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nfeature: demo\nstatus: done\nspec_gate: approved\n---\n\n"
        "# Spec\n\n## Requirements\n- R1.1.1 — binding: integration\n"
    )

    revert(spec)

    text = spec.read_text()
    assert "spec_gate: approved" in text
    assert "## Requirements" in text
    assert "R1.1.1" in text


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_mapping_complete_reports_not_done_without_rows(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec)

    assert main(["mapping-complete", str(spec)]) == NOT_DONE


def test_cli_mapping_complete_reports_error_on_missing_spec(tmp_path: Path) -> None:
    assert main(["mapping-complete", str(tmp_path / "nope.md")]) == ERROR


def test_cli_stamp_is_new_reports_ok_for_a_fresh_stamp(tmp_path: Path) -> None:
    spec = repo_with_spec(tmp_path / "repo", status="in-progress")
    write_spec(spec, status="done")

    assert main(["stamp-is-new", str(spec)]) == OK


def test_cli_stamp_is_new_reports_out_of_scope_for_a_shipped_spec(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = repo_with_spec(tmp_path / "repo", status="done")

    assert main(["stamp-is-new", str(spec)]) == OUT_OF_SCOPE
    assert "NOT enforced" in capsys.readouterr().err


def test_cli_stamp_is_new_says_out_loud_when_the_scope_is_unknowable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tmp_path / "loose" / "spec.md"
    write_spec(spec, status="done")

    assert main(["stamp-is-new", str(spec)]) == OUT_OF_SCOPE
    assert "UNKNOWABLE" in capsys.readouterr().err


def test_cli_stamp_is_new_reports_error_on_missing_spec(tmp_path: Path) -> None:
    assert main(["stamp-is-new", str(tmp_path / "nope.md")]) == ERROR


def test_cli_revert_reports_ok_and_changes_the_file(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec, status="done")

    assert main(["revert", str(spec)]) == OK
    assert "status: in-progress" in spec.read_text()


def test_cli_revert_reports_error_on_unwritable_target(tmp_path: Path) -> None:
    assert main(["revert", str(tmp_path / "nope.md")]) == ERROR


def test_cli_with_no_recognised_command_prints_usage_and_errors() -> None:
    assert main([]) == ERROR
    assert main(["bogus"]) == ERROR
