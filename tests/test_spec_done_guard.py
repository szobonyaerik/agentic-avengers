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

import spec_done_guard  # noqa: E402
from spec_done_guard import (  # noqa: E402
    ERROR,
    NO_REQUIREMENTS,
    NOT_DONE,
    OK,
    OUT_OF_SCOPE,
    NoRequirementsDeclared,
    UndecidableMapping,
    UndecidableRequirements,
    main,
    mapping_complete,
    mapping_owed,
    revert,
    stamp_is_new,
)

TEMPLATE_ROWS = (
    "| requirement id(s) | test name(s) | level | why |\n"
    "|---|---|---|---|\n"
    "| R<n>.<k>.<m>, R<n>.<k>.<m+1> | test_<journey> | e2e | the user path both ids sit on |\n"
    "| R<n>.<k>.<m> | test_<name> | integration | drives <seam> with real collaborators |\n"
    "| R<n>.<k>.<m> | test_<name> | narrow | <mandatory: no reachable seam, because …> |\n"
)


pytestmark = pytest.mark.subprocess(
    "the applicability boundary is what git says is COMMITTED, so a real repository is the subject "
    "under test; a stubbed git would test a reimplementation of the boundary"
)


def write_spec(
    path: Path,
    status: str = "done",
    *,
    requirements: str = "- R1.1.1 — binding: integration\n",
) -> None:
    """A spec that OWES a mapping row by default: one requirement bound to a test.

    A spec declaring only `binding: none` requirements owes no row at all (§4a), so the default here
    is deliberately a spec the mapping check applies to — `requirements` is what varies that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nfeature: demo\nstatus: {status}\n---\n\n"
        f"# Spec\n\n## Requirements\n{requirements}"
    )


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


def test_the_mapping_template_copied_verbatim_is_not_complete(tmp_path: Path) -> None:
    """The state issue #68 actually describes. `skills/tdd` points implementers at this template,
    so copy-then-stamp is the expected flow — and a row COUNT passes it, because the template ships
    three rows. Counting rows is not the same as checking they say anything."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(TEMPLATE_ROWS)

    assert mapping_complete(spec) is False


def test_one_filled_row_beside_the_template_rows_is_complete(tmp_path: Path) -> None:
    """The check must not become "delete the placeholders first" — one real row is completeness,
    however many placeholders sit beside it."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        TEMPLATE_ROWS
        + "| R1.1.1 | test_x.py::test_it | integration | drives the handler |\n"
    )

    assert mapping_complete(spec) is True


@pytest.mark.parametrize(
    "why",
    [
        "drives parse(): Result<Config, Error>",
        "rejects n < 5 or > 10",
        "no seam; asserts the To: header is <ops@example.com>",
    ],
)
def test_angle_brackets_outside_the_id_cell_do_not_make_a_row_a_placeholder(
    tmp_path: Path, why: str
) -> None:
    """The direction that matters most. Read over the whole row, the placeholder test classifies
    ordinary prose as a template row and REVERTS a correctly-stamped spec — worse than the gap it
    closes, and this pipeline is vendored into repos where generics in a cell are unremarkable."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
        f"| R1.1.1 | test_parse | integration | {why} |\n"
    )

    assert mapping_complete(spec) is True


def test_a_placeholder_id_cell_is_a_placeholder_however_filled_the_rest_is(
    tmp_path: Path,
) -> None:
    """The other direction of the same anchor: a real-looking `why` does not launder an id cell
    that still says `R<n>.<k>.<m>`."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
        "| R<n>.<k>.<m> | test_parse | integration | drives the handler with real collaborators |\n"
    )

    assert mapping_complete(spec) is False


# ── a row is only owed where a test is owed (§4a) ──────────────────────────


def test_a_spec_whose_every_requirement_is_binding_none_owes_no_row(
    tmp_path: Path,
) -> None:
    """The tiered-binding rule gives a `binding: none` requirement no test and no mapping row, so
    demanding one asks its author to invent a row for a test the rules forbid — with no remedy,
    since `spec-done` is not in `applicability.RULES` and the revert re-fires on every re-stamp."""
    spec = tmp_path / "spec.md"
    write_spec(
        spec,
        requirements="- R1.1.1 — binding: none\n- R1.1.2 — binding: none\n",
    )

    assert mapping_owed(spec) is False


def test_a_spec_declaring_no_requirements_is_its_own_state_not_that_exemption(
    tmp_path: Path,
) -> None:
    """The two produce the SAME empty binding list, and they are opposites: one is a deliberate
    exemption, the other a spec that states nothing about what it owes. Read as the exemption, every
    spec whose requirements were never written down keeps a `done` stamp over an absent mapping —
    issue #68's own state, reopened for a whole class."""
    spec = tmp_path / "spec.md"
    write_spec(spec, requirements="\n")

    with pytest.raises(NoRequirementsDeclared):
        mapping_owed(spec)


def test_one_requirement_bound_to_a_test_makes_a_row_owed(tmp_path: Path) -> None:
    """The check must still bind the spec this issue is about — the exemption is not a hole."""
    spec = tmp_path / "spec.md"
    write_spec(
        spec,
        requirements="- R1.1.1 — binding: none\n- R1.1.2 — binding: integration\n",
    )

    assert mapping_owed(spec) is True


def test_a_requirement_declaring_no_binding_at_all_is_owed_a_trace(
    tmp_path: Path,
) -> None:
    """The same reading `verifier_precheck.py` takes: a missing `binding:` is a spec defect, not an
    exemption, so the absence of a declaration may not buy the absence of a mapping row."""
    spec = tmp_path / "spec.md"
    write_spec(spec, requirements="- R1.1.1 — the handler rejects an empty body\n")

    assert mapping_owed(spec) is True


def test_a_requirement_layout_that_cannot_be_read_is_undecidable_not_exempt(
    tmp_path: Path,
) -> None:
    """A spec plainly carrying requirement ids in a shape the parser cannot see must not read as
    "nothing is owed" — that exempts the spec from this check on the strength of a failed parse."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nfeature: demo\nstatus: done\n---\n\n"
        "# Spec\n\n## Requirements\n\nThe handler rejects an empty body (R1.1.1).\n"
    )

    with pytest.raises(UndecidableRequirements):
        mapping_owed(spec)


def test_cli_passes_a_binding_none_only_spec_with_no_mapping_at_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec, requirements="- R1.1.1 — binding: none\n")

    assert main(["mapping-complete", str(spec)]) == OK
    assert "owes no row" in capsys.readouterr().err


def test_cli_reports_a_spec_with_no_requirements_as_its_own_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not OK (the exemption), not NOT_DONE (an empty mapping the check never judged): its own exit
    code, so `hook_verifier.sh` can name the remedy that exists — declare the requirements."""
    spec = tmp_path / "spec.md"
    write_spec(spec, requirements="\n")
    (tmp_path / "test-mapping.md").write_text(
        "| requirement | test | level | why |\n|---|---|---|---|\n"
    )

    assert main(["mapping-complete", str(spec)]) == NO_REQUIREMENTS
    err = capsys.readouterr().err
    assert "declares no requirement at all" in err
    assert "NOT the `binding: none` exemption" in err


def test_the_no_requirements_stop_is_not_reused_by_any_other_answer(
    tmp_path: Path,
) -> None:
    """Three states, three codes. A shared code is how the hook came to prescribe one branch's
    remedy for another branch's problem."""
    assert NO_REQUIREMENTS not in (OK, NOT_DONE, OUT_OF_SCOPE, ERROR)


def test_cli_reports_an_unreadable_requirement_layout_as_an_error(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(
        "---\nfeature: demo\nstatus: done\n---\n\n"
        "# Spec\n\n## Requirements\n\nSee R1.1.1 for the empty-body case.\n"
    )

    assert main(["mapping-complete", str(spec)]) == ERROR


# ── unknown is not empty ──────────────────────────────────────────────────


def test_a_mapping_that_cannot_be_read_is_undecidable_not_empty(tmp_path: Path) -> None:
    """A non-UTF-8 byte raises `UnicodeDecodeError` — a `ValueError`, so no `except OSError` sees
    it. Answered as `False`, it reaches the hook as the obligation and rewrites a spec whose
    mapping is full of rows, on the strength of a check that never ran."""
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_bytes(
        b"| R1.1.1 | test_x | integration | \xff\xfe |\n"
    )

    with pytest.raises(UndecidableMapping):
        mapping_complete(spec)


def test_cli_reports_an_unreadable_mapping_as_an_error(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    write_spec(spec)
    (tmp_path / "test-mapping.md").write_bytes(b"|---|\n| \xff\xfe |\n")

    assert main(["mapping-complete", str(spec)]) == ERROR


def test_cli_reports_an_unexpected_failure_as_an_error_never_a_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Python exits 1 on an uncaught exception, and 1 is already both NOT_DONE and OUT_OF_SCOPE —
    each wired to a different hook branch. A crash must never arrive as either."""
    spec = tmp_path / "spec.md"
    write_spec(spec)

    def boom(_path: Path) -> bool:
        raise RuntimeError("git went sideways")

    monkeypatch.setattr(spec_done_guard, "stamp_is_new", boom)

    assert main(["stamp-is-new", str(spec)]) == ERROR
    err = capsys.readouterr().err
    assert "unexpected failure" in err
    assert "RuntimeError" in err


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


def test_a_repository_with_no_commits_at_all_reads_as_new(tmp_path: Path) -> None:
    """The one git failure that genuinely means "there is no committed version": an unborn branch,
    where the stamp cannot predate this change."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    spec = repo / "spec.md"
    write_spec(spec, status="done")

    assert stamp_is_new(spec) is True


def test_a_git_failure_that_is_not_an_absence_leaves_the_scope_unknowable(
    tmp_path: Path,
) -> None:
    """`git show HEAD:<rel>` exits 128 both for "path is not in HEAD" and for a repository state git
    cannot resolve, and reading the second as an absence BINDS a spec that may have shipped and
    reverts its stamp — the direction this module names as the worst to get wrong. Here HEAD points
    at a ref that does not exist while the repository's commits are all still there, so "absent" is
    plainly the wrong answer."""
    spec = repo_with_spec(tmp_path / "repo", status="done")
    (tmp_path / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/gone\n")

    assert stamp_is_new(spec) is None


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
