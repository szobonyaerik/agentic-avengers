#!/usr/bin/env python3
"""A phase's *Done when*, in a form a gate can read - and the one decision it is read for.

## The defect

A phase could not end its own discovery loop (issue #115). `faithful-rep` phase 3 ran about 48
hours because every amendment round found a REAL defect, and a finding had exactly three
dispositions - `open`, `fixed`, `acknowledged` (waived) - none of which means *"this is real, it is
not this phase's to fix, and this phase is done"*. Waiving it would have said the finding did not
matter; fixing it widened the phase; leaving it open blocked the close. A human became the
terminator, judging each remaining item by whether it was needed to make the phase's *Done when*
true (`close-disclosures.md` § 8), and the pipeline had no way to make that judgement itself because
the *Done when* was a paragraph of prose in `plan.md`.

## What this module owns

**The structured half of a phase's *Done when*, and the decision "does this finding block it?"**

The prose stays exactly where it was - `plan.md`'s `- **Done when**:` bullet - because it is what a
human reads. Beside it, in the same phase section, a table gives each condition a stable id:

    | done-when | outcome |
    |-----------|---------|
    | DW-1 | `handstand_hold` holds for the whole clip - no pike, no tumble, no feet on the mat |
    | DW-2 | a cyclic clip loops from a real-frame cut, residual 2-9 mm, never an exact 0.0 |

The plan is written before any requirement exists, so it cannot name which requirements carry a
condition. The **spec writer** does that where it already declares everything else about a
requirement - on the requirement's own declaration line, in the same idiom as `binding:`:

    - R3.3.3 - `binding: e2e` - `done_when: DW-1, DW-3` - one-way reps play once, forward, ...

A requirement with no `done_when:` tag carries no condition. That is a deliberate choice by the
writer of the spec - the same writer who chose the requirement's `binding:` - and it is what makes a
finding against that requirement deferrable.

## The decision, and where it fails closed

`decide(phase_dir, spec_ids)` answers one question: **may a finding against these requirement ids be
deferred out of this phase?** Three answers:

- **BLOCKS** - some id is tagged with a condition of this phase's *Done when*. The finding stays
  this phase's to fix, whatever anyone would prefer. Phase 3's amendment A3 caught a clip that
  contained no exercise; it was against R3.2.4, the cyclic cut, which carries the *Done when*'s
  cyclic condition, and it would have been refused here.
- **CLEAR** - no id is tagged. The finding may be deferred (`scripts/carried_items.py defer`).
- **UNDECIDABLE**, and every shape of it names its remedy rather than guessing:
  - the plan has no table for this phase - the *Done when* is prose only, so nothing here can say
    what a finding would block. The remedy is the table, not a guess about the prose;
  - a condition the plan declares is carried by **no** requirement in the phase's specs - a *Done
    when* nothing carries could not block anything, and reading that as "clear" would make every
    finding deferrable by forgetting to tag one requirement;
  - a spec tags a condition the plan never declared - a tag pointing at nothing.

Undecidable is never "clear". The remedy for a prose-only *Done when* is to structure it, and a
phase written before this rule existed simply cannot defer - which changes nothing else about how
it closes (§3a: a rule binds what is open, and an old phase never had deferral to lose).

## A condition no gate can evaluate says so, and the pass repeats it (issue #116)

Phase 3's deciding condition is what a person SEES: the captain can watch `handstand_hold` hold, no
pike, no tumble, no feet on the mat. The verifier proved execution, ran 162 tests and recorded 23
runs, and **nothing rendered during verification**. `verdict: pass` was honest about its tests and
silent about its blind spot, so it read as though the criterion had been checked; two defects
shipped through that silence - `dips` renders one bar through the athlete's torso,
`australian_pullups` renders its bar end-on at 7.7 degrees - both found by a person looking, at
phase close, not by a gate.

So the row that DEFINES a condition also declares which of two it is, from a closed vocabulary:

    | done-when | outcome | verification |
    |-----------|---------|--------------|
    | DW-1 | `handstand_hold` holds - no pike, no tumble, no feet on the mat | human-observed |
    | DW-2 | a cyclic clip loops from a real-frame cut, residual 2-9 mm | gate-verifiable |

`declaration_problems` holds every row to that declaration, and `disclosure_problems` holds a
`pass` to carrying each `human-observed` condition verbatim in `unverified_by_gate`, with the same
list on the contract card. Both are wired into `verifier_precheck.py`, the mechanical check that
already decides verdicts, on the applicability boundary: a plan this change does not write is
counted and named, never blocked, and a prose-only *Done when* is left exactly where #115 left it.

## What this does NOT decide, said rather than implied

It reads ids, never prose: a finding whose `spec_id` is tagged blocks and one whose is not does
not, and whether the tagging is RIGHT is the spec writer's claim, gated with the rest of the spec.
A finding with no requirement id at all - a defect found by watching a render, belonging to a stage
outside the phase - is decided as `clear` when the *Done when* is fully bound, because the only
mechanical link between a finding and a condition is the requirement id; the caller says so
explicitly (`--no-requirement`) rather than by omitting the ids.

Usage:
    done_when.py show <phase-dir>        the phase's conditions and which requirements carry each
    done_when.py decide <phase-dir> [--spec-id R<n>.<k>.<m> ...] [--no-requirement]
                                         exit 0 clear, 1 blocks (names the condition), 2 undecidable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402
from md_section import slice_section  # noqa: E402
from requirement_cap import DECLARATION, _continuation  # noqa: E402
from spec_gate_context import layout  # noqa: E402

CLEAR = 0
BLOCKS = 1
UNDECIDABLE = 2

#: A phase heading in `plan.md`: `### Phase 3 — real-frames`. This is the ONE owner of that shape;
#: `pipeline_state.py` imports it to count the phases a plan commits to, and this module uses it to
#: find the section a phase's *Done when* lives in. Two copies of "what a phase heading looks like"
#: would drift the moment one of them learned a new dash.
PLAN_PHASE_HEADING = re.compile(
    r"^###\s*Phase\s+(\d+)\s*[—–-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)


def planned_phases(plan_text: str) -> list[tuple[int, str]]:
    """(number, title) for every phase heading in a plan, in document order."""
    return [(int(num), title) for num, title in PLAN_PHASE_HEADING.findall(plan_text)]


def slugify(title: str) -> str:
    """A plan title as a phase directory slug: `real-frames` from `real-frames`, `a-b` from `A / B`."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "phase"


#: The table's header cell - the contract with `docs/templates/plan.template.md`.
TABLE_HEADER = "done-when"

#: The header cell of the condition's THIRD column, and the closed vocabulary it may hold (issue
#: #116). `faithful-rep` phase 3's deciding condition was what a person sees on screen - the captain
#: can watch `handstand_hold` hold - and the verifier ran 162 tests over 23 recorded runs with
#: nothing rendered. The verdict was `pass`: honest about its tests, silent about its blind spot, so
#: it read as if the criterion had been checked. Two defects shipped through that silence, both found
#: by a person looking at phase close.
#:
#: So a condition says which of the two it is, on the row where it is defined. The set is CLOSED and
#: small for the reason every closed set here is closed: a value the table does not know is a hard
#: failure naming what was invented, because guessing `gate-verifiable` hides a criterion nobody
#: checks and guessing `human-observed` invents a disclosure nobody wrote.
VERIFICATION_HEADER = "verification"
GATE_VERIFIABLE = "gate-verifiable"
HUMAN_OBSERVED = "human-observed"
VERIFICATION: tuple[str, ...] = (GATE_VERIFIABLE, HUMAN_OBSERVED)

#: The verdict's field, and the one place it is spelled. It is an extension to the frozen verdict
#: schema made AT the schema (`skills/verifier-triage`), and it holds each `human-observed`
#: condition verbatim plus the statement that this verdict did not verify it - so `pass` reads as
#: "pass on everything I can check, and here is what I cannot".
UNVERIFIED_FIELD = "unverified_by_gate"
VERIFIED_KEY = "verified_by_this_verdict"

#: A condition id. `DW-1` is the template's shape; any id a later stage can grep for qualifies, and a
#: template placeholder (`DW-<n>`) does not.
CONDITION_ID = re.compile(r"\A[A-Za-z][A-Za-z0-9._-]*\Z")

#: The `done_when:` tag on a requirement's declaration line (or, for a heading-style declaration, in
#: the block below it - the same layout rule `requirement_cap.declared_bindings` follows for
#: `binding:`). Both spellings are accepted because both are how people write it.
#:
#: The capture is a COMMA-separated id list and stops at the first thing that is not one, because a
#: declaration line continues into prose and this project writes that prose with plain dashes: a
#: class that swallowed spaces and hyphens read `done_when: DW-1 - one-way reps play once` as seven
#: condition ids, five of which name nothing the plan declares, so the whole phase went UNDECIDABLE
#: and could never defer. `done_when: DW-1, DW-3` is the documented shape (docs/templates/
#: spec.template.md) and the only one a tag may take; a space-separated list is indistinguishable
#: from the prose that follows it.
DONE_WHEN_TAG = re.compile(
    r"done[_-]when:[ \t`]*([A-Za-z0-9._-]+(?:[ \t]*,[ \t]*[A-Za-z0-9._-]+)*)",
    re.IGNORECASE,
)

#: The phase directory name's number: `3-real-frames` -> 3.
PHASE_NUMBER = re.compile(r"^(\d+)-")


class Condition(NamedTuple):
    """One row of the *Done when* table.

    `verification` is the row's own declaration (issue #116), and it has three states rather than
    two: one of `VERIFICATION` is a declaration; `""` is a table that HAS the column and a row that
    left it blank; and `None` is a table with no such column at all - the shape every plan written
    before this rule has, which is counted rather than held (§3a).
    """

    id: str
    outcome: str
    verification: str | None = None


class Decision(NamedTuple):
    """The answer to "may a finding against these ids leave this phase?"."""

    code: int
    reason: str
    blocking: tuple[str, ...] = ()
    checked: tuple[str, ...] = ()

    def describe(self) -> str:
        return self.reason


class DoneWhenError(Exception):
    """The *Done when* could not be read well enough to decide. Always UNDECIDABLE."""


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def phase_number(phase_dir: Path) -> int | None:
    found = PHASE_NUMBER.match(Path(phase_dir).name)
    return int(found.group(1)) if found else None


def plan_section(plan_text: str, number: int) -> str | None:
    """The body of `### Phase <number> — <title>` in a plan, or None when the plan has no such heading.

    The section ends at the next heading of the same or shallower level, which is
    `md_section.slice_section`'s rule. `Phase 3` does not match `Phase 30`: the heading is matched
    with a word boundary after the number.
    """
    found = slice_section(plan_text, f"Phase {number}", allow_trailing=True)
    return None if found is None else found[1]


def conditions(section: str) -> list[Condition] | None:
    """The conditions a phase section's table declares, or None when the section has no table.

    None and [] are different answers: no table is the prose-only state (undecidable), while a table
    with a header and no rows says the phase declared it has no structured conditions - which is
    equally undecidable for a deferral, and `decide` says so, but the two read differently.

    The scan ends at the first line that is not a table row after the header, because a markdown
    table ends there. Running to the end of the phase section instead made every row of any LATER
    table under the same heading - a spec list, a risk table - a phantom condition that no
    requirement carries, so the phase read as undecidable over a plan that declared its conditions
    correctly.
    """
    out: list[Condition] = []
    seen_header = False
    column: int | None = None
    for line in section.splitlines():
        cells = _cells(line)
        if not cells:
            if seen_header:
                break
            continue
        head = cells[0].strip("*`_ \t").lower()
        if head == TABLE_HEADER:
            seen_header = True
            # Found by NAME, never by position: a table that gains a column keeps reading, and a
            # plan written before the column existed yields None here rather than a wrong cell.
            column = next(
                (
                    index
                    for index, cell in enumerate(cells)
                    if cell.strip("*`_ \t").lower() == VERIFICATION_HEADER
                ),
                None,
            )
            continue
        if not seen_header:
            continue
        if set(cells[0]) <= {"-", ":", " "}:
            continue
        identifier = cells[0].strip("*`_ \t")
        if "<" in identifier or ">" in identifier or not CONDITION_ID.match(identifier):
            continue
        verification = None
        if column is not None:
            raw = cells[column] if column < len(cells) else ""
            verification = raw.strip("*`_ \t").lower()
        out.append(
            Condition(identifier, cells[1] if len(cells) > 1 else "", verification)
        )
    return out if seen_header else None


def plan_path(phase_dir: Path) -> Path | None:
    """The `plan.md` a phase's *Done when* lives in, or None when the phase is outside the layout.

    One resolver, so the reader below and the caller that decides whether the plan is inside this
    change's scope (`verifier_precheck.main`) can never disagree about which file was read.
    """
    where = layout(Path(phase_dir))
    return None if where is None else where[0] / "plan.md"


def declared_conditions(phase_dir: Path) -> list[Condition] | None:
    """The conditions `plan.md` declares for this phase, or None when it declares no table.

    Reads the plan through the phase's own location so `check`, the hook and a human at a shell all
    resolve the same file. A phase outside the layout, a missing plan and a plan with no heading for
    this phase all read as "no table": nothing structured was declared.
    """
    plan = plan_path(phase_dir)
    number = phase_number(phase_dir)
    if plan is None or number is None:
        return None
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return None
    section = plan_section(text, number)
    if section is None:
        return None
    return conditions(section)


def tagged_requirements(spec_text: str) -> dict[str, set[str]]:
    """{requirement id: the condition ids its declaration tags} for one spec, untagged ids omitted.

    Where the tag is looked for is `requirement_cap.declared_bindings`' layout rule, reused rather
    than restated: on the declaration line, or - for a heading-style declaration - in the first block
    below it, never past the next declaration or heading.
    """
    lines = spec_text.splitlines()
    starts = [i for i, line in enumerate(lines) if DECLARATION.match(line)]
    out: dict[str, set[str]] = {}
    for position, index in enumerate(starts):
        match = DECLARATION.match(lines[index])
        found = DONE_WHEN_TAG.search(match.group("rest"))
        if found is None:
            limit = starts[position + 1] if position + 1 < len(starts) else len(lines)
            found = DONE_WHEN_TAG.search(
                "\n".join(_continuation(lines, index + 1, limit))
            )
        if found is None:
            continue
        ids = {
            token.strip("*`_ \t")
            for token in re.split(r"[,\s]+", found.group(1))
            if token.strip("*`_ \t")
        }
        if ids:
            out.setdefault(match.group(1), set()).update(ids)
    return out


def bound_requirements(phase_dir: Path) -> dict[str, set[str]]:
    """{condition id: requirement ids tagged with it} over every spec in the phase."""
    out: dict[str, set[str]] = {}
    for spec in sorted(Path(phase_dir).glob("specs/*/spec.md")):
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError as exc:
            raise DoneWhenError(f"cannot read {spec}: {exc}") from exc
        for requirement, tags in tagged_requirements(text).items():
            for tag in tags:
                out.setdefault(tag, set()).add(requirement)
    return out


def human_observed(phase_dir: Path) -> list[Condition]:
    """The conditions this phase declares no gate can evaluate. Empty when it declares none."""
    declared = declared_conditions(phase_dir) or []
    return [c for c in declared if c.verification == HUMAN_OBSERVED]


def declaration_problems(phase_dir: Path) -> list[str]:
    """Every *Done when* row that does not say whether a gate can evaluate it (issue #116).

    Two shapes, one finding each, because their remedies differ: a table with **no** `verification`
    column is a plan written before this rule, and a column with a blank or invented cell is a plan
    written after it that left the question open. A phase with no table at all is UNDECIDABLE as
    issue #115 defined it and is not held here: the remedy for a prose-only *Done when* is the
    table, which is #115's finding to make and not a second copy of it.
    """
    declared = declared_conditions(phase_dir)
    if not declared:
        return []
    name = Path(phase_dir).name
    out: list[str] = []
    for condition in declared:
        if condition.verification in VERIFICATION:
            continue
        if condition.verification is None:
            out.append(
                f"{name}: *Done when* {condition.id} declares no `{VERIFICATION_HEADER}` - "
                f"plan.md's table for this phase has no such column, so nothing says whether a gate "
                f"can evaluate '{condition.outcome}'. A criterion no gate can express passes "
                f"unexamined under a verdict that reads as complete (issue #116). Add the column "
                f"(docs/templates/plan.template.md) and give every row "
                f"`{GATE_VERIFIABLE}` or `{HUMAN_OBSERVED}`."
            )
        elif not condition.verification:
            out.append(
                f"{name}: *Done when* {condition.id} leaves its `{VERIFICATION_HEADER}` cell "
                f"empty. Blank is not `{GATE_VERIFIABLE}`: say which it is."
            )
        else:
            out.append(
                f"{name}: *Done when* {condition.id} declares `{condition.verification}`, which is "
                f"not one of {', '.join(VERIFICATION)}. The vocabulary is closed; a value it does "
                f"not know is named rather than guessed at, because guessing `{GATE_VERIFIABLE}` "
                f"hides a criterion nobody checks and guessing `{HUMAN_OBSERVED}` invents a "
                f"disclosure nobody wrote."
            )
    return out


def _entries(verdict: dict) -> tuple[list[dict], str | None]:
    """(`unverified_by_gate`'s entries, a finding when the field is not the shape it must be)."""
    raw = verdict.get(UNVERIFIED_FIELD, [])
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        return [], (
            f"`{UNVERIFIED_FIELD}` is not a list of entries. It is the field a reader of `pass` "
            f"consults for what this verdict did NOT check; a shape nothing can read discloses "
            f"nothing."
        )
    return raw, None


def disclosure_problems(phase_dir: Path) -> list[str]:
    """A `pass` must carry every `human-observed` condition, and the card beside it must too.

    The declaration alone would be a promise: `pass` is read on the verdict, and the next phase
    reads the contract card. So the same closed list has to appear in both, and the outcome is
    carried **verbatim** - a paraphrase is where a limitation becomes a caveat.

    Only a `pass` is held. A `fail` claims nothing about the phase's conditions, and a verdict that
    is missing or unreadable is `verifier_precheck.verdict_problems`' finding, made once, there.
    A card that has not been written yet owes nothing: it is checked when it exists.
    """
    owed = {c.id: c.outcome for c in human_observed(phase_dir)}
    if not owed:
        return []
    target = Path(phase_dir) / "verdict.json"
    try:
        verdict = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(verdict, dict) or verdict.get("verdict") != "pass":
        return []
    entries, malformed = _entries(verdict)
    if malformed is not None:
        return [f"{target}: {malformed}"]
    stated = {str(entry.get("id")): entry for entry in entries}
    out: list[str] = []
    for identifier, outcome in owed.items():
        entry = stated.get(identifier)
        if entry is None:
            out.append(
                f"{target}: `verdict: pass` over a *Done when* that declares {identifier} "
                f"`{HUMAN_OBSERVED}`, and `{UNVERIFIED_FIELD}` does not name it. A pass that is "
                f"silent about its blind spot reads as one that checked it. Carry the condition: "
                f'{{"id": "{identifier}", "outcome": "<the plan\'s outcome verbatim>", '
                f'"{VERIFIED_KEY}": false}}.'
            )
            continue
        if entry.get("outcome") != outcome:
            out.append(
                f"{target}: `{UNVERIFIED_FIELD}` states {identifier} as "
                f"'{entry.get('outcome')}', and plan.md's row says '{outcome}'. It is carried "
                f"verbatim, because a summary is where a limitation is softened into a caveat."
            )
        if entry.get(VERIFIED_KEY) is not False:
            out.append(
                f"{target}: `{UNVERIFIED_FIELD}` entry {identifier} does not say "
                f"`{VERIFIED_KEY}: false`. The field exists to state what this verdict did not "
                f"establish; an entry that claims otherwise is the silence it replaces."
            )
    card = Path(phase_dir) / "handover.md"
    if card.is_file():
        try:
            text = card.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return out + [f"{card}: unreadable ({exc}) - UNDECIDABLE, not clean."]
        missing = [identifier for identifier in owed if identifier not in text]
        if missing:
            out.append(
                f"{card}: the card does not name {', '.join(missing)}, which this phase's verdict "
                f"declares it did not verify. The card is what the next phase reads; a disclosure "
                f"only in verdict.json reaches nobody."
            )
    return out


def decide(phase_dir: Path, spec_ids: list[str]) -> Decision:
    """May a finding against `spec_ids` be deferred out of this phase? See the module docstring."""
    declared = declared_conditions(phase_dir)
    if declared is None:
        return Decision(
            UNDECIDABLE,
            f"{Path(phase_dir).name}: its *Done when* in plan.md is prose only - no "
            f"`| {TABLE_HEADER} | outcome |` table under its phase heading - so nothing can decide "
            f"what a finding would block. Add the table beside the prose (docs/templates/"
            f"plan.template.md) and tag the requirements that carry each condition with "
            f"`done_when: <id>`; until then this phase cannot defer a finding, and nothing else "
            f"about how it closes changes.",
        )
    if not declared:
        return Decision(
            UNDECIDABLE,
            f"{Path(phase_dir).name}: the *Done when* table in plan.md declares no condition - "
            f"only a header. A phase that is done when nothing holds cannot say what a finding "
            f"blocks. Give each condition a row.",
        )
    bound = bound_requirements(phase_dir)
    known = {condition.id for condition in declared}
    uncarried = [condition.id for condition in declared if not bound.get(condition.id)]
    if uncarried:
        return Decision(
            UNDECIDABLE,
            f"{Path(phase_dir).name}: *Done when* condition(s) {', '.join(uncarried)} are carried "
            f"by NO requirement in this phase's specs. A condition nothing carries could block "
            f"nothing, and reading that as clear would make every finding deferrable by forgetting "
            f"to tag one requirement. Tag the requirement(s) that make each condition true with "
            f"`done_when: <id>` on their declaration line.",
        )
    unknown = sorted(tag for tag in bound if tag not in known)
    if unknown:
        return Decision(
            UNDECIDABLE,
            f"{Path(phase_dir).name}: spec requirement(s) are tagged `done_when: "
            f"{', '.join(unknown)}`, which plan.md's table for this phase never declares. A tag "
            f"pointing at nothing decides nothing; declare the condition or fix the tag.",
        )
    checked = tuple(condition.id for condition in declared)
    blocking = tuple(
        f"{condition.id} (carried by {', '.join(sorted(bound[condition.id] & set(spec_ids)))})"
        for condition in declared
        if bound[condition.id] & set(spec_ids)
    )
    if blocking:
        return Decision(
            BLOCKS,
            f"{Path(phase_dir).name}: a finding against {', '.join(sorted(set(spec_ids)))} blocks "
            f"this phase's *Done when* - {'; '.join(blocking)}. It stays this phase's to fix. "
            f"Deferral is not available for it, and lowering a threshold, weakening a check or "
            f"deleting a test to clear it is not either.",
            blocking,
            checked,
        )
    return Decision(
        CLEAR,
        f"{Path(phase_dir).name}: none of {', '.join(sorted(set(spec_ids))) or '(no requirement)'} "
        f"carries a *Done when* condition ({', '.join(checked)} checked).",
        (),
        checked,
    )


def _show(phase_dir: Path) -> int:
    declared = declared_conditions(phase_dir)
    if declared is None:
        print(
            f"{Path(phase_dir).name}: no *Done when* table in plan.md (prose only).",
            file=sys.stderr,
        )
        return UNDECIDABLE
    bound = bound_requirements(phase_dir)
    for condition in declared:
        carriers = ", ".join(sorted(bound.get(condition.id, ()))) or "(no requirement)"
        print(
            f"{condition.id} [{condition.verification or 'undeclared'}]: {condition.outcome}\n"
            f"    carried by: {carriers}"
        )
    for line in declaration_problems(phase_dir):
        print(line, file=sys.stderr)
    for tag in sorted(set(bound) - {condition.id for condition in declared}):
        print(f"{tag}: tagged in a spec but declared by no row", file=sys.stderr)
    return CLEAR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_show = sub.add_parser("show")
    p_show.add_argument("phase_dir", type=Path)
    p_decide = sub.add_parser("decide")
    p_decide.add_argument("phase_dir", type=Path)
    p_decide.add_argument("--spec-id", action="append", default=[])
    p_decide.add_argument(
        "--no-requirement",
        action="store_true",
        help="the finding belongs to no requirement of this phase - said explicitly, never by "
        "omitting --spec-id",
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "show":
            return _show(args.phase_dir)
        if not args.spec_id and not args.no_requirement:
            print(
                "[done_when] name the requirement id(s) the finding is against (--spec-id), or say "
                "--no-requirement when it belongs to none in this phase. Silence is not either.",
                file=sys.stderr,
            )
            return UNDECIDABLE
        decision = decide(args.phase_dir, args.spec_id)
        print(decision.describe(), file=sys.stderr if decision.code else sys.stdout)
        return decision.code
    except DoneWhenError as exc:
        print(f"[done_when] {exc}", file=sys.stderr)
        return UNDECIDABLE
    except Exception as exc:  # noqa: BLE001 - an undecidable check is never a clear one
        print(f"[done_when] the decision could not be made: {exc!r}", file=sys.stderr)
        return UNDECIDABLE


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
