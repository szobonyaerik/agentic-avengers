"""Tests for the frontmatter contract — issue #29's general half.

The instance-level defect is easy to state: four artifact classes were named by stage instructions
and had no read-path decision recorded either way. The CLASS behind it is the one this repository
keeps producing: **documentation making a claim nothing enforces.** Every individual instance is
small and reasonable; what makes them a class is that the claim and the enforcement are written in
different files by different stages, so nothing compares them.

`check --contract` compares them, in both directions, and each direction gets a test that goes RED
when its claim is violated:

  * the table declares a reader and names `emitted_by`, and that file exists and really instructs
    the `readers:` line (the direction that already existed only as an assertion in
    `test_doc_read_path.py`, now a runnable check the CI gate calls);
  * a canonical source names an artifact class and the table governs it (the direction NOTHING
    checked, which is issue #29 itself).

The last two tests run it against the real repository, which is where a regression would land.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from doc_read_path import (  # noqa: E402
    CANONICAL_MARKER,
    READ_PATH,
    check_contract,
    is_canonical_repo,
    layout_inventory,
    main,
)

REPO = Path(__file__).resolve().parents[1]


def scaffold(root: Path) -> None:
    """A minimal CANONICAL repository the contract check passes, marker and all."""
    (root / CANONICAL_MARKER).parent.mkdir(parents=True, exist_ok=True)
    (root / CANONICAL_MARKER).write_text("# canonical-source tooling\n")
    (root / "docs" / "templates").mkdir(parents=True)
    (root / "skills" / "pipeline-conventions").mkdir(parents=True)
    (root / "agents").mkdir()
    for name, spec in READ_PATH.items():
        emitter = root / spec["emitted_by"]
        emitter.parent.mkdir(parents=True, exist_ok=True)
        if spec["emitted_by"].startswith("docs/templates/") and spec[
            "emitted_by"
        ].endswith(".md"):
            emitter.write_text(
                f"---\nreaders: {'; '.join(spec['readers']) or 'none'}\n---\n"
            )
        else:
            emitter.write_text('readers: declared here\n"readers"\n')


# --- direction one: the table's declared writer instruction ---------------------------------------


def test_an_emitter_that_does_not_exist_is_a_violation(tmp_path: Path) -> None:
    scaffold(tmp_path)
    (tmp_path / READ_PATH["plan.md"]["emitted_by"]).unlink()
    problems = check_contract(tmp_path)
    assert any("plan.md" in p and "does not exist" in p for p in problems), problems


def test_a_template_that_mentions_readers_only_in_prose_is_a_violation(
    tmp_path: Path,
) -> None:
    """A template teaching a document that fails `check_artifacts` is the gap, not a style nit."""
    scaffold(tmp_path)
    emitter = tmp_path / READ_PATH["plan.md"]["emitted_by"]
    emitter.write_text(
        "---\nfeature: <feature>\n---\n\nRemember to state `readers:` somewhere.\n"
    )
    problems = check_contract(tmp_path)
    assert any("carries no `readers:` in its own frontmatter" in p for p in problems), (
        problems
    )


def test_a_non_template_emitter_that_never_says_readers_is_a_violation(
    tmp_path: Path,
) -> None:
    scaffold(tmp_path)
    emitter = tmp_path / READ_PATH["breaker.json"]["emitted_by"]
    emitter.write_text(
        "# the Breaker writes a record and is told nothing about who reads it\n"
    )
    problems = check_contract(tmp_path)
    assert any("never says so" in p and "breaker.json" in p for p in problems), problems


def test_a_correctly_instructed_tree_is_clean(tmp_path: Path) -> None:
    scaffold(tmp_path)
    assert check_contract(tmp_path) == []


# --- direction two: the inverse, which is issue #29 -----------------------------------------------


def test_an_artifact_a_stage_names_that_the_table_does_not_govern_is_a_violation(
    tmp_path: Path,
) -> None:
    """`implementation-report.md`'s exact shape: an instruction naming a path, no table entry."""
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-handover.md").write_text(
        "- implementation: docs/features/<feature>/phases/<n>-<slug>/implementation-report.md\n"
    )
    problems = check_contract(tmp_path)
    assert any(
        "implementation-report.md" in p and "no entry for it" in p for p in problems
    ), problems


def test_the_violation_names_where_the_class_was_named(tmp_path: Path) -> None:
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-handover.md").write_text(
        "see docs/features/<feature>/phases/<n>-<slug>/test-execution-report.md\n"
    )
    problem = next(
        p for p in check_contract(tmp_path) if "test-execution-report.md" in p
    )
    assert "agents/avenger-handover.md:1" in problem


def test_the_violation_names_all_three_honest_outcomes(tmp_path: Path) -> None:
    """A finding that does not name the remedies invites the guess the issue exists to stop."""
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-handover.md").write_text(
        "docs/features/<feature>/fidelity-report.md\n"
    )
    problem = next(p for p in check_contract(tmp_path) if "fidelity-report.md" in p)
    assert "READ_PATH entry" in problem
    assert "readers: none" in problem
    assert "stop writing it" in problem


def test_the_layout_block_is_the_second_inventory(tmp_path: Path) -> None:
    """A class listed in the canonical layout and instructed NOWHERE is still undecided."""
    scaffold(tmp_path)
    (tmp_path / "skills" / "pipeline-conventions" / "SKILL.md").write_text(
        "- **Layout:**\n"
        "  ```\n"
        "  docs/features/<feature>/\n"
        "    overview.md\n"
        "    scoreboard.md            # nobody is told to write this\n"
        "  ```\n"
        "- next bullet\n"
    )
    problems = check_contract(tmp_path)
    assert any("scoreboard.md" in p for p in problems), problems


def test_the_layout_block_stops_at_its_closing_fence(tmp_path: Path) -> None:
    scaffold(tmp_path)
    (tmp_path / "skills" / "pipeline-conventions" / "SKILL.md").write_text(
        "- **Layout:**\n"
        "  ```\n"
        "    overview.md\n"
        "  ```\n"
        "\nProse after the block naming `scoreboard.md` is not an inventory entry.\n"
    )
    assert layout_inventory(tmp_path) == {"overview.md"}
    assert check_contract(tmp_path) == []


def test_an_absent_layout_source_yields_no_inventory(tmp_path: Path) -> None:
    scaffold(tmp_path)
    (tmp_path / "skills" / "pipeline-conventions" / "SKILL.md").unlink(missing_ok=True)
    assert layout_inventory(tmp_path) == set()


def test_a_numbered_archive_name_resolves_to_its_table_entry(tmp_path: Path) -> None:
    """`verdict-attempt-<n>.json` is governed by a needle, not by its literal name."""
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-verifier.md").write_text(
        "docs/features/<feature>/phases/<n>-<slug>/verdict-attempt-2.json\n"
    )
    assert check_contract(tmp_path) == []


def test_a_slice_review_resolves_to_its_table_entry(tmp_path: Path) -> None:
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-spec-writer.md").write_text(
        "docs/features/<feature>/scoped/review-idempotency.md\n"
    )
    assert check_contract(tmp_path) == []


# --- the real repository --------------------------------------------------------------------------


def test_the_real_repository_satisfies_its_own_frontmatter_contract() -> None:
    assert check_contract(REPO) == []


def test_the_four_classes_of_issue_29_each_have_a_recorded_decision() -> None:
    """Three were removed and one joined the table; none may return undecided.

    A regression here is not cosmetic: `implementation-report.md` coming back as a path literal in
    a stage instruction, with no table entry, is the state that made a Stop hook's whole sweep
    depend on a file nothing was instructed to write.
    """
    removed = (
        "implementation-report.md",
        "test-execution-report.md",
        "fidelity-report.md",
    )
    for name in removed:
        assert name not in READ_PATH
    assert "review-<slice>.md" in READ_PATH
    assert READ_PATH["review-<slice>.md"]["readers"], (
        "a kept class must name a real reader"
    )
    assert check_contract(REPO) == []


def test_the_cli_returns_one_on_a_contract_violation_and_zero_when_clean(
    tmp_path: Path,
) -> None:
    scaffold(tmp_path)
    assert main(["check", "--contract-only", str(tmp_path)]) == 0
    (tmp_path / "agents" / "avenger-handover.md").write_text(
        "docs/features/<feature>/phases/<n>-<slug>/implementation-report.md\n"
    )
    assert main(["check", "--contract-only", str(tmp_path)]) == 1


def test_a_source_that_is_not_utf8_is_a_named_finding_not_a_traceback(
    tmp_path: Path,
) -> None:
    """One non-UTF-8 file under a scanned directory must not crash CI.

    `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so it escaped the fail-closed guard in
    `_read` and reached `gate_ci.sh` as a stack trace instead of the named refusal every other
    unreadable file gets. The same bug class this change fixed in `phase_artifacts._status`.
    """
    scaffold(tmp_path)
    rogue = tmp_path / "agents" / "rogue.md"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_bytes(b"# caf\xe9 in latin-1\n")

    with pytest.raises(SystemExit) as raised:
        check_contract(tmp_path)
    assert "cannot read" in str(raised.value)
    assert "rogue.md" in str(raised.value)
    assert "fail closed" in str(raised.value)


# --- the vendored install: what this check may read, and where it may run -------------------------


def vendored(root: Path) -> Path:
    """A tree shaped like a repo that INSTALLED the pipeline: no canonical marker, no `agents/`.

    `scripts/install.sh` SRC_SETS vendors `skills/`, `prompts/`, `docs/templates/`, `AGENTS.md` and
    a named subset of `scripts/`. It does not vendor `agents/`, and it does not vendor
    `scripts/sync_opencode.py`, which is what makes that file usable as the canonical marker.
    """
    scaffold(root)
    (root / CANONICAL_MARKER).unlink()
    for path in sorted((root / "agents").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (root / "agents").rmdir()
    return root


def test_the_canonical_marker_is_not_vendored_by_install_sh() -> None:
    """The marker only works while `install.sh` leaves it out, so that is pinned, not assumed.

    SRC_SETS is a shipped payload manifest - a declared contract about what a consumer receives -
    and this reads it as one: the set of entries, asked whether any of them grants the marker. A
    future change that starts vendoring it would silently turn the guard into a no-op, and every
    consumer repo would go back to failing C1 from installation onwards.
    """
    line = next(
        raw
        for raw in (REPO / "scripts" / "install.sh").read_text().splitlines()
        if raw.startswith("SRC_SETS=")
    )
    shipped = line.split("=", 1)[1].strip().strip('"').split()
    granted = [
        entry
        for entry in shipped
        if entry == CANONICAL_MARKER
        or CANONICAL_MARKER.startswith(f"{entry.rstrip('/')}/")
    ]
    assert not granted, f"{CANONICAL_MARKER} is vendored through {granted}"


def test_a_vendored_install_does_not_check_the_emitter_direction_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression: a consumer repo that owns a top-level `agents/` of its own.

    Keying the boundary on the DIRECTORY NAME let any such repo defeat it - the check read that
    unrelated directory as the canonical inventory and reported the pipeline's own entries as broken
    declarations. `.pre-commit-config.yaml` and the CI workflow are both vendored and both call
    `gate_ci.sh`, which runs this non-diff-scoped with no `--all` escape and no exception route, so
    that repo failed every commit and every CI run from installation onwards.
    """
    root = vendored(tmp_path)
    (root / "agents").mkdir()
    (root / "agents" / "my-own-agent.md").write_text("a consumer's own agent\n")

    assert not is_canonical_repo(root)
    assert check_contract(root) == []
    said = capsys.readouterr().err
    assert "NOT CHECKED" in said, said
    assert "not the canonical pipeline repository" in said, said
    assert "agentic-avengers" in said, said


def test_the_cli_never_reports_clean_without_naming_the_skipped_direction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent dimension that reports nothing is the class issue #69 exists for."""
    root = vendored(tmp_path)
    assert main(["check", "--contract-only", str(root)]) == 0
    captured = capsys.readouterr()
    assert "[doc_read_path] clean" in captured.out
    assert "NOT CHECKED" in captured.err, captured.err


def test_the_canonical_repository_says_nothing_of_the_kind_and_still_fails_c1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half: with the marker present the direction really runs."""
    scaffold(tmp_path)
    assert is_canonical_repo(tmp_path)
    (tmp_path / READ_PATH["breaker.json"]["emitted_by"]).unlink()
    problems = check_contract(tmp_path)
    assert any("breaker.json" in p and "does not exist" in p for p in problems), (
        problems
    )
    assert "NOT CHECKED" not in capsys.readouterr().err


def test_a_vendored_install_still_runs_the_artifact_class_direction(
    tmp_path: Path,
) -> None:
    """C3 reads the VENDORED stage instructions, so it is unaffected and must keep firing."""
    root = vendored(tmp_path)
    skill = root / "skills" / "phase-handover"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "write docs/features/<feature>/phases/<n>-<slug>/implementation-report.md\n"
    )
    assert any("implementation-report.md" in p for p in check_contract(root))


def test_a_consumer_owned_file_naming_an_artifact_path_is_not_judged(
    tmp_path: Path,
) -> None:
    """`gate_ci.sh` sets ROOT to the repository it runs in, so these files are not the pipeline's.

    In a vendored install `scripts/` is the consumer's project code, `README.md` is the consumer's
    readme and `CLAUDE.md` is the consumer's own instructions. Judging them is not a wider version
    of this check, it is a different one, and there is no waiver short of bypassing the whole gate.
    """
    scaffold(tmp_path)
    literal = "docs/features/checkout/design-notes.md"
    (tmp_path / "README.md").write_text(f"See {literal} for details.\n")
    (tmp_path / "CLAUDE.md").write_text(f"Our own note lives at {literal}.\n")
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts" / "project_tool.py").write_text(f'PATH = "{literal}"\n')

    assert check_contract(tmp_path) == []


def test_the_same_literal_in_a_canonical_stage_instruction_is_judged(
    tmp_path: Path,
) -> None:
    """The other half of the boundary: narrowing the inventory must not blunt the check."""
    scaffold(tmp_path)
    literal = "docs/features/checkout/design-notes.md"
    (tmp_path / "agents" / "avenger-handover.md").write_text(f"write {literal}\n")
    assert any("design-notes.md" in p for p in check_contract(tmp_path))

    (tmp_path / "agents" / "avenger-handover.md").unlink()
    (tmp_path / "skills" / "phase-handover").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skills" / "phase-handover" / "SKILL.md").write_text(
        f"write {literal}\n"
    )
    assert any("design-notes.md" in p for p in check_contract(tmp_path))


def test_a_glob_path_literal_names_no_class(tmp_path: Path) -> None:
    """`*.md` out of a glob is a match rule, not a document, and has no honest outcome to choose.

    Reported as a class it prescribes three remedies for a file that does not exist, and neither
    giving `*.md` a READ_PATH entry nor deleting it is a thing anyone can do.
    """
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-handover.md").write_text(
        "Ignore docs/features/**/*.md when linting.\n"
    )
    assert check_contract(tmp_path) == []


def test_a_placeholder_inside_a_real_stem_is_still_a_class(tmp_path: Path) -> None:
    """`review-<slice>.md` is only nameable with a placeholder, so one may not disqualify a name."""
    scaffold(tmp_path)
    (tmp_path / "agents" / "avenger-handover.md").write_text(
        "docs/features/<feature>/phases/<n>-<slug>/scoped/summary-<slice>.md\n"
    )
    assert any("summary-<slice>.md" in p for p in check_contract(tmp_path))
