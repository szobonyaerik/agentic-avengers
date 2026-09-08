"""Replay of `faithful-rep` phase 3's ledger against the deferral gate (issue #115).

Phase 3 ran about 48 hours because every amendment round found a real defect and nothing could say
"this finding is real AND this phase is done"; a human became the terminator, judging each remaining
item against the phase's *Done when* by hand (`close-disclosures.md` § 8). This file replays that
ledger's SHAPE - the amendment requirement sets A1..A5, the five attempt-1 findings by id and
`spec_id`, the handover's FWD-1..FWD-5 and the three Breaker observations - against the gate, with
the phase's *Done when* structured as its three prose conditions and the requirements that carry
them tagged.

The ledger is transcribed, not read from the other repository: a test must not depend on a tree it
does not own. Ids, `spec_id`s and requirement sets are the real ones; the requirement titles are
abbreviated.

What the replay establishes, and what the PR states as its hypothesis outcome: the judgement the
terminator made by hand is now mechanical, and it agrees with the ledger where the ledger recorded a
judgement. It does NOT establish that the phase would have closed after attempt 1 - two of the five
attempt-1 findings are against requirements that carry the *Done when*, and the gate refuses to defer
them, which is the property the issue names as non-negotiable.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import carried_items  # noqa: E402
import done_when  # noqa: E402
from verdict_findings import open_findings  # noqa: E402


@pytest.fixture(autouse=True)
def _metrics_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`defer`/`recarry` emit a measurement through whatever writer is on PATH. A unit test must
    never reach the operator's live firstmate store, so emission is off here; the emission itself
    is tested against the stub sink in tests/test_pipeline_metrics.py."""
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")


PLAN = """---
feature: faithful-rep
readers: x
---
# Implementation Plan: faithful-rep

### Phase 1 — catalogue-and-proof-harness
- **Done when**: prose only.

### Phase 2 — support-model
- **Done when**: prose only.

### Phase 3 — real-frames
- **Done when**: **the captain can watch `handstand_hold` hold** - the handstand stays a handstand
  for the whole clip, with no pike, no tumble and no feet on the mat - a cyclic clip loops from a
  real-frame cut with a residual in the 2-9 mm range D4 expects rather than an exact zero, and both
  one-way shapes play once and hold without ever running backwards.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | `handstand_hold` holds for the whole clip - no pike, no tumble, no feet on the mat |
  | DW-2 | a cyclic clip loops from a real-frame cut, residual 2-9 mm, never an exact 0.0 |
  | DW-3 | both one-way shapes play once and hold, never running backwards |

### Phase 4 — support-fidelity
- **Done when**: limbs sit on their bearing surfaces.

  | done-when | outcome |
  |-----------|---------|
  | DW-1 | every supporting limb sits on its bearing surface |

### Phase 5 — asymmetry
- **Done when**: prose only.

### Phase 6 — muscle-azimuth
- **Done when**: prose only.

### Phase 7 — fleet-and-captains-eye
- **Done when**: prose only.
"""

SPEC = """---
feature: faithful-rep
phase: {phase}
spec: {spec}
readers: x
---
## Requirements
{requirements}

## Acceptance criteria
Done.
"""

# Requirement declarations with the tags a spec writer would give them against the three conditions.
# Every id and binding is the real spec's; the titles are abbreviated.
SPECS = {
    "3.1-smooth": """- **R3.1.1** — `binding: e2e` — filter_capture returns filtered rotations
- **R3.1.2** — `binding: e2e` — body joints filtered at 6 Hz
- **R3.1.3** — `binding: e2e` — the finger subtree at its own cutoff
- **R3.1.4** — `binding: integration` — the finger subtree is derived from the rig
- **R3.1.5** — `binding: integration` — render_demo's import graph is scipy-free
- **R3.1.6** — `binding: integration` — smooth is the one scipy importer
""",
    "3.2-span": """- **R3.2.1** — `binding: e2e` — `done_when: DW-1, DW-3` — classify_motion: cyclic vs one-way from raw coords
- **R3.2.2** — `binding: e2e` — `done_when: DW-1` — classify_motion returns hold for handstand_hold
- **R3.2.3** — `binding: e2e` — the single clip-blind threshold classifies all ten
- **R3.2.4** — `binding: e2e` — `done_when: DW-2` — the cyclic (start, lag) pair minimising pose-space RMS
- **R3.2.5** — `binding: e2e` — `done_when: DW-1, DW-3` — the one-way span is the largest real run
- **R3.2.6** — `binding: integration` — the returned span's frames are all usable
- **R3.2.7** — `binding: none` — span imports nothing from stage 6. Enforced by: nothing
- **R3.2.8** — `binding: integration` — SpanError when no admissible pair exists
- **R3.2.9** — `binding: e2e` — classification_margin reports the deciding number
""",
    "3.3-make-rep-orchestration": """- **R3.3.1** — `binding: e2e` — `done_when: DW-1, DW-2, DW-3` — synthesise: filter, span, support, plant, report
- **R3.3.2** — `binding: e2e` — `done_when: DW-2` — a cyclic rep is exactly N whole repeats
- **R3.3.3** — `binding: e2e` — `done_when: DW-1, DW-3` — a one-way rep is the span's real frames once, forward
- **R3.3.4** — `binding: e2e` — the rep block lands in reps/<slug>.qc.json
- **R3.3.5** — `binding: integration` — the npz layout is unchanged
- **R3.3.6** — `binding: e2e` — `done_when: DW-1` — handstand_hold builds with rep.method captured
- **R3.3.7** — `binding: integration` — rejected_frames uses the capture's frame count as denominator
- **R3.3.8** — `binding: e2e` — waypoints reports 0 for the chest-tap clip
- **R3.3.9** — `binding: integration` — the fixture regeneration is amendment-shaped
- **R3.3.10** — `binding: e2e` — the classification block is additive
""",
    "3.4-fallback-and-marks": """- **R3.4.1** — `binding: e2e` — SpanError falls back to interpolation, marked
- **R3.4.2** — `binding: e2e` — the interpolated_fallback check fails on a fallback build
- **R3.4.3** — `binding: e2e` — summarise gains a method column
- **R3.4.4** — `binding: e2e` — make_rep prints the fallback line
- **R3.4.5** — `binding: e2e` — `done_when: DW-2` — loop_residual judges a cyclic clip's residual against the limit
- **R3.4.6** — `binding: integration` — `done_when: DW-2` — an exact 0.0 residual fails despite the limit
- **R3.4.7** — `binding: none` — the closed-world guard. Enforced by: prove_rep_checks
- **R3.4.8** — `binding: e2e` — the three surfaces agree on a fallback build
- **R3.4.9** — `binding: e2e` — the loop_residual why names the shape
""",
}

#: `amendments.json` A1..A5, requirement sets verbatim.
AMENDMENTS = {
    "A1": ["R3.2.5", "R3.2.8"],
    "A2": ["R3.2.1", "R3.3.1", "R3.3.10"],
    "A3": ["R3.2.4", "R3.2.8"],
    "A4": ["R3.3.4"],
    "A5": ["R3.3.3"],
}

#: `verdict-attempt-1.json` findings: id, spec_id, severity - verbatim.
ATTEMPT_1 = [
    {
        "id": "ef21ea5ddeec",
        "kind": "code",
        "spec_id": "R3.3.7",
        "severity": "major",
        "status": "open",
    },
    {
        "id": "6b7a472288af",
        "kind": "code",
        "spec_id": "R3.4.9",
        "severity": "minor",
        "status": "open",
    },
    {
        "id": "6b92d31abc84",
        "kind": "code",
        "spec_id": "R3.4.5",
        "severity": "minor",
        "status": "open",
    },
    {
        "id": "9242522a918d",
        "kind": "gamed-test",
        "spec_id": "R3.4.8",
        "severity": "minor",
        "status": "open",
    },
    {
        "id": "da2efcf8ed6b",
        "kind": "code",
        "spec_id": "R3.3.3",
        "severity": "minor",
        "status": "open",
    },
]

#: `handover.md` FWD-1..FWD-5 and OBS-1..OBS-3, with the owner § 8 named and the measurement it
#: carried. `spec` is the requirement each is nearest to, or None where § 8 named none.
CARRIED = {
    "FWD-1": (
        "6",
        None,
        "`dips` renders one bar through the torso\nspan 66 frames before and after A3, unchanged",
    ),
    "FWD-2": (
        "6",
        None,
        "`australian_pullups` renders its bar end-on\n7.7 deg between bar axis and view direction, against 33.8-34.2 on the other three bar clips",
    ),
    "FWD-3": (
        "6",
        None,
        "`loop_span` finds a period on only 3 of 7 cyclic clips\nperiod found on australian_pullups (207/69), dips (198/66), push_up_standard (165/55); whole-clip on the other four",
    ),
    "FWD-4": (
        "4",
        None,
        "`floor_authenticity` red across the one-way fleet\nhandstand_hold left palm 61.21 mm off the floor; planche_lean_hold 108.68 mm; lsit_to_handstand 19.38 mm",
    ),
    "FWD-5": (
        "1",
        None,
        "phase 1's own verdict is `fail` with amendments A8-A11 pending\n58 passed, 1 skipped, exit 0 on that suite",
    ),
    "OBS-1": (
        "6",
        "R3.4.6",
        "`loop_residual` rounds value to 2dp but decides passed unrounded\nloop_residual_check('cyclic', 1e-12) gives value 0.0, passed true",
    ),
    "OBS-2": (
        "6",
        "R3.2.4",
        "`full_range = np.ptp(signal)` not masked by usable\nan outlier on a rejected frame moves the chosen span on 6 of 7 cyclic clips",
    ),
    "OBS-3": (
        "1",
        "R1.3.4",
        "`prove_rep_checks` pins the interpolated_fallback fault to explosive_high_pullup\n4 of 10 captures raise DetectError under that fault",
    ),
}


def row(identifier: str, title: str, owner: str) -> str:
    return f"| {identifier} | deferred-finding | {title} | carried.json#deferrals (owner {owner}) |"


@pytest.fixture
def feature(tmp_path: Path) -> Path:
    root = tmp_path / "docs" / "features" / "faithful-rep"
    (root / "phases").mkdir(parents=True)
    (root / "plan.md").write_text(PLAN, encoding="utf-8")
    three = root / "phases" / "3-real-frames"
    for name, requirements in SPECS.items():
        directory = three / "specs" / name
        directory.mkdir(parents=True)
        (directory / "spec.md").write_text(
            SPEC.format(phase="3-real-frames", spec=name, requirements=requirements),
            encoding="utf-8",
        )
    (three / "verdict-attempt-1.json").write_text(
        json.dumps({"attempt": 1, "verdict": "fail", "findings": ATTEMPT_1})
    )
    return root


def phase3(feature: Path) -> Path:
    return feature / "phases" / "3-real-frames"


# --- the Done when, structured, is fully bound ------------------------------------------------------


def test_phase_3s_done_when_is_readable_and_every_condition_is_carried(
    feature: Path,
) -> None:
    conditions = done_when.declared_conditions(phase3(feature))
    assert [c.id for c in conditions] == ["DW-1", "DW-2", "DW-3"]
    bound = done_when.bound_requirements(phase3(feature))
    assert bound["DW-2"] == {"R3.2.4", "R3.3.1", "R3.3.2", "R3.4.5", "R3.4.6"}
    assert done_when.decide(phase3(feature), []).code == done_when.CLEAR


# --- the amendment loop: which of A1..A5 the gate would have let leave ----------------------------


@pytest.mark.parametrize(
    "amendment, expected",
    [
        (
            "A1",
            done_when.BLOCKS,
        ),  # the hold's span floor: the handstand fell through to interpolation
        (
            "A2",
            done_when.BLOCKS,
        ),  # the classifier read filtered: handstand_hold -> transition
        (
            "A3",
            done_when.BLOCKS,
        ),  # a cyclic span containing no movement: toes_to_bar never raised
        (
            "A4",
            done_when.CLEAR,
        ),  # persisting travel_share into the report: not what the captain watches
        (
            "A5",
            done_when.BLOCKS,
        ),  # the held tail made lsit_to_handstand render as sitting on a mat
    ],
)
def test_each_amendments_defect_is_judged_against_the_done_when_by_id(
    feature: Path, amendment: str, expected: int
) -> None:
    decision = done_when.decide(phase3(feature), AMENDMENTS[amendment])
    assert decision.code == expected, decision.reason


def test_a3_the_clip_containing_no_exercise_is_refused_deferral(feature: Path) -> None:
    """The issue's non-negotiable: phase 3's A3 caught a clip containing no exercise, and telling
    stages to care less would have shipped it."""
    with pytest.raises(carried_items.DeferralRefused, match="DW-2"):
        carried_items.defer(
            phase3(feature),
            "A3-as-finding",
            "6-muscle-azimuth",
            "toes_to_bar renders motionless\ntravel share 0.004 of the signal's range",
            spec_ids=AMENDMENTS["A3"],
        )
    assert carried_items.deferrals(phase3(feature)) == []


# --- attempt 1's five findings ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "finding_id, expected",
    [
        (
            "ef21ea5ddeec",
            done_when.CLEAR,
        ),  # R3.3.7 rejected_frames denominator, fallback path
        ("6b7a472288af", done_when.CLEAR),  # R3.4.9 the why names no shape
        (
            "6b92d31abc84",
            done_when.BLOCKS,
        ),  # R3.4.5 passes AT the 35.0 mm limit: the cyclic condition
        ("9242522a918d", done_when.CLEAR),  # R3.4.8 the third surface is not asserted
        (
            "da2efcf8ed6b",
            done_when.BLOCKS,
        ),  # R3.3.3 A5's record understates what moved: the one-way condition
    ],
)
def test_each_attempt_1_finding_is_judged_by_its_spec_id(
    feature: Path, finding_id: str, expected: int
) -> None:
    finding = next(f for f in ATTEMPT_1 if f["id"] == finding_id)
    decision = done_when.decide(phase3(feature), [finding["spec_id"]])
    assert decision.code == expected, decision.reason


def test_the_phase_would_not_have_closed_after_attempt_1_on_this_gate(
    feature: Path,
) -> None:
    """Hypothesis check, recorded rather than hidden: two of five attempt-1 findings carry the Done
    when, so the gate keeps them open. Three could have left with their measurement."""
    deferrable = [
        f["id"]
        for f in ATTEMPT_1
        if done_when.decide(phase3(feature), [f["spec_id"]]).code == done_when.CLEAR
    ]
    assert deferrable == ["ef21ea5ddeec", "6b7a472288af", "9242522a918d"]
    for finding_id in deferrable:
        carried_items.defer(
            phase3(feature),
            finding_id,
            "6-muscle-azimuth",
            f"{finding_id}\nmeasurement carried from verdict-attempt-1.json",
            finding=finding_id,
        )
    resolved = [
        dict(f, status="deferred", deferred_to="6-muscle-azimuth")
        if f["id"] in deferrable
        else f
        for f in ATTEMPT_1
    ]
    still_open = open_findings(
        resolved, carried_items.backed_deferrals(phase3(feature))
    )
    assert {f["id"] for f in still_open} == {"6b92d31abc84", "da2efcf8ed6b"}


# --- the handover's carried rows: FWD-1..FWD-5 and OBS-1..OBS-3 -------------------------------------


def test_fwd_1_to_4_are_deferrals_and_fwd_5_is_not(feature: Path) -> None:
    """§ 8 carried FWD-1..3 to phase 6 and FWD-4 to phase 4 by hand; the gate records the same
    four. FWD-5 names phase 1, which is BEHIND phase 3: not a deferral, and refused as one."""
    three = phase3(feature)
    for identifier in ("FWD-1", "FWD-2", "FWD-3", "FWD-4"):
        owner, spec, record = CARRIED[identifier]
        result = carried_items.defer(
            three, identifier, owner, record, no_requirement=True
        )
        assert result["measurement"] == record.split("\n", 1)[1]
    assert [d["owner"] for d in carried_items.deferrals(three)] == [
        "6-muscle-azimuth",
        "6-muscle-azimuth",
        "6-muscle-azimuth",
        "4-support-fidelity",
    ]
    with pytest.raises(carried_items.CarriedError, match="not later than"):
        owner, _spec, record = CARRIED["FWD-5"]
        carried_items.defer(three, "FWD-5", owner, record, no_requirement=True)


def test_the_breaker_observations_against_the_done_when_stay_with_the_phase(
    feature: Path,
) -> None:
    three = phase3(feature)
    for identifier in ("OBS-1", "OBS-2"):
        owner, spec, record = CARRIED[identifier]
        with pytest.raises(carried_items.DeferralRefused):
            carried_items.defer(three, identifier, owner, record, spec_ids=[spec])
    owner, spec, record = CARRIED["OBS-3"]
    assert (
        done_when.decide(three, [spec]).code == done_when.CLEAR
    )  # phase 1 territory, not bound
    with pytest.raises(carried_items.CarriedError, match="not later than"):
        carried_items.defer(three, "OBS-3", owner, record, spec_ids=[spec])


# --- the phase closes with its deferrals on the card, and phase 4 carries them on unchanged ---------


def test_the_close_shape_and_the_recarry_chain(feature: Path) -> None:
    three = phase3(feature)
    rows = []
    for identifier in ("FWD-1", "FWD-2", "FWD-3", "FWD-4"):
        owner, _spec, record = CARRIED[identifier]
        result = carried_items.defer(
            three, identifier, owner, record, no_requirement=True
        )
        rows.append(row(identifier, result["title"], result["owner"]))
    (three / "verdict.json").write_text(
        json.dumps(
            {
                "attempt": 2,
                "verdict": "pass",
                "findings": [dict(f, status="fixed") for f in ATTEMPT_1],
            }
        )
    )
    (three / "handover.md").write_text(
        "---\nfeature: faithful-rep\nphase: 3-real-frames\nnext: 4-support-fidelity\nreaders: x\n---\n"
        "## Phase 3-real-frames - contract card\n\n## Open items\n"
        "| id | kind | one-line title | where the detail lives |\n|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n## Next phase\n> 4-support-fidelity\n",
        encoding="utf-8",
    )
    assert carried_items.phase_problems(three) == []

    four = feature / "phases" / "4-support-fidelity"
    (four / "specs" / "4.1-a").mkdir(parents=True)
    (four / "specs" / "4.1-a" / "spec.md").write_text(
        SPEC.format(
            phase="4-support-fidelity",
            spec="4.1-a",
            requirements="- R4.1.1 — `binding: e2e` — `done_when: DW-1` — limbs on surfaces\n",
        ),
        encoding="utf-8",
    )
    owed = {item.id: item for item in carried_items.owed(four)}
    assert (
        owed["FWD-4"].owner == "4-support-fidelity"
        and owed["FWD-1"].owner == "6-muscle-azimuth"
    )

    # FWD-4 is phase 4's own; FWD-1..3 are carried on, measurement byte-for-byte.
    carried_items.discharge(four, "FWD-4", "built", by="R4.1.1 in the support spec")
    with pytest.raises(carried_items.CarriedError, match="RE-CARRIED"):
        carried_items.discharge(four, "FWD-1", "declined", reason="not ours")
    for identifier in ("FWD-1", "FWD-2", "FWD-3"):
        carried = carried_items.recarry(four, identifier)
        source = next(
            d for d in carried_items.deferrals(three) if d["id"] == identifier
        )
        assert carried["measurement"] == source["measurement"]
        assert (
            carried["origin"] == "3-real-frames"
            and carried["recarried_by"] == "4-support-fidelity"
        )
    assert carried_items.undischarged(four) == []
    # No card yet: nothing to compare the ledger against. A card that says `none` over three
    # re-carried deferrals is the disagreement the close refuses; the rows settle it.
    assert carried_items.deferral_problems(four) == []
    card = four / "handover.md"
    head = "---\nfeature: faithful-rep\nphase: 4-support-fidelity\nnext: 5-asymmetry\nreaders: x\n---\n"
    card.write_text(head + "## Open items\nnone\n", encoding="utf-8")
    problems = carried_items.deferral_problems(four)
    assert len(problems) == 3 and all(
        "no `deferred-finding` row" in p for p in problems
    )
    recarried = [
        row(d["id"], d["title"], d["owner"]) for d in carried_items.deferrals(four)
    ]
    card.write_text(
        head
        + "## Open items\n| id | kind | one-line title | where the detail lives |\n|---|---|---|---|\n"
        + "\n".join(recarried)
        + "\n",
        encoding="utf-8",
    )
    assert carried_items.phase_problems(four) == []
