"""The handover gate, and the one thing in it that used to report a limit while enforcing none.

`scripts/verifier_attempts.py` decides the 3-attempt cap, and its own tests cover that decision. What
is pinned HERE is that the decision is acted on: the hook called it as `... || true`, printed the cap
notice, and then routed the phase back anyway, so a fourth attempt proceeded with nothing stopping
it — while `CLAUDE.md` and `skills/pipeline-conventions` both say it "stops the loop", and H4 is
measured on `verification_attempts`.

A cap whose test passes against the un-capped code is not a cap, so these drive the real hook.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PASSING = {
    "verdict": "pass",
    "attempt": 3,
    "findings": [],
    "test_quality": {"reviewed": True, "scope": {"test_files": ["tests/demo/1-a/test_x.py"]}},
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "docs" / "features" / "demo" / "phases" / "1-demo").mkdir(parents=True)
    return tmp_path


def phase_dir(project: Path) -> Path:
    return project / "docs" / "features" / "demo" / "phases" / "1-demo"


def write_spec(project: Path, spec: str = "1.1-a", *, criticality: str = "standard") -> None:
    """A spec that also satisfies `verifier_precheck.py` (Acceptance criteria heading, a fresh gate
    stamp, no untraced requirement ids) — that check is not what these tests are about, so it is
    driven clean the same way `spec_gate_cache.py` itself would leave a spec after an approval."""
    spec_dir = phase_dir(project) / "specs" / spec
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / "spec.md"
    path.write_text(
        f"---\nfeature: demo\nphase: 1-demo\nspec: {spec}\ncriticality: {criticality}\n---\n\n"
        f"# Spec\n\n## Acceptance criteria\n\nDone.\n"
    )
    subprocess.run(
        [sys.executable, str(project / "scripts" / "spec_gate_cache.py"), "stamp", str(path),
         "gate", "APPROVED"],
        check=True, capture_output=True,
    )


def attempts(project: Path, series: list[tuple[int, int, str]]) -> None:
    """Write the phase's verdict history: the archives, then the live verdict as the last entry."""
    for number, findings, result in series[:-1]:
        (phase_dir(project) / f"verdict-attempt-{number}.json").write_text(json.dumps({
            "attempt": number, "verdict": result,
            "findings": [{"id": f"a{number}-{i}"} for i in range(findings)],
        }))
    number, findings, result = series[-1]
    verdict = {
        "attempt": number, "verdict": result,
        "findings": [{"id": f"f{i}", "status": "open"} for i in range(findings)],
        "routed": [{"to": "implementer", "reason": "code issue", "finding_id": "f0"}],
    }
    if result == "pass":
        verdict["findings"] = []
        verdict["test_quality"] = PASSING["test_quality"]
    (phase_dir(project) / "verdict.json").write_text(json.dumps(verdict))


def run_hook(project: Path, card: str = "# handover\n\n## Open items\nnone\n", **env: str
             ) -> subprocess.CompletedProcess:
    handover = phase_dir(project) / "handover.md"
    # A closing phase states what it carries forward, so the card these tests write says `none`
    # explicitly. `tests/test_carried_items.py` owns that rule; here it is only enough card for the
    # hook to reach the attempt cap, which is what this file pins.
    handover.write_text(card)
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_verifier.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % handover,
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(project),
             "CLAUDE_PROJECT_DIR": str(project), **env},
    )


# ── the cap binds, rather than being announced ───────────────────────────────


def test_a_fourth_attempt_is_refused_at_handover(project: Path) -> None:
    """The failure this replaced: the notice printed, then `fail` routed back regardless, and
    attempt 5 followed. Enforcement was an instruction to the model — the exact class this branch
    replaces everywhere else."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail"), (4, 3, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "a further attempt is" in result.stderr and "refused" in result.stderr
    assert "route back per its findings" not in result.stderr, (
        "at the cap the phase does not go round again; it is carried, waived or escalated"
    )


def test_the_cap_names_the_three_honest_ways_out(project: Path) -> None:
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])

    err = run_hook(project).stderr

    assert "KNOWN-OPEN" in err
    assert "waive" in err
    assert "escalate" in err


def test_under_the_cap_the_phase_still_routes_back_normally(project: Path) -> None:
    """The cap must not swallow the ordinary route-back it sits in front of."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "route back per its findings" in result.stderr
    assert "refused" not in result.stderr


def test_a_phase_that_passes_cleanly_on_its_last_allowed_attempt_still_passes(project: Path) -> None:
    """The cap is on the LOOP, not on the phase. Turning a successful third attempt into a stop
    would be weakening the gate in the other direction."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 0, "pass")])

    result = run_hook(project)

    assert result.returncode == 0, result.stderr


def test_a_record_the_cap_cannot_read_stops_without_claiming_to_be_the_cap(project: Path) -> None:
    """`CAPPED` is exit 1 and so is an uncaught exception, so a malformed `attempt` arrived here as a
    cap: the handover was refused with "carry, waive or escalate" for a phase that might be on
    attempt 1, whose real problem is a file none of those three remedies can repair."""
    (phase_dir(project) / "verdict.json").write_text(json.dumps({
        "attempt": "N/A", "verdict": "fail", "findings": [{"id": "f0", "status": "open"}],
        "routed": [{"to": "implementer", "reason": "code issue", "finding_id": "f0"}],
    }))

    result = run_hook(project)

    assert result.returncode == 2
    assert "could not be DECIDED" in result.stderr
    assert "not the cap itself" in result.stderr
    assert "A fourth attempt is not one of the three" not in result.stderr
    assert "KNOWN-OPEN" not in result.stderr


def test_the_cap_is_escapable_and_audited_rather_than_a_hard_wedge(project: Path) -> None:
    """Consistent with every other blocking check here: break-glass through the same `fail()` path,
    logged and visible, never silent."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])

    result = run_hook(project, GATE_BYPASS="captain accepts the remaining findings as known-open")

    assert result.returncode == 0, result.stderr
    assert (project / "gate-overrides.log").is_file()


# ── a passing phase does not close over what the previous one carried ────────
#
# The other thing this hook now refuses. Phase 8 of one measured feature wrote down that
# caller-supplied identifiers would become a problem in phases 9 to 12; phase 9 was the first such
# caller and shipped exactly that defect, past every gate, because the prediction was prose.
# `tests/test_carried_items.py` owns the rule; what is pinned here is that the hook acts on it, and
# that it does so WITHOUT masking the stop that would otherwise have fired.


def prior_card(project: Path, items: str) -> None:
    """A phase 0 whose card carries `items`, so 1-demo owes it an answer."""
    directory = project / "docs" / "features" / "demo" / "phases" / "0-prior"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "handover.md").write_text(
        f"---\nreaders: x\n---\n# card\n\n## Open items\n{items}\n", encoding="utf-8"
    )


CARRIED = (
    "| id | kind | title | where |\n|---|---|---|---|\n"
    "| FWD-1 | forward-claim | caller-supplied ids reach the path unencoded | this card |"
)


def test_a_passing_phase_does_not_close_over_an_undischarged_carried_item(project: Path) -> None:
    attempts(project, [(1, 0, "pass")])
    prior_card(project, CARRIED)

    result = run_hook(project)

    assert result.returncode == 2
    assert "FWD-1" in result.stderr
    assert "no answer in this phase" in result.stderr


def test_discharging_the_item_lets_the_phase_close(project: Path) -> None:
    attempts(project, [(1, 0, "pass")])
    prior_card(project, CARRIED)
    subprocess.run(
        [sys.executable, str(project / "scripts" / "carried_items.py"), "discharge",
         str(phase_dir(project)), "FWD-1", "--as", "built", "--by", "R1.1.3"],
        check=True, capture_output=True,
    )

    assert run_hook(project).returncode == 0


def test_a_card_that_says_nothing_about_what_it_carries_does_not_close(project: Path) -> None:
    """Silence is not `none`. A prediction left out of the table is owed to nobody, which is the
    state phase 8's card was in."""
    attempts(project, [(1, 0, "pass")])

    result = run_hook(project, card="# handover\n\nno items section here\n")

    assert result.returncode == 2
    assert "does not say what it carries forward" in result.stderr


LAST_CARD = (
    "---\nnext: e2e\nreaders: x\n---\n# handover\n\n## Open items\n"
    "| id | kind | title | where |\n|---|---|---|---|\n"
    "| FWD-1 | forward-claim | caller-supplied ids reach the path unencoded | %s |\n"
)


def test_the_last_phase_does_not_close_over_a_forward_claim_owed_to_nobody(project: Path) -> None:
    """`next: e2e` means no phase follows to answer this row, so the claim has to be filed. Asserted
    through the real hook: a rule only its unit test knows about is the promise-with-no-mechanism
    this whole change removes."""
    attempts(project, [(1, 0, "pass")])

    result = run_hook(project, card=LAST_CARD % "this card")

    assert result.returncode == 2
    assert "FWD-1" in result.stderr
    assert "issue reference" in result.stderr


def test_naming_an_issue_on_that_row_lets_the_last_phase_close(project: Path) -> None:
    attempts(project, [(1, 0, "pass")])

    assert run_hook(project, card=LAST_CARD % "filed as #41").returncode == 0


def test_the_carried_gate_never_masks_the_stop_that_would_have_fired(project: Path) -> None:
    """It runs inside the `pass` branch on purpose. A phase at the attempt cap is already not
    closing, and answering an unasked question there would hide the one that stopped it."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])
    prior_card(project, CARRIED)

    result = run_hook(project, card="# handover\n\nno items section here\n")

    assert "refused" in result.stderr
    assert "carries forward" not in result.stderr


# ── the Breaker obligation (issue #45) ────────────────────────────────────────────────────────────
#
# All four phase-8 specs and all four phase-9 specs of one measured feature declared
# `criticality: critical`, which is what routes the Breaker — and it was owed twice and ran neither
# time, with zero trace of it anywhere in the feature's docs or tests. This drives the REAL hook, the
# same discipline the attempt cap above holds itself to: a rule whose test passes against the
# un-enforced hook is not a rule.


def test_a_critical_phase_does_not_close_without_a_breaker_record(project: Path) -> None:
    """The gap the issue describes, reproduced directly against the hook that is supposed to stop
    it. Without the fix this phase closes silently — exactly what happened twice already."""
    write_spec(project, criticality="critical")
    attempts(project, [(1, 0, "pass")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "Breaker obligation is not met" in result.stderr
    assert "breaker.json" in result.stderr


def test_a_valid_breaker_record_lets_a_critical_phase_close(project: Path) -> None:
    write_spec(project, criticality="critical")
    attempts(project, [(1, 0, "pass")])
    (phase_dir(project) / "breaker.json").write_text(
        json.dumps({"verdict": "clean", "attacked": ["replay", "auth bypass"]})
    )

    result = run_hook(project)

    assert result.returncode == 0, result.stderr


def test_a_standard_phase_closes_with_no_breaker_record(project: Path) -> None:
    write_spec(project, criticality="standard")
    attempts(project, [(1, 0, "pass")])

    assert run_hook(project).returncode == 0


def test_the_breaker_gate_never_masks_a_failed_suite(project: Path) -> None:
    """Runs inside the `pass` branch on purpose, same as the carried-items gate: a red suite must
    stop the phase for its own reason, not a Breaker record it never got to check."""
    write_spec(project, criticality="critical")
    attempts(project, [(1, 6, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "route back per its findings" in result.stderr
    assert "Breaker obligation" not in result.stderr
