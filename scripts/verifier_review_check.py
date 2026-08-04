#!/usr/bin/env python3
"""Substance check for the Verifier's cross-family review verdict.

The Verifier's review is the pipeline's independence mechanism, and its worst failure is a verdict
that says GO without the model having actually read the review set. That was reachable: when the
bundle exceeded `VERIFIER_SRC_LIMIT`, `verifier_review.sh` truncated it and appended a note *asking*
the model to report the review as partial — an instruction, not an assertion — then returned the
model's verdict as its exit code. A truncated review could pass a phase silently, and review sets
only grow, so every later phase was likelier to hit it than the last.

This asserts the verdict shows evidence of the review having happened:

  1. the verdict value is one the rubric allows,
  2. the report is non-empty,
  3. the report or a finding names at least one file from the review set,
  4. the review does not itself say it was partial.

(3) is the load-bearing one: a verdict that names nothing it was given is one that could have been
written without opening the bundle. A terse-but-genuine review that names no file will be refused —
that is the intended trade. This gate's failure modes are not symmetric: a false refusal costs a
re-run, a false pass ships an unverified phase and every later phase builds on it.

    python3 scripts/verifier_review_check.py <verdict.json> <review-set-file>...
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

#: Verdict tokens the review rubric allows. Anything else is a malformed reply, not a decision.
VALID_VERDICTS = ("GO", "REVIEW", "NO-GO")

#: Phrases a model uses when it knows it saw only part of the bundle. The old code asked for exactly
#: these and then ignored them; honouring them is the point.
PARTIAL_MARKERS = (
    "truncat",
    "partial review",
    "review was partial",
    "not seen the whole",
    "have not seen all",
    "only part of",
    "incomplete review",
)


class SubstanceError(Exception):
    """The verdict does not evidence a completed review. Always fail closed on this."""


def _text_of(verdict: dict) -> str:
    """Every place the model can demonstrate it read something, as one lowercase blob."""
    parts = [str(verdict.get("report") or "")]
    for finding in verdict.get("findings") or []:
        if isinstance(finding, dict):
            parts.extend(str(finding.get(k) or "") for k in ("target", "detail", "instruction"))
    return "\n".join(parts).lower()


def assert_substance(verdict: dict, review_set: list[str]) -> None:
    """Raise SubstanceError unless the verdict evidences a real, complete review."""
    if not review_set:
        raise SubstanceError(
            "empty review set: a review of zero files is not a clean review"
        )

    value = verdict.get("verdict")
    if value not in VALID_VERDICTS:
        raise SubstanceError(
            f"verdict is {value!r}, not one of {', '.join(VALID_VERDICTS)} — malformed reply"
        )

    report = str(verdict.get("report") or "")
    if not report.strip():
        raise SubstanceError("empty report: a verdict with no reasoning is not a review")

    blob = _text_of(verdict)

    for marker in PARTIAL_MARKERS:
        if marker in blob:
            raise SubstanceError(
                f"the review reports itself as partial ({marker!r}). A partial review is an "
                "unreviewed phase — raise VERIFIER_SRC_LIMIT or split the review set and re-run"
            )

    # A review that names nothing it was handed could have been written without the bundle.
    basenames = {Path(f).name for f in review_set if f}
    if not any(re.search(rf"\b{re.escape(name)}", blob) for name in basenames):
        raise SubstanceError(
            f"the report names none of the {len(basenames)} review-set file(s) — no evidence the "
            "bundle was read. The rubric requires naming what was reviewed"
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print(
            "usage: verifier_review_check.py <verdict.json> <review-set-file>...",
            file=sys.stderr,
        )
        return 2

    path, review_set = args[0], args[1:]
    try:
        with open(path, encoding="utf-8") as fh:
            verdict = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"verifier-review: cannot read verdict {path}: {exc} — fail closed", file=sys.stderr)
        return 2

    try:
        assert_substance(verdict, review_set)
    except SubstanceError as exc:
        print(f"verifier-review: {exc} — fail closed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
