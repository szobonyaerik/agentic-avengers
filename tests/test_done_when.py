"""A phase's *Done when* in a form a gate can read, and the one decision read from it.

`faithful-rep` phase 3 ran about 48 hours because a real finding had no state meaning "confirmed,
owned by a later phase", and a human judged each item against the phase's *Done when* by hand. The
*Done when* was prose. These tests hold the structured half beside it: a table in plan.md gives each
condition an id, the specs' requirement lines tag the conditions they carry, and `decide` answers
whether a finding against a requirement blocks the phase - failing closed, never clear, wherever
that cannot be read.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import done_when  # noqa: E402
import pipeline_state  # noqa: E402

PLAN = """---
feature: demo
readers: x
---
# Implementation Plan: demo

## Phase plan

### Phase 1 — webhook
- **Goal**: receive.
- **Done when**: a webhook lands a row.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | a webhook lands a row |

### Phase 3 — real-frames
- **Goal**: a rep is real frames.
- **Done when**: **the captain can watch `handstand_hold` hold** - and a cyclic clip loops from a
  real-frame cut with a residual in the 2-9 mm range rather than an exact zero.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | `handstand_hold` holds for the whole clip - no pike, no tumble, no feet on the mat |
  | DW-2 | a cyclic clip loops from a real-frame cut, residual 2-9 mm, never an exact 0.0 |
  | DW-<n> | <a placeholder row, never a condition> |

### Phase 30 — thirty
- **Done when**: something else entirely.

  | done-when | outcome |
  |-----------|---------|
  | DW-9 | thirty's own condition |

### Phase 4 — support-fidelity
- **Done when**: limbs sit on their bearing surfaces.
"""

SPEC = """---
feature: demo
phase: 3-real-frames
spec: {spec}
readers: x
---
# Spec

## Requirements
{requirements}

## Acceptance criteria
Done.
"""


def feature(tmp_path: Path, plan: str = PLAN) -> Path:
    root = tmp_path / "docs" / "features" / "demo"
    (root / "phases").mkdir(parents=True)
    (root / "plan.md").write_text(plan, encoding="utf-8")
    return root


def phase(feature_dir: Path, slug: str = "3-real-frames") -> Path:
    directory = feature_dir / "phases" / slug
    directory.mkdir(exist_ok=True)
    return directory


def spec(phase_dir: Path, name: str, requirements: str) -> Path:
    directory = phase_dir / "specs" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "spec.md"
    path.write_text(SPEC.format(spec=name, requirements=requirements), encoding="utf-8")
    return path


BOUND = """- R3.2.4 — `binding: integration` — `done_when: DW-2` — the cyclic (start, lag) pair
- R3.2.5 — `binding: e2e` — `done_when: DW-1` — the one-way span is the largest real run
- R3.2.9 — `binding: e2e` — the classification margin is persisted
"""


# --- the table in plan.md -------------------------------------------------------------------------


def test_the_table_is_read_from_the_phase_section_and_placeholders_are_not_conditions(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    found = done_when.declared_conditions(phase(root))
    assert [c.id for c in found] == ["DW-1", "DW-2"]
    assert "handstand_hold" in found[0].outcome


def test_phase_3_does_not_read_phase_30s_section(tmp_path: Path) -> None:
    """`Phase 3` matches on a word boundary - phase 30's DW-9 must never become phase 3's."""
    root = feature(tmp_path)
    assert [c.id for c in done_when.declared_conditions(phase(root))] == [
        "DW-1",
        "DW-2",
    ]
    assert [c.id for c in done_when.declared_conditions(phase(root, "30-thirty"))] == [
        "DW-9"
    ]


def test_a_prose_only_done_when_declares_no_table(tmp_path: Path) -> None:
    root = feature(tmp_path)
    assert done_when.declared_conditions(phase(root, "4-support-fidelity")) is None


def test_no_plan_and_no_heading_both_read_as_no_table(tmp_path: Path) -> None:
    root = feature(tmp_path)
    assert done_when.declared_conditions(phase(root, "7-unplanned")) is None
    (root / "plan.md").unlink()
    assert done_when.declared_conditions(phase(root)) is None


def test_a_header_with_no_rows_is_an_empty_table_not_a_missing_one() -> None:
    assert done_when.conditions("| done-when | outcome |\n|---|---|\n") == []
    assert done_when.conditions("prose only") is None


# --- the tags on requirement lines ---------------------------------------------------------------


def test_tags_are_read_off_the_declaration_line_in_both_spellings() -> None:
    text = (
        "- R3.2.4 — `binding: integration` — `done_when: DW-2` — cyclic\n"
        "- R3.2.5 — `binding: e2e` — done-when: DW-1, DW-3 — hold\n"
        "- R3.2.9 — `binding: e2e` — margin, which mentions done_when in prose only later\n"
    )
    tagged = done_when.tagged_requirements(text)
    assert tagged == {"R3.2.4": {"DW-2"}, "R3.2.5": {"DW-1", "DW-3"}}


def test_a_heading_style_declaration_carries_its_tag_in_the_block_below() -> None:
    text = "### R3.2.4\nbinding: integration\ndone_when: DW-2\n\n### R3.2.5\nbinding: e2e\n"
    assert done_when.tagged_requirements(text) == {"R3.2.4": {"DW-2"}}


def test_a_tag_never_leaks_across_the_next_declaration() -> None:
    text = "### R3.2.4\nbinding: integration\n\n### R3.2.5\ndone_when: DW-1\n"
    assert done_when.tagged_requirements(text) == {"R3.2.5": {"DW-1"}}


# --- the decision ---------------------------------------------------------------------------------


def test_a_finding_against_a_tagged_requirement_blocks_and_names_the_condition(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    decision = done_when.decide(phase(root), ["R3.2.4"])
    assert decision.code == done_when.BLOCKS
    assert "DW-2" in decision.reason and "R3.2.4" in decision.reason


def test_a_finding_against_an_untagged_requirement_is_clear(tmp_path: Path) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    decision = done_when.decide(phase(root), ["R3.2.9"])
    assert decision.code == done_when.CLEAR
    assert decision.checked == ("DW-1", "DW-2")


def test_a_finding_with_no_requirement_is_clear_when_the_done_when_is_fully_bound(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    assert done_when.decide(phase(root), []).code == done_when.CLEAR


def test_one_blocking_id_among_several_still_blocks(tmp_path: Path) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    assert done_when.decide(phase(root), ["R3.2.9", "R3.2.5"]).code == done_when.BLOCKS


def test_a_prose_only_done_when_is_undecidable_not_clear(tmp_path: Path) -> None:
    root = feature(tmp_path)
    target = phase(root, "4-support-fidelity")
    spec(target, "4.1-a", "- R4.1.1 — `binding: e2e` — limbs\n")
    decision = done_when.decide(target, ["R4.1.1"])
    assert decision.code == done_when.UNDECIDABLE
    assert "prose only" in decision.reason and "cannot defer" in decision.reason


def test_a_condition_no_requirement_carries_is_undecidable_not_clear(
    tmp_path: Path,
) -> None:
    """Leaving one requirement untagged must not make every finding deferrable."""
    root = feature(tmp_path)
    spec(
        phase(root),
        "3.2-span",
        "- R3.2.4 — `binding: integration` — `done_when: DW-2` — x\n",
    )
    decision = done_when.decide(phase(root), ["R3.2.9"])
    assert decision.code == done_when.UNDECIDABLE
    assert "DW-1" in decision.reason and "NO requirement" in decision.reason


def test_a_tag_naming_a_condition_the_plan_never_declared_is_undecidable(
    tmp_path: Path,
) -> None:
    root = feature(tmp_path)
    spec(
        phase(root),
        "3.2-span",
        BOUND + "- R3.2.10 — `binding: e2e` — `done_when: DW-7` — x\n",
    )
    decision = done_when.decide(phase(root), ["R3.2.9"])
    assert decision.code == done_when.UNDECIDABLE
    assert "DW-7" in decision.reason


def test_a_table_with_no_rows_is_undecidable(tmp_path: Path) -> None:
    plan = PLAN.replace("  | DW-1 | a webhook lands a row |\n", "")
    root = feature(tmp_path, plan)
    target = phase(root, "1-webhook")
    spec(target, "1.1-a", "- R1.1.1 — `binding: e2e` — x\n")
    assert done_when.decide(target, ["R1.1.1"]).code == done_when.UNDECIDABLE


# --- the CLI --------------------------------------------------------------------------------------


def test_the_cli_exit_codes_are_clear_blocks_undecidable(tmp_path: Path) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    assert done_when.main(["decide", str(phase(root)), "--spec-id", "R3.2.9"]) == 0
    assert done_when.main(["decide", str(phase(root)), "--spec-id", "R3.2.4"]) == 1
    assert (
        done_when.main(
            ["decide", str(phase(root, "4-support-fidelity")), "--spec-id", "R4.1.1"]
        )
        == 2
    )


def test_the_cli_refuses_silence_about_the_requirement(tmp_path: Path, capsys) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    assert done_when.main(["decide", str(phase(root))]) == 2
    assert "Silence is not either" in capsys.readouterr().err
    assert done_when.main(["decide", str(phase(root)), "--no-requirement"]) == 0


def test_show_prints_each_condition_with_its_carriers(tmp_path: Path, capsys) -> None:
    root = feature(tmp_path)
    spec(phase(root), "3.2-span", BOUND)
    assert done_when.main(["show", str(phase(root))]) == 0
    out = capsys.readouterr().out
    assert "DW-1" in out and "R3.2.5" in out and "DW-2" in out and "R3.2.4" in out


# --- one owner of the plan heading ----------------------------------------------------------------


def test_pipeline_state_reads_plan_headings_through_done_when() -> None:
    assert pipeline_state.PLAN_PHASE_HEADING is done_when.PLAN_PHASE_HEADING
    assert done_when.planned_phases(PLAN) == [
        (1, "webhook"),
        (3, "real-frames"),
        (30, "thirty"),
        (4, "support-fidelity"),
    ]
    assert done_when.slugify("Support / Fidelity") == "support-fidelity"


@pytest.mark.parametrize("dash", ["—", "–", "-"])
def test_every_dash_a_plan_has_used_is_a_phase_heading(dash: str) -> None:
    assert done_when.planned_phases(f"### Phase 2 {dash} storage\n") == [(2, "storage")]
