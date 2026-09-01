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

3. **Resolved, not merely named.** A discharge as `built` or `tested` names the artifact that now
   covers the item, and until this module resolved that name it could be anything at all - `--by x`
   was accepted, while this module's own docstring said "naming it is what makes the discharge
   checkable later". That is the shape the whole module exists to remove, one layer down: a sentence
   claiming behaviour nothing enforces. The name is resolved when the discharge is written AND
   re-resolved every time the obligation is checked, so a claim discharged into a test somebody later
   deleted is owed again rather than left pointing at nothing. **This is the executable form of a
   forward-looking warning**: clickup-agents phase 12 warned phase 13 that single-replica deployment
   was load-bearing - the poller has no cross-process lock, so its at-most-once property holds only
   with a single writer - and phase 13 honoured it only because a human copied the warning into the
   next worker's instructions by hand.

4. **Filed, on the LAST card.** The final phase has no successor, so obligation 2 has nobody to bind
   and its forward claims would be owed to no one - the one card where the hole this module closes
   would reopen. A card whose `next:` is `e2e` or `ship` must therefore name an **issue reference**
   on every `forward-claim` row. That is a presence check and nothing more: whether the claim was
   worth carrying is not this check's business, and never a model's.

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
                                            `--by` is RESOLVED, not merely recorded: a requirement
                                            id no spec or test-mapping row under this feature
                                            states, or a path that is not a file, is refused.
    carried_items.py due <phase-dir>        exit 1 when any owed item is undischarged

    `list`, `discharge` and `due` read the PRIOR phase's card, so all three exit 2 - undecidable,
    never exit 1 - when its `## Open items` section is present and states neither an item nor an
    explicit `none`. The remedy is on that card, and `discharge` cannot reach it. `check` reports
    the same state as a problem per phase rather than aborting its sweep of the others.
    carried_items.py filed <phase-dir>      exit 1 when a forward claim on the LAST card names no
                                            issue. Clean for every card that has a successor.
    carried_items.py check [--root .] [--all]
                                            every obligation over every phase, for CI. Diff-scoped
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from doc_read_path import changed_paths  # noqa: E402
from md_section import slice_section  # noqa: E402
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

#: The `kind` column's known values. A row that names one has that kind; a card on the older
#: three-column layout has no kind column at all and its second cell is the title, not a kind.
KINDS = ("open-finding", "forward-claim")

#: The forward-looking half - the kind obligation 3 applies to.
FORWARD_KIND = "forward-claim"

#: A card with one of these as its frontmatter `next:` has no successor phase. `skills/phase-handover`
#: already requires the value, so this needs no new marker and no scan for the highest phase number.
LAST_CARD_NEXT = ("e2e", "ship")

#: The card's frontmatter `next:` value.
NEXT_FIELD = re.compile(r"^next:[ \t]*(.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)

#: What counts as NAMING AN ISSUE, in one constant so widening it later is a single deliberate edit
#: rather than a regex hunt: a `#<number>` token, or an issue URL (GitHub, GitLab and the rest all
#: put `/issues/<number>` in the path). It is a presence test over the row's text and nothing more.
ISSUE_REFERENCE = re.compile(r"#\d+|https?://\S+/issues/\d+", re.IGNORECASE)

#: What a `--by` value has to CONTAIN before it can be resolved. A discharge names "the spec,
#: requirement id or test that now covers it", and until this module resolved it that name could be
#: anything at all - `--by x` was accepted. A prediction discharged into a name that resolves to
#: nothing is the prose this module exists to replace, one layer down.
#:
#: Two shapes are resolved and nothing else is: a requirement id, and a path. Everything else in the
#: value is prose the writer is free to add ("R9.1.4 in the write spec"), because the check is on the
#: REFERENCES, never on the sentence around them.
REQUIREMENT_REFERENCE = re.compile(r"\AR\d+\.\d+\.\d+\Z")

#: A path-shaped token: it holds a separator, or a file extension (a letter after the dot, so
#: `R9.1.4` is a requirement id and never a file). An optional `::node` suffix names a test inside it.
PATH_REFERENCE = re.compile(r"\A[\w.\-/]+\.[A-Za-z][A-Za-z0-9]{0,7}(::[^\s]+)?\Z")

#: Trimmed off a token before it is judged: markdown emphasis, and the punctuation a sentence puts
#: after a reference. A writer who wrote ``R9.1.4`` or "…spec.md," named the same thing.
TOKEN_TRIM = "*`_,;:.()[]\"'"

#: Where a requirement id is looked for. The id is the pipeline's own identifier, so a spec or a
#: test-mapping row naming it is the artifact that covers it. The card itself is deliberately NOT
#: searched: an id that appears only on the handover that predicted it resolves to the prediction.
REQUIREMENT_ARTIFACTS = ("phases/*/specs/*/spec.md", "phases/*/specs/*/test-mapping.md")

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
    row: str = ""

    def is_forward(self) -> bool:
        return self.kind.strip(EMPHASIS).lower() == FORWARD_KIND

    def names_an_issue(self) -> bool:
        """Whether the row names an issue reference. Presence only - it reads nothing into the text."""
        return bool(ISSUE_REFERENCE.search(self.row))

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
    items`, so the level is read from whatever the card actually used rather than pinned - which is
    `md_section.slice_section`'s rule, shared with the three other readers of a named section here.
    An empty section is an empty body, never None: a card that has the heading and says nothing under
    it is the "silence is not none" case, and it must reach `parse_items`, not read as no section.
    """
    found = slice_section(text, heading)
    return None if found is None else found[1]


def _is_placeholder(cells: list[str]) -> bool:
    """A row straight out of the template, never a real item.

    Judged on the ID CELL ALONE. The template's placeholders (`OBS-<n>`, `FWD-<n>`) live there, and
    reading angle brackets anywhere in the row discarded real items whose title or detail cell held
    them - `<slug>` reaches the route unencoded, `docs/features/<feature>/...`, `Map<String,X>`. Such
    a row then never reached `owed()` and the next phase was never asked about it, which is precisely
    the silent loss this module exists to remove; if it was the card's only row the card also read as
    saying nothing at all, and the gate blocked over a row plainly on the page.
    """
    return bool(cells) and ("<" in cells[0] or ">" in cells[0])


def _kind_and_title(cells: list[str]) -> tuple[str, str]:
    """(kind, title) for one row, on the current four-column layout OR the older three-column one.

    Every pre-rule card on disk is `| OBS-1 | title | verdict.json#observations[0] |`, with no kind
    column. Assuming the kind is always the second cell reported the title as the kind and the
    pointer as the title, so the very line the next phase's spec writer is asked to act on named the
    pointer as the item. The blocking behaviour never depended on it - the id does - but a message
    that misnames what it is about is one the reader has to go and re-derive.
    """
    if len(cells) >= 4:
        return (cells[1] or "item"), cells[2]
    if len(cells) == 3 and cells[1].strip(EMPHASIS).lower() in KINDS:
        return cells[1], cells[2]
    return "item", (cells[1] if len(cells) > 1 else "")


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
        kind, title = _kind_and_title(cells)
        items.append(Item(identifier, kind, title, phase, line.strip()))
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

    An unresolvable layout, a first phase, or a prior card with no `## Open items` section AT ALL all
    mean nothing is owed. None of those is an error: a feature's first phase inherits nothing by
    construction, and a repository upgrading to this rule must not be held hostage by a card written
    before the section existed.

    A prior card whose section IS present but declares neither an item nor an explicit `none` is a
    different state, and reading it the same way as "nothing carried" is the defect this function
    exists to close: phase 9's card wrote `### Open items` with bullet rows (`- OBS-1 | ... | ...`),
    the table parser found no rows and no `none`, and `due` on phase 10 read that silence as nothing
    owed - so OBS-1 through OBS-4 were never answered, not because they were discharged but because
    the parser could not see them. That must FAIL, loudly, rather than pass as nothing-carried.

    A card left holding the template's own unfilled placeholder rows is the same state and fails the
    same way - the section is on the page and says nothing, which is exactly what cannot be read as
    `none`. The message says so rather than claiming the table failed to parse: a placeholder row
    parses perfectly and is simply not an item.
    """
    where = layout(Path(phase_dir))
    if where is None:
        return []
    feature_dir, phase = where
    previous = prior_phase(feature_dir, phase)
    if previous is None:
        return []
    items, says_none, present = declared(previous)
    if present and not items and not says_none:
        raise CarriedError(
            f"{previous}/handover.md has a `## {SECTION_HEADING}` section that declares neither an "
            f"item nor an explicit `none`, so what this phase inherits CANNOT BE DETERMINED. That "
            f"covers every shape the row parser does not read as an item - phase 9's bullet rows "
            f"under an `### Open items` heading, and a card left holding the template's own "
            f"unfilled placeholder rows. It is NOT the same as nothing carried: reading it that way "
            f"is what let a phase close without answering what it inherited. Fix the prior phase's "
            f"card to the documented table format - a row per item, or an explicit `none` row if it "
            f"truly carries nothing."
        )
    return items


def card_next(phase_dir: Path) -> str | None:
    """This card's frontmatter `next:` value, lowercased, or None when it has none.

    A card with no `next:` at all - every pre-rule card - is simply not known to be a last card, and
    owes nothing here. Nothing may hard-fail a repository over history it has not touched.
    """
    try:
        text = (Path(phase_dir) / "handover.md").read_text(encoding="utf-8")
    except OSError:
        return None
    found = NEXT_FIELD.search(text)
    return found.group(1).strip().strip(EMPHASIS).lower() if found else None


def unfiled(phase_dir: Path) -> list[Item]:
    """Forward claims on a LAST card that name no issue reference. Empty for every other card.

    The last card is the one place with no successor and therefore no backstop, which is exactly
    where the hole this module closes would reopen: `owed()` reads the immediately prior card, so a
    claim written on the final phase's card is owed to nobody. Filing it as an issue is the only
    thing that outlives the feature.

    This is a PRESENCE check, the same kind as everything else here. It never asks whether a claim is
    worth carrying, important, duplicated, or something that should have been built - that judgement
    belongs to a human, not to a gate at feature close, and certainly not to a model.
    """
    if card_next(phase_dir) not in LAST_CARD_NEXT:
        return []
    items, _says_none, present = declared(phase_dir)
    if not present:
        return []
    return [item for item in items if item.is_forward() and not item.names_an_issue()]


def repo_root(phase_dir: Path) -> Path | None:
    """The tree a path-shaped reference is resolved against - the directory holding `docs/features`.

    Derived from the phase's own location rather than from the process's cwd, because `check`,
    `hook_verifier.sh` and a human at a shell all run from different places and a reference that
    resolves from one of them and not the others is worse than one that never resolves.
    """
    where = layout(Path(phase_dir))
    if where is None:
        return None
    feature_dir, _phase = where
    parents = feature_dir.resolve().parents
    return parents[2] if len(parents) > 2 else None


def references(by: str) -> list[str]:
    """The reference-shaped tokens in a `--by` value. Prose around them is not a reference."""
    out: list[str] = []
    for raw in re.split(r"[\s,]+", by or ""):
        token = raw.strip(TOKEN_TRIM)
        if not token:
            continue
        if REQUIREMENT_REFERENCE.match(token) or PATH_REFERENCE.match(token):
            out.append(token)
    return out


def _requirement_resolves(phase_dir: Path, identifier: str) -> bool:
    """Whether some spec or test-mapping row under this feature states the requirement id."""
    where = layout(Path(phase_dir))
    if where is None:
        return False
    feature_dir, _phase = where
    for pattern in REQUIREMENT_ARTIFACTS:
        for artifact in feature_dir.glob(pattern):
            try:
                if identifier in artifact.read_text(encoding="utf-8"):
                    return True
            except OSError:
                continue
    return False


def _bases(phase_dir: Path) -> list[Path]:
    """Where a path-shaped reference may be rooted, nearest-writer-first.

    A writer names an artifact the way they would say it out loud: `tests/demo/9-b/test_x.py` from
    the repository root, or `specs/9.1-write/spec.md` from the phase they are standing in. Both are
    real references to a real file, and refusing the second would push writers back to prose.
    """
    bases: list[Path] = []
    where = layout(Path(phase_dir))
    root = repo_root(phase_dir)
    if root is not None:
        bases.append(root)
    if where is not None:
        bases.extend([Path(phase_dir), where[0]])
    bases.append(Path.cwd())
    return bases


def _path_resolves(phase_dir: Path, token: str) -> bool:
    """Whether a path-shaped reference names a file that exists - and, with `::node`, holds it.

    The file existing is not the claim a discharge makes; the named test existing is. A node id
    pointing into a file that no longer contains it is the same rot one level finer, so it is read.
    """
    relative, _, node = token.partition("::")
    wanted = node.split("::")[-1].split("[")[0] if node else ""
    for base in _bases(phase_dir):
        candidate = base / relative
        if not candidate.is_file():
            continue
        if not wanted:
            return True
        try:
            if wanted in candidate.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def unresolved(phase_dir: Path, by: str) -> list[str]:
    """The reference-shaped tokens in `by` that name nothing on disk. Empty means every one resolves."""
    return [
        token
        for token in references(by)
        if not (
            _requirement_resolves(phase_dir, token)
            if REQUIREMENT_REFERENCE.match(token)
            else _path_resolves(phase_dir, token)
        )
    ]


def _by_problem(phase_dir: Path, by: str, how: str) -> str | None:
    """Why this `--by` value names nothing checkable, or None when it names something that resolves.

    ONE function, used at both ends on purpose. A discharge is refused here when it is written, and
    every later check re-resolves it: the artifact a claim was discharged into can be deleted, and a
    ledger entry pointing at a test somebody removed is a discharged item that is no longer answered.
    That re-resolution is what gives a forward-looking warning a form that can FAIL - phase 12 warned
    phase 13 that single-replica deployment was load-bearing and the warning survived only because a
    human copied it forward by hand.
    """
    found = references(by)
    if not found:
        return (
            f"`--by {by!r}` names nothing this check can resolve. Discharging an item as {how!r} "
            f"names the artifact that now covers it - a requirement id (`R9.1.4`), a spec or a test "
            f"path (`tests/demo/9-b/test_x.py`, optionally `::test_name`). Naming it is the whole "
            f"difference between a discharge and a second sentence about the first one."
        )
    missing = unresolved(phase_dir, by)
    if missing:
        return (
            f"`--by` names {', '.join(missing)}, which resolve to nothing: a requirement id no "
            f"spec or test-mapping row under this feature states, or a path that is not a file "
            f"here. A claim discharged into a name that resolves to nothing is prose with an id on "
            f"it."
        )
    return None


def path_for(phase_dir: Path) -> Path:
    return Path(phase_dir) / FILENAME


def load(phase_dir: Path) -> dict:
    """This phase's discharge ledger, or an empty one. An unreadable ledger is an error, not empty.

    Reading a corrupt ledger as "nothing discharged" would be the safe direction, but reading it as
    "everything discharged" would not, and the two are one typo apart. So it refuses instead.
    """
    target = path_for(phase_dir)
    if not target.is_file():
        return {
            "phase": Path(phase_dir).name,
            "readers": list(READERS),
            "discharges": [],
        }
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
    phase_dir: Path,
    item_id: str,
    how: str,
    by: str | None = None,
    reason: str | None = None,
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
    else:
        problem = _by_problem(phase_dir, by, how)
        if problem is not None:
            raise CarriedError(problem)

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


def stale_discharges(phase_dir: Path) -> list[str]:
    """Discharges whose named artifact no longer resolves, as lines. Empty means every one still does.

    A discharge is not a one-time ceremony. The test that pinned a carried claim can be deleted, the
    spec renamed, the requirement id dropped - and the ledger would go on saying the claim was
    answered. Re-resolving here is what makes the answer keep having to be true.
    """
    out: list[str] = []
    for record in load(phase_dir)["discharges"]:
        by = record.get("by")
        if record.get("as") not in ("built", "tested") or not by:
            continue
        problem = _by_problem(phase_dir, by, record.get("as") or "built")
        if problem is not None:
            out.append(
                f"{record.get('item')} is discharged as {record.get('as')}, but {problem}"
            )
    return out


def answered(phase_dir: Path) -> set[str]:
    """The ids this phase has answered with an answer that still holds."""
    stale = {line.split(" ", 1)[0] for line in stale_discharges(phase_dir)}
    return {
        d.get("item")
        for d in load(phase_dir)["discharges"]
        if d.get("item") not in stale
    }


def undischarged(phase_dir: Path) -> list[Item]:
    """Items the prior phase carried that this phase has not answered - or no longer answers."""
    done = answered(phase_dir)
    return [item for item in owed(phase_dir) if item.id not in done]


def _unfiled_line(phase_dir: Path, item: Item) -> str:
    return (
        f"{phase_dir}/handover.md: {item.id} is a forward claim on the LAST card (next: "
        f"{card_next(phase_dir)}), so no later phase is owed it - {item.title}. Add an issue "
        f"reference to that row (#<number>, or an issue URL); filing it is the only thing that "
        f"outlives the feature."
    )


def phase_problems(phase_dir: Path) -> list[str]:
    """Every obligation for one closed phase, as lines. Empty means clean."""
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
    # The ledger is read OUTSIDE the guard below, which is the whole point of not calling
    # `undischarged()` here: a corrupt `carried.json` is undecidable (exit 2) and must keep
    # propagating, and catching it here would answer malformed JSON with `run discharge` - the exact
    # collapse `main()` exists to prevent. Only `owed()`'s own state is caught, because `check` sweeps
    # many phases in one pass and one phase's unreadable prior card must not abort the scan of the
    # rest; `declared`/`due` run standalone (hook_verifier.sh, one phase at a time) and let the same
    # CarriedError reach `main()` as the loud, undecidable failure.
    settled = answered(phase_dir)
    out.extend(f"{phase_dir}: {line}" for line in stale_discharges(phase_dir))
    try:
        carried = owed(phase_dir)
    except CarriedError as exc:
        out.append(f"{phase_dir}: {exc}")
        carried = []
    for item in carried:
        if item.id in settled:
            continue
        out.append(
            f"{phase_dir}: {item.id} ({item.phase}) has no answer here - {item.title}"
        )
    for item in unfiled(phase_dir):
        out.append(_unfiled_line(phase_dir, item))
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
        print(
            f"[carried_items] no phase contract cards under {root} - nothing to check",
            file=sys.stderr,
        )
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
            and any(
                changed == resolved or resolved in changed.parents for changed in scope
            )
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
    """The CLI. **Exit 1 means the obligation, and exit 2 means it could not be DECIDED.**

    Collapsing the two is how a corrupt `carried.json` came to be answered with `run discharge` - a
    remedy that cannot repair malformed JSON - so an unexpected failure exits 2 with its own cause
    rather than escaping as a traceback and an exit 1 that reads as an owed item.
    """
    try:
        return _dispatch(_parse(argv))
    except CarriedError as exc:
        print(f"[carried_items] {exc}", file=sys.stderr)
        return ERROR
    except Exception as exc:  # noqa: BLE001 - an undecidable check is never an owed item
        print(
            f"[carried_items] the check could not be decided: {exc!r}", file=sys.stderr
        )
        return ERROR


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    for name in ("declared", "list", "due", "filed"):
        p = sub.add_parser(name)
        p.add_argument("phase_dir", type=Path)

    p_discharge = sub.add_parser("discharge")
    p_discharge.add_argument("phase_dir", type=Path)
    p_discharge.add_argument("item_id")
    p_discharge.add_argument(
        "--as", dest="how", required=True, choices=list(DISCHARGES)
    )
    p_discharge.add_argument(
        "--by",
        help="the spec, requirement id or test that now covers it (built/tested). It is RESOLVED: "
        "a requirement id (R9.1.4) some spec or test-mapping row under this feature states, or a "
        "path to a file that exists, optionally ::the-test-inside-it. Prose around the reference "
        "is fine; a value with no resolvable reference in it is refused.",
    )
    p_discharge.add_argument(
        "--reason-file",
        help="path to a file holding the reason for declining. A reason is author-written prose, "
        "and prose belongs in a file the command reads - never on a command line, where the "
        "auto-approve hook matches its deny regex against the whole command string.",
    )

    p_check = sub.add_parser("check")
    p_check.add_argument("--root", default=".", type=Path)
    p_check.add_argument(
        "--all", action="store_true", help="every phase, not just changed ones"
    )

    return parser.parse_args(argv)


def _filed(phase_dir: Path) -> int:
    """The last card's forward claims, each naming an issue - or exit 1 naming the rows that do not.

    A card that is not a last card, a card with no forward claims and a pre-rule card with no
    section all pass, and the clean answer says which on stderr rather than passing invisibly.
    """
    nxt = card_next(phase_dir)
    remaining = unfiled(phase_dir)
    if not remaining:
        why = (
            "every forward claim on it names an issue"
            if nxt in LAST_CARD_NEXT
            else "not a last card, so its claims are owed to the phase after it"
        )
        print(
            f"[carried_items] {phase_dir} (next: {nxt or 'unset'}) - {why}.",
            file=sys.stderr,
        )
        return OK
    print(
        f"{len(remaining)} forward claim(s) on this phase's card name no issue, and this is the LAST "
        f"card - `next: {nxt}`, so no later phase is owed them. A claim owed to "
        f"nobody is how a correct, specific, actionable prediction became nothing:",
        file=sys.stderr,
    )
    for item in remaining:
        print(item.describe(), file=sys.stderr)
    print(
        "  Answer each one: add an issue reference to its row on handover.md - `#<number>` or an "
        "issue URL. Filing it is the only thing that outlives the feature; nothing here judges "
        "whether the claim was worth carrying.",
        file=sys.stderr,
    )
    return OWED


def _dispatch(args: argparse.Namespace) -> int:
    if args.action == "check":
        problems = check(args.root, enforce_all=args.all)
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

    if args.action == "filed":
        return _filed(args.phase_dir)

    if args.action == "discharge":
        reason = None
        if args.reason_file:
            try:
                reason = Path(args.reason_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(
                    f"[carried_items] cannot read the reason file: {exc}",
                    file=sys.stderr,
                )
                return ERROR
        record = discharge(args.phase_dir, args.item_id, args.how, args.by, reason)
        print(f"{record['item']} discharged as {record['as']}")
        return OK

    remaining = undischarged(args.phase_dir)
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
    raise SystemExit(guard_scope.run(__file__, main))
