#!/usr/bin/env python3
"""Substance check for the Verifier's cross-family review verdict.

The Verifier's review is the pipeline's independence mechanism, and its worst failure is a verdict
that says GO without the model having actually read the review set. That was reachable: when the
bundle exceeded `VERIFIER_SRC_LIMIT`, `verifier_review.sh` truncated it and appended a note *asking*
the model to report the review as partial — an instruction, not an assertion — then returned the
model's verdict as its exit code. A truncated review could pass a phase silently, and review sets
only grow, so every later phase was likelier to hit it than the last.

This asserts the verdict shows evidence of the review having happened:

  1. the verdict value is one gate_runner and the rubric agree on,
  2. the report is non-empty,
  3. the report or a finding names at least one file from the review set,
  4. the report does not itself say the review was partial.

(3) is the load-bearing one: a verdict that names nothing it was given is one that could have been
written without opening the bundle. `prompts/verifier-review.md` requires the report to name every
file it reviewed and what was checked in each, so this asserts a contract the model was actually
given rather than one invented here. A terse review that names no file is still refused — that is
the intended trade. This gate's failure modes are not symmetric: a false refusal costs a re-run, a
false pass ships an unverified phase and every later phase builds on it.

What this does NOT bound: fabrication. The `--- <path> ---` headers are part of the prompt, so a
reply that echoes a filename plus a generic sentence passes all four checks. The check bounds
*hollow* verdicts (empty report, wrong token, self-reported partial), not *fabricated* ones; the
only real guard against fabrication remains the cross-family model itself and the rubric's "do not
emit a finding you cannot point at a line of the bundle for". Do not later trust this as proof that
the bundle was read — a gate trusted for more than it does is how the original fail-open survived.

Note also that `VERIFIER_SRC_LIMIT` bounds the review-set source only, not the whole bundle.

    python3 scripts/verifier_review_check.py <verdict.json> <review-set-file>...
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from gate_runner import VERDICT_OK

#: Verdict tokens that evidence a decision: whatever `gate_runner` treats as a pass, plus the
#: rubric's NO-GO. Imported rather than restated so the two sets cannot drift apart — a token
#: gate_runner passes on but this rejected would be deleted after the tokens were already spent.
#: The comparison is normalised the way gate_runner normalises it (`.strip().upper()`), because a
#: shared set still drifts if the two sides compare it differently: `{"verdict":"go"}` passes there.
VALID_VERDICTS = tuple(sorted(VERDICT_OK)) + ("NO-GO",)

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

#: How far back to look for a negation before a marker. Wide enough for "the bundle was not
#: truncated" and "no truncation reaches the model", narrow enough that it cannot reach into an
#: unrelated preceding clause — and the lookback stops at the nearest clause boundary, so "found no
#: issues. Truncated bundle." is still read as a partial self-report rather than a denial of one.
_NEGATION_WINDOW = 16
_NEGATIONS = ("not ", "n't ", "no ")
_CLAUSE_BOUNDARIES = ".;!?\n"


class SubstanceError(Exception):
    """The verdict does not evidence a completed review. Always fail closed on this."""


def _text_of(verdict: dict) -> str:
    """Every place the model can demonstrate it read something, as one lowercase blob."""
    parts = [str(verdict.get("report") or "")]
    for finding in verdict.get("findings") or []:
        if isinstance(finding, dict):
            parts.extend(str(finding.get(k) or "") for k in ("target", "detail", "instruction"))
    return "\n".join(parts).lower()


def self_reported_partial(report: str) -> str | None:
    """The marker by which the report claims it saw only part of the bundle, or None.

    Deliberately scoped to `report` — the model's self-report about the bundle. A finding's
    target/detail/instruction is prose *about the code under review*, where these substrings are
    ordinary vocabulary ("test_parse covers only part of R1.2.4's criteria") rather than a
    completeness claim, so matching there deleted legitimate NO-GOs and their findings.
    """
    lowered = report.lower()
    for marker in PARTIAL_MARKERS:
        start = 0
        while (idx := lowered.find(marker, start)) != -1:
            before = lowered[max(0, idx - _NEGATION_WINDOW):idx]
            for boundary in _CLAUSE_BOUNDARIES:
                before = before.rpartition(boundary)[2]
            if not any(neg in before for neg in _NEGATIONS):
                return marker
            start = idx + 1
    return None


def assert_substance(verdict: dict, review_set: list[str]) -> None:
    """Raise SubstanceError unless the verdict evidences a real, complete review."""
    if not review_set:
        raise SubstanceError(
            "empty review set: a review of zero files is not a clean review"
        )

    value = str(verdict.get("verdict") or "").strip().upper()
    if value not in VALID_VERDICTS:
        raise SubstanceError(
            f"verdict is {value!r}, not one of {', '.join(VALID_VERDICTS)} — malformed reply"
        )

    report = str(verdict.get("report") or "")
    if not report.strip():
        raise SubstanceError("empty report: a verdict with no reasoning is not a review")

    marker = self_reported_partial(report)
    if marker is not None:
        raise SubstanceError(
            f"the report says the review was partial ({marker!r}). A partial review is an "
            "unreviewed phase — re-run it; if the model keeps reporting a partial read, split the "
            "review set and merge the findings rather than accepting the verdict"
        )

    # A review that names nothing it was handed could have been written without the bundle.
    blob = _text_of(verdict)
    basenames = {Path(f).name for f in review_set if f}
    if not any(re.search(rf"\b{re.escape(name)}", blob) for name in basenames):
        raise SubstanceError(
            f"the report names none of the {len(basenames)} review-set file(s) — no evidence the "
            "bundle was read. prompts/verifier-review.md requires the report to name every file it "
            "reviewed and what was checked in each"
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
