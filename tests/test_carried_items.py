"""A handover's forward-looking claims are discharged, not merely written.

Phase 8 of one measured feature recorded, verbatim, that caller-supplied identifiers would become a
problem in phases 9 to 12. Phase 9 was the first such caller and shipped exactly that defect - a
user-controlled path segment interpolated unencoded, so a name containing `?` or `#` retargets the
write. The review gate caught it after verification had already passed. The prediction was correct,
specific, actionable, and became nothing: no spec line, no test, no check.

The fix reuses the slot the contract card already shipped - `## Open items`, a table with stable ids -
rather than adding a second mechanism beside it. These tests hold the two obligations that make it
binding: a phase says what it carries, and the next phase answers every row.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# The CLI is driven in-process below; the only real processes here are `git`, and they are the
# subject: the diff scoping that lets a repository upgrade is a claim about what git reports, and a
# faked diff would test the fake. See scripts/subprocess_check.py, the pipeline's only cost gate.
pytestmark = pytest.mark.subprocess(
    "the diff scoping is a claim about what git actually reports; a faked diff tests the fake"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import carried_items  # noqa: E402

CARD = """---
feature: demo
phase: {phase}
stage: handover
readers: avenger-spec-writer @ per spec
---
## Phase {phase} - contract card

Delivered the thing.

## Open items
{items}

## Next phase
> later
"""

TABLE = """| id | kind | one-line title | where the detail lives |
|----|------|----------------|------------------------|
| OBS-1 | open-finding | rate limiter has no negative case | verdict.json#observations[1] |
| FWD-1 | forward-claim | caller-supplied ids reach the URL path unencoded from phase 9 on | this card |"""


def feature(tmp_path: Path) -> Path:
    root = tmp_path / "docs" / "features" / "demo" / "phases"
    root.mkdir(parents=True)
    return root


def phase(root: Path, slug: str, items: str) -> Path:
    directory = root / slug
    directory.mkdir(exist_ok=True)
    (directory / "handover.md").write_text(
        CARD.format(phase=slug, items=items), encoding="utf-8"
    )
    return directory


def run(*args: str) -> int:
    """The CLI, driven IN-PROCESS. `main()` is the whole command, so spawning a python for each of
    these would buy nothing but a process launch on every run of the suite - which is exactly what
    `scripts/subprocess_check.py`, this pipeline's only cost gate, exists to stop."""
    return carried_items.main(list(args))


# --- the slot the template already had -----------------------------------------------------------


def test_the_shipped_template_and_skill_use_the_heading_this_parses() -> None:
    """The fix is the existing slot made binding, so the three must name the same section. If they
    drift, a card written exactly as instructed fails a check that says it carries nothing."""
    for relative in (
        "docs/templates/handover.template.md",
        "skills/phase-handover/SKILL.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert carried_items.section_body(text) is not None, (
            f"{relative} no longer has a `{carried_items.SECTION_HEADING}` section that "
            f"carried_items.py can read"
        )


def test_the_template_teaches_a_row_for_a_forward_looking_claim() -> None:
    """An open finding was always in the template. The prediction is the half that was missing."""
    text = (ROOT / "docs" / "templates" / "handover.template.md").read_text(
        encoding="utf-8"
    )
    assert "forward-claim" in text


# --- reading a card ------------------------------------------------------------------------------


def test_items_are_read_with_their_ids_and_kinds(tmp_path: Path) -> None:
    directory = phase(feature(tmp_path), "8-a", TABLE)
    items, says_none, present = carried_items.declared(directory)
    assert present and not says_none
    assert [(i.id, i.kind) for i in items] == [
        ("OBS-1", "open-finding"),
        ("FWD-1", "forward-claim"),
    ]


def test_template_placeholder_rows_are_not_items(tmp_path: Path) -> None:
    """A card left with the template's own `| OBS-<n> | … |` row carries nothing, and must not read
    as an item nobody can ever discharge."""
    directory = phase(
        tmp_path / "x" if False else feature(tmp_path),
        "8-a",
        "| id | kind | title | where |\n|---|---|---|---|\n| OBS-<n> | open-finding | <t> | <w> |",
    )
    items, says_none, present = carried_items.declared(directory)
    assert present and not items and not says_none


def test_angle_brackets_outside_the_id_cell_do_not_erase_the_row(
    tmp_path: Path,
) -> None:
    """Placeholder-ness is judged on the ID CELL, where the template's `OBS-<n>` lives. Reading them
    anywhere in the row dropped real items - a claim about `<slug>`, a path under
    `docs/features/<feature>/`, a title naming `Map<String,X>` - and a dropped row is never owed to
    the next phase, which is the silent loss this module exists to remove."""
    directory = phase(
        feature(tmp_path),
        "8-a",
        "| id | kind | title | where |\n|---|---|---|---|\n"
        "| FWD-1 | forward-claim | `<slug>` reaches the route unencoded | this card |\n"
        "| OBS-2 | open-finding | Map<String,X> key collision | docs/features/<feature>/x.md |",
    )
    items, says_none, present = carried_items.declared(directory)
    assert present and not says_none
    assert [i.id for i in items] == ["FWD-1", "OBS-2"]


def test_a_three_column_pre_rule_row_reports_its_title_not_its_pointer(
    tmp_path: Path,
) -> None:
    """Every card written before the `kind` column exists is `| id | title | pointer |`. Assuming the
    second cell is always a kind named the pointer as the item, in the very message the next phase's
    spec writer is asked to act on."""
    directory = phase(
        feature(tmp_path),
        "8-a",
        "| id | title | where |\n|---|---|---|\n"
        "| OBS-1 | rate limiter has no negative case | verdict.json#observations[1] |",
    )
    items, _says_none, _present = carried_items.declared(directory)
    assert [(i.kind, i.title) for i in items] == [
        ("item", "rate limiter has no negative case")
    ]


def test_an_emphasised_id_is_still_an_item(tmp_path: Path) -> None:
    """A writer who bolds or code-quotes the id has written a real item. Going blind on it would drop
    it silently - the failure `requirement_cap.py` had when a table-formatted spec counted zero."""
    directory = phase(
        feature(tmp_path),
        "8-a",
        "| id | kind | title | where |\n|---|---|---|---|\n"
        "| **FWD-1** | forward-claim | ids reach the path unencoded | this card |\n"
        "| `OBS-2` | open-finding | rate limiter | verdict.json |",
    )
    items, _says_none, _present = carried_items.declared(directory)
    assert [i.id for i in items] == ["FWD-1", "OBS-2"]


def test_an_explicit_none_is_an_answer_and_silence_is_not(
    tmp_path: Path, capsys
) -> None:
    root = feature(tmp_path)
    explicit = phase(root, "8-a", "none")
    assert carried_items.declared(explicit) == ([], True, True)
    assert run("declared", str(explicit)) == 0

    silent = phase(root, "9-b", "")
    assert carried_items.declared(silent) == ([], False, True)
    assert run("declared", str(silent)) == 1
    assert "explicit `none`" in capsys.readouterr().err


def test_a_card_with_no_section_at_all_does_not_close_the_phase(
    tmp_path: Path, capsys
) -> None:
    directory = feature(tmp_path) / "8-a"
    directory.mkdir()
    (directory / "handover.md").write_text(
        "---\nfeature: demo\nreaders: x\n---\n# card\n\n## Next phase\n> later\n",
        encoding="utf-8",
    )
    assert run("declared", str(directory)) == 1
    assert "Silence is not `none`" in capsys.readouterr().err


# --- what the next phase owes ---------------------------------------------------------------------


def test_the_next_phase_owes_the_previous_phases_items(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    assert [i.id for i in carried_items.owed(nxt)] == ["OBS-1", "FWD-1"]
    assert run("due", str(nxt)) == 1


def test_a_feature_first_phase_owes_nothing(tmp_path: Path) -> None:
    """Nothing precedes it, so there is nothing to answer - never an error."""
    first = phase(feature(tmp_path), "1-a", TABLE)
    assert carried_items.owed(first) == []
    assert run("due", str(first)) == 0


def test_a_pre_rule_card_with_no_section_owes_nothing(tmp_path: Path) -> None:
    """A repository upgrading to this version must not be held hostage by history it never touched -
    the same rule the verifier pre-check, the spec re-gate cache and the mutation gate run on."""
    root = feature(tmp_path)
    old = root / "8-a"
    old.mkdir()
    (old / "handover.md").write_text(
        "---\nreaders: x\n---\n# old card\n", encoding="utf-8"
    )
    assert run("due", str(phase(root, "9-b", "none"))) == 0


def test_only_the_immediately_prior_card_is_owed(tmp_path: Path) -> None:
    """The same rule the spec gate's CONTEXT block uses, and it is that module's decision, not a
    second copy here. An item that still applies further out is re-carried, never re-inherited."""
    root = feature(tmp_path)
    phase(root, "7-old", TABLE)
    phase(root, "8-a", "none")
    assert carried_items.owed(phase(root, "9-b", "none")) == []


def test_a_gap_in_phase_numbering_does_not_lose_the_prior_card(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "2-a", TABLE)
    assert [i.id for i in carried_items.owed(phase(root, "4-c", "none"))] == [
        "OBS-1",
        "FWD-1",
    ]


# --- the last card, which has no successor to owe -------------------------------------------------

LAST_CARD = """---
feature: demo
phase: {phase}
stage: handover
next: {next}
readers: avenger-spec-writer @ per spec
---
# Phase {phase} - contract card

## Open items
{items}

## Next phase
> {next}
"""

FORWARD = "| id | kind | title | where |\n|---|---|---|---|\n| FWD-1 | forward-claim | {t} | {w} |"


def last_phase(root: Path, slug: str, items: str, nxt: str = "e2e") -> Path:
    directory = root / slug
    directory.mkdir(exist_ok=True)
    (directory / "handover.md").write_text(
        LAST_CARD.format(phase=slug, items=items, next=nxt), encoding="utf-8"
    )
    return directory


@pytest.mark.parametrize("nxt", ["e2e", "ship"])
def test_a_last_card_forward_claim_that_names_an_issue_passes(
    tmp_path: Path, nxt: str
) -> None:
    directory = last_phase(
        feature(tmp_path),
        "9-b",
        FORWARD.format(t="ids reach the path unencoded", w="filed as #41"),
        nxt=nxt,
    )
    assert carried_items.unfiled(directory) == []
    assert run("filed", str(directory)) == 0


def test_a_last_card_forward_claim_naming_no_issue_does_not_close(
    tmp_path: Path, capsys
) -> None:
    """The last card is the one place the answer-every-row obligation binds nobody, so it is exactly
    where the hole this module closes would reopen."""
    directory = last_phase(
        feature(tmp_path),
        "9-b",
        FORWARD.format(t="ids reach the path unencoded", w="this card"),
    )
    assert [i.id for i in carried_items.unfiled(directory)] == ["FWD-1"]
    assert run("filed", str(directory)) == 1
    err = capsys.readouterr().err
    assert "FWD-1" in err and "issue" in err


def test_an_issue_url_counts_as_naming_one(tmp_path: Path) -> None:
    directory = last_phase(
        feature(tmp_path),
        "9-b",
        FORWARD.format(
            t="ids reach the path unencoded", w="https://github.com/o/r/issues/41"
        ),
    )
    assert carried_items.unfiled(directory) == []


def test_a_non_last_card_may_carry_a_bare_forward_claim(tmp_path: Path) -> None:
    """It is owed to its successor, not to ship - and answering it there is the ordinary path."""
    directory = last_phase(
        feature(tmp_path), "8-a", FORWARD.format(t="x", w="this card"), nxt="9-b"
    )
    assert carried_items.unfiled(directory) == []
    assert run("filed", str(directory)) == 0


def test_a_last_card_with_no_forward_claim_owes_nothing(tmp_path: Path) -> None:
    """An `OBS-<n>` open finding on the last card is deliberately out of scope: the cap already made
    it visible, and widening this presence check into one would be a second obligation nobody asked
    for."""
    directory = last_phase(
        feature(tmp_path),
        "9-b",
        "| id | kind | title | where |\n|---|---|---|---|\n"
        "| OBS-1 | open-finding | rate limiter | verdict.json |",
    )
    assert carried_items.unfiled(directory) == []
    assert run("filed", str(directory)) == 0


def test_a_pre_rule_card_with_no_next_field_owes_nothing(tmp_path: Path) -> None:
    """Nothing here may hard-fail a repository over history it has not touched."""
    directory = phase(feature(tmp_path), "9-b", FORWARD.format(t="x", w="this card"))
    assert carried_items.card_next(directory) is None
    assert carried_items.unfiled(directory) == []
    assert run("filed", str(directory)) == 0


def test_the_ci_sweep_holds_the_last_cards_claims_too(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    last_phase(feature(root), "1-only", FORWARD.format(t="x", w="this card"))
    assert any("FWD-1" in line for line in carried_items.check(root))


# --- present but unparseable: the phase-9 defect (issue #47) --------------------------------------
#
# Phase 9's real card wrote `### Open items` (h3) with bullet rows (`- OBS-1 | ... | ...`), not a
# table. `owed()` used to read that as "nothing carried" - the same shape as an explicit `none` -
# because it discarded `declared()`'s own present/says-none distinction. `due` on phase 10 then
# exited 0 and the phase closed without answering OBS-1 through OBS-4, not because they were
# discharged but because the parser could not see them. This is the mirror of retroactive blocking,
# and the worse direction: it passes old work silently instead of failing loudly.

H3_BULLET_CARD = """---
feature: demo
phase: {phase}
stage: handover
readers: x
---
## Phase {phase} - contract card

Delivered the thing.

### Open items
- OBS-1 | rate limiter has no negative case | verdict.json#observations[1]
- OBS-2 | retry loop can spin | verdict.json#observations[2]
- OBS-3 | webhook replay window | verdict.json#observations[3]
- OBS-4 | idempotency key collision | verdict.json#observations[4]

## Next phase
> later
"""


def test_the_heading_level_was_never_the_defect_bullet_rows_are(
    tmp_path: Path, capsys
) -> None:
    """`section_body` already reads whatever level a card used (h2 or h3) - that part always worked.
    Feed the parser phase 9's ACTUAL shape: an h3 heading with bullet rows instead of a table. It must
    now refuse instead of silently passing as nothing-carried - a guard is proven by going red when
    the defect it guards against returns, not by passing the happy path."""
    root = feature(tmp_path)
    nine = root / "9-b"
    nine.mkdir()
    (nine / "handover.md").write_text(
        H3_BULLET_CARD.format(phase="9-b"), encoding="utf-8"
    )

    items, says_none, present = carried_items.declared(nine)
    assert present and not items and not says_none

    nxt = phase(root, "10-c", "none")
    with pytest.raises(carried_items.CarriedError, match="could not be parsed"):
        carried_items.owed(nxt)

    assert run("due", str(nxt)) == 2
    err = capsys.readouterr().err
    assert "could not be parsed" in err
    assert "9-b" in err


def test_check_reports_the_unparseable_prior_card_and_keeps_scanning_other_phases(
    tmp_path: Path,
) -> None:
    """`check` sweeps every phase in one pass; one phase's undecidable prior card must not abort the
    scan of the rest. It is reported as a problem for the phase that owes it, alongside phase 9's own
    (separate) `declared` obligation failing on its own unparseable section."""
    root = feature(tmp_path)
    nine = root / "9-b"
    nine.mkdir()
    (nine / "handover.md").write_text(
        H3_BULLET_CARD.format(phase="9-b"), encoding="utf-8"
    )
    phase(root, "10-c", "none")

    problems = carried_items.check(tmp_path, enforce_all=True)
    assert any("could not be parsed" in p and "9-b" in p for p in problems)
    assert any("9-b" in p and "neither an item nor an explicit" in p for p in problems)


def test_an_explicit_none_under_an_h3_heading_is_still_none(tmp_path: Path) -> None:
    """Pins the fix does not widen into failing everything: only unparseable content must fail, and an
    explicit `none` - even under the h3 level phase 9 also used - is still a real answer."""
    root = feature(tmp_path)
    nine = root / "9-b"
    nine.mkdir()
    (nine / "handover.md").write_text(
        "---\nfeature: demo\nphase: 9-b\nstage: handover\nreaders: x\n---\n"
        "## Phase 9-b - contract card\n\nDelivered the thing.\n\n### Open items\nnone\n\n"
        "## Next phase\n> later\n",
        encoding="utf-8",
    )
    items, says_none, present = carried_items.declared(nine)
    assert present and says_none and not items

    nxt = phase(root, "10-c", "none")
    assert carried_items.owed(nxt) == []
    assert run("due", str(nxt)) == 0


# --- discharging ----------------------------------------------------------------------------------


def test_discharging_every_item_clears_the_phase(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    reason = tmp_path / "why.txt"
    reason.write_text(
        "phases 10-12 still affected; re-carried on this card\n", encoding="utf-8"
    )

    assert (
        run(
            "discharge",
            str(nxt),
            "FWD-1",
            "--as",
            "built",
            "--by",
            "R9.1.4 in specs/9.1-write/spec.md",
        )
        == 0
    )
    assert run("due", str(nxt)) == 1
    assert (
        run(
            "discharge",
            str(nxt),
            "OBS-1",
            "--as",
            "declined",
            "--reason-file",
            str(reason),
        )
        == 0
    )
    assert run("due", str(nxt)) == 0


def test_the_ledger_records_which_card_the_item_came_from(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    carried_items.discharge(nxt, "FWD-1", "tested", by="tests/demo/9-b/test_paths.py")
    record = json.loads((nxt / "carried.json").read_text(encoding="utf-8"))
    assert record["discharges"][0]["from_phase"] == "8-a"
    assert record["discharges"][0]["as"] == "tested"
    assert record["readers"], (
        "the ledger declares its own readers, like every governed document"
    )


def test_an_id_the_prior_card_never_declared_is_refused(tmp_path: Path, capsys) -> None:
    """A typo would otherwise record an answer to a question nobody asked while the real item stays
    owed - and the phase would then close over it."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    assert run("discharge", str(nxt), "FWD-9", "--as", "built", "--by", "x") == 2
    assert "FWD-1" in capsys.readouterr().err


def test_declining_without_a_reason_is_refused(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    with pytest.raises(carried_items.CarriedError, match="stated reason"):
        carried_items.discharge(nxt, "OBS-1", "declined")


def test_building_or_testing_without_naming_the_artifact_is_refused(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    for how in ("built", "tested"):
        with pytest.raises(carried_items.CarriedError, match="--by"):
            carried_items.discharge(nxt, "OBS-1", how)


def test_a_second_discharge_of_one_item_replaces_the_first(tmp_path: Path) -> None:
    """A declined item later built is one answer, not two contradictory ones on the record."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    carried_items.discharge(nxt, "OBS-1", "declined", reason="not this phase")
    carried_items.discharge(nxt, "OBS-1", "built", by="R9.1.1")
    ledger = json.loads((nxt / "carried.json").read_text(encoding="utf-8"))
    assert [d["as"] for d in ledger["discharges"]] == ["built"]


def test_a_corrupt_ledger_is_an_error_not_an_empty_one(tmp_path: Path) -> None:
    """Read as "nothing discharged" it is safe; read as "everything discharged" it is not, and the
    two are one typo apart. So it refuses."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    (nxt / "carried.json").write_text("{oh no", encoding="utf-8")
    assert run("due", str(nxt)) == 2


# --- the CI sweep, and why it is scoped -------------------------------------------------------------


def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S603,S607
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)  # noqa: S603,S607
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)  # noqa: S603,S607
    (tmp_path / ".keep").write_text("", encoding="utf-8")
    commit_all(tmp_path)  # `git diff HEAD` has no answer in a repo with no commits
    return tmp_path


def commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S603,S607
    subprocess.run(["git", "commit", "-qm", "x"], cwd=root, check=True)  # noqa: S603,S607


def test_check_holds_a_phase_the_diff_touches(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    phases = feature(root)
    phase(phases, "8-a", TABLE)
    phase(phases, "9-b", "none")
    assert carried_items.check(root), (
        "an undischarged item in an untracked phase must be enforced"
    )


def test_check_only_counts_a_phase_the_diff_does_not_touch(tmp_path: Path) -> None:
    """The rule lands on a document class every consumer repo already has on disk, so a full audit
    would fail CI over cards written before it existed. The hook still holds the phase being closed,
    which is a phase the diff touches by construction."""
    root = git_repo(tmp_path)
    phases = feature(root)
    phase(phases, "8-a", TABLE)
    phase(phases, "9-b", "none")
    commit_all(root)

    assert carried_items.check(root) == []
    assert carried_items.check(root, enforce_all=True), (
        "--all is the audit, and it audits"
    )


def test_check_enforces_nothing_when_git_cannot_say_what_changed(
    tmp_path: Path,
) -> None:
    """Falling back to enforcing everything is the hostage failure the scoping removes."""
    phases = feature(tmp_path)
    phase(phases, "8-a", TABLE)
    phase(phases, "9-b", "none")
    assert carried_items.check(tmp_path) == []


def test_check_over_a_tree_with_no_cards_is_clean_and_says_so(
    tmp_path: Path, capsys
) -> None:
    assert carried_items.check(tmp_path) == []
    assert "nothing to check" in capsys.readouterr().err


# --- it is enforced, not asked for ------------------------------------------------------------------


def test_the_in_session_hook_holds_every_obligation() -> None:
    """A rule only CI applies arrives after the phase has already closed."""
    text = (ROOT / "scripts" / "hook_verifier.sh").read_text(encoding="utf-8")
    assert "carried_items.py" in text
    for action in ("declared", "due", "filed"):
        assert f'carried_items.py" {action}' in text, (
            f"the hook does not run `{action}`"
        )


def test_the_hook_tells_an_undecidable_check_apart_from_an_owed_item() -> None:
    """A corrupt ledger prescribed `discharge`, which cannot repair malformed JSON. Exit 1 is the
    obligation and anything else is a cause of its own - the rule the attempt cap in the same file
    already follows."""
    text = (ROOT / "scripts" / "hook_verifier.sh").read_text(encoding="utf-8")
    assert "carried-undecidable" in text and "could not be DECIDED" in text


def test_ci_sweeps_it_too() -> None:
    """And a rule only an in-session hook can apply stops existing the moment the phase is driven
    any other way - the same reason the verification attempt cap is enforced in both places."""
    assert "carried_items.py" in (ROOT / "scripts" / "gate_ci.sh").read_text(
        encoding="utf-8"
    )


def test_the_ledger_is_on_the_read_path_table() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from doc_read_path import READ_PATH

    assert carried_items.FILENAME in READ_PATH, (
        "a document the pipeline writes and reads declares its readers in the table - a class that "
        "is not there is one nobody decided the cost of"
    )
