"""A stamp is not a completion signal (issue #68) unless something makes it one.

`status: done` is written by the same implementer whose work it claims is finished, and used to
keep working afterward — `test-mapping.md` was still empty and the phase's mutation gate had not
run. This module is the mechanism: it decides whether a spec's own mapping is non-empty, and it can
revert a premature `done` back to `in-progress` so the false stamp does not survive on disk.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from spec_done_guard import ERROR, NOT_DONE, OK, main, mapping_complete, revert  # noqa: E402


def write_spec(path: Path, status: str = "done") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nfeature: demo\nstatus: {status}\n---\n\n# Spec\n")


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


def test_cli_mapping_complete_reports_ok_on_missing_spec(tmp_path: Path) -> None:
    assert main(["mapping-complete", str(tmp_path / "nope.md")]) == ERROR


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
