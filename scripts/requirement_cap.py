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

**The cap binds a spec that can still be split.** That is the applicability boundary
(`scripts/applicability.py`), and it is not a softening of the rule - it is what makes the rule a
gate rather than a wedge. A spec stamped `status: done` has been implemented: its requirement ids
are already pointed at by `test-mapping.md` rows, by `verdict.json` findings and by prior handovers,
and dividing it into siblings would renumber all of them. The only remedy this check prescribes is
therefore unavailable, and a check whose remedy is unavailable cannot be answered at all - two
shipped specs of one measured feature declared 30 and 29 requirements against a cap of 12, so **no
verdict of any kind was reachable for them, ever**. Over the cap, a shipped spec is COUNTED and
NAMED and never blocked; a spec still being written is blocked and split exactly as before. The cap
measurably removed spec-size growth and it keeps doing so, because growth happens while a spec is
being written and that is precisely where it still binds.

It is not an escape hatch for a draft: `status: done` is stamped by the implementer, not the writer,
and it is what fires `hook_verifier.sh` (the phase suite must pass) and `verifier_precheck.py` (every
requirement id owed a trace must have one). A draft claiming to be done fails both, loudly. A
recorded `requirement-cap` exception on the phase's ledger is the explicit second route, for a spec
that must be excused by decision rather than by evidence.

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

**Where a requirement's `binding:` sits is part of the same layout knowledge, so it is owned here
too** (`declared_bindings`). Widening `DECLARATION` without teaching the precheck the same layouts
was one parsing rule stated in two places, and it drifted the moment one moved: a heading-style spec
counted correctly here and was then reported at handover as an untraced coverage gap.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402

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
#: ("covers R1.1.1, R1.1.4") does not. `rest` is the remainder of the line.
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

#: The `binding:` a declaration carries. Read from the declaration line, or - when the layout puts it
#: there - from the block immediately below it. See `declared_bindings`.
BINDING = re.compile(r"binding:[ \t`]*([a-z0-9-]+)", re.IGNORECASE)

#: Any markdown heading. Ends a declaration's continuation block, so a requirement can never inherit
#: the binding of whatever follows the section it lives in.
ANY_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+")

REQUIREMENTS_HEADING = re.compile(r"^(##+)[ \t]*Requirements\b.*$", re.IGNORECASE | re.MULTILINE)


def declared_bindings(text: str) -> list[tuple[str, str | None]]:
    """(requirement id, its `binding:` or None) for every declaration, in document order.

    **Where a binding is found is a property of the LAYOUT, so it is owned here, beside the layout
    regex.** `verifier_precheck.py` used to read the binding from the declaration line alone; when
    `DECLARATION` was widened to accept headings and ordered lists, the two statements of one parsing
    rule drifted instantly - a `### R1.1.1` / `binding: none` spec counted correctly at the cap and
    was then reported at handover as an untraced coverage gap, for a requirement that is exempt by
    construction. One module owns how a requirement is declared AND where its binding is.

    Inline layouts (list item, bold, table row) carry the binding on the declaration line, including
    in a later table cell. A heading-style declaration carries it in the block below, so the search
    continues past blank lines into the first block and stops at the next declaration or the next
    heading — never further. That bound is what stops a requirement inheriting its neighbour's
    binding.

    A binding that cannot be read is None, and the caller keeps such a requirement **owed a trace**:
    an unreadable binding buys no more than a missing one does.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if DECLARATION.match(line)]
    out: list[tuple[str, str | None]] = []
    for position, index in enumerate(starts):
        match = DECLARATION.match(lines[index])
        found = BINDING.search(match.group("rest"))
        if found is None:
            limit = starts[position + 1] if position + 1 < len(starts) else len(lines)
            found = BINDING.search("\n".join(_continuation(lines, index + 1, limit)))
        out.append((match.group(1), found.group(1).lower() if found else None))
    return out


def _continuation(lines: list[str], start: int, limit: int) -> list[str]:
    """The first non-empty block after a declaration line, bounded by `limit` and any heading."""
    block: list[str] = []
    for line in lines[start:limit]:
        if ANY_HEADING.match(line):
            break
        if not line.strip():
            if block:
                break
            continue
        block.append(line)
    return block


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


def unenforceable(spec: Path) -> str | None:
    """Why the cap cannot bind this spec, or None when it binds.

    Two evidences, both from the applicability boundary: the spec has **shipped** (its remedy no
    longer exists), or the phase records a disclosed **exception** for this rule and this spec.

    An unreadable ledger grants nothing and says so — under-report, exactly as the resolver does.
    """
    if applicability.spec_shipped(spec):
        return (
            "it is stamped `status: done`, so it has been implemented and the SPLIT this check "
            "prescribes would renumber requirement ids that test-mapping rows, verdict findings "
            "and prior handovers already point at"
        )
    try:
        phase_dir = spec.resolve().parents[2]   # specs/<n>.<k>-<sub>/spec.md -> the phase directory
    except IndexError:
        return None
    try:
        record = applicability.excepted(phase_dir, "requirement-cap", spec.resolve().parent.name)
    except applicability.ApplicabilityError as exc:
        print(
            f"[requirement_cap] {phase_dir.name} has an exception ledger this cannot read ({exc}). "
            f"No exception is granted.",
            file=sys.stderr,
        )
        return None
    return f"a recorded exception covers it — {record.describe()}" if record else None


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
        why = unenforceable(args.spec)
        if why is None:
            print(split_message(args.spec, ids, limit, scope), file=sys.stderr)
            return OVER
        # Counted and named, never blocked. The number still has to be visible: a check that goes
        # quiet on the specs it stopped enforcing is indistinguishable from a check that passed.
        applicability.report_unenforced(
            "requirement_cap",
            1,
            f"{args.spec} declares {len(ids)} requirements ({scope}) against a cap of {limit}, "
            f"and {why}",
        )
        return WITHIN
    print(f"  requirements: {len(ids)}/{limit} ({scope})", file=sys.stderr)
    return WITHIN


if __name__ == "__main__":
    raise SystemExit(main())
