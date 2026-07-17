#!/usr/bin/env python3
"""Deterministic mutation scorer for the per-phase mutation gate.

Why this exists instead of `cr-rate --fail-over`: cosmic-ray's own survival-rate tool
fails OPEN in three ways, each of which would let the gate pass vacuously.

1. Skipped mutants count as kills. `WorkResult.is_killed` is `test_outcome != SURVIVED`
   (cosmic_ray/work_item.py), and a filtered-out job is stored with `test_outcome=None`.
   `None != SURVIVED` is True, so every mutant `cr-filter-git` skips inflates the score.
   A fully-filtered session scores a perfect 1.0.
2. An empty session scores 0% survival (`survival_rate()` returns 0 when there are no
   results), so a broken `module-path` that generates no mutants reports a clean run.
3. `--fail-over 0` is ignored: the check is `if fail_over and min_rate > fail_over`, and
   0 is falsy — so demanding a perfect score silently disables the gate.

This scorer counts only mutants that were actually built and tested, and fails closed
(exit 2) whenever it cannot reach an honest verdict. Per pipeline convention, a gate that
cannot reach a verdict stops; it does not pass.

Exit codes:
    0  GO      — score >= --min-score, or every mutant fell outside the diff.
    1  NO-GO   — survivors pushed the score below --min-score. Caller reports and routes back.
    2  ERROR   — cannot score (no mutants, unreadable session, bad threshold). Fail closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from cosmic_ray.work_db import WorkDB, use_db
    from cosmic_ray.work_item import TestOutcome, WorkerOutcome
except ImportError as exc:  # cosmic-ray absent -> the gate cannot run -> fail closed
    print(f"[mutation_score] cosmic-ray is not importable: {exc}", file=sys.stderr)
    sys.exit(2)

GO = 0
NO_GO = 1
FAIL_CLOSED = 2


@dataclass(frozen=True)
class Score:
    """Outcome of scoring one cosmic-ray session.

    `tested` is the denominator: mutants that were actually built and run. Skipped
    (filtered) and incompetent mutants are excluded from it — neither is evidence about
    the test suite.
    """

    total: int
    skipped: int
    incompetent: int
    survivors: int
    tested: int

    @property
    def score(self) -> float:
        """Mutation score in [0, 1]: the fraction of tested mutants the suite killed."""
        if self.tested == 0:
            return 0.0
        return 1.0 - (self.survivors / self.tested)

    def as_dict(self) -> dict[str, float | int]:
        """JSON-serializable summary, for the gate report and for tests."""
        return {
            "total": self.total,
            "skipped": self.skipped,
            "incompetent": self.incompetent,
            "survivors": self.survivors,
            "tested": self.tested,
            "score": round(self.score, 4),
        }


def score_session(db: WorkDB) -> Score:
    """Count mutant outcomes in an opened session.

    Classifies each stored result by its worker outcome first (a skipped job never ran, so
    its test outcome is meaningless), then by test outcome.
    """
    skipped = 0
    incompetent = 0
    survivors = 0
    tested = 0

    for _job_id, result in db.results:
        if result.worker_outcome == WorkerOutcome.SKIPPED:
            skipped += 1
            continue
        if result.test_outcome == TestOutcome.INCOMPETENT:
            incompetent += 1
            continue
        tested += 1
        if result.test_outcome == TestOutcome.SURVIVED:
            survivors += 1

    return Score(
        total=db.num_work_items,
        skipped=skipped,
        incompetent=incompetent,
        survivors=survivors,
        tested=tested,
    )


def parse_min_score(raw: str) -> float:
    """Validate the threshold as a float in [0, 1]. Anything else is a fail-closed error."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"min-score must be a number in [0,1], got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"min-score must be within [0,1], got {value}")
    return value


def verdict(score: Score, min_score: float) -> tuple[int, str]:
    """Map a score to (exit code, human-readable line). Fails closed on degenerate sessions."""
    if score.total == 0:
        return (
            FAIL_CLOSED,
            "no mutants were generated — check cosmic-ray module-path. Refusing to pass a "
            "gate that measured nothing.",
        )
    if score.tested == 0:
        if score.skipped == score.total:
            return (
                GO,
                f"all {score.total} mutants fall outside the diff (filtered) — nothing to "
                "mutate in this phase.",
            )
        return (
            FAIL_CLOSED,
            f"no mutant was actually tested ({score.incompetent} incompetent, "
            f"{score.skipped} skipped of {score.total}) — no evidence about the suite.",
        )
    if score.score < min_score:
        return (
            NO_GO,
            f"mutation score {score.score:.4f} is below the {min_score:.2f} threshold "
            f"({score.survivors} survivor(s) of {score.tested} tested).",
        )
    return (
        GO,
        f"mutation score {score.score:.4f} >= {min_score:.2f} threshold "
        f"({score.survivors} survivor(s) of {score.tested} tested).",
    )


def main(argv: list[str] | None = None) -> int:
    """Score the session named on the command line and return the gate's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "session", type=Path, help="path to the cosmic-ray session file"
    )
    parser.add_argument(
        "--min-score",
        default="0.85",
        help="minimum acceptable mutation score in [0,1] (default: 0.85)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the score summary as JSON on stdout"
    )
    args = parser.parse_args(argv)

    try:
        min_score = parse_min_score(args.min_score)
    except ValueError as exc:
        print(f"[mutation_score] {exc}", file=sys.stderr)
        return FAIL_CLOSED

    if not args.session.is_file():
        print(f"[mutation_score] session not found: {args.session}", file=sys.stderr)
        return FAIL_CLOSED

    try:
        with use_db(str(args.session), WorkDB.Mode.open) as db:
            score = score_session(db)
    except OSError as exc:
        print(f"[mutation_score] cannot read session: {exc}", file=sys.stderr)
        return FAIL_CLOSED

    code, message = verdict(score, min_score)
    if args.json:
        print(json.dumps(score.as_dict()))
    stream = sys.stdout if code == GO else sys.stderr
    print(f"[mutation_score] {message}", file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
