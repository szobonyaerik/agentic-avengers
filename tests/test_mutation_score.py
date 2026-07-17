"""Tests for the deterministic mutation gate scorer.

These drive `verdict()` — the seam the hook actually depends on — through `Score`, the same
way `main()` does. Each case pins a fail-open that either cosmic-ray's own `cr-rate` has or
that an earlier draft of this scorer had, so a regression here means the gate silently starts
passing work it should stop.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mutation_score import (  # noqa: E402
    FAIL_CLOSED,
    GO,
    NO_GO,
    Score,
    parse_min_score,
    verdict,
)

SCORER = Path(__file__).resolve().parents[1] / "scripts" / "mutation_score.py"


def make_score(total=10, skipped=0, incompetent=0, survivors=0, tested=10) -> Score:
    """Build a Score with sensible defaults so each test states only what it cares about."""
    return Score(
        total=total,
        skipped=skipped,
        incompetent=incompetent,
        survivors=survivors,
        tested=tested,
    )


class TestScoreArithmetic:
    def test_no_survivors_is_a_perfect_score(self):
        assert make_score(survivors=0, tested=10).score == 1.0

    def test_all_survivors_is_a_zero_score(self):
        assert make_score(survivors=10, tested=10).score == 0.0

    def test_score_excludes_skipped_and_incompetent_from_the_denominator(self):
        # 10 mutants: 3 filtered out of the diff, 1 incompetent, 6 genuinely tested, 3 survived.
        score = make_score(total=10, skipped=3, incompetent=1, survivors=3, tested=6)
        assert score.score == pytest.approx(0.5)


class TestThreshold:
    def test_score_at_the_threshold_passes(self):
        code, _ = verdict(make_score(survivors=1, tested=10), 0.9)
        assert code == GO

    def test_score_below_the_threshold_routes_back(self):
        code, msg = verdict(make_score(survivors=2, tested=10), 0.9)
        assert code == NO_GO
        assert "below" in msg

    def test_perfect_threshold_is_enforced_not_ignored(self):
        """cr-rate's `--fail-over 0` is falsy and silently disables the gate. Ours must not."""
        code, _ = verdict(make_score(survivors=1, tested=10), 1.0)
        assert code == NO_GO

    def test_perfect_threshold_still_passes_a_perfect_suite(self):
        code, _ = verdict(make_score(survivors=0, tested=10), 1.0)
        assert code == GO


class TestFailsClosed:
    def test_no_mutants_generated_fails_closed(self):
        """cr-rate reports 0% survival for an empty session, i.e. a clean pass."""
        code, msg = verdict(make_score(total=0, tested=0), 0.85)
        assert code == FAIL_CLOSED
        assert "no mutants" in msg

    def test_nothing_tested_but_not_all_skipped_fails_closed(self):
        code, _ = verdict(
            make_score(total=5, skipped=2, incompetent=3, tested=0),
            0.85,
        )
        assert code == FAIL_CLOSED

    def test_everything_filtered_out_of_the_diff_is_a_pass(self):
        """A phase that changed no mutable code has nothing to answer for."""
        code, msg = verdict(make_score(total=5, skipped=5, tested=0), 0.85)
        assert code == GO
        assert "outside the diff" in msg

    @pytest.mark.parametrize("bad", ["", "abc", "-0.1", "1.1", "nan"])
    def test_invalid_thresholds_are_rejected(self, bad):
        with pytest.raises(ValueError):
            parse_min_score(bad)

    @pytest.mark.parametrize("good", ["0", "0.85", "1", "1.0"])
    def test_valid_thresholds_are_accepted(self, good):
        assert 0.0 <= parse_min_score(good) <= 1.0


class TestCli:
    def test_missing_session_exits_fail_closed(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCORER), str(tmp_path / "nope.sqlite")],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == FAIL_CLOSED

    def test_bad_threshold_exits_fail_closed(self, tmp_path):
        session = tmp_path / "s.sqlite"
        session.touch()
        proc = subprocess.run(
            [sys.executable, str(SCORER), "--min-score", "7", str(session)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == FAIL_CLOSED
        assert "min-score" in proc.stderr
