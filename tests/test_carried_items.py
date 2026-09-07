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


@pytest.fixture(autouse=True)
def _metrics_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`defer`/`recarry` emit a measurement through whatever writer is on PATH. A unit test must
    never reach the operator's live firstmate store, so emission is off here; the emission itself
    is tested against the stub sink in tests/test_pipeline_metrics.py."""
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")


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
    with pytest.raises(carried_items.CarriedError, match="CANNOT BE DETERMINED"):
        carried_items.owed(nxt)

    assert run("due", str(nxt)) == 2
    err = capsys.readouterr().err
    assert "CANNOT BE DETERMINED" in err
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
    assert any("CANNOT BE DETERMINED" in p and "9-b" in p for p in problems)
    assert any("9-b" in p and "Silence is not none" in p for p in problems)


def test_check_reports_an_unreadable_verdict_and_keeps_scanning_other_phases(
    tmp_path: Path,
) -> None:
    """A verdict is read for the deferral stamps it carries, and one that cannot be parsed is that
    phase's own problem line - never the end of the sweep. Raising through `check` reported nothing
    about any other phase, including the phases with real owed items, and did it over a phase the
    diff never touched."""
    root = feature(tmp_path)
    eight = phase(root, "8-a", TABLE)
    (eight / "verdict.json").write_text("{not json", encoding="utf-8")
    phase(root, "9-b", "none")

    problems = carried_items.check(tmp_path, enforce_all=True)
    assert any("8-a" in p and "cannot read" in p for p in problems)
    assert any("9-b" in p and "FWD-1" in p and "no answer here" in p for p in problems)


def test_an_unparseable_prior_card_names_what_it_is_not_a_broken_table(
    tmp_path: Path,
) -> None:
    """The template ships placeholder rows (`| OBS-<n> | ... |`), which parse perfectly and are not
    items. Such a card is the same undecidable state and must still fail - but the message must not
    tell its reader to fix a table format that is already correct."""
    root = feature(tmp_path)
    nine = phase(
        root,
        "9-b",
        "| id | kind | one-line title | where the detail lives |\n"
        "|----|------|----------------|------------------------|\n"
        "| OBS-<n> | open-finding | <title> | verdict.json#observations[n] |",
    )
    items, says_none, present = carried_items.declared(nine)
    assert present and not items and not says_none

    nxt = phase(root, "10-c", "none")
    with pytest.raises(carried_items.CarriedError) as caught:
        carried_items.owed(nxt)
    message = str(caught.value)
    assert "could not be parsed" not in message
    assert "placeholder" in message
    assert "9-b/handover.md" in message


def test_a_corrupt_ledger_stays_undecidable_in_the_ci_sweep(
    tmp_path: Path, capsys
) -> None:
    """`check` catches `owed()`'s undecidable prior card so one phase does not abort the sweep - it
    must NOT also catch the corrupt ledger `load()` refuses. Demoted to a problem line, exit 2
    (undecidable) becomes exit 1 (owed), and the remedy printed is `discharge`, which cannot repair
    malformed JSON."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    (nxt / "carried.json").write_text("{oh no", encoding="utf-8")

    with pytest.raises(carried_items.CarriedError, match="cannot read"):
        carried_items.check(tmp_path, enforce_all=True)

    assert run("check", "--root", str(tmp_path), "--all") == 2
    err = capsys.readouterr().err
    assert "cannot read" in err
    assert "discharge <phase-dir>" not in err


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
    spec_with(root, "9-b", "9.1-write", "R9.1.4 the caller-supplied id is encoded\n")
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
    pinned = tmp_path / "tests" / "demo" / "9-b" / "test_paths.py"
    pinned.parent.mkdir(parents=True)
    pinned.write_text("def test_paths_are_encoded():\n    pass\n", encoding="utf-8")
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
    spec_with(
        root, "9-b", "9.1-write", "R9.1.1 the rate limiter refuses a negative window\n"
    )
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


# --- the discharge NAMES something, and the name is resolved (retro: a warning nothing enforced) ---
#
# Phase 12 of clickup-agents closed with a forward-looking warning that single-replica deployment was
# load-bearing: the poller has no cross-process lock, so its at-most-once property holds only with a
# single writer. Phase 13 honoured it - but only because a human copied the warning into the next
# worker's instructions by hand. Nothing enforced it.
#
# The mechanism was already here and stopped one step short. `discharge --by` says, in its own help,
# that it names "the spec, requirement id or test that now covers it", and the module's own docstring
# says "naming it is what makes the discharge checkable later" - and NOTHING RESOLVED IT. `--by x`
# was accepted. A prediction discharged into a name that resolves to nothing is the prose this whole
# module exists to replace, one layer down.


def spec_with(root: Path, phase_slug: str, sub: str, body: str) -> Path:
    """A spec on disk under a phase, so a requirement id named in a discharge can resolve to it."""
    directory = root / phase_slug / "specs" / sub
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "spec.md"
    target.write_text(body, encoding="utf-8")
    return target


def test_a_discharge_naming_something_that_does_not_exist_is_refused(
    tmp_path: Path,
) -> None:
    """`--by x` used to be accepted, so the one thing that makes a discharge checkable - the name -
    could be anything at all."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    with pytest.raises(carried_items.CarriedError, match="resolve"):
        carried_items.discharge(nxt, "FWD-1", "built", by="x")


def test_a_requirement_id_no_spec_declares_is_refused(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    spec_with(root, "9-b", "9.1-write", "R9.1.1 the write is encoded\n")
    with pytest.raises(carried_items.CarriedError, match="R9.1.4"):
        carried_items.discharge(nxt, "FWD-1", "built", by="R9.1.4")


def test_a_requirement_id_a_spec_declares_resolves(tmp_path: Path) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    spec_with(root, "9-b", "9.1-write", "R9.1.4 the caller-supplied id is encoded\n")
    carried_items.discharge(
        nxt, "FWD-1", "built", by="R9.1.4 in specs/9.1-write/spec.md"
    )


def test_a_test_path_that_exists_resolves_and_one_that_does_not_is_refused(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    tests_dir = tmp_path / "tests" / "demo" / "9-b"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_single_writer.py").write_text(
        "def test_only_one_poller_writes():\n    pass\n", encoding="utf-8"
    )
    carried_items.discharge(
        nxt, "FWD-1", "tested", by="tests/demo/9-b/test_single_writer.py"
    )
    with pytest.raises(carried_items.CarriedError, match="resolve"):
        carried_items.discharge(
            nxt, "OBS-1", "tested", by="tests/demo/9-b/test_gone.py"
        )


def test_a_named_test_node_must_be_in_the_file_it_names(tmp_path: Path) -> None:
    """The file existing is not the claim; the test existing is. A node id pointing into a file that
    does not contain it is the same rot one level finer."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    tests_dir = tmp_path / "tests" / "demo" / "9-b"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_single_writer.py").write_text(
        "def test_only_one_poller_writes():\n    pass\n", encoding="utf-8"
    )
    carried_items.discharge(
        nxt,
        "FWD-1",
        "tested",
        by="tests/demo/9-b/test_single_writer.py::test_only_one_poller_writes",
    )
    with pytest.raises(carried_items.CarriedError, match="resolve"):
        carried_items.discharge(
            nxt,
            "OBS-1",
            "tested",
            by="tests/demo/9-b/test_single_writer.py::test_two_pollers",
        )


def test_a_discharge_whose_artifact_is_later_deleted_reopens_the_item(
    tmp_path: Path,
) -> None:
    """THIS is what gives a forward-looking warning a form that can fail. A discharge is not a
    one-time ceremony: the artifact it names is re-resolved every time the obligation is checked, so
    deleting the test that pinned "single-replica is load-bearing" turns the claim red again instead
    of leaving a ledger entry pointing at nothing."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    pinned = tmp_path / "tests" / "demo" / "9-b" / "test_single_writer.py"
    pinned.parent.mkdir(parents=True)
    pinned.write_text(
        "def test_only_one_poller_writes():\n    pass\n", encoding="utf-8"
    )
    carried_items.discharge(
        nxt, "FWD-1", "tested", by="tests/demo/9-b/test_single_writer.py"
    )
    carried_items.discharge(nxt, "OBS-1", "declined", reason="not this phase")
    assert run("due", str(nxt)) == 0

    pinned.unlink()
    assert run("due", str(nxt)) == 1, (
        "the discharge still names a test that no longer exists, and the item is owed again"
    )
    assert any(
        "test_single_writer.py" in line for line in carried_items.phase_problems(nxt)
    )


def test_declining_needs_no_artifact(tmp_path: Path) -> None:
    """`declined` answers with a stated reason, not with a pointer. Requiring one there would make
    the answer the module calls real into the one nobody can give."""
    root = feature(tmp_path)
    phase(root, "8-a", TABLE)
    nxt = phase(root, "9-b", "none")
    carried_items.discharge(nxt, "FWD-1", "declined", reason="phases 10-12; re-carried")
    assert carried_items.stale_discharges(nxt) == []


# --- deferred findings: the fourth disposition, in the same slot (issue #115) ---------------------

PLAN = """---
feature: demo
readers: x
---
### Phase 8 — poller
- **Done when**: the poller polls.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | the poller polls exactly once per cycle |

### Phase 9 — writer
- **Done when**: a name reaches the store.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | a caller-supplied name lands in the store |

### Phase 10 — reader
- **Done when**: prose only here.

### Phase 12 — staging
- **Done when**: props are drawn.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | every prop is drawn where the athlete touches it |
"""

SPEC = """---
feature: demo
phase: {phase}
spec: {spec}
readers: x
---
## Requirements
{requirements}

## Acceptance criteria
Done.
"""

RECORD = "`dips` renders one bar through the torso\nspan 66 frames before and after A3, unchanged; bar axis through the torso at every frame\n"

DEFERRED_ROW = "| id | kind | one-line title | where the detail lives |\n|----|------|----------------|------------------------|\n| FWD-1 | deferred-finding | `dips` renders one bar through the torso | carried.json#deferrals (owner 12-staging) |"


def plan(root: Path) -> None:
    (root.parent / "plan.md").write_text(PLAN, encoding="utf-8")


def spec(phase_dir: Path, name: str, requirements: str) -> None:
    directory = phase_dir / "specs" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.md").write_text(
        SPEC.format(phase=phase_dir.name, spec=name, requirements=requirements),
        encoding="utf-8",
    )


def bound_phase(root: Path, slug: str = "8-poller", items: str = "none") -> Path:
    """A phase whose Done when is structured and fully carried: R<n>.1.1 carries DW-1, R<n>.1.2 does not."""
    plan(root)
    directory = phase(root, slug, items)
    n = slug.split("-")[0]
    spec(
        directory,
        f"{n}.1-a",
        f"- R{n}.1.1 — `binding: e2e` — `done_when: DW-1` — the bound one\n"
        f"- R{n}.1.2 — `binding: integration` — the unbound one\n",
    )
    return directory


def record_file(tmp_path: Path, text: str = RECORD) -> Path:
    path = tmp_path / "record.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_deferral_records_the_owner_the_measurement_and_the_done_when_it_was_checked_against(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    record = carried_items.defer(
        eight, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.2"]
    )
    assert record["owner"] == "12-staging"
    assert record["title"] == "`dips` renders one bar through the torso"
    assert record["measurement"].startswith(
        "span 66 frames before and after A3, unchanged"
    )
    assert record["done_when"] == {"phase": "8-poller", "checked": ["DW-1"]}
    assert record["origin"] == "8-poller"
    assert json.loads((eight / "carried.json").read_text())["deferrals"][0] == record


def test_a_finding_that_blocks_the_done_when_is_refused_with_exit_1(
    tmp_path: Path, capsys
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    rc = run(
        "defer",
        str(eight),
        "FWD-1",
        "--to",
        "12-staging",
        "--record-file",
        str(record_file(tmp_path)),
        "--spec-id",
        "R8.1.1",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "blocks this phase's *Done when*" in err and "DW-1" in err
    assert not (eight / "carried.json").exists()


def test_a_prose_only_done_when_cannot_defer_and_says_so_with_exit_2(
    tmp_path: Path, capsys
) -> None:
    root = feature(tmp_path)
    plan(root)
    ten = phase(root, "10-reader", "none")
    spec(ten, "10.1-a", "- R10.1.1 — `binding: e2e` — x\n")
    rc = run(
        "defer",
        str(ten),
        "FWD-1",
        "--to",
        "12-staging",
        "--record-file",
        str(record_file(tmp_path)),
        "--spec-id",
        "R10.1.1",
    )
    assert rc == 2
    assert "prose only" in capsys.readouterr().err


def test_an_owner_that_is_not_a_later_planned_phase_is_refused(tmp_path: Path) -> None:
    root = feature(tmp_path)
    nine = bound_phase(root, "9-writer")
    with pytest.raises(carried_items.CarriedError, match="not later than"):
        carried_items.defer(nine, "FWD-1", "8-poller", RECORD, spec_ids=["R9.1.2"])
    with pytest.raises(carried_items.CarriedError, match="not later than"):
        carried_items.defer(nine, "FWD-1", "9-writer", RECORD, spec_ids=["R9.1.2"])
    with pytest.raises(carried_items.CarriedError, match="does not declare"):
        carried_items.defer(nine, "FWD-1", "11-nowhere", RECORD, spec_ids=["R9.1.2"])


def test_a_bare_phase_number_resolves_to_the_plans_slug(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    record = carried_items.defer(eight, "FWD-1", "12", RECORD, spec_ids=["R8.1.2"])
    assert record["owner"] == "12-staging"


def test_a_record_with_no_measurement_is_refused(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    with pytest.raises(carried_items.CarriedError, match="no measurement"):
        carried_items.defer(
            eight, "FWD-1", "12-staging", "title only\n", spec_ids=["R8.1.2"]
        )


def test_silence_about_the_requirement_is_refused(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    with pytest.raises(carried_items.CarriedError, match="Silence is not either"):
        carried_items.defer(eight, "FWD-1", "12-staging", RECORD)
    record = carried_items.defer(
        eight, "FWD-1", "12-staging", RECORD, no_requirement=True
    )
    assert record["spec_ids"] == []


def test_a_deferral_is_never_edited(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    carried_items.defer(eight, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.2"])
    with pytest.raises(carried_items.CarriedError, match="already deferred"):
        carried_items.defer(
            eight, "FWD-1", "12-staging", "other\nnumbers\n", spec_ids=["R8.1.2"]
        )


def test_a_verdict_finding_is_deferred_by_its_id_and_its_spec_id_is_read_off_the_verdict(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    (eight / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "fail",
                "attempt": 1,
                "findings": [
                    {"id": "abc123def456", "spec_id": "R8.1.2", "status": "open"}
                ],
            }
        )
    )
    record = carried_items.defer(
        eight, "abc123def456", "12-staging", RECORD, finding="abc123def456"
    )
    assert record["finding"] == "abc123def456" and record["spec_ids"] == ["R8.1.2"]
    assert carried_items.backed_deferrals(eight) == {"abc123def456"}


def test_a_verdict_finding_the_verifier_never_raised_cannot_be_deferred(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    with pytest.raises(carried_items.CarriedError, match="no verdict record"):
        carried_items.defer(eight, "x1", "12-staging", RECORD, finding="nope")


def test_a_verdict_finding_against_a_bound_requirement_is_refused_even_when_the_caller_names_none(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    (eight / "verdict.json").write_text(
        json.dumps({"verdict": "fail", "findings": [{"id": "f1", "spec_id": "R8.1.1"}]})
    )
    with pytest.raises(carried_items.DeferralRefused):
        carried_items.defer(eight, "f1", "12-staging", RECORD, finding="f1")


# --- the row and the record are checked against each other -----------------------------------------


def test_a_deferral_with_no_row_on_the_card_fails_declared(
    tmp_path: Path, capsys
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    carried_items.defer(eight, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.2"])
    assert run("declared", str(eight)) == 1
    assert "no `deferred-finding` row" in capsys.readouterr().err
    assert run("deferred", str(eight)) == 1


def test_a_deferred_row_with_no_record_fails_declared(tmp_path: Path, capsys) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW)
    assert run("declared", str(eight)) == 1
    assert "no record in carried.json" in capsys.readouterr().err


def test_row_and_record_together_are_clean(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW)
    carried_items.defer(eight, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.2"])
    assert run("declared", str(eight)) == 0
    assert run("deferred", str(eight)) == 0
    assert carried_items.phase_problems(eight) == []


def test_a_deferred_stamp_nothing_backs_is_named(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    (eight / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "pass",
                "findings": [{"id": "f1", "spec_id": "R8.1.2", "status": "deferred"}],
            }
        )
    )
    problems = carried_items.deferral_problems(eight)
    assert (
        len(problems) == 1
        and "f1" in problems[0]
        and "nothing behind it" in problems[0]
    )
    assert run("deferred", str(eight)) == 1


def test_a_deferred_stamp_on_a_superseded_attempt_that_was_then_fixed_closes(
    tmp_path: Path,
) -> None:
    """Attempt 1 stamps `deferred` before any ledger record - the order the skill warns about - the
    handover is refused, the Verifier FIXES the finding instead, and attempt 2 passes.

    The archived stamp survives that (an archived attempt is not edited), and held against the
    phase forever it prescribed a remedy that does not exist: `defer` refuses a finding whose
    requirement carries a *Done when* condition, which is often exactly why it was fixed. Each
    finding is judged by the last record that states anything about it.
    """
    root = feature(tmp_path)
    eight = bound_phase(root, items="none")
    (eight / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "fail",
                "attempt": 1,
                "findings": [{"id": "f1", "spec_id": "R8.1.1", "status": "deferred"}],
            }
        )
    )
    assert run("deferred", str(eight)) == 1

    (eight / "verdict-attempt-1.json").write_text((eight / "verdict.json").read_text())
    (eight / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "pass",
                "attempt": 2,
                "findings": [{"id": "f1", "spec_id": "R8.1.1", "status": "fixed"}],
            }
        )
    )
    assert carried_items.deferral_problems(eight) == []
    assert run("deferred", str(eight)) == 0
    assert carried_items.phase_problems(eight) == []


def test_a_deferred_stamp_no_later_attempt_answers_is_still_named(
    tmp_path: Path,
) -> None:
    """History is still read for a stamp that was never rowed: an attempt-1 deferral nothing backs,
    and a later attempt that says nothing about it, is the state this check exists for."""
    root = feature(tmp_path)
    eight = bound_phase(root, items="none")
    (eight / "verdict-attempt-1.json").write_text(
        json.dumps(
            {
                "verdict": "fail",
                "attempt": 1,
                "findings": [{"id": "f1", "spec_id": "R8.1.2", "status": "deferred"}],
            }
        )
    )
    (eight / "verdict.json").write_text(
        json.dumps({"verdict": "pass", "attempt": 2, "findings": []})
    )
    problems = carried_items.deferral_problems(eight)
    assert len(problems) == 1 and "f1" in problems[0]
    assert run("deferred", str(eight)) == 1


def test_a_backed_deferral_on_a_later_attempt_answers_the_earlier_stamp(
    tmp_path: Path,
) -> None:
    """The ledger record written after attempt 1 resolves the stamp on both records, so the phase
    reports the deferral once - through the row-and-record checks - and not twice."""
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW)
    (eight / "verdict-attempt-1.json").write_text(
        json.dumps(
            {
                "verdict": "fail",
                "attempt": 1,
                "findings": [{"id": "f1", "spec_id": "R8.1.2", "status": "deferred"}],
            }
        )
    )
    (eight / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "pass",
                "attempt": 2,
                "findings": [
                    {
                        "id": "f1",
                        "spec_id": "R8.1.2",
                        "status": "deferred",
                        "deferred_to": "12-staging",
                    }
                ],
            }
        )
    )
    carried_items.defer(eight, "FWD-1", "12-staging", RECORD, finding="f1")
    assert carried_items.deferral_problems(eight) == []
    assert run("deferred", str(eight)) == 0


def test_a_stamp_that_disagrees_with_the_ledger_about_the_owner_is_named(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW)
    (eight / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "pass",
                "findings": [
                    {
                        "id": "f1",
                        "spec_id": "R8.1.2",
                        "status": "deferred",
                        "deferred_to": "9-writer",
                    }
                ],
            }
        )
    )
    carried_items.defer(eight, "FWD-1", "12-staging", RECORD, finding="f1")
    problems = carried_items.deferral_problems(eight)
    assert len(problems) == 1 and "disagree" in problems[0]


def test_a_pre_rule_ledger_with_no_deferrals_key_is_a_ledger_with_no_deferrals(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    eight = phase(root, "8-poller", "none")
    (eight / "carried.json").write_text(
        json.dumps({"phase": "8-poller", "discharges": []})
    )
    assert carried_items.deferrals(eight) == []
    assert carried_items.backed_deferrals(eight) == set()
    assert carried_items.deferral_problems(eight) == []


def test_an_unreadable_ledger_backs_nothing(tmp_path: Path) -> None:
    root = feature(tmp_path)
    eight = phase(root, "8-poller", "none")
    (eight / "carried.json").write_text("{not json")
    assert carried_items.backed_deferrals(eight) == set()


# --- the next phase inherits it with its owner, and carries it on unchanged ------------------------


def deferring_eight(tmp_path: Path) -> Path:
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW)
    carried_items.defer(eight, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.2"])
    return root


def test_the_next_phase_sees_the_row_with_its_owner(tmp_path: Path) -> None:
    root = deferring_eight(tmp_path)
    nine = bound_phase(root, "9-writer")
    [item] = carried_items.owed(nine)
    assert item.id == "FWD-1" and item.is_deferred() and item.owner == "12-staging"
    assert "owned by 12-staging" in item.describe()


def test_a_deferred_row_with_no_record_behind_it_is_undecidable_for_the_next_phase(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    plan(root)
    phase(root, "8-poller", DEFERRED_ROW)
    nine = phase(root, "9-writer", "none")
    with pytest.raises(carried_items.CarriedError, match="records no deferral"):
        carried_items.owed(nine)
    assert run("due", str(nine)) == 2


def test_declining_a_deferred_finding_owned_by_a_later_phase_is_refused(
    tmp_path: Path,
) -> None:
    root = deferring_eight(tmp_path)
    nine = bound_phase(root, "9-writer")
    with pytest.raises(carried_items.CarriedError, match="RE-CARRIED"):
        carried_items.discharge(nine, "FWD-1", "declined", reason="not ours")


def test_recarry_copies_the_record_byte_for_byte_and_records_the_discharge(
    tmp_path: Path,
) -> None:
    root = deferring_eight(tmp_path)
    nine = bound_phase(root, "9-writer")
    source = carried_items.deferrals(root / "8-poller")[0]
    carried = carried_items.recarry(nine, "FWD-1")
    for key in (
        "id",
        "finding",
        "spec_ids",
        "title",
        "owner",
        "measurement",
        "origin",
        "at",
    ):
        assert carried[key] == source[key]
    assert carried["recarried_by"] == "9-writer"
    ledger = json.loads((nine / "carried.json").read_text())
    [discharge] = ledger["discharges"]
    assert discharge["item"] == "FWD-1" and discharge["as"] == "declined"
    assert discharge["reason"] == carried_items.RECARRY_REASON.format(
        owner="12-staging"
    )
    assert carried_items.undischarged(nine) == []


def test_recarry_still_owes_the_row_on_this_phases_own_card(tmp_path: Path) -> None:
    root = deferring_eight(tmp_path)
    nine = bound_phase(root, "9-writer")
    carried_items.recarry(nine, "FWD-1")
    problems = carried_items.deferral_problems(nine)
    assert len(problems) == 1 and "no `deferred-finding` row" in problems[0]
    (nine / "handover.md").write_text(
        CARD.format(phase="9-writer", items=DEFERRED_ROW), encoding="utf-8"
    )
    assert carried_items.phase_problems(nine) == []


def test_recarry_is_unconditional_even_where_the_gate_would_have_blocked(
    tmp_path: Path,
) -> None:
    """The *Done when* gate is not asked again at a re-carry, and this is the case that used to be
    the only way to make it answer BLOCKS.

    It only ever answered on ids belonging to another phase: `done_when.decide` matches a finding's
    requirement ids against the conditions THIS phase's specs tag, and a deferral carries the ids of
    the phase that raised it, so the intersection is empty for every real deferral. Constructing a
    refusal took a cross-phase id (`R9.1.1` deferred out of phase 8) that the pipeline's own id
    scheme cannot produce. That same shape now carries on, with the measurement unchanged.
    """
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW.replace("FWD-1", "F9"))
    carried_items.defer(eight, "F9", "12-staging", RECORD, spec_ids=["R9.1.1"])
    nine = bound_phase(root, "9-writer")
    assert (
        carried_items.done_when.decide(nine, ["R9.1.1"]).code
        == carried_items.done_when.BLOCKS
    )
    carried = carried_items.recarry(nine, "F9")
    assert carried["measurement"] == carried_items.deferrals(eight)[0]["measurement"]
    assert carried["owner"] == "12-staging"
    (nine / "handover.md").write_text(
        CARD.format(phase="9-writer", items=DEFERRED_ROW.replace("FWD-1", "F9")),
        encoding="utf-8",
    )
    assert carried_items.phase_problems(nine) == []
    assert run("due", str(nine)) == 0


def test_a_fresh_defer_still_refuses_a_finding_that_blocks_this_phase(
    tmp_path: Path,
) -> None:
    """Dropping the re-ask does not touch the deferral decision path, which is where the gate's ids
    ARE the phase's own: phase 3's A3 stays refused."""
    root = feature(tmp_path)
    eight = bound_phase(root, items=DEFERRED_ROW)
    with pytest.raises(carried_items.DeferralRefused, match="DW-1"):
        carried_items.defer(eight, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.1"])


@pytest.mark.parametrize("slug", ["9-writer", "10-reader"])
def test_recarry_carries_on_where_this_phases_done_when_cannot_be_read(
    tmp_path: Path, slug: str
) -> None:
    """An undecidable *Done when* refuses a fresh deferral; it must not refuse a re-carry.

    Re-carrying decides nothing - the record crosses byte-for-byte, still owed to the same later
    phase - so refusing it wedged an intermediate phase that did nothing wrong: `discharge --as
    declined` is refused for an item a later phase owns, `built`/`tested` need a fix that does not
    exist, and the close then had only `GATE_BYPASS` left. Both undecidable shapes are here - phase
    9 declares a condition no spec carries, phase 10's *Done when* is prose only - and each closes.
    """
    root = deferring_eight(tmp_path)
    plan(root)
    intermediate = phase(root, slug, "none")
    carried = carried_items.recarry(intermediate, "FWD-1")
    source = carried_items.deferrals(root / "8-poller")[0]
    assert carried["measurement"] == source["measurement"]
    assert carried["owner"] == "12-staging" and carried["origin"] == "8-poller"
    (intermediate / "handover.md").write_text(
        CARD.format(phase=slug, items=DEFERRED_ROW), encoding="utf-8"
    )
    assert carried_items.phase_problems(intermediate) == []
    assert run("due", str(intermediate)) == 0


def test_the_owner_answers_it_like_any_row_and_cannot_recarry(tmp_path: Path) -> None:
    root = deferring_eight(tmp_path)
    nine = bound_phase(root, "9-writer", items=DEFERRED_ROW)
    carried_items.recarry(nine, "FWD-1")
    twelve = bound_phase(root, "12-staging")
    [item] = carried_items.owed(twelve)
    assert item.owner == "12-staging"
    with pytest.raises(carried_items.CarriedError, match="This phase answers it"):
        carried_items.recarry(twelve, "FWD-1")
    carried_items.discharge(
        twelve, "FWD-1", "declined", reason="props redesign lands in 13"
    )
    assert carried_items.undischarged(twelve) == []


def test_fixing_a_deferred_finding_early_is_allowed(tmp_path: Path) -> None:
    root = deferring_eight(tmp_path)
    nine = bound_phase(root, "9-writer")
    carried_items.discharge(nine, "FWD-1", "built", by="R9.1.2 in the writer spec")
    assert carried_items.undischarged(nine) == []


def test_a_forward_claim_is_not_recarried(tmp_path: Path) -> None:
    root = feature(tmp_path)
    plan(root)
    phase(root, "8-poller", TABLE)
    nine = phase(root, "9-writer", "none")
    with pytest.raises(carried_items.CarriedError, match="not a `deferred-finding`"):
        carried_items.recarry(nine, "FWD-1")


def test_a_deferred_finding_on_the_last_card_must_name_an_issue(tmp_path: Path) -> None:
    root = feature(tmp_path)
    last = bound_phase(root, items=DEFERRED_ROW)
    carried_items.defer(last, "FWD-1", "12-staging", RECORD, spec_ids=["R8.1.2"])
    card = last / "handover.md"
    card.write_text(
        card.read_text().replace("stage: handover", "stage: handover\nnext: ship")
    )
    assert [i.id for i in carried_items.unfiled(last)] == ["FWD-1"]
    card.write_text(
        card.read_text().replace("(owner 12-staging)", "(owner 12-staging) #115")
    )
    assert carried_items.unfiled(last) == []


def test_the_cli_prints_the_row_to_paste(tmp_path: Path, capsys) -> None:
    root = feature(tmp_path)
    eight = bound_phase(root)
    rc = run(
        "defer",
        str(eight),
        "FWD-1",
        "--to",
        "12-staging",
        "--record-file",
        str(record_file(tmp_path)),
        "--spec-id",
        "R8.1.2",
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "FWD-1 deferred to 12-staging" in captured.out
    assert "| FWD-1 | deferred-finding |" in captured.err
