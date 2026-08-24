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
    READ_PATH,
    check_contract,
    layout_inventory,
    main,
)

REPO = Path(__file__).resolve().parents[1]


def scaffold(root: Path) -> None:
    """A minimal repository the contract check passes: one entry, one template that instructs it."""
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
    """`check --contract` scans arbitrary project code, so one latin-1 file must not crash CI.

    The source scan widened from `*.md` under four canonical directories to every `.md/.json/.py/.sh`
    under `scripts/` and `docs/templates/`, which in a consumer repo is that project's own code.
    `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so it escaped the fail-closed guard and
    reached `gate_ci.sh` as a stack trace instead of the named refusal every other unreadable file
    gets. The same bug class this change fixed in `phase_artifacts._status`.
    """
    scaffold(tmp_path)
    rogue = tmp_path / "scripts" / "rogue.py"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_bytes(b"# caf\xe9 in latin-1\n")

    with pytest.raises(SystemExit) as raised:
        check_contract(tmp_path)
    assert "cannot read" in str(raised.value)
    assert "rogue.py" in str(raised.value)
    assert "fail closed" in str(raised.value)
