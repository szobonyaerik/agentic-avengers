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

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from doc_read_path import HANDOVER_MAX_BYTES  # noqa: E402
from spec_gate_context import (  # noqa: E402
    MARKER,
    build,
    check,
    contracts_section,
    layout,
    main,
    prior_card,
)

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
    path = (
        feature / "phases" / phase / "specs" / f"{phase.split('-')[0]}.1-a" / "spec.md"
    )
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
    block, notes, degraded = build(spec_at(feature, "3-api"))

    assert block.startswith(MARKER)
    assert "Tokens are stored in the vault" in block
    assert "`TokenStore.put` is idempotent." in block
    assert any("overview.md" in note and "included" in note for note in notes)
    assert any("2-storage" in note and "included" in note for note in notes)
    assert degraded is False


def test_only_the_contracts_section_of_the_overview_is_carried(feature: Path) -> None:
    """Not the whole document. The read path grants this reader that header and grants the spec
    WRITER the whole file; sending more here re-acquires a read that was deliberately removed."""
    block, _, _ = build(spec_at(feature, "1-first"))

    assert "## Contracts and Decisions" in block
    assert "Ship the thing." not in block
    assert "Nobody reads this part." not in block


def test_a_subheading_inside_the_contracts_section_does_not_truncate_it(
    tmp_path: Path,
) -> None:
    text = "## Contracts and Decisions\n\n### Storage\n\nVault only.\n\n## Risks\n\nignored\n"
    section = contracts_section(text)
    assert "Vault only." in section
    assert "ignored" not in section


def test_only_the_immediately_prior_phase_card_is_carried(feature: Path) -> None:
    """Not every prior phase's: re-reading all of them is the 272 KB read the contract card replaced,
    and it cost one phase ~527k tokens before it wrote a line."""
    card_at(
        feature,
        "1-first",
        CARD.replace("2-storage", "1-first").replace(
            "`TokenStore.put` is idempotent.", "the oldest contract"
        ),
    )
    card_at(feature, "2-storage")

    block, _, _ = build(spec_at(feature, "3-api"))

    assert "`TokenStore.put` is idempotent." in block
    assert "the oldest contract" not in block
    assert "2-storage" in block


def test_the_archive_is_never_read(feature: Path) -> None:
    """`handover-archive.md` is the half no stage reads. Reading it here would put the cost back."""
    card_at(feature, "2-storage")
    (feature / "phases" / "2-storage" / "handover-archive.md").write_text(
        "the archived detail\n"
    )

    block, _, _ = build(spec_at(feature, "3-api"))

    assert "the archived detail" not in block


def test_a_gap_in_the_numbering_still_finds_the_nearest_prior_card(
    feature: Path,
) -> None:
    card_at(feature, "1-first")
    assert prior_card(feature, 4).phase == "1-first"


def test_an_oversized_handover_is_bounded_by_the_read_paths_own_cap(
    feature: Path,
) -> None:
    """The cap's enforcement is diff-scoped, so a pre-rule handover is counted and not blocked.
    Reading it whole here would prepend all of it to EVERY spec write in the next phase — one
    measured handover held 272 KB, which is the cost the contract card was introduced to remove."""
    card_at(feature, "1-first", "# handover\n\n" + "x" * (HANDOVER_MAX_BYTES * 3))

    card = prior_card(feature, 2)
    block, notes, _ = build(spec_at(feature, "2-storage"))

    assert card.truncated is True
    assert len(card.body.encode("utf-8")) <= HANDOVER_MAX_BYTES
    assert len(block.encode("utf-8")) < HANDOVER_MAX_BYTES * 2
    assert any("TRUNCATED" in note for note in notes), (
        "a truncated context must never be silent"
    )


def test_a_card_within_the_cap_is_carried_whole_and_says_nothing_about_truncation(
    feature: Path,
) -> None:
    card_at(feature, "1-first")

    card = prior_card(feature, 2)
    _, notes, _ = build(spec_at(feature, "2-storage"))

    assert card.truncated is False
    assert "`TokenStore.put` is idempotent." in card.body
    assert not any("TRUNCATED" in note for note in notes)


# ── absent parts are normal, never errors ────────────────────────────────────


def test_the_first_phase_has_no_prior_card_and_that_is_not_a_failure(
    feature: Path,
) -> None:
    block, notes, degraded = build(spec_at(feature, "1-first"))

    assert "Tokens are stored in the vault" in block
    assert any("no prior phase card" in note for note in notes)
    assert degraded is False


def test_no_overview_at_all_is_degraded_not_a_silent_pass(tmp_path: Path) -> None:
    """A feature with no `overview.md` yet loses the same half of `contradiction` as one with the
    wrong heading, and there is no silent exemption for it: reporting success over zero contracts is
    the defect in its purest form. A legitimate "genuinely has none yet" state belongs on a recorded
    exception, never on this function inventing one to stay green."""
    root = tmp_path / "docs" / "features" / "demo"
    (root / "phases").mkdir(parents=True)
    block, notes, degraded = build(spec_at(root, "1-first"))

    assert block == ""
    assert degraded is True
    assert any(
        note.startswith("DEGRADED:") and "no readable overview.md" in note
        for note in notes
    )


def test_a_spec_outside_the_layout_is_reported_rather_than_guessed(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n")

    block, notes, degraded = build(spec)

    assert block == ""
    assert layout(spec) is None
    assert any("outside docs/features" in note for note in notes)
    assert degraded is False


def test_an_unreadable_overview_is_degraded_not_omitted(feature: Path) -> None:
    """An unreadable overview loses this reader's half of `contradiction` exactly like a missing
    one - there is no meaningful difference between the two from the gate's perspective, so both are
    degraded rather than one being reported and the other quietly omitted."""
    (feature / "overview.md").unlink()
    (feature / "overview.md").mkdir()

    block, notes, degraded = build(spec_at(feature, "1-first"))

    assert block == ""
    assert degraded is True
    assert any(
        note.startswith("DEGRADED:") and "no readable overview.md" in note
        for note in notes
    )


# ── the heading-missing case is DEGRADED, never a silent pass (issue #57) ────


def test_an_overview_missing_the_heading_entirely_is_reported_degraded(
    feature: Path,
) -> None:
    """This is clickup-agents' actual shape across 11 phases: the overview exists and has real
    content, just under `## Interfaces & contracts` and `## Key decisions & trade-offs` instead of
    `## Contracts and Decisions`. The old behaviour reported this on stderr and nothing else -
    `contradiction` silently lost half of what it checks, for every spec, forever."""
    (feature / "overview.md").write_text(
        "# Demo\n\n## Interfaces & contracts\n\nPUT /tokens.\n\n## Key decisions & trade-offs\n\n"
        "Vault-only storage.\n"
    )
    card_at(feature, "1-first")

    block, notes, degraded = build(spec_at(feature, "2-storage"))

    assert degraded is True
    assert "`TokenStore.put` is idempotent." in block, "the prior card is still carried"
    assert "PUT /tokens." not in block, (
        "content under the wrong heading is never picked up"
    )
    assert any(note.startswith("DEGRADED:") for note in notes)
    assert any("no ## Contracts and Decisions" in note for note in notes)


def test_a_heading_holding_only_the_templates_html_comment_is_degraded(
    feature: Path,
) -> None:
    """`docs/templates/overview.template.md` ships this exact heading followed by an HTML comment of
    instructional text. A freshly-templated, never-filled-in overview matches that shape verbatim,
    so it must degrade like a missing heading - not read as `included` just because the section has
    visible characters in it."""
    (feature / "overview.md").write_text(
        "# Demo\n\n"
        "## Contracts and Decisions\n"
        "<!-- A STABLE HEADER, and the only section some readers open. So everything a later spec "
        "can CONTRADICT belongs here. -->\n\n"
        "## Risks\n"
    )
    card_at(feature, "1-first")

    block, notes, degraded = build(spec_at(feature, "2-storage"))

    assert degraded is True
    assert "STABLE HEADER" not in block
    assert any(note.startswith("DEGRADED:") and "boilerplate" in note for note in notes)


def test_boilerplate_mixed_with_real_prose_is_not_degraded(feature: Path) -> None:
    """The comment-stripping is meant to catch a section that is ONLY boilerplate, not to punish an
    overview that keeps the template's comment alongside real, filled-in contracts."""
    (feature / "overview.md").write_text(
        "# Demo\n\n"
        "## Contracts and Decisions\n"
        "<!-- template instructions -->\n\n"
        "- Tokens are stored in the vault.\n"
    )

    block, notes, degraded = build(spec_at(feature, "1-first"))

    assert degraded is False
    assert "Tokens are stored in the vault." in block


def test_the_boilerplate_this_reader_discounts_is_not_carried_as_a_contract(
    feature: Path,
) -> None:
    """What is judged and what is sent must be the same text. Stripping comments to decide the
    section is boilerplate and then carrying the raw section anyway would ship the template's own
    instructional text into the observe pass under "the binding contracts the spec must not
    contradict" - so "do not rename this heading" would be presented to the model as a contract."""
    (feature / "overview.md").write_text(
        "# Demo\n\n"
        "## Contracts and Decisions\n"
        "<!-- Do not rename this heading - it is a read target. -->\n\n"
        "- Tokens are stored in the vault.\n"
    )

    block, _, degraded = build(spec_at(feature, "1-first"))

    assert degraded is False
    assert "Tokens are stored in the vault." in block
    assert "Do not rename this heading" not in block


def test_a_feature_with_a_genuinely_empty_contracts_section_is_not_degraded(
    feature: Path,
) -> None:
    """The heading IS present, just with nothing under it yet - a feature early in planning. That is
    the normal "not written yet" state `overview_heading_missing` must not confuse with a heading
    that is missing altogether."""
    (feature / "overview.md").write_text(
        "# Demo\n\n## Contracts and Decisions\n\n## Risks\n"
    )
    card_at(feature, "1-first")

    _, notes, degraded = build(spec_at(feature, "2-storage"))

    assert degraded is False
    assert not any(note.startswith("DEGRADED:") for note in notes)


def test_the_degraded_exit_code_is_not_discarded_by_the_caller(
    feature: Path, capsys
) -> None:
    """`main()` is what a shell hook actually calls. Exit 3 is the signal a caller must not `|| :`
    away - this pins the contract at the boundary the fix was for, not just the pure function."""
    (feature / "overview.md").write_text(
        "# Demo\n\n## Interfaces & contracts\n\nPUT /tokens.\n"
    )
    spec = spec_at(feature, "1-first")

    rc = main([str(spec)])

    assert rc == 3
    assert "DEGRADED" in capsys.readouterr().err


def test_a_normal_context_exits_zero(feature: Path) -> None:
    rc = main([str(spec_at(feature, "1-first"))])
    assert rc == 0


# ── check: every overview.md on disk, independent of any one spec being gated ────


def test_check_finds_an_overview_missing_the_heading(tmp_path: Path) -> None:
    feature = tmp_path / "docs" / "features" / "demo"
    feature.mkdir(parents=True)
    (feature / "overview.md").write_text(
        "# Demo\n\n## Interfaces & contracts\n\nPUT /tokens.\n"
    )

    problems = check(tmp_path, enforce_all=True)

    assert len(problems) == 1
    assert "no ## Contracts and Decisions heading" in problems[0]


def test_check_passes_an_overview_that_has_the_heading(feature: Path) -> None:
    problems = check(feature.parents[1], enforce_all=True)
    assert problems == []


def test_check_finds_a_feature_with_no_overview_at_all(tmp_path: Path) -> None:
    """A missing `overview.md` can never itself appear as a changed path, so this has to be found by
    walking feature directories, not by globbing for files that exist."""
    feature = tmp_path / "docs" / "features" / "demo"
    feature.mkdir(parents=True)

    problems = check(tmp_path, enforce_all=True)

    assert len(problems) == 1
    assert "no readable overview.md" in problems[0]


def test_check_finds_a_boilerplate_only_heading(tmp_path: Path) -> None:
    feature = tmp_path / "docs" / "features" / "demo"
    feature.mkdir(parents=True)
    (feature / "overview.md").write_text(
        "# Demo\n\n## Contracts and Decisions\n<!-- unfilled template comment -->\n"
    )

    problems = check(tmp_path, enforce_all=True)

    assert len(problems) == 1
    assert "boilerplate" in problems[0]


@pytest.mark.subprocess(
    "the subject IS the diff scope, which only the real git binary can answer: a stubbed "
    "changed_paths would prove the stub, not the boundary this check runs on"
)
def test_check_is_diff_scoped_by_default(tmp_path: Path, monkeypatch) -> None:
    """Without --all, an overview the current change did not touch is COUNTED, never blocked - the
    same boundary every other check on this repo shares (scripts/applicability.py). Without this, a
    project with years of pre-rule overviews would fail CI the moment this check shipped."""
    feature = tmp_path / "docs" / "features" / "demo"
    feature.mkdir(parents=True)
    (feature / "overview.md").write_text(
        "# Demo\n\n## Interfaces & contracts\n\nPUT /tokens.\n"
    )

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # An identity, before the commit that needs one: a container with no global git config exits
    # 128 with "Please tell me who you are", which errors this test instead of failing it.
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init", "--no-verify"], cwd=tmp_path, check=True
    )

    problems = check(tmp_path)

    assert problems == [], "a committed, untouched overview is counted, not blocked"


@pytest.mark.subprocess(
    "the subject IS the diff scope, which only the real git binary can answer: a stubbed "
    "changed_paths would prove the stub, not the boundary this check runs on"
)
def test_check_scopes_a_missing_overview_by_its_feature_directory(
    tmp_path: Path,
) -> None:
    """A missing `overview.md` can never itself be a changed path (there is nothing on disk to
    name), so scoping on the artifact path alone would make it permanently unenforceable. Touching
    ANY file under the feature directory - here, a phase's spec - must be enough to bring the
    missing overview into scope."""
    feature = tmp_path / "docs" / "features" / "demo"
    spec = feature / "phases" / "1-first" / "specs" / "1.1-a" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# spec\n")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init", "--no-verify"], cwd=tmp_path, check=True
    )
    spec.write_text("# spec\n\nedited\n")

    problems = check(tmp_path)

    assert len(problems) == 1
    assert "no readable overview.md" in problems[0]
