#!/usr/bin/env python3
"""One owner of "the body under a markdown heading, up to the next heading of the same level".

Four modules in this pipeline slice a named section out of a markdown document: the requirement cap
reads `## Requirements`, the spec gate's CONTEXT block reads `## Contracts and Decisions`, the spec
rubric lifts prompt sections verbatim, and the carried-items ledger reads the contract card's
`## Open items`. All four had their own copy of the same regex pair and the same same-or-shallower
level rule, differing only in what they do with an empty body.

A second statement of a parsing rule is the one that drifts, and this repository has already paid for
that twice: `requirement_cap.DECLARATION` was copied into `verifier_precheck.py` and both went blind
on table-formatted specs at the same moment, and `spec_gate_context.prior_phase` was extracted for
exactly this reason. So the slicing lives here and the callers keep only their own policy - whether
the heading line is carried, whether the body is stripped, and whether an empty body means missing.

**The level rule is the load-bearing half.** A section ends at the next heading of the SAME OR
SHALLOWER level, never at a deeper one, so a subsection under the heading stays inside it. Ending at
any deeper heading truncated `## Requirements` to nothing, which read as a clean `0/12` and stopped
the cap from ever firing.
"""

from __future__ import annotations

import re

MAX_LEVEL = 6


def slice_section(
    text: str, heading: str, *, min_level: int = 1, allow_trailing: bool = False
) -> tuple[str, str] | None:
    """(the heading line, the body under it), or None when `text` has no such heading.

    `min_level` is the shallowest heading that counts (`2` = `##` and deeper). `allow_trailing`
    accepts extra text after the heading name on the same line, which the overview's and the spec's
    own headings carry; without it the heading must stand alone.

    Neither part is stripped and an empty body is returned as an empty string - "the section exists
    but says nothing" and "there is no section" are different answers, and it is the caller that
    knows which of the two should block.
    """
    tail = r"\b.*$" if allow_trailing else r"[ \t]*$"
    start = re.search(
        rf"^(\#{{{min_level},{MAX_LEVEL}}})[ \t]*{re.escape(heading)}{tail}",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if not start:
        return None
    rest = text[start.end():]
    end = re.compile(rf"^#{{1,{len(start.group(1))}}}[ \t]+", re.MULTILINE).search(rest)
    return start.group(0), (rest[: end.start()] if end else rest)
