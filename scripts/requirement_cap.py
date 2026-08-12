#!/usr/bin/env python3
"""The requirement cap - a SPLIT trigger, counted before the gate runs and never a gate verdict.

A phase's spec grew 25k -> 51k characters across four rejected rounds because the only response
available to a rejected spec was more text, and nothing anywhere had a size ceiling, a requirement
cap, or a cost dimension. The fix is not to teach a reviewer to say "too big". It is to decide size
**mechanically, before any model sees the spec**, and to make the remedy a split rather than a
rejection.

That distinction is the whole design:

- **A rejection for size is one more thing to grow around.** The spec writer answers a rejection with
  more prose - that is what produced the 51k spec. So the gate rubrics are told, in as many words,
  never to reject a spec for being large; size is not one of their four blocking categories.
- **A split is a different instruction.** Over the cap, this check names the ids and tells the writer
  to divide the spec into siblings `<n>.<k>` under the same phase. Each half is then independently
  gated, implemented and traced - which is what the numbered-spec layout was always for.

Twelve is the cap (`SPEC_REQUIREMENT_MAX` overrides it). It is a budget, not a discovery: it is the
number at which one spec still reads as one increment.

## How requirements are counted

A **declaration** is a line whose first content is a requirement id (`R<n>.<k>.<m>`), reached through
an optional markdown table pipe, heading marker, list marker (ordered or unordered) and/or bold. All
six of these are one declaration:

    - R1.1.1 — `binding: e2e` — …
    1. R1.1.1 — `binding: e2e` — …
    ### R1.1.1
    **R1.1.1** — …
    R1.1.1 — …
    | R1.1.1 | binding: e2e | … |

The table row is not a hypothetical: a spec that laid 19 requirements out as a markdown table - the
format this pipeline already uses for `test-mapping.md` - counted **zero**, printed
`requirements: 0/12`, and the cap, the only counterweight to spec growth in the whole pipeline, never
fired. The observe pass is forbidden from raising size, so no model caught it either. A guard that
cannot fire is the defect class this overhaul exists to remove, so `DECLARATION` is **one regex,
owned here**, imported by `verifier_precheck.py` rather than copied into it - the copy is what went
blind in two places at once.

Counting is over **distinct ids**, so the same id restated under `## Acceptance criteria` is not
counted twice, and an id merely mentioned mid-sentence ("covers R1.1.1, R1.1.4") is not a
declaration.

When the spec has a `## Requirements` heading the count is taken from that section - the authored
set, not every mention. When it does not, the whole body is scanned instead: a spec that keeps its
requirements somewhere else still gets counted, so the cap cannot be evaded by moving the heading.
The mode used is always printed, because a silent fallback is how a check comes to mean something
other than what its caller believes.

**A count of zero over text that plainly contains requirement ids is not a count of zero.** It is a
layout this parser cannot read, and reporting it as WITHIN is the same silent-pass failure a fifth
layout would reproduce. So it is an ERROR that names what it saw, never a pass.

Usage:
    requirement_cap.py <spec.md> [--max N]     exit 0 = at or under, 1 = over (split),
                                               2 = the count could not be decided
    requirement_cap.py <spec.md> --count       print the count alone and exit 0
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

WITHIN = 0
OVER = 1
ERROR = 2

#: One spec, one increment. Above this the spec splits into siblings rather than being rejected.
DEFAULT_MAX = 12

#: A requirement id: phase, spec, requirement.
REQUIREMENT_ID = re.compile(r"R\d+\.\d+\.\d+")

#: A DECLARATION - a line whose first content is a requirement id, reached through an optional
#: markdown table pipe, heading marker, list marker (ordered `1.`/`1)` or unordered) and/or bold, in
#: that order. `- R1.1.2 — binding: e2e — …`, `1. R1.1.2 — …`, `### R1.1.2`, `**R1.1.2**` and
#: `| R1.1.2 | binding: e2e | … |` all count; a line that merely mentions an id mid-sentence
#: ("covers R1.1.1, R1.1.4") does not. `rest` is the remainder of the line, which is where
#: `verifier_precheck.py` reads the `binding:` - including from a later table cell.
#:
#: The layout set is widened at THIS regex whenever a real one is found, because the alternative is
#: `unreadable_layout()` failing the gate closed on an ordinary markdown document until its author
#: re-lays it out. A numbered requirements list and an id-per-heading are both ordinary; going blind
#: on them is the same defect the table row already caused once.
#:
#: This is the ONE declaration regex. verifier_precheck.py imports it; it used to hold its own copy,
#: and both went blind on table-formatted specs at the same moment, which is what a second copy is
#: for.
DECLARATION = re.compile(
    r"^[ \t]*(?:\|[ \t]*)?(?:\#{1,6}[ \t]+)?(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:\*\*)?"
    r"(R\d+\.\d+\.\d+)\b(?P<rest>.*)$"
)

REQUIREMENTS_HEADING = re.compile(r"^(##+)[ \t]*Requirements\b.*$", re.IGNORECASE | re.MULTILINE)


def requirements_section(text: str) -> str | None:
    """The body of the `## Requirements` section, or None when the spec has no such heading.

    The section ends at the next heading of the SAME OR SHALLOWER level, never at a deeper one: a
    spec that gives each requirement its own `### R1.1.1` heading keeps them inside its
    `## Requirements` section. Ending at any `##+` truncated such a section to nothing, and a section
    with no ids in it is not even caught by `unreadable_layout()` - it reads as a clean `0/12`, which
    is the silent-pass failure this whole check exists to prevent.
    """
    start = REQUIREMENTS_HEADING.search(text)
    if not start:
        return None
    rest = text[start.end() :]
    end = re.compile(rf"^#{{1,{len(start.group(1))}}}[ \t]+", re.MULTILINE).search(rest)
    return rest[: end.start()] if end else rest


def declared_ids(text: str) -> tuple[list[str], str]:
    """Distinct requirement ids declared in `text`, in document order, and the scope used.

    Returns (ids, scope) where scope is `requirements-section` or `whole-body`.
    """
    section = requirements_section(text)
    scope = "requirements-section" if section is not None else "whole-body"
    seen: list[str] = []
    for line in (section if section is not None else text).splitlines():
        match = DECLARATION.match(line)
        if match and match.group(1) not in seen:
            seen.append(match.group(1))
    return seen, scope


def unreadable_layout(text: str) -> str | None:
    """Why the count cannot be trusted, or None when it can.

    Requirement ids present in the counted scope but **not one of them recognised as a declaration**
    means the parser cannot see this spec's requirement set. Reporting that as `0/12` is how the cap
    silently stopped existing for table-formatted specs, and widening the regex fixes the layout that
    was found rather than the class. This is the floor: the check says it cannot count, and fails
    closed, instead of passing something it never read.
    """
    section = requirements_section(text)
    scope = section if section is not None else text
    if not REQUIREMENT_ID.search(scope):
        return None
    ids, _ = declared_ids(text)
    if ids:
        return None
    sample = next(
        (line.strip() for line in scope.splitlines() if REQUIREMENT_ID.search(line)), ""
    )
    return (
        "requirement ids appear in this spec but none of them is DECLARED in a shape this check can "
        "read, so the count would be 0 and the cap would never fire. Declare each requirement id as "
        "the first content of its own line, list item or table row.\n"
        f"  First unrecognised line: {sample[:200]}"
    )


def cap() -> int:
    """The cap in force. A value that does not parse is a hard error, never a silent default."""
    raw = os.environ.get("SPEC_REQUIREMENT_MAX", "").strip()
    if not raw:
        return DEFAULT_MAX
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"SPEC_REQUIREMENT_MAX={raw!r} is not a whole number of requirements. It decides when a "
            f"spec must split, so it is not guessed."
        ) from None
    if value < 1:
        raise ValueError(f"SPEC_REQUIREMENT_MAX={raw!r} must be at least 1.")
    return value


def split_message(spec: Path, ids: list[str], limit: int, scope: str) -> str:
    """What the writer is told when a spec is over the cap. A split instruction, never a rejection."""
    return "\n".join(
        [
            f"spec-gate: {spec} declares {len(ids)} requirements ({scope}); the cap is {limit}.",
            "",
            "  This is a SPLIT TRIGGER, not a gate rejection. The spec is not wrong for being large,",
            "  and no model has judged it - this count ran before the gate. Do not answer it with",
            "  more text: divide it into sibling specs under the same phase, each one an",
            "  independently gated increment.",
            "",
            f"  Declared: {', '.join(ids)}",
            "",
            "  Split on the seam the requirements already group around, keep the phase number, and",
            "  renumber the siblings <n>.<k>. Each sibling is gated, implemented and traced on its",
            "  own. Requirement ids move with the spec they belong to.",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--max", type=int, default=None, help="override SPEC_REQUIREMENT_MAX")
    parser.add_argument("--count", action="store_true", help="print the count alone")
    args = parser.parse_args(argv)

    try:
        text = args.spec.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[requirement_cap] cannot read {args.spec}: {exc}", file=sys.stderr)
        return ERROR

    try:
        limit = args.max if args.max is not None else cap()
    except ValueError as exc:
        print(f"[requirement_cap] {exc}", file=sys.stderr)
        return ERROR

    blind = unreadable_layout(text)
    if blind is not None:
        print(f"[requirement_cap] {args.spec}: {blind}", file=sys.stderr)
        return ERROR

    ids, scope = declared_ids(text)
    if args.count:
        print(len(ids))
        return WITHIN
    if len(ids) > limit:
        print(split_message(args.spec, ids, limit, scope), file=sys.stderr)
        return OVER
    print(f"  requirements: {len(ids)}/{limit} ({scope})", file=sys.stderr)
    return WITHIN


if __name__ == "__main__":
    raise SystemExit(main())
