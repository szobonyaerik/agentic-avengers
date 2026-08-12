"""The verification loop is capped, and the cap is on the LOOP rather than on the phase.

Measured: 28 attempts across 8 phases, 20 of them re-attempts, and 16 of those 20 caused by a
finding the Verifier itself generated. One phase's new-finding series was 6, 2, 8, 4, 2, 1, 0, 6 —
a gate disclosing a subset of what it could already see, one expensive round at a time.

The trade is real and named: at the cap, some findings are carried rather than fixed. What must not
happen is the cap turning a phase that succeeded on its third attempt into a stop.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verifier_attempts import DEFAULT_MAX_ATTEMPTS, attempts, current, main  # noqa: E402


@pytest.fixture
def phase(tmp_path: Path) -> Path:
    directory = tmp_path / "8-clickup"
    directory.mkdir()
    return directory


def verdict(
    phase: Path, attempt: int, findings: int, result: str = "fail", status: str = "open",
    break_glass: bool = False,
) -> None:
    (phase / "verdict.json").write_text(json.dumps({
        "attempt": attempt, "verdict": result,
        "findings": [
            {"id": f"f{i}", "status": status, "break_glass": break_glass}
            for i in range(findings)
        ],
    }))


def archive(phase: Path, attempt: int, findings: int, result: str = "fail") -> None:
    (phase / f"verdict-attempt-{attempt}.json").write_text(json.dumps({
        "attempt": attempt, "verdict": result,
        "findings": [{"id": f"a{attempt}-{i}"} for i in range(findings)],
    }))


# ── the series ───────────────────────────────────────────────────────────────


def test_the_cap_is_three() -> None:
    assert DEFAULT_MAX_ATTEMPTS == 3


def test_the_series_comes_from_the_archives_plus_the_live_verdict(phase: Path) -> None:
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 8)
    assert [(a.number, a.findings, a.verdict) for a in attempts(phase)] == [
        (1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail"),
    ]
    assert current(phase) == 3


def test_a_phase_never_verified_is_attempt_zero(phase: Path) -> None:
    assert attempts(phase) == []
    assert current(phase) == 0


def test_an_unreadable_archive_is_skipped_not_fatal(phase: Path) -> None:
    (phase / "verdict-attempt-1.json").write_text("{broken")
    verdict(phase, 2, 1)
    assert [(a.number, a.findings, a.verdict) for a in attempts(phase)] == [(2, 1, "fail")]


def test_an_attempt_separates_what_it_raised_from_what_is_still_unresolved(phase: Path) -> None:
    """The series is what each attempt RAISED — a finding later fixed or waived still happened. The
    verdict can only be judged clean against what is still open and unwaived."""
    verdict(phase, 1, 3, result="pass", status="acknowledged", break_glass=True)
    latest = attempts(phase)[-1]
    assert latest.findings == 3
    assert latest.unresolved == 0


# ── the cap ──────────────────────────────────────────────────────────────────


def test_under_the_cap_is_fine(phase: Path) -> None:
    archive(phase, 1, 6)
    verdict(phase, 2, 2)
    assert main(["check", str(phase)]) == 0


def test_at_the_cap_and_still_failing_stops_the_loop(phase: Path) -> None:
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 8)
    assert main(["check", str(phase)]) == 1


def test_at_the_cap_and_clean_is_NOT_a_stop(phase: Path) -> None:
    """The cap is on the loop. Failing a phase that finished on its last allowed attempt would turn
    a success into a stop."""
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 0, result="pass")
    assert main(["check", str(phase)]) == 0


def test_a_fourth_attempt_is_refused(phase: Path) -> None:
    """The cap has to bind the attempt AFTER the last allowed one, or it bounds nothing — and
    `verification_attempts` is the metric H4 is judged on."""
    for n in (1, 2, 3):
        archive(phase, n, 4)
    verdict(phase, 4, 2)
    assert main(["check", str(phase)]) == 1


# ── the three ways a verdict can be resolved, per the schema ─────────────────
#
# skills/verifier-triage defines `pass`/`bypassed: false` as "no findings, or all findings resolved
# (fixed)" and `pass`/`bypassed: true` as "every remaining finding is acknowledged (waived)". A cap
# whose exemption reads "the findings array is empty" cannot be cleared by two of the three remedies
# its own message prescribes.


def test_at_the_cap_with_every_finding_fixed_is_resolved(phase: Path) -> None:
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 4, result="pass", status="fixed")
    assert main(["check", str(phase)]) == 0


def test_at_the_cap_with_every_finding_waived_is_resolved(phase: Path) -> None:
    """Waiving the remainder is one of the three remedies the cap's own message names. The Verifier
    records a waiver by leaving the finding in place with `break_glass`, so a check that demanded an
    empty array left CI permanently red with no action that could clear it."""
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 4, result="pass", status="acknowledged", break_glass=True)
    assert main(["check", str(phase)]) == 0


def test_a_pass_still_carrying_an_open_finding_is_not_resolved(phase: Path) -> None:
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 2, result="pass")
    assert main(["check", str(phase)]) == 1


def test_a_phase_past_the_cap_that_ended_resolved_is_not_held_hostage(phase: Path) -> None:
    """The cap stops a LOOP, and a resolved verdict has ended it. A phase cannot un-run its own
    history, so refusing it forever would be a red with no clearing action — and the measured feature
    ran eight attempts, so a repo upgrading to this version would fail CI on its own past."""
    for n in (1, 2, 3, 4, 5):
        archive(phase, n, 4)
    verdict(phase, 6, 3, result="pass", status="fixed")
    assert main(["check", str(phase)]) == 0


def test_a_failing_verdict_past_the_cap_still_stops(phase: Path) -> None:
    """Which is what actually refuses a further attempt."""
    for n in (1, 2, 3, 4):
        archive(phase, n, 4)
    verdict(phase, 5, 3)
    assert main(["check", str(phase)]) == 1


def test_the_cap_can_be_raised_for_one_call(phase: Path) -> None:
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 8)
    assert main(["check", str(phase), "--max", "5"]) == 0


def test_the_stop_message_offers_the_three_honest_ways_out(
    phase: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cap with no named remedy is just a wall. Carry, waive, or escalate — and a fourth attempt
    is not one of the three."""
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 8)
    main(["check", str(phase)])
    err = capsys.readouterr().err
    assert "KNOWN-OPEN" in err
    assert "waive" in err
    assert "escalate" in err
    assert "A fourth attempt is not one of the three" in err


def test_the_stop_message_shows_the_trickle(phase: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A drop in new findings is not convergence when the same gate later produces six more, so the
    series is printed rather than the shape being inferred from a feeling."""
    archive(phase, 1, 6)
    archive(phase, 2, 2)
    verdict(phase, 3, 8)
    main(["check", str(phase)])
    err = capsys.readouterr().err
    assert "attempt 1: 6 finding(s)" in err
    assert "attempt 2: 2 finding(s)" in err
    assert "attempt 3: 8 finding(s)" in err


def test_series_prints_without_judging(phase: Path, capsys: pytest.CaptureFixture[str]) -> None:
    archive(phase, 1, 6)
    verdict(phase, 2, 0, result="pass")
    assert main(["series", str(phase)]) == 0
    assert "attempt 2: 0 finding(s), verdict pass" in capsys.readouterr().out


def test_a_missing_phase_directory_is_an_error(tmp_path: Path) -> None:
    assert main(["check", str(tmp_path / "nope")]) == 2
