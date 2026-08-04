"""Tests for the verifier-review substance check.

The Verifier's cross-family review is the pipeline's independence mechanism, and its failure mode is
the worst one available: a verdict that says GO without the model having actually read the review
set. `verifier_review.sh` used to hand a truncated bundle to the model with a note *asking* it to
report the review as partial — an instruction, not an assertion — and then returned the model's
verdict as the exit code. A truncated review could therefore pass a phase silently.

So these pin the opposite bias to most gates: when in doubt, REFUSE. A false refusal costs a re-run;
a false pass ships an unverified phase and every later phase builds on it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from verifier_review_check import (  # noqa: E402
    SubstanceError,
    assert_substance,
)


def verdict(**over):
    base = {
        "verdict": "GO",
        "report": "Reviewed test_auth.py and test_login.py; no tautological assertions found.",
        "findings": [],
    }
    base.update(over)
    return base


REVIEW_SET = ["tests/feat/1-a/test_auth.py", "tests/feat/1-a/test_login.py"]


# ── the happy path still passes ──────────────────────────────────────────────


def test_a_substantive_report_passes() -> None:
    assert_substance(verdict(), REVIEW_SET)


def test_findings_that_name_files_pass_even_with_a_terse_report() -> None:
    v = verdict(
        report="See findings.",
        findings=[{"kind": "wrong test", "target": "tests/feat/1-a/test_auth.py"}],
    )
    assert_substance(v, REVIEW_SET)


# ── empty / generic reports are refused ──────────────────────────────────────


@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
def test_empty_report_is_refused(empty: str) -> None:
    with pytest.raises(SubstanceError):
        assert_substance(verdict(report=empty), REVIEW_SET)


def test_missing_report_key_is_refused() -> None:
    v = verdict()
    del v["report"]
    with pytest.raises(SubstanceError):
        assert_substance(v, REVIEW_SET)


def test_report_naming_no_review_set_file_is_refused() -> None:
    """A verdict that could have been written without opening the bundle."""
    v = verdict(report="The tests look reasonable and follow good practice. No issues found.")
    with pytest.raises(SubstanceError):
        assert_substance(v, REVIEW_SET)


def test_report_naming_a_file_not_in_the_review_set_is_refused() -> None:
    v = verdict(report="Reviewed test_something_else.py thoroughly.")
    with pytest.raises(SubstanceError):
        assert_substance(v, REVIEW_SET)


# ── the truncation self-report must be honoured, not ignored ─────────────────


@pytest.mark.parametrize(
    "report",
    [
        "The review was partial; I did not see the whole review set. test_auth.py looked fine.",
        "TRUNCATED bundle — judging only what was shown of test_auth.py.",
        "I have not seen the whole review set. test_auth.py is fine.",
    ],
)
def test_a_self_reported_partial_review_is_refused(report: str) -> None:
    """The old code asked the model to say this, then passed the phase anyway."""
    with pytest.raises(SubstanceError):
        assert_substance(verdict(report=report), REVIEW_SET)


def test_coverage_gap_prose_in_a_finding_is_not_a_partial_self_report() -> None:
    """Category 3/4 findings are about coverage, so 'only part of' is ordinary vocabulary there.
    Matching it deleted a legitimate NO-GO and discarded its findings."""
    v = verdict(
        verdict="NO-GO",
        report="Reviewed tests/feat/1-a/test_auth.py against R1.2.4.",
        findings=[
            {
                "kind": "coverage-gap",
                "target": "tests/feat/1-a/test_auth.py",
                "instruction": "test_parse covers only part of R1.2.4's criteria; add the "
                "expired-token case, which no test exercises.",
            }
        ],
    )
    assert_substance(v, REVIEW_SET)


def test_a_report_denying_truncation_is_not_a_partial_self_report() -> None:
    """A rubric-following model asserting completeness must not be read as claiming the opposite."""
    v = verdict(report="The bundle was not truncated; test_auth.py and test_login.py both read in full.")
    assert_substance(v, REVIEW_SET)


# ── the accepted verdict tokens are gate_runner's, not a second set ──────────


def test_pass_is_accepted_because_gate_runner_treats_it_as_a_pass() -> None:
    """gate_runner exits 0 on PASS; rejecting it here would delete a verdict already paid for."""
    assert_substance(verdict(verdict="PASS"), REVIEW_SET)


def test_review_is_refused_because_the_rubric_never_emits_it() -> None:
    with pytest.raises(SubstanceError):
        assert_substance(verdict(verdict="REVIEW"), REVIEW_SET)


# ── shape errors fail closed rather than passing through ─────────────────────


def test_missing_verdict_key_is_refused() -> None:
    v = verdict()
    del v["verdict"]
    with pytest.raises(SubstanceError):
        assert_substance(v, REVIEW_SET)


@pytest.mark.parametrize("bad", ["", "MAYBE", "ok", None])
def test_unknown_verdict_value_is_refused(bad) -> None:
    with pytest.raises(SubstanceError):
        assert_substance(verdict(verdict=bad), REVIEW_SET)


def test_an_empty_review_set_is_refused() -> None:
    """`verifier_review.sh` already refuses zero files; the check must not disagree."""
    with pytest.raises(SubstanceError):
        assert_substance(verdict(), [])


def test_a_no_go_verdict_still_needs_substance() -> None:
    """A NO-GO routes work back, so a hallucinated one is expensive too."""
    with pytest.raises(SubstanceError):
        assert_substance(verdict(verdict="NO-GO", report="Bad."), REVIEW_SET)


def test_findings_referencing_the_review_set_satisfy_a_no_go() -> None:
    v = verdict(
        verdict="NO-GO",
        report="Two gamed tests.",
        findings=[{"kind": "wrong test", "target": "tests/feat/1-a/test_login.py"}],
    )
    assert_substance(v, REVIEW_SET)


def test_cli_exits_nonzero_on_a_hollow_verdict(tmp_path: Path) -> None:
    """The shell script consumes this through the CLI, so the exit code is the contract."""
    import subprocess

    vfile = tmp_path / "v.json"
    vfile.write_text(json.dumps(verdict(report="Looks fine.")), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "verifier_review_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(vfile), *REVIEW_SET],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert proc.stderr.strip()


# ── the checks assert the rubric's contract, not an invented one ─────────────

RUBRIC = (Path(__file__).resolve().parents[1] / "prompts" / "verifier-review.md").read_text()


def test_the_rubric_requires_the_report_to_name_every_file_reviewed() -> None:
    """The naming refusal is only legitimate if the model was told to name what it reviewed."""
    assert "name every file" in RUBRIC.lower()


def test_the_rubric_does_not_prime_the_truncation_language() -> None:
    """The bundle can never reach the model truncated, and that dead instruction primed the exact
    self-report the partial check refuses on."""
    assert "treat the review as partial" not in RUBRIC


def test_cli_exits_zero_on_a_substantive_verdict(tmp_path: Path) -> None:
    import subprocess

    vfile = tmp_path / "v.json"
    vfile.write_text(json.dumps(verdict()), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "verifier_review_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(vfile), *REVIEW_SET],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
