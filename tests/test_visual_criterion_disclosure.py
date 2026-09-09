"""A *Done when* condition no gate can evaluate is DECLARED, and a pass says so (issue #116).

`faithful-rep` phase 3's *Done when* is what a person sees: the captain can watch `handstand_hold`
hold, with no pike, no tumble and no feet on the mat. The verifier proved execution, ran 162 tests
and recorded 23 runs, and **nothing rendered during verification**. The verdict was `pass` - honest
about its tests, silent about its blind spot - so it read as though the criterion that actually
decides the phase had been checked. Two defects shipped through that silence: `dips` renders one bar
through the athlete's torso, `australian_pullups` renders its bar end-on at 7.7 degrees. Both were
found by a person looking, at phase close, not by a gate.

The reproduction is the first test below: the phase as it was, with a visual condition the table
does not declare and a verdict with no render step, over the mechanical check that already decides
verdicts. Before this issue it was CLEAN.

What this file does NOT establish is stated where it is checked (`scripts/guard_scope.toml`): a row
declared `gate-verifiable` is taken at its word, so a wrong declaration is invisible here.
"""

import json
import sys
from pathlib import Path

import pytest

# The check itself runs in-process. The only real processes are `git`, and they are the subject:
# the applicability boundary is a claim about what git actually reports, so a faked diff would
# test the fake. Declared per scripts/subprocess_check.py, this pipeline's only cost gate.
pytestmark = pytest.mark.subprocess(
    "the applicability boundary is a claim about what git actually reports; a faked diff tests the fake"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import done_when  # noqa: E402
import spec_gate_cache  # noqa: E402
from verifier_precheck import check_phase  # noqa: E402

#: Phase 3's three conditions, verbatim from the plan the replay transcribes. DW-1 is the visual one.
DW1 = (
    "`handstand_hold` holds for the whole clip - no pike, no tumble, no feet on the mat"
)
DW2 = "a cyclic clip loops from a real-frame cut, residual 2-9 mm, never an exact 0.0"

PLAN = """---
feature: faithful-rep
readers: x
---
# Implementation Plan: faithful-rep

### Phase 3 — real-frames
- **Done when**: **the captain can watch `handstand_hold` hold** - no pike, no tumble, no feet on
  the mat - and a cyclic clip loops from a real-frame cut.

{table}
"""

UNDECLARED = f"""  | done-when | outcome |
  |-----------|---------|
  | DW-1 | {DW1} |
  | DW-2 | {DW2} |
"""

DECLARED = f"""  | done-when | outcome | verification |
  |-----------|---------|--------------|
  | DW-1 | {DW1} | human-observed |
  | DW-2 | {DW2} | gate-verifiable |
"""

SPEC = """---
feature: faithful-rep
phase: 3-real-frames
spec: 3.2-span
spec_gate: approved
---

# Spec

## Requirements
- R3.2.2 — `binding: none` — `done_when: DW-1` — classify_motion returns hold for handstand_hold
- R3.2.4 — `binding: none` — `done_when: DW-2` — the cyclic (start, lag) pair minimising RMS

## Acceptance criteria
- R3.2.2 — passes when: …; fails when: …
"""

#: `verdict.json` as phase 3 recorded it: pass, attempt 2, 162 tests, 23 recorded runs, no render.
VERDICT = {
    "feature": "faithful-rep",
    "phase": "3-real-frames",
    "attempt": 2,
    "report": "the suite is green and the execution evidence holds",
    "tests": {"total": 162, "passed": 162, "failed": 0},
    "coverage": {"requirements": 0, "traced": 0, "untraced": []},
    "execution": {"evidence": "verification-evidence.json", "chain": "abc", "runs": 23},
    "findings": [],
    "verdict": "pass",
    "bypassed": False,
}


@pytest.fixture
def phase(tmp_path: Path) -> Path:
    """The phase as the issue found it: a visual *Done when*, and a verdict with no render step."""
    feature = tmp_path / "docs" / "features" / "faithful-rep"
    directory = feature / "phases" / "3-real-frames"
    (directory / "specs" / "3.2-span").mkdir(parents=True)
    (feature / "plan.md").write_text(PLAN.format(table=UNDECLARED), encoding="utf-8")
    spec = directory / "specs" / "3.2-span" / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    spec.write_text(
        spec_gate_cache.stamp(spec.read_text(), "gate", "APPROVED"), encoding="utf-8"
    )
    write_verdict(directory, VERDICT)
    return directory


def write_plan(phase: Path, table: str) -> None:
    (phase.parents[1] / "plan.md").write_text(
        PLAN.format(table=table), encoding="utf-8"
    )


def write_verdict(phase: Path, verdict: dict) -> None:
    (phase / "verdict.json").write_text(json.dumps(verdict), encoding="utf-8")


def disclosed(*conditions: tuple[str, str]) -> dict:
    """A verdict carrying the field the fix asks for."""
    verdict = dict(VERDICT)
    verdict[done_when.UNVERIFIED_FIELD] = [
        {"id": identifier, "outcome": outcome, "verified_by_this_verdict": False}
        for identifier, outcome in conditions
    ]
    return verdict


# --- the reproduction ---------------------------------------------------------------------------


def test_a_visual_condition_that_declares_nothing_cannot_pass_silently(
    phase: Path,
) -> None:
    """THE REPRODUCTION. Phase 3 exactly as it closed: a *Done when* whose deciding condition is
    what a person sees, a verdict that renders nothing, and a mechanical check that said CLEAN.

    Before this issue the assertion below was `== []`. The pass now has to say which conditions it
    did not check - or say, on the row, that a gate can check them."""
    findings = check_phase(phase)
    assert findings, "a Done when row that declares no verification passed unexamined"
    assert any("DW-1" in line and "verification" in line for line in findings)


# --- the declaration: one attribute, a closed vocabulary ------------------------------------------


def test_the_row_declares_which_conditions_a_gate_can_evaluate(phase: Path) -> None:
    write_plan(phase, DECLARED)
    conditions = done_when.declared_conditions(phase)
    assert [(c.id, c.verification) for c in conditions] == [
        ("DW-1", done_when.HUMAN_OBSERVED),
        ("DW-2", done_when.GATE_VERIFIABLE),
    ]
    assert [c.id for c in done_when.human_observed(phase)] == ["DW-1"]


def test_a_vocabulary_the_table_does_not_know_is_a_finding_naming_what_was_invented(
    phase: Path,
) -> None:
    """The set is closed for the reason every closed set here is: guessing 'gate-verifiable' hides
    a criterion nobody checks, and guessing 'human-observed' invents a disclosure nobody wrote."""
    write_plan(phase, DECLARED.replace("human-observed", "eyeballed"))
    findings = check_phase(phase)
    assert any("eyeballed" in line for line in findings)


def test_a_declared_column_with_an_empty_cell_is_a_finding(phase: Path) -> None:
    write_plan(phase, DECLARED.replace(" human-observed ", "  "))
    assert any("DW-1" in line for line in check_phase(phase))


def test_a_prose_only_done_when_is_left_exactly_as_115_left_it(phase: Path) -> None:
    """No table is UNDECIDABLE for a deferral and nothing here changes that: this check reads the
    table, and a phase that has none is not held to a column it never had."""
    write_plan(phase, "")
    assert check_phase(phase) == []
    assert done_when.declared_conditions(phase) is None


# --- the verdict carries what it did not check ----------------------------------------------------


def test_a_pass_that_names_each_human_observed_condition_verbatim_is_clean(
    phase: Path,
) -> None:
    write_plan(phase, DECLARED)
    write_verdict(phase, disclosed(("DW-1", DW1)))
    assert check_phase(phase) == []


def test_a_pass_that_declares_the_condition_and_then_omits_it_is_a_finding(
    phase: Path,
) -> None:
    """The declaration alone would be a promise: the verdict is where `pass` is read."""
    write_plan(phase, DECLARED)
    findings = check_phase(phase)
    assert any(
        done_when.UNVERIFIED_FIELD in line and "DW-1" in line for line in findings
    )


def test_a_paraphrase_is_not_verbatim(phase: Path) -> None:
    """Carried verbatim, because a summary is where a limitation gets softened into a caveat."""
    write_plan(phase, DECLARED)
    write_verdict(phase, disclosed(("DW-1", "the handstand looks fine")))
    assert any("verbatim" in line for line in check_phase(phase))


def test_the_entry_may_not_claim_the_verdict_verified_it(phase: Path) -> None:
    write_plan(phase, DECLARED)
    verdict = disclosed(("DW-1", DW1))
    verdict[done_when.UNVERIFIED_FIELD][0]["verified_by_this_verdict"] = True
    write_verdict(phase, verdict)
    assert any("verified_by_this_verdict" in line for line in check_phase(phase))


def test_a_failing_verdict_claims_nothing_and_is_not_held(phase: Path) -> None:
    write_plan(phase, DECLARED)
    write_verdict(phase, dict(VERDICT, verdict="fail"))
    assert check_phase(phase) == []


def test_a_gate_verifiable_row_owes_the_verdict_nothing(phase: Path) -> None:
    write_plan(phase, DECLARED.replace("human-observed", "gate-verifiable"))
    assert check_phase(phase) == []


# --- the card shows the same list -----------------------------------------------------------------


def test_the_contract_card_shows_the_same_conditions(phase: Path) -> None:
    """The card is what the next phase reads; a disclosure only in the verdict reaches nobody."""
    write_plan(phase, DECLARED)
    write_verdict(phase, disclosed(("DW-1", DW1)))
    (phase / "handover.md").write_text("# card\n- Verifier: pass\n", encoding="utf-8")
    findings = check_phase(phase)
    assert any("handover.md" in line and "DW-1" in line for line in findings)
    (phase / "handover.md").write_text(
        "# card\n- Verifier: pass\n- Human-observed (NOT verified by the verdict): DW-1\n",
        encoding="utf-8",
    )
    assert check_phase(phase) == []


# --- the applicability boundary: a plan written before this rule ----------------------------------
#
# Every check here was added after the tree it runs on, so without the boundary this one asks "does
# every plan in the repository satisfy a rule we added later?" A plan the diff does not write is a
# plan written before the column existed, and its remedy belongs to whoever next writes it.


def git_repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "base",
            "--allow-empty",
        ],
        cwd=root,
        check=True,
    )


def commit_all(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "history",
        ],
        cwd=root,
        check=True,
    )


def test_a_plan_this_change_does_not_write_is_counted_and_named_never_blocked(
    phase: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from verifier_precheck import main

    git_repo(tmp_path)
    commit_all(tmp_path)
    assert main(["--root", str(tmp_path)]) == 0
    assert main(["--all", "--root", str(tmp_path)]) == 0, (
        "a full audit would fail a consumer repo's CI over plans written before the column existed"
    )
    assert "NOT enforced" in capsys.readouterr().err


def test_a_plan_this_change_writes_is_held(phase: Path, tmp_path: Path) -> None:
    from verifier_precheck import main

    git_repo(tmp_path)
    assert main(["--root", str(tmp_path)]) == 1, (
        "the plan is uncommitted, so this change is the one writing it"
    )


def test_the_disclosure_binds_the_phase_the_handover_hook_names(phase: Path) -> None:
    """The DECLARATION follows the plan's own age; the DISCLOSURE does not. A verdict and a card
    are written by the phase that is closing, over a condition its plan already declares
    `human-observed`, and the hook names that phase with no diff to scope against."""
    from verifier_precheck import main

    write_plan(phase, DECLARED)
    assert main([str(phase)]) == 1, "the pass omits the condition no gate evaluated"
    write_verdict(phase, disclosed(("DW-1", DW1)))
    assert main([str(phase)]) == 0


def test_a_plan_the_scope_cannot_be_stated_for_holds_nothing(
    phase: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """tmp_path is no git repository, so nothing can say whether this change writes the plan.
    Falling back to enforcing is the hostage failure the whole boundary removes."""
    from verifier_precheck import main

    assert main([str(phase)]) == 0
    assert "NOT enforced" in capsys.readouterr().err
