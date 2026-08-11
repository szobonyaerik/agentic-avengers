"""The blocking set stays closed, and the verdict stays out of the model's hands.

The gate this replaces told its reviewer to assume gaps and, when unsure, to choose NO-GO. Four
rejected rounds later one spec had grown from 25k to 51k characters, because the only response
available to a rejection is more text. The counter-design is that a model reports, a model
classifies, and a **script** decides — so what is pinned here is the arithmetic of the decision and
the refusals that stop it drifting.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from spec_gate_triage import (  # noqa: E402
    BLOCKING,
    CATEGORIES,
    NOTE,
    TriageError,
    decide,
    main,
)

TRIAGE_PROMPT = ROOT / "prompts" / "spec-gate-triage.md"
OBSERVE_PROMPT = ROOT / "prompts" / "spec-gate-observe.md"


def obs(*ids: str) -> list[dict]:
    return [{"id": i, "area": "requirements", "spec_ref": "R1.1.1", "statement": f"about {i}"}
            for i in ids]


def cls(**kinds: str) -> list[dict]:
    return [{"id": i, "category": c, "why": "because"} for i, c in kinds.items()]


# ── the set is closed, and it is exactly four things ─────────────────────────


def test_exactly_four_things_block() -> None:
    """A fifth category is a deliberate change to the table, reviewed as such — never something a
    rubric edit or a well-argued observation can do at run time."""
    assert set(BLOCKING) == {
        "missing-requirement",
        "contradiction",
        "untestable-criterion",
        "unhandled-critical-edge-case",
    }
    assert CATEGORIES == (*BLOCKING, NOTE)


def test_the_prompt_and_the_table_name_the_same_categories() -> None:
    """The prompt restates the set for the model; the script enforces it. Two statements of one
    closed set drift, and the one that drifts first is the one nobody is reading."""
    prompt = TRIAGE_PROMPT.read_text(encoding="utf-8")
    for category in CATEGORIES:
        assert f"`{category}`" in prompt or category in prompt, (
            f"{category} is in the table but not in the prompt the triage model reads"
        )
    invented = [
        word.strip("`")
        for word in prompt.split()
        if word.startswith("`") and word.endswith("`") and word.strip("`").count("-") >= 1
        and word.strip("`").replace("-", "").isalpha()
        and word.strip("`").islower()
        and word.strip("`").endswith(("requirement", "contradiction", "criterion", "case"))
    ]
    assert set(invented) <= set(CATEGORIES), f"the prompt names a category the table lacks: {invented}"


# ── notes never block ────────────────────────────────────────────────────────


def test_notes_never_block_however_many_there_are() -> None:
    decision = decide(obs("o1", "o2", "o3"), cls(o1=NOTE, o2=NOTE, o3=NOTE))
    assert decision.approved
    assert len(decision.notes) == 3
    assert decision.blocking == ()


def test_one_blocker_blocks() -> None:
    decision = decide(obs("o1", "o2"), cls(o1=NOTE, o2="missing-requirement"))
    assert not decision.approved
    assert [f["id"] for f in decision.blocking] == ["o2"]


def test_nothing_observed_is_an_approval_not_a_failure_to_find_something() -> None:
    assert decide([], []).approved


def test_the_decision_carries_the_observation_it_came_from() -> None:
    """A blocker the author cannot read is a rejection with no reasoning — which is what produced
    blind rewriting in the first place."""
    (finding,) = decide(obs("o1"), cls(o1="contradiction")).blocking
    assert finding["statement"] == "about o1"
    assert finding["spec_ref"] == "R1.1.1"
    assert finding["why"] == "because"


# ── everything ambiguous fails CLOSED, never toward a verdict ────────────────


def test_an_invented_category_is_refused_rather_than_guessed() -> None:
    """Guessing 'blocking' reinstates the ratchet; guessing 'note' silently deletes a finding."""
    with pytest.raises(TriageError) as raised:
        decide(obs("o1"), [{"id": "o1", "category": "too-vague", "why": "x"}])
    assert raised.value.cause == "unknown-category"
    assert "too-vague" in str(raised.value)
    assert "closed" in str(raised.value)


def test_an_unclassified_observation_is_refused() -> None:
    with pytest.raises(TriageError) as raised:
        decide(obs("o1", "o2"), cls(o1=NOTE))
    assert raised.value.cause == "unclassified"
    assert "o2" in str(raised.value)


def test_a_classification_for_an_observation_nobody_made_is_refused() -> None:
    with pytest.raises(TriageError) as raised:
        decide(obs("o1"), cls(o1=NOTE, o9=NOTE))
    assert raised.value.cause == "unmatched-classification"


def test_a_double_classification_is_refused() -> None:
    with pytest.raises(TriageError) as raised:
        decide(obs("o1"), [{"id": "o1", "category": NOTE}, {"id": "o1", "category": "contradiction"}])
    assert raised.value.cause == "duplicate-classification"


def test_category_matching_ignores_case_and_padding() -> None:
    """A model that answers `Note ` has answered; refusing that would fail closed on a real verdict."""
    assert decide(obs("o1"), [{"id": "o1", "category": " NOTE "}]).approved


# ── the CLI contract the hook branches on ────────────────────────────────────


def _files(tmp_path: Path, observations: list[dict], classifications: list[dict]) -> tuple[str, str]:
    o = tmp_path / "obs.json"
    c = tmp_path / "cls.json"
    o.write_text(json.dumps({"observations": observations}))
    c.write_text(json.dumps({"classifications": classifications}))
    return str(o), str(c)


def test_cli_exit_codes_are_approved_blocked_error(tmp_path: Path) -> None:
    assert main(["decide", *_files(tmp_path, obs("o1"), cls(o1=NOTE))]) == 0
    assert main(["decide", *_files(tmp_path, obs("o1"), cls(o1="contradiction"))]) == 1
    assert main(["decide", *_files(tmp_path, obs("o1"), [{"id": "o1", "category": "nope"}])]) == 2


def test_a_reply_in_the_wrong_shape_is_an_error_not_an_approval(tmp_path: Path) -> None:
    """The gate must never approve a spec because the triage pass answered unreadably — that is the
    same fail-open as a killed hook reporting nothing."""
    o = tmp_path / "obs.json"
    c = tmp_path / "cls.json"
    o.write_text(json.dumps({"observations": []}))
    c.write_text("not json at all")
    assert main(["decide", str(o), str(c)]) == 2


def test_the_observe_prompt_is_never_asked_for_a_verdict() -> None:
    """The separation is the whole design: a pass that cannot block does not have to weigh whether
    something is bad enough to stop the build."""
    text = OBSERVE_PROMPT.read_text(encoding="utf-8")
    assert "You are not a gate" in text
    assert '"observations"' in text
    assert "NO-GO" not in text.split("## What you must NOT do")[1].split("## Input format")[0].replace(
        "No GO, no NO-GO", ""
    )


def test_neither_prompt_asks_for_a_bigger_spec() -> None:
    """The measured harm: the only answer a spec has to 'too vague' is more prose."""
    for prompt in (OBSERVE_PROMPT, TRIAGE_PROMPT):
        # Whitespace-normalised: these are wrapped prose files, and a line break falling inside the
        # sentence would make this pass or fail on the width of the paragraph.
        text = " ".join(prompt.read_text(encoding="utf-8").lower().split())
        assert "size is decided mechanically" in text, (
            f"{prompt.name} does not say that size is settled before it runs, so nothing stops "
            "it treating a large spec as a defect"
        )


def test_the_report_names_the_category_the_reference_and_the_statement() -> None:
    """A rejection with no reasoning is what produced blind rewriting. The first version of this
    renderer was an inline `python3 -c` whose shell quoting made it emit nothing at all."""
    from spec_gate_triage import report

    decision = decide(obs("o1", "o2"), cls(o1="contradiction", o2=NOTE))
    text = report(json.loads(decision.as_json()))
    assert "[contradiction]" in text
    assert "R1.1.1" in text
    assert "about o1" in text
    assert "1 non-blocking note(s)" in text


def test_the_report_of_an_approval_is_only_its_notes() -> None:
    from spec_gate_triage import report

    text = report(json.loads(decide(obs("o1"), cls(o1=NOTE)).as_json()))
    assert text == "(1 non-blocking note(s) recorded in spec-notes.md — they do not block.)"
