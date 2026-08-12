#!/usr/bin/env python3
"""The spec gate's `## CONTEXT (reference only)` block - the contracts a spec can contradict.

One of the four things that BLOCK a spec is a `contradiction`, and the triage table defines it as
"two statements in this spec that cannot both hold, **or one that contradicts a binding contract the
overview or the prior phase's card declares**". The observe pass was documented as receiving those
two documents (`prompts/spec-gate-observe.md`) and `scripts/doc_read_path.py` declares the spec gate
as their reader - but nothing ever assembled them, so half of that category was unobservable and the
closed set of four was really three and a claim. A declared reader that does not read is exactly the
promise-versus-enforcement shape the read-path table exists to catch.

This module is the mechanism behind that declaration, and it carries **exactly the extents the read
path declares and no more**:

- `overview.md` -> the `## Contracts and Decisions` section ONLY. Not the whole document: the read
  path gives the spec gate that header and gives the spec *writer* the whole file, and sending more
  here would quietly re-acquire a read the read-path work removed.
- `handover.md` -> the **immediately prior** phase's contract card. Not every prior phase's - that
  is the 272 KB, 485k-1,475k-token read the contract card replaced - and never
  `handover-archive.md`, which no stage reads.

Both are derived from the spec's own path, never asked of a model:
`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`.

**Missing pieces are normal, and are never an error.** Phase 1 has no prior card; a feature may have
no contracts section yet; a spec may sit outside the layout entirely. Each absent part is omitted and
named on stderr, so a gate running with no context is visible rather than silent. Nothing here can
fail the gate: the block is reference material, and the observe pass is told not to raise
observations *about* it.

Usage:
    spec_gate_context.py <spec.md>    the block on stdout (empty when there is none),
                                      what was included and what was absent on stderr
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: The stable header the overview carries its binding contracts under.
CONTRACTS_HEADING = re.compile(
    r"^(##+)[ \t]*Contracts and Decisions\b.*$", re.IGNORECASE | re.MULTILINE
)

#: A phase directory: `<n>-<slug>`, where `<n>` orders the phases.
PHASE_DIR = re.compile(r"^(\d+)-")

MARKER = "## CONTEXT (reference only)"


def layout(spec: Path) -> tuple[Path, int] | None:
    """(feature directory, phase number) for `spec`, or None when it is outside the layout."""
    parts = spec.resolve().parts
    try:
        index = len(parts) - 1 - parts[::-1].index("phases")
    except ValueError:
        return None
    if index == 0 or index + 1 >= len(parts):
        return None
    number = PHASE_DIR.match(parts[index + 1])
    if not number:
        return None
    return Path(*parts[:index]), int(number.group(1))


def contracts_section(text: str) -> str | None:
    """The `## Contracts and Decisions` section of an overview, heading included, or None.

    Ends at the next heading of the SAME OR SHALLOWER level, so a contract written under its own
    `### ` subheading stays inside the section rather than truncating it - and the section still
    stops well short of the whole document, which is the extent the read path grants this reader.
    """
    start = CONTRACTS_HEADING.search(text)
    if not start:
        return None
    rest = text[start.end():]
    end = re.compile(rf"^#{{1,{len(start.group(1))}}}[ \t]+", re.MULTILINE).search(rest)
    body = rest[: end.start()] if end else rest
    return (start.group(0) + body).strip() or None


def overview_contracts(feature_dir: Path) -> str | None:
    """The feature overview's contracts section, or None when there is no overview or no section."""
    try:
        text = (feature_dir / "overview.md").read_text(encoding="utf-8")
    except OSError:
        return None
    return contracts_section(text)


def prior_card(feature_dir: Path, phase: int) -> tuple[str, str] | None:
    """(phase directory name, contract card) for the immediately prior phase, or None.

    "Immediately prior" is the highest phase number below this one that actually has a card, so a
    feature numbered 1, 2, 4 does not lose its context to a gap - but only ONE card is ever carried.
    """
    candidates: list[tuple[int, Path]] = []
    for directory in (feature_dir / "phases").glob("*"):
        number = PHASE_DIR.match(directory.name)
        if not directory.is_dir() or not number or int(number.group(1)) >= phase:
            continue
        if (directory / "handover.md").is_file():
            candidates.append((int(number.group(1)), directory))
    if not candidates:
        return None
    _, directory = max(candidates, key=lambda pair: pair[0])
    try:
        card = (directory / "handover.md").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return (directory.name, card) if card else None


def build(spec: Path) -> tuple[str, list[str]]:
    """(the CONTEXT block, one note per part included or absent). An empty block is a normal answer."""
    where = layout(spec)
    if where is None:
        return "", [f"absent: {spec} is outside docs/features/<feature>/phases/… — no context"]
    feature_dir, phase = where

    notes: list[str] = []
    parts: list[str] = []

    contracts = overview_contracts(feature_dir)
    if contracts:
        parts.append(contracts)
        notes.append(f"included: {feature_dir.name}/overview.md — ## Contracts and Decisions only")
    else:
        notes.append(f"absent: no ## Contracts and Decisions section in {feature_dir.name}/overview.md")

    card = prior_card(feature_dir, phase)
    if card:
        name, body = card
        parts.append(f"### Contract card carried forward from phase {name}\n\n{body}")
        notes.append(f"included: phases/{name}/handover.md — the immediately prior phase's card")
    else:
        notes.append(f"absent: no prior phase card before phase {phase}")

    if not parts:
        return "", notes
    preamble = (
        "These are the binding contracts the spec under review must not contradict. They are "
        "BACKGROUND: do not make observations about them, and do not judge them. Use them only to "
        "notice where the spec under review contradicts one."
    )
    return "\n\n".join([MARKER, preamble, *parts]), notes


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: spec_gate_context.py <spec.md>", file=sys.stderr)
        return 2
    block, notes = build(Path(args[0]))
    for note in notes:
        print(f"  spec-gate context: {note}", file=sys.stderr)
    if block:
        print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
