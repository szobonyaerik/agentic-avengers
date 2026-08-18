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

There are three exceptions, and all three are `degraded=True`:

- `overview.md` existing but carrying no `## Contracts and Decisions` heading at all
  (clickup-agents' overview never has, across 11 phases - it uses `## Interfaces & contracts` and
  `## Key decisions & trade-offs` instead). That is not "not written yet"; it is the heading this
  reader looks for not existing.
- `overview.md` carrying the heading but nothing under it except boilerplate - an HTML comment, or
  the instructional text `docs/templates/overview.template.md` ships under this exact heading. A
  freshly-templated overview LOOKS filled in at this heading and carries nothing a spec could
  contradict; treating that as "included" is the same defect wearing the shape every project that
  starts from the template actually has.
- `overview.md` missing or unreadable outright. A feature with no overview yet loses the same half
  of `contradiction` as one with the wrong heading, and there is no silent exemption for it here.
  This one has no ledger to fall back on either: `applicability.excepted` records against a PHASE
  directory, and a feature with no overview yet has no phase directory - there is nowhere for a
  recorded exception to live at that point in the pipeline. So this reader does not special-case it;
  it reports the same degraded state a broken overview would, for as long as the overview is missing.

Any of the three means `contradiction` - one of the four things that BLOCK a spec - can only ever be
checked against the prior phase's card, never against the feature's own contracts, and every spec in
the feature pays for it. Reporting that on stderr and nothing else was the defect this module shipped
with: eleven phases read "reported success" while the gate quietly checked a fraction of what it
claimed to. `build()` marks each of these `degraded=True` and `main()` exits **3** for it (never 1 or
2, which mean something else on this gate) - still not a gate failure, but no longer a state
indistinguishable from "nothing to carry yet". The caller decides what to do with that signal; it
must not be discarded with `|| true`. `check()` below is the other half: it finds every feature on
disk in one of these three states, independent of any one spec being gated.

A section that is genuinely blank - the heading is there, nothing is written under it, not even a
comment - stays the ordinary, non-degraded "not written yet" case: a feature early in planning. That
is the one absence this module still treats as normal, and the only one.

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
from applicability import changed_paths, report_unenforced, touched  # noqa: E402

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


#: Boilerplate this reader must not count as real contracts: an HTML comment. The template's own
#: instructional text under this exact heading lives inside one (`docs/templates/overview.template.md`),
#: so a freshly-templated, never-filled-in overview matches this and nothing else needs to.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class Contracts(NamedTuple):
    """What `## Contracts and Decisions` looks like in one overview, as this reader may see it.

    `heading` is whether the heading exists at all. `body` is the section's MEANINGFUL content -
    comments stripped - or None when there is none to send. `boilerplate` is True for the specific
    case in between: the section has visible characters, so it is not simply unwritten, but every
    one of them is inside an HTML comment - it LOOKS filled in and carries nothing a spec could
    contradict. `heading=True, body=None, boilerplate=False` is the one truly ordinary case: the
    heading is there and genuinely nothing is written under it yet.
    """

    heading: bool
    body: str | None
    boilerplate: bool


def contracts(text: str) -> Contracts:
    """`Contracts` for one overview's text. ONE slice answers every question this reader asks, and
    this is the only place the slice arguments are written - two calls with the same arguments are
    two things to keep in step, and one of them drifts."""
    found = slice_section(text, CONTRACTS_HEADING, min_level=2, allow_trailing=True)
    if found is None:
        return Contracts(False, None, False)
    _, body = found
    # The heading LINE always makes `"".join(found)` non-empty, so emptiness is judged on the body
    # alone - a heading with nothing under it, not "a heading" (which is never nothing).
    if not body.strip():
        return Contracts(True, None, False)
    if not _COMMENT.sub("", body).strip():
        return Contracts(True, None, True)
    # Comments are stripped from what is CARRIED as well as from what is judged. Judging on the
    # stripped text and sending the raw section would ship the template's own instructional comment
    # into the observe pass under "these are the binding contracts the spec must not contradict" -
    # presenting "do not rename this heading" to the model as a contract. One text, one meaning.
    return Contracts(True, _COMMENT.sub("", "".join(found)).strip(), False)


def contracts_section(text: str) -> str | None:
    """The `## Contracts and Decisions` section of an overview, heading included, or None.

    Ends at the next heading of the SAME OR SHALLOWER level, so a contract written under its own
    `### ` subheading stays inside the section rather than truncating it - and the section still
    stops well short of the whole document, which is the extent the read path grants this reader.
    That level rule is `md_section.slice_section`'s; what stays here is the policy - the heading line
    is carried into the block, and a section with nothing under it BUT boilerplate is no context at
    all, same as one with nothing under it at all.
    """
    return contracts(text).body


def overview_state(feature_dir: Path) -> Contracts | None:
    """`contracts()` for the feature overview, or None when there is no readable `overview.md`.

    One read of one file answers everything `build()` needs. Reading it twice - once for the
    section, once to ask whether the heading was there at all - is the same file opened twice per
    spec gated, on the read path this module exists to hold the line on.

    None is a THIRD degraded shape, not a softer version of "not written yet": a feature with no
    readable overview at all loses the same half of `contradiction` as one with the wrong heading or
    a boilerplate-only section, and `build()` treats it exactly that way.
    """
    try:
        text = (feature_dir / "overview.md").read_text(encoding="utf-8")
    except OSError:
        return None
    return contracts(text)


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
    may have the heading with genuinely nothing under it yet. `degraded=True` means one of the three
    shapes named at the top of this module - no `## Contracts and Decisions` heading at all, that
    heading holding only boilerplate, or no readable `overview.md` at all - so `contradiction` loses
    half of what it is defined to check for every spec in the feature, not just this one. Each shape
    names its own remedy in its own note, because the caller carries that line verbatim. The caller
    decides what to do with the signal; this function only refuses to make it indistinguishable from
    "nothing to carry yet".
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

    state = overview_state(feature_dir)
    if state is None:
        degraded = True
        notes.append(
            f"DEGRADED: no readable overview.md in {feature_dir.name} — contradiction can only be "
            f"checked against the prior phase's card, never this feature's own contracts, for "
            f"EVERY spec in this feature, for as long as the overview stays missing. There is no "
            f"exemption for a feature that genuinely has none yet: write overview.md before writing "
            f"specs, or accept the degraded gate until you do."
        )
    elif state.body:
        parts.append(state.body)
        notes.append(
            f"included: {feature_dir.name}/overview.md — ## Contracts and Decisions only"
        )
    elif not state.heading:
        degraded = True
        notes.append(
            f"DEGRADED: no ## Contracts and Decisions heading in {feature_dir.name}/overview.md — "
            f"contradiction can only be checked against the prior phase's card, never this "
            f"feature's own contracts, for EVERY spec in this feature. "
            f"See docs/templates/overview.template.md."
        )
    elif state.boilerplate:
        degraded = True
        notes.append(
            f"DEGRADED: ## Contracts and Decisions in {feature_dir.name}/overview.md holds only "
            f"boilerplate (an HTML comment, e.g. the unfilled template) — nothing a spec could "
            f"contradict, the same gap as a missing heading, for EVERY spec in this feature."
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
    """Every feature under `docs/features/` whose overview cannot carry its contracts: a missing or
    unreadable `overview.md`, one with no `## Contracts and Decisions` heading, or one where that
    heading holds only boilerplate.

    This is the other half of the fix: `build()` reports degradation per spec gated, which only ever
    surfaces the defect one spec write at a time and only for a project someone is actively working
    on. This finds it directly, for every feature on disk, the moment an overview is written, edited,
    or never written at all - the heading contract the template already states
    (`docs/templates/overview.template.md`) made explicit and CHECKED rather than merely written down.

    Diff-scoped like every other check on this applicability boundary (`scripts/applicability.py`):
    a feature the current change did not touch is COUNTED on stderr, never blocked. That is not a
    grandfathering exception written for this issue - it is what lets a project with years of
    pre-rule overviews (clickup-agents included) adopt this check without every one of them failing
    CI on day one.

    **`gate_ci.sh` keeps it diff-scoped even under `--full`**, on `carried_items.py`'s precedent
    rather than `doc_read_path.py`'s: this obligation lands on a document class every consumer repo
    already has on disk, so a full audit wired into CI would fail its build over overviews written
    before the rule existed. `check(..., enforce_all=True)` - the `--all` flag - audits every
    feature regardless of scope, and is deliberately something a person runs, not something CI runs
    unconditionally.
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
                f"unknowable and no feature is enforced. Run `check --all` for a full audit.",
                file=sys.stderr,
            )
            return []

    problems: list[str] = []
    unenforced = 0
    for feature in sorted(p for p in features.iterdir() if p.is_dir()):
        overview = feature / "overview.md"
        # The FEATURE DIRECTORY, never `overview.md` itself: a missing overview can never appear as
        # a changed path, so scoping on the artifact would make that shape permanently
        # unenforceable. `applicability.touched` already answers "this path, or anything under it",
        # which is exactly the question — a second copy of the containment rule is the one that
        # drifts.
        in_scope = enforce_all or touched(feature, scope or set())

        try:
            text = overview.read_text(encoding="utf-8")
        except OSError as exc:
            message = (
                f"{feature}: no readable overview.md ({exc}) — the spec gate's CONTEXT block can "
                f"never carry this feature's contracts without one, so `contradiction` is checked "
                f"only against the prior phase's card, for every spec this feature ever gates, "
                f"until overview.md exists. There is no exemption for it: this feature has no phase "
                f"directory yet either, so there is nowhere for a recorded exception to live."
            )
        else:
            state = contracts(text)
            if not state.heading:
                message = (
                    f"{overview}: no ## Contracts and Decisions heading — the spec gate's CONTEXT "
                    f"block can never carry this feature's contracts, so `contradiction` is checked "
                    f"only against the prior phase's card, for every spec this feature ever gates. "
                    f"Add the heading (see docs/templates/overview.template.md)."
                )
            elif state.boilerplate:
                message = (
                    f"{overview}: ## Contracts and Decisions holds only boilerplate (an HTML "
                    f"comment, e.g. the unfilled template) — nothing a spec could contradict, the "
                    f"same gap as a missing heading."
                )
            else:
                continue

        if in_scope:
            problems.append(message)
        else:
            unenforced += 1

    report_unenforced(
        "spec_gate_context",
        unenforced,
        "with no usable ## Contracts and Decisions content - checked when that feature is next "
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
