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
- `handover.md` -> the **immediately prior** phase's contract card, bounded by the read path's own
  `HANDOVER_MAX_BYTES`. Not every prior phase's - that is the 272 KB, 485k-1,475k-token read the
  contract card replaced - and never `handover-archive.md`, which no stage reads. The byte bound
  matters because the cap's own enforcement is diff-scoped: an oversized pre-rule handover is
  counted, not blocked, and reading it whole would hand this new reader the entire cost the card was
  introduced to remove. A truncated card is reported on stderr, never carried silently.

Both are derived from the spec's own path, never asked of a model:
`docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`.

**Missing pieces are normal, and are never an error** - with one exception. Phase 1 has no prior
card; a feature may not have written any contracts yet; a spec may sit outside the layout entirely.
Each of those is omitted and named on stderr, so a gate running with no context is visible rather
than silent. Nothing here fails the gate: the block is reference material, and the observe pass is
told not to raise observations *about* it.

The exception is `overview.md` existing but carrying no `## Contracts and Decisions` heading at all
(clickup-agents' overview never has, across 11 phases - it uses `## Interfaces & contracts` and
`## Key decisions & trade-offs` instead). That is not "not written yet"; it is the heading this reader
looks for not existing, so `contradiction` - one of the four things that BLOCK a spec - can only ever
be checked against the prior phase's card, never against the feature's own contracts, and every spec
in the feature pays for it. Reporting that on stderr and nothing else was the defect this module
shipped with: eleven phases read "reported success" while the gate quietly checked a fraction of what
it claimed to. `build()` therefore marks this case `degraded=True` and `main()` exits **3** for it
(never 1 or 2, which mean something else on this gate) - still not a gate failure, but no longer a
state indistinguishable from "nothing to carry yet". The caller decides what to do with that signal;
it must not be discarded with `|| true`. `check()` below is the other half: it finds every overview on
disk missing the heading, independent of any one spec being gated.

Usage:
    spec_gate_context.py <spec.md>          the block on stdout (empty when there is none), what was
                                            included/absent/degraded on stderr, exit 3 iff degraded
    spec_gate_context.py check [--all] [root]
                                             every overview.md missing the heading, diff-scoped
                                            unless --all; exit 1 iff any is in scope and missing it
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The diff-scope mechanism every check on this boundary shares, and the one line every such check
# prints when it counted a finding instead of enforcing it.
from applicability import changed_paths, report_unenforced  # noqa: E402

# The contract card's cap belongs to the read-path table, which owns every extent in this file. A
# literal 6144 here would be a second statement of it, and the second statement is the one that
# drifts.
from doc_read_path import HANDOVER_MAX_BYTES  # noqa: E402

# The same-or-shallower-level section slice, owned in one place rather than restated per reader.
from md_section import slice_section  # noqa: E402

#: The stable header the overview carries its binding contracts under.
CONTRACTS_HEADING = "Contracts and Decisions"

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
    That level rule is `md_section.slice_section`'s; what stays here is the policy - the heading line
    is carried into the block, and a section with nothing under it is no context at all.
    """
    found = slice_section(text, CONTRACTS_HEADING, min_level=2, allow_trailing=True)
    if found is None:
        return None
    return "".join(found).strip() or None


def heading_present(text: str) -> bool:
    """Whether `## Contracts and Decisions` appears at all, regardless of what is under it.

    `contracts_section` collapses "no heading" and "heading with an empty body" to the same `None` -
    correctly, since neither carries anything to send. This is the one place those two are told
    apart: a heading that is simply not written yet is normal, but its total absence from an
    `overview.md` that otherwise exists means this reader can never find it, no matter how many
    phases pass. That distinction is what `degraded` in `build()` is built on.
    """
    return (
        slice_section(text, CONTRACTS_HEADING, min_level=2, allow_trailing=True)
        is not None
    )


def overview_contracts(feature_dir: Path) -> str | None:
    """The feature overview's contracts section, or None when there is no overview or no section."""
    try:
        text = (feature_dir / "overview.md").read_text(encoding="utf-8")
    except OSError:
        return None
    return contracts_section(text)


def overview_heading_missing(feature_dir: Path) -> bool:
    """True only when `overview.md` exists, is readable, and has no `## Contracts and Decisions`
    heading anywhere - the state `build()` reports as `degraded`, never as a plain absence."""
    try:
        text = (feature_dir / "overview.md").read_text(encoding="utf-8")
    except OSError:
        return False
    return not heading_present(text)


class Card(NamedTuple):
    """The prior phase's contract card as this reader is allowed to carry it."""

    phase: str
    body: str
    truncated: bool


def prior_phase(feature_dir: Path, phase: int) -> Path | None:
    """The immediately prior phase's directory, or None when this is the first card-bearing phase.

    "Immediately prior" is the highest phase number below this one that actually **has a card**, so a
    feature numbered 1, 2, 4 does not lose its context to a gap - and only ONE phase is ever prior.

    This is the one owner of that rule. `scripts/carried_items.py` imports it rather than
    re-deriving it: the carried-items ledger and this CONTEXT block must agree about which card is
    in force, and two statements of "which phase came before" is the copy that drifts.
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
    return max(candidates, key=lambda pair: pair[0])[1]


def prior_card(feature_dir: Path, phase: int) -> Card | None:
    """The immediately prior phase's contract card, bounded by the read path's own cap, or None.

    Which phase is prior is `prior_phase`'s decision, not this function's.

    **Bounded by `doc_read_path.HANDOVER_MAX_BYTES`**, imported rather than restated. That cap is
    what makes a handover a contract card rather than a document, and its own enforcement is
    diff-scoped, so a pre-rule or hand-edited oversized handover is counted and not blocked. Reading
    it whole here would prepend all of it to EVERY spec write in the next phase - re-acquiring, for a
    brand-new reader, precisely the cost the contract card was introduced to remove: one measured
    handover held 272 KB and cost 485k-1,475k tokens. An over-cap card is carried as its bounded
    prefix and the truncation is reported, never silent.
    """
    directory = prior_phase(feature_dir, phase)
    if directory is None:
        return None
    try:
        raw = (directory / "handover.md").read_text(encoding="utf-8")
    except OSError:
        return None
    encoded = raw.encode("utf-8")
    truncated = len(encoded) > HANDOVER_MAX_BYTES
    if truncated:
        raw = encoded[:HANDOVER_MAX_BYTES].decode("utf-8", "ignore")
    card = raw.strip()
    return Card(directory.name, card, truncated) if card else None


def build(spec: Path) -> tuple[str, list[str], bool]:
    """(the CONTEXT block, one note per part included/absent/degraded, whether it is degraded).

    An empty block with `degraded=False` is a normal answer - phase 1 has no prior card, a feature
    may not have written contracts yet. `degraded=True` means `overview.md` exists but never had a
    `## Contracts and Decisions` heading at all, so `contradiction` loses half of what it is defined
    to check for every spec in the feature, not just this one. The caller decides what to do with
    that; this function only refuses to make it indistinguishable from "nothing to carry yet".
    """
    where = layout(spec)
    if where is None:
        return (
            "",
            [
                f"absent: {spec} is outside docs/features/<feature>/phases/… — no context"
            ],
            False,
        )
    feature_dir, phase = where

    notes: list[str] = []
    parts: list[str] = []
    degraded = False

    contracts = overview_contracts(feature_dir)
    if contracts:
        parts.append(contracts)
        notes.append(
            f"included: {feature_dir.name}/overview.md — ## Contracts and Decisions only"
        )
    elif overview_heading_missing(feature_dir):
        degraded = True
        notes.append(
            f"DEGRADED: no ## Contracts and Decisions heading in {feature_dir.name}/overview.md — "
            f"contradiction can only be checked against the prior phase's card, never this "
            f"feature's own contracts, for EVERY spec in this feature. "
            f"See docs/templates/overview.template.md."
        )
    else:
        notes.append(
            f"absent: no ## Contracts and Decisions section in {feature_dir.name}/overview.md"
        )

    card = prior_card(feature_dir, phase)
    if card:
        parts.append(
            f"### Contract card carried forward from phase {card.phase}\n\n{card.body}"
        )
        bound = (
            f" — TRUNCATED to the first {HANDOVER_MAX_BYTES} bytes; that handover is over the "
            f"contract-card cap"
            if card.truncated
            else ""
        )
        notes.append(
            f"included: phases/{card.phase}/handover.md — the immediately prior phase's card{bound}"
        )
    else:
        notes.append(f"absent: no prior phase card before phase {phase}")

    if not parts:
        return "", notes, degraded
    preamble = (
        "These are the binding contracts the spec under review must not contradict. They are "
        "BACKGROUND: do not make observations about them, and do not judge them. Use them only to "
        "notice where the spec under review contradicts one."
    )
    return "\n\n".join([MARKER, preamble, *parts]), notes, degraded


# --- check: every overview.md on disk, independent of any one spec being gated -------------------


def check(root: Path, *, enforce_all: bool = False) -> list[str]:
    """Every `docs/features/*/overview.md` with no `## Contracts and Decisions` heading.

    This is the other half of the fix: `build()` reports degradation per spec gated, which only ever
    surfaces the defect one spec write at a time and only for a project someone is actively working
    on. This finds it directly, for every feature on disk, the moment `overview.md` is written or
    edited - the heading contract the template already states (`docs/templates/overview.template.md`)
    made explicit and CHECKED rather than merely written down.

    Diff-scoped like every other check on this applicability boundary (`scripts/applicability.py`):
    an overview the current change did not touch is COUNTED on stderr, never blocked. That is not a
    grandfathering exception written for this issue - it is the same rule `doc_read_path.py check`
    already runs, and it is what lets a project with years of pre-rule overviews (clickup-agents
    included) adopt this check without every one of them failing CI on day one. `check(...,
    enforce_all=True)` — the `--all` flag — audits every overview regardless of scope.
    """
    features = root / "docs" / "features"
    if not features.is_dir():
        print(f"[spec_gate_context] no {features} — nothing to check", file=sys.stderr)
        return []

    scope: set[Path] | None = None
    if not enforce_all:
        scope = changed_paths(root)
        if scope is None:
            print(
                f"[spec_gate_context] git cannot say what changed under {root}, so the scope is "
                f"unknowable and no overview is enforced. Run `check --all` for a full audit.",
                file=sys.stderr,
            )
            return []

    problems: list[str] = []
    unenforced = 0
    for overview in sorted(features.glob("*/overview.md")):
        try:
            text = overview.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{overview}: unreadable ({exc}) — fail closed")
            continue
        if heading_present(text):
            continue
        message = (
            f"{overview}: no ## Contracts and Decisions heading — the spec gate's CONTEXT block can "
            f"never carry this feature's contracts, so `contradiction` is checked only against the "
            f"prior phase's card, for every spec this feature ever gates. Add the heading (see "
            f"docs/templates/overview.template.md)."
        )
        if enforce_all or overview.resolve() in (scope or set()):
            problems.append(message)
        else:
            unenforced += 1

    report_unenforced(
        "spec_gate_context",
        unenforced,
        "missing the ## Contracts and Decisions heading - checked when that overview is next "
        "touched, and `check --all` audits them now",
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] == "check":
        rest = args[1:]
        enforce_all = "--all" in rest
        positional = [a for a in rest if a != "--all"]
        root = Path(positional[0]) if positional else Path(".")
        problems = check(root, enforce_all=enforce_all)
        for problem in problems:
            print(f"[spec_gate_context] {problem}", file=sys.stderr)
        if problems:
            return 1
        print("[spec_gate_context] clean", file=sys.stderr)
        return 0

    if len(args) != 1:
        print(
            "usage: spec_gate_context.py <spec.md> | spec_gate_context.py check [--all] [root]",
            file=sys.stderr,
        )
        return 2
    block, notes, degraded = build(Path(args[0]))
    for note in notes:
        print(f"  spec-gate context: {note}", file=sys.stderr)
    if degraded:
        print(
            "  spec-gate context: DEGRADED — contradiction cannot be checked against this "
            "feature's own contracts. This is reported, not swallowed: the caller must not "
            "discard this exit code.",
            file=sys.stderr,
        )
    if block:
        print(block)
    return 3 if degraded else 0


if __name__ == "__main__":
    raise SystemExit(main())
