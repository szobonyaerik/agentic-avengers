"""An amendment is the pipeline's only way to say "this changed, and only this re-verifies".

Without one, a post-verification correction cost a full verification round: one measured phase ran
eight attempts, and rounds 3 through 8 were that shape. The saving is batching — but batching a
credential leak is not a saving, so the escape hatch is pinned here alongside it.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from amendments import (  # noqa: E402
    AmendmentError,
    close_amendment,
    due,
    load,
    main,
    open_amendment,
    pending,
    scope,
)


@pytest.fixture
def phase(tmp_path: Path) -> Path:
    directory = tmp_path / "8-clickup"
    directory.mkdir()
    return directory


def passing_verdict(phase: Path) -> None:
    (phase / "verdict.json").write_text(json.dumps({"verdict": "pass", "attempt": 2, "findings": []}))


def reason_file(tmp_path: Path, text: str = "the scrub was defeated by JSON escaping") -> Path:
    path = tmp_path / "reason.md"
    path.write_text(text)
    return path


# ── the record itself ────────────────────────────────────────────────────────


def test_an_amendment_names_the_requirement_ids_it_touches(phase: Path) -> None:
    record = open_amendment(phase, ["R8.2.30", "R8.2.31"], "a fix")
    assert record["id"] == "A1"
    assert record["requirements"] == ["R8.2.30", "R8.2.31"]
    assert record["status"] == "pending"
    assert load(phase)["amendments"] == [record]


def test_ids_increment_per_phase(phase: Path) -> None:
    assert open_amendment(phase, ["R8.2.1"], "x")["id"] == "A1"
    assert open_amendment(phase, ["R8.2.2"], "y")["id"] == "A2"


def test_an_amendment_with_no_requirement_ids_is_refused(phase: Path) -> None:
    """The naming IS the scope of the re-verification it owes. Without ids it re-verifies nothing,
    which is the same as skipping verification while claiming to have narrowed it."""
    with pytest.raises(AmendmentError, match="re-verifies nothing"):
        open_amendment(phase, [], "x")


def test_something_that_is_not_a_requirement_id_is_refused(phase: Path) -> None:
    with pytest.raises(AmendmentError, match="not requirement ids"):
        open_amendment(phase, ["the login flow"], "x")


def test_an_amendment_must_say_why(phase: Path) -> None:
    with pytest.raises(AmendmentError, match="why"):
        open_amendment(phase, ["R8.2.1"], "   ")


# ── batched at phase close, EXCEPT security ──────────────────────────────────


def test_an_ordinary_amendment_is_batched(phase: Path) -> None:
    """No passing verdict yet: the phase has not closed, so nothing is owed right now."""
    open_amendment(phase, ["R8.2.1"], "a rename")
    assert pending(phase)
    assert due(phase) == []


def test_a_security_amendment_is_never_batched(phase: Path) -> None:
    """A phase-8 credential leak must not sit in a batch waiting for the phase to close. The cost
    argument that justifies batching does not apply to a secret already in a log."""
    open_amendment(phase, ["R8.2.30"], "the key reaches audit_log.error", security=True)
    owed = due(phase)
    assert [a["id"] for a in owed] == ["A1"]


def test_a_pending_amendment_on_a_PASSING_phase_is_owed_now(phase: Path) -> None:
    """That verdict is a claim about code that has since changed."""
    passing_verdict(phase)
    open_amendment(phase, ["R8.2.1"], "a rename")
    assert [a["id"] for a in due(phase)] == ["A1"]


def test_closing_an_amendment_clears_it(phase: Path) -> None:
    passing_verdict(phase)
    open_amendment(phase, ["R8.2.1"], "a rename")
    close_amendment(phase, "A1", "tests/demo/8-clickup/test_rename.py")
    assert pending(phase) == []
    assert due(phase) == []


def test_closing_needs_its_own_evidence(phase: Path) -> None:
    """The point of the amendment path is that the amended requirements carry evidence, not that
    they skip it."""
    open_amendment(phase, ["R8.2.1"], "a rename")
    with pytest.raises(AmendmentError, match="evidence"):
        close_amendment(phase, "A1", "  ")


def test_closing_an_unknown_amendment_is_refused(phase: Path) -> None:
    with pytest.raises(AmendmentError, match="no amendment"):
        close_amendment(phase, "A9", "somewhere")


# ── the re-verify scope is the ids, not the phase ────────────────────────────


def test_scope_is_the_pending_requirement_ids_deduplicated(phase: Path) -> None:
    open_amendment(phase, ["R8.2.1", "R8.2.2"], "x")
    open_amendment(phase, ["R8.2.2", "R8.2.3"], "y")
    assert scope(phase) == ["R8.2.1", "R8.2.2", "R8.2.3"]


def test_a_closed_amendment_leaves_the_scope(phase: Path) -> None:
    open_amendment(phase, ["R8.2.1"], "x")
    open_amendment(phase, ["R8.2.2"], "y")
    close_amendment(phase, "A1", "evidence")
    assert scope(phase) == ["R8.2.2"]


# ── fail closed ──────────────────────────────────────────────────────────────


def test_a_corrupt_ledger_is_an_error_not_an_empty_one(phase: Path) -> None:
    """Reading a corrupt ledger as 'no amendments' would silently drop a pending security
    re-verification, which is the one thing this file exists to make impossible to lose."""
    (phase / "amendments.json").write_text("{oh no")
    with pytest.raises(AmendmentError):
        load(phase)


def test_a_phase_with_no_ledger_has_no_amendments(phase: Path) -> None:
    assert load(phase)["amendments"] == []
    assert due(phase) == []


def test_an_unreadable_verdict_does_not_count_as_passing(phase: Path) -> None:
    (phase / "verdict.json").write_text("not json")
    open_amendment(phase, ["R8.2.1"], "x")
    assert due(phase) == [], "an unreadable verdict is not a pass, so nothing is owed by that route"


# ── the CLI, and the prose-in-a-file rule it obeys ───────────────────────────


def test_the_reason_comes_from_a_file(phase: Path, tmp_path: Path) -> None:
    """Author-written prose on a command line is denied by content under --auto. The reason is
    prose, so it is read from a file — the same rule as --intent and GATE_BYPASS."""
    assert main(["open", str(phase), "--requirements", "R8.2.1",
                 "--reason-file", str(reason_file(tmp_path))]) == 0
    assert load(phase)["amendments"][0]["reason"].startswith("the scrub")


def test_cli_due_exits_one_when_re_verification_is_owed(phase: Path, tmp_path: Path) -> None:
    main(["open", str(phase), "--requirements", "R8.2.30",
          "--reason-file", str(reason_file(tmp_path)), "--security"])
    assert main(["due", str(phase)]) == 1
    assert main(["scope", str(phase)]) == 0


def test_cli_due_exits_zero_on_a_clean_phase(phase: Path) -> None:
    assert main(["due", str(phase)]) == 0


def test_the_ledger_declares_its_own_readers(phase: Path, tmp_path: Path) -> None:
    """Every document the read-path table governs declares who reads it, IN the document. JSON has
    no frontmatter, so it is a top-level key — and it is written by the only writer rather than
    asked of anyone: three artifact classes once shipped with a declared reader and nothing
    instructing a writer to emit it."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import doc_read_path

    main(["open", str(phase), "--requirements", "R8.2.1", "--reason-file", str(reason_file(tmp_path))])
    ledger = json.loads((phase / "amendments.json").read_text())
    assert ledger["readers"], "amendments.json is on the read path and must say who reads it"
    assert doc_read_path.READ_PATH["amendments.json"]["readers"] == ledger["readers"], (
        "the table and the writer disagree about who reads this file"
    )


def test_a_legacy_ledger_gains_readers_on_the_next_write(phase: Path, tmp_path: Path) -> None:
    (phase / "amendments.json").write_text(json.dumps({"phase": "8-clickup", "amendments": []}))
    main(["open", str(phase), "--requirements", "R8.2.1", "--reason-file", str(reason_file(tmp_path))])
    assert json.loads((phase / "amendments.json").read_text())["readers"]
