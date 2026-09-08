"""A partial measurement written up as a complete result (issue #96, and #117 folded into it).

Seven instances in one feature, four in another, and every one produced work that LOOKED finished:
a sweep done by reading, a probe that drove one axis of two, a spec asserting a method "only calls
X, Y and float()" that nobody measured, an amendment claiming "nothing in the catalogue" over a
four-field diff. Five of the seven were caught by a human reading artifacts against source, one by a
gate, none by a test.

What is pinned here is the rule in its mechanical form: an acceptance criterion names what it
DRIVES, a verifier finding names its METHOD, and a claim that quantifies universally carries the set
it was MEASURED OVER as a field. The vocabulary of universals is closed and pinned, because a
detector that grows by accretion is one nobody can predict.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from measurement_claims import (  # noqa: E402
    CLEAN,
    DRIVES,
    ERROR,
    FINDINGS,
    MEASURED_OVER,
    METHOD,
    UNIVERSALS,
    claim_problem,
    criteria_problems,
    finding_problems,
    main,
    universals,
)

SPEC = """---
feature: demo
phase: 1-core
spec: 1.1-a
status: {status}
spec_gate: {gate}
---

# Spec

## Requirements
- R1.1.1 — `binding: integration` — the poller retries once. Why an e2e cannot see it: fault injection.
- R1.1.2 — `binding: e2e` — a user sees the order.

## Journeys
- J1 — place an order — covers R1.1.2

## Acceptance criteria
{criteria}

## Interfaces / contracts
"""

DRIVEN = (
    "- R1.1.1 — passes when: the second attempt succeeds; fails when: both fail — "
    "drives: `Poller.run_once()` with a collaborator that raises on the first call\n"
    "- J1 — passes when: the order is listed; fails when: it is not — "
    "drives: POST /orders then GET /orders through the test client"
)

UNDRIVEN = (
    "- R1.1.1 — passes when: the second attempt succeeds; fails when: both fail\n"
    "- J1 — passes when: the order is listed; fails when: it is not — "
    "drives: POST /orders then GET /orders through the test client"
)


def spec_text(
    criteria: str = DRIVEN, status: str = "draft", gate: str = "pending"
) -> str:
    return SPEC.format(criteria=criteria, status=status, gate=gate)


# ── the closed vocabulary ────────────────────────────────────────────────────


def test_the_universal_vocabulary_is_closed_and_pinned() -> None:
    """Every word here is one that appeared in a measured false claim, or is its plain synonym.

    A7 said "nothing in the catalogue"; A5 said "every other clip ... bit-for-bit identical"; A6 said
    "subsumed"; the phase-3 card said "every limb"; the spec said "only calls X, Y and float()". A
    detector that matched more would refuse ordinary prose and be answered with a bypass.
    """
    assert set(UNIVERSALS) == {
        "all",
        "every",
        "everything",
        "nothing",
        "none",
        "no other",
        "never",
        "always",
        "only",
        "entire",
        "identical",
        "unchanged",
        "byte-for-byte",
        "bit-for-bit",
        "subsumed",
    }


@pytest.mark.parametrize(
    "text, expected",
    [
        ("nothing in the catalogue can turn waypoints red", ["nothing"]),
        (
            "left every other clip's report bit-for-bit identical",
            ["every", "bit-for-bit", "identical"],
        ),
        (
            "the byte-for-byte test is subsumed, not abandoned",
            ["byte-for-byte", "subsumed"],
        ),
        ("every limb the model calls support is now on its bearing surface", ["every"]),
        ("the method only calls X, Y and float()", ["only"]),
        ("no other stage reads the frame count", ["no other"]),
    ],
)
def test_the_measured_false_claims_are_all_detected(
    text: str, expected: list[str]
) -> None:
    assert universals(text) == expected


def test_a_word_inside_a_longer_word_is_not_a_quantifier() -> None:
    """`call`, `small`, `install`, `nonetheless` and `everyday` carry no universal claim."""
    assert universals("we call install on a small nonetheless everyday path") == []


def test_inline_code_is_not_prose() -> None:
    """`None`, `all()` and `only` as identifiers in backticks are code, not a claim about the world."""
    assert universals("returns `None` when `all(rows)` is false; see `only_once`") == []
    assert universals("returns `None` and the whole thing is unchanged") == [
        "unchanged"
    ]


def test_detection_ignores_case_and_reports_each_word_once() -> None:
    assert universals("ALL of them, all of them, Every one") == ["all", "every"]


# ── a scope claim carries its set ────────────────────────────────────────────


def test_a_universal_claim_with_no_measured_set_is_refused_naming_the_words() -> None:
    problem = claim_problem("nothing in the catalogue can turn waypoints red", None)
    assert problem is not None
    assert "nothing" in problem
    assert MEASURED_OVER in problem


def test_a_universal_claim_with_its_set_is_clean() -> None:
    assert (
        claim_problem(
            "nothing in the catalogue turns red", ["capture-01", "capture-02"]
        )
        is None
    )


def test_a_claim_with_no_universal_needs_no_set() -> None:
    assert claim_problem("the scrub was defeated by JSON escaping", None) is None


def test_a_blank_set_is_no_set() -> None:
    """`--measured-over ""` is not a measurement; neither is a list of empty strings."""
    assert claim_problem("all ten clips render", []) is not None
    assert claim_problem("all ten clips render", ["", "  "]) is not None
    assert claim_problem("all ten clips render", "   ") is not None


def test_the_set_is_a_field_so_a_string_and_a_list_both_count() -> None:
    assert claim_problem("all ten clips render", "the ten committed captures") is None


# ── an acceptance criterion names what it drives ─────────────────────────────


def test_a_criterion_that_names_what_it_drives_is_clean() -> None:
    assert criteria_problems(spec_text(DRIVEN)) == []


def test_a_criterion_with_no_drives_field_is_a_finding_naming_the_criterion() -> None:
    problems = criteria_problems(spec_text(UNDRIVEN))
    assert len(problems) == 1
    assert "R1.1.1" in problems[0]
    assert DRIVES in problems[0]


def test_a_sweep_criterion_is_held_to_the_same_field() -> None:
    """ "Sweep both adapters for this class" is satisfied by reading. `drives:` is what cannot be."""
    sweep = "- R1.1.1 — passes when: both adapters are swept for the narrowed-except class; fails when: one is not"
    problems = criteria_problems(spec_text(sweep))
    assert len(problems) == 1 and "R1.1.1" in problems[0]


def test_the_field_may_sit_on_a_continuation_line() -> None:
    criteria = (
        "- R1.1.1 — passes when: the second attempt succeeds; fails when: both fail\n"
        "  drives: `Poller.run_once()` against a collaborator that raises once"
    )
    assert criteria_problems(spec_text(criteria)) == []


def test_a_criterion_may_declare_itself_undriven_and_is_not_refused_here() -> None:
    """The door issue #116 needs stays open: a visual criterion can say it is undriven.

    This check asks that the field EXISTS; what an `undriven` value must carry is that issue's
    decision, not this one's, so the value is not judged.
    """
    criteria = "- J1 — passes when: the bar is visible on screen; fails when: it is not — drives: undriven (visual)"
    assert criteria_problems(spec_text(criteria)) == []


def test_a_spec_with_no_acceptance_section_is_not_this_checks_finding() -> None:
    """verifier_precheck already refuses a spec with no `## Acceptance criteria` heading."""
    text = spec_text(DRIVEN).replace("## Acceptance criteria\n", "## Something else\n")
    assert criteria_problems(text) == []


def test_only_criterion_items_are_read_not_prose_under_the_heading() -> None:
    criteria = (
        "A sentence introducing the criteria.\n"
        "- R1.1.1 — passes when: it retries; fails when: it does not — drives: `Poller.run_once()`\n"
        "- J1 — passes when: listed; fails when: not — drives: the HTTP seam"
    )
    assert criteria_problems(spec_text(criteria)) == []


# ── a verifier finding names its method ──────────────────────────────────────


def finding(**overrides) -> dict:
    base = {
        "id": "abc123def456",
        "kind": "code",
        "spec_id": "R1.1.1",
        "target": "src/poller.py",
        "severity": "blocker",
        "instruction": "sweep both adapters for the narrowed except clause",
        "route_to": "avenger-backend-architect",
        "status": "open",
        METHOD: "drove every public method of both adapters with a raising collaborator and compared outcomes",
    }
    return {**base, **overrides}


def test_a_finding_with_a_method_is_clean() -> None:
    assert finding_problems({"findings": [finding()]}) == []


def test_a_finding_with_no_method_is_a_finding_naming_the_finding() -> None:
    problems = finding_problems({"findings": [finding(**{METHOD: None})]})
    assert len(problems) == 1
    assert "abc123def456" in problems[0] and METHOD in problems[0]
    absent = {k: v for k, v in finding().items() if k != METHOD}
    assert len(finding_problems({"findings": [absent]})) == 1


def test_a_blank_method_is_no_method() -> None:
    assert len(finding_problems({"findings": [finding(**{METHOD: "   "})]})) == 1


def test_a_fixed_or_waived_finding_is_held_too() -> None:
    """The rule is about the record, not the routing: a fixed finding still says how it was checked."""
    assert (
        len(
            finding_problems({"findings": [finding(**{METHOD: "", "status": "fixed"})]})
        )
        == 1
    )


def test_a_verdict_with_no_findings_is_clean() -> None:
    assert finding_problems({"findings": []}) == []
    assert finding_problems({}) == []


# ── the CLI ──────────────────────────────────────────────────────────────────


def write_spec(tmp_path: Path, text: str) -> Path:
    spec = (
        tmp_path
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / "1-core"
        / "specs"
        / "1.1-a"
        / "spec.md"
    )
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(text, encoding="utf-8")
    return spec


def test_cli_spec_is_clean_findings_error(tmp_path: Path, capsys) -> None:
    assert main(["spec", str(write_spec(tmp_path, spec_text(DRIVEN)))]) == CLEAN
    assert main(["spec", str(write_spec(tmp_path, spec_text(UNDRIVEN)))]) == FINDINGS
    assert "R1.1.1" in capsys.readouterr().err
    assert main(["spec", str(tmp_path / "missing.md")]) == ERROR


@pytest.mark.parametrize(
    "status, gate",
    [("done", "approved"), ("in-progress", "approved"), ("draft", "approved")],
)
def test_a_spec_the_pipeline_is_past_is_counted_and_named_never_blocked(
    tmp_path: Path, capsys, status: str, gate: str
) -> None:
    """The applicability boundary (CLAUDE.md 3a): a spec already approved or implemented has shipped
    its criteria, and the remedy - rewrite them - is not available to a stage that has ended. It is
    counted on stderr, in the boundary's one spelling, and never blocked."""
    spec = write_spec(tmp_path, spec_text(UNDRIVEN, status=status, gate=gate))
    assert main(["spec", str(spec)]) == CLEAN
    err = capsys.readouterr().err
    assert "measurement_claims" in err and "R1.1.1" in err


def test_a_blocked_spec_is_still_going_through_the_gate_and_is_held(
    tmp_path: Path,
) -> None:
    spec = write_spec(tmp_path, spec_text(UNDRIVEN, status="draft", gate="blocked"))
    assert main(["spec", str(spec)]) == FINDINGS


def test_cli_verdict_reads_the_phase_directory(tmp_path: Path, capsys) -> None:
    phase = tmp_path / "1-core"
    phase.mkdir()
    assert main(["verdict", str(phase)]) == CLEAN, "no verdict yet is nothing to check"
    (phase / "verdict.json").write_text(
        json.dumps({"verdict": "fail", "findings": [finding()]})
    )
    assert main(["verdict", str(phase)]) == CLEAN
    (phase / "verdict.json").write_text(
        json.dumps(
            {"verdict": "fail", "findings": [{"id": "deadbeef0000", "status": "open"}]}
        )
    )
    assert main(["verdict", str(phase)]) == FINDINGS
    assert "deadbeef0000" in capsys.readouterr().err
    (phase / "verdict.json").write_text("{not json")
    assert main(["verdict", str(phase)]) == ERROR


def test_usage_is_an_error_not_a_clean_result() -> None:
    assert main([]) == ERROR
    assert main(["nonsense"]) == ERROR
