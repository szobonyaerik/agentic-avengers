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
  4. the report's PROSE does not itself say the review was partial.

(3) and (4) pull against each other, and (4) is where that showed: (3) requires the report to name
the files it reviewed, so a marker matched as a bare substring is matched against filenames the
rubric obliged the model to write down. A phase-10 review naming `test_r10_3_8_message_truncation.py`
was refused as truncated while being complete, and neither remedy this check prescribes can rename a
file the report is required to name. So (4) reads prose only — see `_NON_PROSE`. The markers, the
negation lookback and the verdict are unchanged; only the region scanned is.

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
from collections.abc import Sequence
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

#: Regions of a report that are NOT the model talking about its own read: a code span, a filename or
#: path, a `snake_case` symbol. A marker here is the name of a thing under review, not a claim about
#: the bundle — and this document is *required* by `prompts/verifier-review.md` to list the name of
#: every file it reviewed, so a bare substring scan over it asks a report to avoid vocabulary the
#: rubric obliges it to use. Measured: a complete phase-10 review was refused because one reviewed
#: file was `test_r10_3_8_message_truncation.py`, and neither prescribed remedy could clear it —
#: re-running reproduces it and a split review set still has to name the file.
#:
#: Nothing here is a *general* prose filter; it is three shapes English confessions never take. An
#: extension never ends an English word, a model admitting it saw half the bundle does not put the
#: admission in backticks, and an identifier carries an underscore BETWEEN two word characters.
#: That last one is stated precisely on purpose: these reports are markdown, where `_word_` is
#: emphasis, not a symbol — a leading or trailing underscore is punctuation the writer put around a
#: sentence word, so `_truncated_` is prose and must stay readable, while `test_r10_3_8` and
#: `_prose_of` are names and are blanked. "the bundle was truncated", "truncated/partial read",
#: "TRUNCATED bundle" all survive untouched.
_NON_PROSE = re.compile(
    r"`[^`]*`"                            # a code span
    r"|[\w./\\-]*\w\.[A-Za-z]\w{0,7}\b"   # something.ext, with or without a leading path
    r"|\b\w+_\w+\b"                       # snake_case — an underscore between two word characters
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


def _blank(text: str, span: tuple[int, int]) -> str:
    """`text` with `span` replaced by spaces. Offsets are preserved deliberately: the negation
    lookback reads backwards by character, so shortening the string would let a blanked filename
    pull an unrelated clause into a marker's window."""
    start, end = span
    return text[:start] + " " * (end - start) + text[end:]


def _prose_of(lowered: str, review_set: Sequence[str] = ()) -> str:
    """The report with every non-prose region blanked out — see `_NON_PROSE`.

    The review set is blanked first and literally, because it is the one set of names this document
    is *obliged* to contain: a file the rubric made the report name is never evidence about how much
    of the bundle the model saw, whatever shape its name happens to have.
    """
    names = {str(item).lower() for item in review_set if item}
    names |= {Path(name).name for name in names}
    for name in sorted(names, key=len, reverse=True):
        start = 0
        while (idx := lowered.find(name, start)) != -1:
            lowered = _blank(lowered, (idx, idx + len(name)))
            start = idx + len(name)
    return _NON_PROSE.sub(lambda m: " " * len(m.group(0)), lowered)


def self_reported_partial(report: str, review_set: Sequence[str] = ()) -> str | None:
    """The marker by which the report claims it saw only part of the bundle, or None.

    Deliberately scoped to `report` — the model's self-report about the bundle. A finding's
    target/detail/instruction is prose *about the code under review*, where these substrings are
    ordinary vocabulary ("test_parse covers only part of R1.2.4's criteria") rather than a
    completeness claim, so matching there deleted legitimate NO-GOs and their findings.

    And scoped, within the report, to its PROSE: a marker inside a filename, a path or a code
    identifier is a name, not a claim. Nothing about which reports are refused changes — the same
    seven markers, the same negation lookback, the same verdict — only where the text is read.
    """
    lowered = _prose_of(report.lower(), review_set)
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

    marker = self_reported_partial(report, review_set)
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
