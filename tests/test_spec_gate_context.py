"""The CONTEXT block, which is what makes `contradiction` an observable blocking category.

Four things block a spec, and one of them is a statement that "contradicts a binding contract the
overview or the prior phase's card declares". Nothing assembled those two documents, so that half of
the category could never fire: a closed set of four was three items and a claim.

Two properties matter as much as building the block at all, and both are cost:

  * it carries EXACTLY the extents `scripts/doc_read_path.py` declares — the overview's
    `## Contracts and Decisions` section, and the IMMEDIATELY prior phase's card — because sending
    the whole overview, or every prior phase's handover, is the read that the read-path work removed;
  * an absent part is normal and never an error. Phase 1 has no prior card.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from spec_gate_context import MARKER, build, contracts_section, layout, prior_card  # noqa: E402

OVERVIEW = """---
feature: demo
---

# Demo

## Goal

Ship the thing.

## Contracts and Decisions

- Tokens are stored in the vault, never in the database.
- The API returns the envelope shape.

## Risks

Nobody reads this part.
"""

CARD = """---
phase: 2-storage
---

# Phase 2 handover

## Binding contracts

- `TokenStore.put` is idempotent.
"""


@pytest.fixture
def feature(tmp_path: Path) -> Path:
    root = tmp_path / "docs" / "features" / "demo"
    (root / "phases").mkdir(parents=True)
    (root / "overview.md").write_text(OVERVIEW)
    return root


def spec_at(feature: Path, phase: str) -> Path:
    path = feature / "phases" / phase / "specs" / f"{phase.split('-')[0]}.1-a" / "spec.md"
    path.parent.mkdir(parents=True)
    path.write_text("# spec\n")
    return path


def card_at(feature: Path, phase: str, body: str = CARD) -> None:
    directory = feature / "phases" / phase
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "handover.md").write_text(body)


# ── both parts, and only the named extents ───────────────────────────────────


def test_both_parts_are_carried_when_both_exist(feature: Path) -> None:
    card_at(feature, "2-storage")
    block, notes = build(spec_at(feature, "3-api"))

    assert block.startswith(MARKER)
    assert "Tokens are stored in the vault" in block
    assert "`TokenStore.put` is idempotent." in block
    assert any("overview.md" in note and "included" in note for note in notes)
    assert any("2-storage" in note and "included" in note for note in notes)


def test_only_the_contracts_section_of_the_overview_is_carried(feature: Path) -> None:
    """Not the whole document. The read path grants this reader that header and grants the spec
    WRITER the whole file; sending more here re-acquires a read that was deliberately removed."""
    block, _ = build(spec_at(feature, "1-first"))

    assert "## Contracts and Decisions" in block
    assert "Ship the thing." not in block
    assert "Nobody reads this part." not in block


def test_a_subheading_inside_the_contracts_section_does_not_truncate_it(tmp_path: Path) -> None:
    text = "## Contracts and Decisions\n\n### Storage\n\nVault only.\n\n## Risks\n\nignored\n"
    section = contracts_section(text)
    assert "Vault only." in section
    assert "ignored" not in section


def test_only_the_immediately_prior_phase_card_is_carried(feature: Path) -> None:
    """Not every prior phase's: re-reading all of them is the 272 KB read the contract card replaced,
    and it cost one phase ~527k tokens before it wrote a line."""
    card_at(feature, "1-first", CARD.replace("2-storage", "1-first").replace(
        "`TokenStore.put` is idempotent.", "the oldest contract"))
    card_at(feature, "2-storage")

    block, _ = build(spec_at(feature, "3-api"))

    assert "`TokenStore.put` is idempotent." in block
    assert "the oldest contract" not in block
    assert "2-storage" in block


def test_the_archive_is_never_read(feature: Path) -> None:
    """`handover-archive.md` is the half no stage reads. Reading it here would put the cost back."""
    card_at(feature, "2-storage")
    (feature / "phases" / "2-storage" / "handover-archive.md").write_text("the archived detail\n")

    block, _ = build(spec_at(feature, "3-api"))

    assert "the archived detail" not in block


def test_a_gap_in_the_numbering_still_finds_the_nearest_prior_card(feature: Path) -> None:
    card_at(feature, "1-first")
    assert prior_card(feature, 4)[0] == "1-first"


# ── absent parts are normal, never errors ────────────────────────────────────


def test_the_first_phase_has_no_prior_card_and_that_is_not_a_failure(feature: Path) -> None:
    block, notes = build(spec_at(feature, "1-first"))

    assert "Tokens are stored in the vault" in block
    assert any("no prior phase card" in note for note in notes)


def test_a_feature_with_no_contracts_section_still_carries_the_card(feature: Path) -> None:
    (feature / "overview.md").write_text("# Demo\n\n## Goal\n\nShip it.\n")
    card_at(feature, "1-first")

    block, notes = build(spec_at(feature, "2-storage"))

    assert "`TokenStore.put` is idempotent." in block
    assert "Ship it." not in block
    assert any("no ## Contracts and Decisions" in note for note in notes)


def test_no_context_at_all_is_an_empty_block_that_says_so(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "features" / "demo"
    (root / "phases").mkdir(parents=True)
    block, notes = build(spec_at(root, "1-first"))

    assert block == ""
    assert len(notes) == 2 and all("absent" in note for note in notes)


def test_a_spec_outside_the_layout_is_reported_rather_than_guessed(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n")

    block, notes = build(spec)

    assert block == ""
    assert layout(spec) is None
    assert any("outside docs/features" in note for note in notes)


def test_an_unreadable_overview_omits_it_rather_than_raising(feature: Path) -> None:
    (feature / "overview.md").unlink()
    (feature / "overview.md").mkdir()

    block, notes = build(spec_at(feature, "1-first"))

    assert block == ""
    assert any("no ## Contracts and Decisions" in note for note in notes)
