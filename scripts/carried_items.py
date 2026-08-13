#!/usr/bin/env python3
"""Carried items - a handover's forward-looking claims, discharged by the next phase or not at all.

## The defect

Phase 8 of one measured feature wrote down, verbatim, that caller-supplied identifiers would become
a problem in phases 9 to 12. Phase 9 was the first such caller and **shipped exactly that defect**: a
user-controlled path segment interpolated unencoded, so a name containing `?` or `#` retargets the
write. The review gate caught it after verification had already passed.

The prediction was correct, specific, actionable, and **became nothing** - no spec line, no test, no
check. The same phase produced the mirror defect: a handover asserting a protection its own phase had
deleted, which then propagated into the next phase's instructions as binding. In one direction the
record over-claimed, in the other it under-delivered, and **both passed every check**, because a
handover's forward-looking claims were prose and prose is not owed an answer.

## The fix uses the slot that already existed

`docs/templates/handover.template.md` has carried a `## Open items` table with an `id` column since
the contract card was introduced - measured, and for a measured reason: of 8 items carried as prose
across 53.6 KB, exactly one was ever picked up by a later phase, and it was the id that carried it,
not the story. So this does not add a second mechanism. It **widens that section to hold
forward-looking claims alongside open findings, and makes it binding**:

1. **Declared.** A phase does not close unless its own card's `## Open items` section states what it
   carries - a row per item, or an explicit `none`. Silence is not `none`; silence is what phase 8's
   prediction was written into.
2. **Discharged.** A phase does not close while an item on the **immediately prior** phase's card has
   no discharge record here. Three ways to discharge one, and all three are answers: `built` into a
   spec, `tested`, or `declined` with a stated reason. An item that still applies to a later phase is
   `declined` and **re-carried on this phase's own card**, which is what makes a multi-phase claim
   survive without being owed to every phase at once.

An id is scoped by the phase directory that declared it, so `OBS-1` on phase 8's card and `OBS-1` on
phase 9's are different items and the ids already in use keep working unchanged.

Which phase is "immediately prior" is **not decided here** - `spec_gate_context.prior_phase` owns it,
because the spec gate's CONTEXT block and this ledger must agree about which card is in force.

`carried.json` lives beside `verdict.json` in the phase that does the discharging, mirroring
`amendments.json`: the record belongs to the phase that acted, not to the phase that asked.

Usage:
    carried_items.py declared <phase-dir>   what THIS phase's card carries forward
                                            exit 1 when the card states nothing at all
    carried_items.py list <phase-dir>       the prior phase's items this phase owes an answer to
    carried_items.py discharge <phase-dir> <item-id> --as built|tested|declined
                                            [--by <spec/test/requirement>] [--reason-file <f>]
    carried_items.py due <phase-dir>        exit 1 when any owed item is undischarged
    carried_items.py check [--root .] [--all]
                                            both obligations over every phase, for CI. Diff-scoped
                                            by default; `--all` audits every phase.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from doc_read_path import changed_paths  # noqa: E402
from spec_gate_context import layout, prior_phase  # noqa: E402

OK = 0
OWED = 1
ERROR = 2

FILENAME = "carried.json"

#: The section of the contract card this reads. It is the slot the template already shipped; the
#: heading is the contract with `docs/templates/handover.template.md` and with
#: `skills/phase-handover`, and `tests/test_carried_items.py` pins that all three still agree.
SECTION_HEADING = "Open items"

#: How an item was answered. `declined` is a real answer and is deliberately as valid as the other
#: two: an item that must not be built in this phase still has to be looked at and dismissed on the
#: record, which is the whole difference between this and a prediction written into prose.
DISCHARGES = ("built", "tested", "declined")

#: An item id: something a later phase can grep for. `OBS-1`, `FWD-2`, `CARRY-8.1` all qualify; a
#: template placeholder (`OBS-<n>`) does not, and neither does a prose fragment.
ITEM_ID = re.compile(r"\A[A-Za-z][A-Za-z0-9._/-]*\Z")

#: Markdown emphasis around an id, stripped before matching. A writer who bolds or code-quotes the id
#: has written a real item, and a parser that went blind on `**OBS-1**` would drop it silently - the
#: same failure `requirement_cap.py` suffered when a table-formatted spec counted zero requirements
#: and the cap, its only counterweight, never fired.
EMPHASIS = "*`_ \t"

#: The explicit "this phase carries nothing" answer. Silence is not this.
NONE = "none"

READERS = [
    "avenger-spec-writer @ per phase, to see what it still owes an answer to",
    "phase-handover @ per phase",
]


class CarriedError(Exception):
    """A malformed card, ledger or request. Always fails the caller closed."""


class Item(NamedTuple):
    """One thing a phase's card carries forward."""

    id: str
    kind: str
    title: str
    phase: str

    def describe(self) -> str:
        return f"  {self.id} [{self.kind}] ({self.phase}): {self.title}"


def _cells(line: str) -> list[str]:
    """The cells of a markdown table row, or [] when the line is not one."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def section_body(text: str, heading: str = SECTION_HEADING) -> str | None:
    """The body under the card's items heading, up to the next heading of the same or shallower level.

    The template writes it as `## Open items` and `skills/phase-handover` renders it as `### Open
    items`, so the level is read from whatever the card actually used rather than pinned.
    """
    start = re.search(
        rf"^(\#{{1,6}})[ \t]*{re.escape(heading)}[ \t]*$", text, re.IGNORECASE | re.MULTILINE
    )
    if not start:
        return None
    rest = text[start.end():]
    end = re.compile(rf"^#{{1,{len(start.group(1))}}}[ \t]+", re.MULTILINE).search(rest)
    return rest[: end.start()] if end else rest


def _is_placeholder(cells: list[str]) -> bool:
    """A row straight out of the template, never a real item."""
    return any("<" in cell or ">" in cell for cell in cells)


def parse_items(body: str, phase: str) -> tuple[list[Item], bool]:
    """(the items in a card's section, whether it explicitly says `none`).

    Header rows, separator rows and template placeholders are skipped. A row whose first cell is not
    a usable id is skipped too rather than raising: a card may legitimately carry a sentence above
    its table, and a parser that failed on prose would make the section harder to write than the
    prose it replaces.
    """
    items: list[Item] = []
    says_none = False
    for line in body.splitlines():
        bare = line.strip().strip("|").strip().lower()
        if bare == NONE:
            says_none = True
            continue
        cells = _cells(line)
        if not cells or _is_placeholder(cells):
            continue
        identifier = cells[0].strip(EMPHASIS)
        if identifier.lower() in {"id", NONE}:
            says_none = says_none or identifier.lower() == NONE
            continue
        if set(identifier) <= {"-", ":", " "} or not ITEM_ID.match(identifier):
            continue
        kind = cells[1] if len(cells) > 2 else "item"
        title = cells[2] if len(cells) > 2 else (cells[1] if len(cells) > 1 else "")
        items.append(Item(identifier, kind or "item", title, phase))
    return items, says_none


def declared(phase_dir: Path) -> tuple[list[Item], bool, bool]:
    """(items, says `none`, the section exists at all) for one phase's own card.

    A phase with no `handover.md` yet declares nothing and the section is absent - that is the state
    a phase is in *while* it is being written, and it is not an error here.
    """
    card = Path(phase_dir) / "handover.md"
    try:
        text = card.read_text(encoding="utf-8")
    except OSError:
        return [], False, False
    body = section_body(text)
    if body is None:
        return [], False, False
    items, says_none = parse_items(body, Path(phase_dir).name)
    return items, says_none, True


def owed(phase_dir: Path) -> list[Item]:
    """Everything the immediately prior phase's card carries, which this phase owes an answer to.

    An unresolvable layout, a first phase, or a prior card with nothing on it all mean nothing is
    owed. None of those is an error: a feature's first phase inherits nothing by construction.
    """
    where = layout(Path(phase_dir))
    if where is None:
        return []
    feature_dir, phase = where
    previous = prior_phase(feature_dir, phase)
    if previous is None:
        return []
    items, _says_none, _present = declared(previous)
    return items


def path_for(phase_dir: Path) -> Path:
    return Path(phase_dir) / FILENAME


def load(phase_dir: Path) -> dict:
    """This phase's discharge ledger, or an empty one. An unreadable ledger is an error, not empty.

    Reading a corrupt ledger as "nothing discharged" would be the safe direction, but reading it as
    "everything discharged" would not, and the two are one typo apart. So it refuses instead.
    """
    target = path_for(phase_dir)
    if not target.is_file():
        return {"phase": Path(phase_dir).name, "readers": list(READERS), "discharges": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CarriedError(f"cannot read {target}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("discharges"), list):
        raise CarriedError(f"{target} is not a carried-items ledger")
    return data


def save(phase_dir: Path, ledger: dict) -> Path:
    """Write the ledger, always carrying its own `readers:` declaration."""
    ledger["readers"] = list(READERS)
    target = path_for(phase_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return target


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discharge(
    phase_dir: Path, item_id: str, how: str, by: str | None = None, reason: str | None = None
) -> dict:
    """Answer one carried item. Refuses an item the prior card never declared.

    That refusal is load-bearing: a typo in an id would otherwise record a discharge that satisfies
    nothing while the real item stays owed - or, worse, read as an answer to a question nobody asked.
    """
    if how not in DISCHARGES:
        raise CarriedError(f"{how!r} is not one of {', '.join(DISCHARGES)}")
    available = {item.id: item for item in owed(phase_dir)}
    if item_id not in available:
        raise CarriedError(
            f"the prior phase's card declares no item {item_id!r}. It carries: "
            f"{', '.join(sorted(available)) or '(nothing)'}."
        )
    if how == "declined":
        if not (reason or "").strip():
            raise CarriedError(
                "declining an item needs a stated reason - declining IS an answer, and an answer "
                "with no reason is the silence this ledger exists to remove."
            )
    elif not (by or "").strip():
        raise CarriedError(
            f"discharging an item as {how!r} needs `--by`: the spec, requirement id or test that "
            f"now covers it. Naming it is what makes the discharge checkable later."
        )

    ledger = load(phase_dir)
    ledger["discharges"] = [d for d in ledger["discharges"] if d.get("item") != item_id]
    record = {
        "item": item_id,
        "from_phase": available[item_id].phase,
        "as": how,
        "by": (by or "").strip() or None,
        "reason": (reason or "").strip() or None,
        "at": _now(),
    }
    ledger["discharges"].append(record)
    ledger["phase"] = Path(phase_dir).name
    save(phase_dir, ledger)
    return record


def undischarged(phase_dir: Path) -> list[Item]:
    """Items the prior phase carried that this phase has not answered."""
    answered = {d.get("item") for d in load(phase_dir)["discharges"]}
    return [item for item in owed(phase_dir) if item.id not in answered]


def phase_problems(phase_dir: Path) -> list[str]:
    """Both obligations for one closed phase, as lines. Empty means clean."""
    out: list[str] = []
    items, says_none, present = declared(phase_dir)
    if not present:
        out.append(
            f"{phase_dir}/handover.md: no `## {SECTION_HEADING}` section - the card does not say "
            f"what it carries forward. A row per item, or an explicit `none` row."
        )
    elif not items and not says_none:
        out.append(
            f"{phase_dir}/handover.md: the `## {SECTION_HEADING}` section states neither an item "
            f"nor an explicit `none`. Silence is not none."
        )
    for item in undischarged(phase_dir):
        out.append(f"{phase_dir}: {item.id} ({item.phase}) has no answer here - {item.title}")
    return out


def closed_phases(root: Path) -> list[Path]:
    """Every phase that has written a contract card, in path order."""
    return sorted(
        card.parent for card in Path(root).glob("docs/features/*/phases/*/handover.md")
    )


def check(root: Path, *, enforce_all: bool = False) -> list[str]:
    """Both obligations across the repository. **Diff-scoped unless `enforce_all`.**

    The scoping is not a softening, it is what makes the rule adoptable. This obligation lands on a
    document class every consumer repo already has on disk, so a full audit would fail CI over cards
    written years before the rule existed - the hostage failure `doc_read_path.check_artifacts` and
    `verifier_precheck` are already scoped against, whose `changed_paths` this reuses rather than
    re-implementing. Nothing is lost by it: `scripts/hook_verifier.sh` enforces both obligations on
    the phase being closed, and a phase being closed is a phase the diff touches by construction, so
    every phase closed from here on is held to the rule whatever CI audits.

    When git cannot say what changed the scope is unknowable, so nothing is enforced and that is said
    out loud rather than falling back to enforcing everything.
    """
    phases = closed_phases(root)
    if not phases:
        print(f"[carried_items] no phase contract cards under {root} - nothing to check",
              file=sys.stderr)
        return []

    scope: set[Path] | None = None
    if not enforce_all:
        scope = changed_paths(Path(root))
        if scope is None:
            print(
                f"[carried_items] git cannot say what changed under {root}, so the scope is "
                f"unknowable and no phase is checked. Run `check --all` for a full audit.",
                file=sys.stderr,
            )
            return []

    problems: list[str] = []
    unenforced = 0
    for phase in phases:
        found = phase_problems(phase)
        if not found:
            continue
        resolved = phase.resolve()
        if enforce_all or (
            scope is not None
            and any(changed == resolved or resolved in changed.parents for changed in scope)
        ):
            problems.extend(found)
        else:
            unenforced += 1
    if unenforced:
        print(
            f"[carried_items] {unenforced} phase(s) predate this rule and are not enforced - they "
            f"are checked when you next change them.",
            file=sys.stderr,
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    for name in ("declared", "list", "due"):
        p = sub.add_parser(name)
        p.add_argument("phase_dir", type=Path)

    p_discharge = sub.add_parser("discharge")
    p_discharge.add_argument("phase_dir", type=Path)
    p_discharge.add_argument("item_id")
    p_discharge.add_argument("--as", dest="how", required=True, choices=list(DISCHARGES))
    p_discharge.add_argument(
        "--by", help="the spec, requirement id or test that now covers it (built/tested)"
    )
    p_discharge.add_argument(
        "--reason-file",
        help="path to a file holding the reason for declining. A reason is author-written prose, "
        "and prose belongs in a file the command reads - never on a command line, where the "
        "auto-approve hook matches its deny regex against the whole command string.",
    )

    p_check = sub.add_parser("check")
    p_check.add_argument("--root", default=".", type=Path)
    p_check.add_argument("--all", action="store_true", help="every phase, not just changed ones")

    args = parser.parse_args(argv)

    if args.action == "check":
        try:
            problems = check(args.root, enforce_all=args.all)
        except CarriedError as exc:
            print(f"[carried_items] {exc}", file=sys.stderr)
            return ERROR
        if not problems:
            return OK
        print(
            "carried items: a handover's forward-looking claims are owed an answer by the next "
            "phase, or they become nothing - which is how a correct, specific prediction shipped as "
            "a defect one phase later:",
            file=sys.stderr,
        )
        for line in problems:
            print(f"  x {line}", file=sys.stderr)
        return OWED

    try:
        if args.action == "declared":
            items, says_none, present = declared(args.phase_dir)
            if not present:
                print(
                    f"[carried_items] {args.phase_dir}/handover.md has no `## {SECTION_HEADING}` "
                    f"section. A phase states what it carries forward - a row per item, or an "
                    f"explicit `none`. Silence is not `none`: a prediction written into prose is "
                    f"exactly what shipped as a defect one phase later.",
                    file=sys.stderr,
                )
                return OWED
            if not items and not says_none:
                print(
                    f"[carried_items] {args.phase_dir}/handover.md has a `## {SECTION_HEADING}` "
                    f"section with neither an item nor an explicit `none` row. Say which.",
                    file=sys.stderr,
                )
                return OWED
            for item in items:
                print(item.describe())
            return OK

        if args.action == "list":
            for item in owed(args.phase_dir):
                print(item.describe())
            return OK

        if args.action == "discharge":
            reason = None
            if args.reason_file:
                try:
                    reason = Path(args.reason_file).read_text(encoding="utf-8")
                except OSError as exc:
                    print(f"[carried_items] cannot read the reason file: {exc}", file=sys.stderr)
                    return ERROR
            record = discharge(args.phase_dir, args.item_id, args.how, args.by, reason)
            print(f"{record['item']} discharged as {record['as']}")
            return OK

        remaining = undischarged(args.phase_dir)
    except CarriedError as exc:
        print(f"[carried_items] {exc}", file=sys.stderr)
        return ERROR

    if not remaining:
        return OK
    print(
        f"{len(remaining)} item(s) carried by the previous phase's contract card have no answer in "
        f"this phase. A forward-looking claim that becomes nothing is how a correct, specific, "
        f"actionable prediction shipped as a defect one phase later:",
        file=sys.stderr,
    )
    for item in remaining:
        print(item.describe(), file=sys.stderr)
    print(
        "  Answer each one: scripts/carried_items.py discharge <phase-dir> <id> "
        "--as built|tested --by <spec/requirement/test>, or --as declined --reason-file <f>. An "
        "item that still applies to a LATER phase is declined here and re-carried on this phase's "
        "own card.",
        file=sys.stderr,
    )
    return OWED


if __name__ == "__main__":
    raise SystemExit(main())
