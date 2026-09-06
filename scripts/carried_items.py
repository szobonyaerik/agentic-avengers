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

5. **Deferred, with its measurement, to the phase that owns it** (issue #115). A phase could not
   end its own discovery loop: a finding had three dispositions - `open`, `fixed`, `acknowledged`
   (waived) - and none meant *"this is real, it does not block THIS phase's Done when, and a later
   phase owns it"*. `faithful-rep` phase 3 ran about 48 hours because every amendment round found a
   real defect and a human had to be the terminator. A **deferral** is that fourth disposition, and
   it is the same slot again rather than a parallel ledger: a `deferred-finding` row on this card,
   backed by a record in `carried.json`'s `deferrals` list carrying the finding's measurement
   verbatim and the owning phase. It is **gated on the phase's *Done when*** - `scripts/done_when.py`
   decides, from the plan's structured table and the specs' `done_when:` tags, whether the
   requirement the finding is against carries a condition of this phase; a finding that does is
   refused, whatever anyone would prefer, because phase 3's A3 caught a clip containing no exercise
   and telling stages to care less would have shipped it. A prose-only *Done when* cannot defer at
   all, and says so. The Verifier stamps such a finding `status: deferred`, and
   `verdict_findings.open_findings` resolves that stamp ONLY when a record here backs it - a stamp
   with nothing behind it is open. Between the deferring phase and the owner, every phase answers
   the row by `recarry`: the record is copied with its measurement byte-for-byte, the *Done when*
   gate is asked again against THAT phase, and the discharge is recorded as `declined` with the
   structural reason. The owner answers it like any other row. Nothing here may weaken a check,
   delete a test or move a threshold: the deferral carries the number forward unchanged, and
   there is no command that edits one.

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
    carried_items.py defer <phase-dir> <item-id> --to <n>-<slug> --record-file <f>
                                            [--finding <verdict-finding-id>] [--spec-id R<n>.<k>.<m>]...
                                            [--no-requirement]
                                            record a deferral: the file's first line is the row's
                                            title, the rest its MEASUREMENT, carried verbatim.
                                            Exit 1 when the finding blocks this phase's Done when,
                                            2 when that cannot be decided (prose-only Done when).
    carried_items.py recarry <phase-dir> <item-id>
                                            answer a deferred-finding row owned by a LATER phase:
                                            copy its record unchanged, re-ask the Done when gate
                                            against this phase, record the discharge as declined
    carried_items.py deferred <phase-dir>   exit 1 when a `status: deferred` verdict finding has no
                                            deferral behind it, or the card and the ledger disagree
                                            about what is deferred
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

import done_when  # noqa: E402
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
KINDS = ("open-finding", "forward-claim", "deferred-finding")

#: The forward-looking half - the kind obligation 3 applies to.
FORWARD_KIND = "forward-claim"

#: A finding deferred to a later phase with its measurement (obligation 5). Every row of this kind is
#: backed by a record in the ledger's `deferrals` list, and every record has a row: the two are
#: checked against each other, because a row with no record carries no measurement and a record with
#: no row is off the read path.
DEFERRED_KIND = "deferred-finding"

#: The structural reason a `recarry` records for its `declined` discharge. It is not author prose -
#: only the owner is substituted - so it never needs a file.
RECARRY_REASON = "deferred finding owned by {owner}; re-carried on this phase's card with its measurement unchanged"

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

#: An item id: something a later phase can grep for. `OBS-1`, `FWD-2`, `CARRY-8.1` and a verdict
#: finding's 12-hex id (`6b92d31abc84`, which may start with a digit) all qualify; a template
#: placeholder (`OBS-<n>`) does not, and neither does a prose fragment.
ITEM_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]*\Z")

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
    "avenger-verifier @ per phase, for the deferrals that back a `status: deferred` finding",
]


class CarriedError(Exception):
    """A malformed card, ledger or request. Always fails the caller closed."""


class DeferralRefused(CarriedError):
    """A deferral the *Done when* gate decided against. Exit 1: decided, and the answer is no."""


class Item(NamedTuple):
    """One thing a phase's card carries forward.

    `owner` is set only for a `deferred-finding` row: the phase that owns the finding, read from the
    declaring phase's ledger rather than from the row, so the card never carries a second statement
    of it.
    """

    id: str
    kind: str
    title: str
    phase: str
    row: str = ""
    owner: str | None = None

    def is_forward(self) -> bool:
        return self.kind.strip(EMPHASIS).lower() == FORWARD_KIND

    def is_deferred(self) -> bool:
        return self.kind.strip(EMPHASIS).lower() == DEFERRED_KIND

    def needs_filing(self) -> bool:
        """Whether, on a LAST card, this row must name an issue: a claim or a deferral owed to no
        later phase is the hole obligation 4 closes, and a deferred finding is owed to one exactly as
        a forward claim is."""
        return self.is_forward() or self.is_deferred()

    def names_an_issue(self) -> bool:
        """Whether the row names an issue reference. Presence only - it reads nothing into the text."""
        return bool(ISSUE_REFERENCE.search(self.row))

    def describe(self) -> str:
        owner = f" -> owned by {self.owner}" if self.owner else ""
        return f"  {self.id} [{self.kind}] ({self.phase}): {self.title}{owner}"


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
    items = _with_owners(previous, items)
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


def _with_owners(declaring_phase: Path, items: list[Item]) -> list[Item]:
    """Attach each `deferred-finding` row's owner from the declaring phase's ledger.

    A deferred row with no record behind it is undecidable rather than a plain item: the row says a
    finding was deferred and nothing says to whom or with what measurement, which is the state this
    obligation exists to make impossible. The remedy is on the declaring phase (`defer`), so it is
    raised here, where the reader of that card asks, and reported per phase by `check`.
    """
    records = {record.get("id"): record for record in deferrals(declaring_phase)}
    out: list[Item] = []
    for item in items:
        if not item.is_deferred():
            out.append(item)
            continue
        record = records.get(item.id)
        if record is None or not str(record.get("owner") or "").strip():
            raise CarriedError(
                f"{declaring_phase}/handover.md carries {item.id} as a `{DEFERRED_KIND}` row, but "
                f"{declaring_phase}/{FILENAME} records no deferral of that id. A deferred row with "
                f"no record names no owner and carries no measurement, so what this phase inherits "
                f"CANNOT BE DETERMINED. Record it on the declaring phase: scripts/carried_items.py "
                f"defer {declaring_phase} {item.id} --to <n>-<slug> --record-file <f> ..."
            )
        out.append(item._replace(owner=str(record["owner"])))
    return out


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
    return [item for item in items if item.needs_filing() and not item.names_an_issue()]


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
            "deferrals": [],
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CarriedError(f"cannot read {target}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("discharges"), list):
        raise CarriedError(f"{target} is not a carried-items ledger")
    # A ledger written before deferrals existed has no `deferrals` key, and that is a ledger with no
    # deferrals - not a malformed one. A key that IS present and is not a list is malformed.
    if "deferrals" not in data:
        data["deferrals"] = []
    elif not isinstance(data["deferrals"], list):
        raise CarriedError(f"{target}: `deferrals` is not a list")
    return data


def deferrals(phase_dir: Path) -> list[dict]:
    """The deferral records this phase's ledger holds. An unreadable ledger raises, as `load` does."""
    return [d for d in load(phase_dir)["deferrals"] if isinstance(d, dict)]


def backed_deferrals(phase_dir: Path) -> set[str]:
    """The verdict finding ids this phase's ledger has deferred - what resolves a `status: deferred`.

    An unreadable ledger backs NOTHING. That fails closed: every `deferred` finding then reads as
    open, and `deferred` (the CLI) names the corrupt file at the handover rather than this function
    guessing. Only records that name a verdict `finding` count; a deferral of a defect that was never
    a verdict finding (one found by watching a render) backs no stamp because there is none.
    """
    try:
        records = deferrals(phase_dir)
    except CarriedError:
        return set()
    return {
        str(record["finding"])
        for record in records
        if str(record.get("finding") or "").strip()
    }


def _phase_number(phase_dir: Path) -> int:
    number = done_when.phase_number(phase_dir)
    if number is None:
        raise CarriedError(f"{phase_dir} is not a `<n>-<slug>` phase directory")
    return number


def _owner_number(owner: str) -> int:
    found = re.match(r"^\s*(\d+)", owner or "")
    if not found:
        raise CarriedError(f"owner {owner!r} does not start with a phase number")
    return int(found.group(1))


def _resolve_owner(phase_dir: Path, to: str) -> str:
    """The `<n>-<slug>` a deferral is owed to, checked against the plan and against direction.

    A deferral hands a finding FORWARD: the owner must be a phase the plan declares and a later one
    than this. An earlier phase is not a deferral - that phase's own verdict and amendments already
    bind it, and a row here would be owed to nobody by construction, since `owed()` reads only the
    immediately prior card. The same phase is not a deferral either; it is the finding, still open.
    """
    where = layout(Path(phase_dir))
    if where is None:
        raise CarriedError(f"{phase_dir} is outside the docs/features layout")
    feature_dir, _phase = where
    here = _phase_number(phase_dir)
    number = _owner_number(to)
    if number <= here:
        raise CarriedError(
            f"`--to {to}` is not later than {Path(phase_dir).name}. A deferral hands a finding "
            f"FORWARD to the phase that owns it; a phase already behind this one is bound by its "
            f"own verdict and amendments, and a row here would be owed to nobody - `owed()` reads "
            f"only the immediately prior card. Record it as a forward-claim naming that phase's "
            f"ledger, or fix it there."
        )
    try:
        plan = (feature_dir / "plan.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise CarriedError(f"cannot read {feature_dir / 'plan.md'}: {exc}") from exc
    planned = {num: title for num, title in done_when.planned_phases(plan)}
    if number not in planned:
        raise CarriedError(
            f"`--to {to}` names phase {number}, which plan.md does not declare "
            f"(it declares {', '.join(str(n) for n in sorted(planned)) or 'no phases'}). A "
            f"deferral is owed to a phase that will exist."
        )
    slug = to.strip()
    if not re.match(r"^\d+-[A-Za-z0-9._-]+$", slug):
        slug = f"{number}-{done_when.slugify(planned[number])}"
    return slug


def _finding_record(phase_dir: Path, finding_id: str) -> dict:
    """The verdict finding this deferral is about, from any attempt on record. Absent is refused."""
    from verifier_attempts import (
        verdict_records,
    )  # lazy: verifier_attempts imports this module

    for path in reversed(verdict_records(Path(phase_dir))):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for finding in (
            payload.get("findings") if isinstance(payload, dict) else None
        ) or []:
            if isinstance(finding, dict) and str(finding.get("id") or "") == finding_id:
                return finding
    raise CarriedError(
        f"no verdict record in {phase_dir} carries a finding {finding_id!r}. A deferral of a "
        f"verdict finding names one the Verifier actually raised; a defect found outside the "
        f"verdict is deferred without --finding."
    )


def _split_record_file(text: str) -> tuple[str, str]:
    """(title, measurement) from the record file: first non-empty line, and everything after it."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        raise CarriedError(
            "the record file is empty - a deferral carries a title and a measurement"
        )
    title = lines[0].strip().strip("#").strip()
    measurement = "\n".join(lines[1:]).strip()
    if not title:
        raise CarriedError(
            "the record file's first line is the row's one-line title, and it is blank"
        )
    if not measurement:
        raise CarriedError(
            "the record file carries no measurement after its title line. A deferral carries the "
            "finding's MEASUREMENT forward unchanged - the number, the clip, the reading - so the "
            "owner inherits the evidence and not a sentence about it."
        )
    return title, measurement


def _decide_or_refuse(
    phase_dir: Path, spec_ids: list[str], what: str
) -> done_when.Decision:
    decision = done_when.decide(Path(phase_dir), spec_ids)
    if decision.code == done_when.BLOCKS:
        raise DeferralRefused(f"{what} cannot be deferred: {decision.reason}")
    if decision.code != done_when.CLEAR:
        raise CarriedError(f"{what} cannot be deferred: {decision.reason}")
    return decision


def _emit_deferrals(phase_dir: Path) -> None:
    """Measurement, never a gate: the phase record counts deferrals (§6d), and a write that fails
    is swallowed by the sink and never fails the deferral it measures."""
    try:
        import pipeline_metrics  # lazy: pipeline_metrics imports verifier_attempts imports this

        pipeline_metrics.record_deferrals(str(phase_dir))
    except Exception:  # noqa: BLE001 - measurement never fails the command it measures
        pass


def defer(
    phase_dir: Path,
    item_id: str,
    to: str,
    record_text: str,
    finding: str | None = None,
    spec_ids: list[str] | None = None,
    no_requirement: bool = False,
) -> dict:
    """Record one deferral: a real finding, not this phase's *Done when*, owned by a later phase.

    Refused, each naming why: an id already deferred here (a deferral is never edited - there is no
    `widen`, exactly as `amendments.py` has none); a finding the verdict never raised; a finding
    against a requirement that carries a *Done when* condition (exit 1 - decided, and the answer is
    no); a *Done when* that cannot be read (exit 2); an owner that is not a later planned phase; a
    record with no measurement.
    """
    identifier = item_id.strip(EMPHASIS)
    if not ITEM_ID.match(identifier):
        raise CarriedError(f"{item_id!r} is not a usable item id")
    ledger = load(phase_dir)
    if any(d.get("id") == identifier for d in ledger["deferrals"]):
        raise CarriedError(
            f"{identifier} is already deferred in {path_for(phase_dir)}. A deferral is a record of "
            f"what was decided and when; there is no command that edits one, exactly as "
            f"`amendments.py` has `open` and `close` and no `widen`. A different decision is a "
            f"different id."
        )
    owner = _resolve_owner(phase_dir, to)
    title, measurement = _split_record_file(record_text)
    ids = [s.strip() for s in (spec_ids or []) if s.strip()]
    bad = [s for s in ids if not REQUIREMENT_REFERENCE.match(s)]
    if bad:
        raise CarriedError(
            f"not requirement ids: {', '.join(bad)} (expected R<n>.<k>.<m>)"
        )
    verdict_finding: dict | None = None
    if finding:
        verdict_finding = _finding_record(phase_dir, finding.strip())
        declared_id = str(verdict_finding.get("spec_id") or "").strip()
        if (
            declared_id
            and REQUIREMENT_REFERENCE.match(declared_id)
            and declared_id not in ids
        ):
            ids.append(declared_id)
    if not ids and not no_requirement:
        raise CarriedError(
            "name the requirement id(s) the finding is against (--spec-id R<n>.<k>.<m>), or say "
            "--no-requirement when it belongs to no requirement of this phase - a defect found by "
            "watching a render, owned by a stage outside the phase. Silence is not either: the "
            "Done when gate decides on the ids, and an omitted id is not a clear one."
        )
    decision = _decide_or_refuse(phase_dir, ids, f"{identifier}")
    record = {
        "id": identifier,
        "finding": verdict_finding.get("id") if verdict_finding else None,
        "spec_ids": ids,
        "title": title,
        "owner": owner,
        "measurement": measurement,
        "done_when": {"phase": Path(phase_dir).name, "checked": list(decision.checked)},
        "origin": Path(phase_dir).name,
        "at": _now(),
    }
    ledger["deferrals"].append(record)
    ledger["phase"] = Path(phase_dir).name
    save(phase_dir, ledger)
    _emit_deferrals(phase_dir)
    return record


def recarry(phase_dir: Path, item_id: str) -> dict:
    """Answer a deferred-finding row owned by a LATER phase: carry it on, byte-for-byte.

    The record is copied from the declaring phase's ledger with its measurement unchanged, the *Done
    when* gate is asked again against THIS phase (a finding that blocks this phase's Done when is
    this phase's to fix, however it arrived), and the discharge is recorded as `declined` with the
    structural reason. The row on this phase's own card is still owed, and `declared` checks it.
    """
    available = {item.id: item for item in owed(phase_dir)}
    item = available.get(item_id)
    if item is None:
        raise CarriedError(
            f"the prior phase's card declares no item {item_id!r}. It carries: "
            f"{', '.join(sorted(available)) or '(nothing)'}."
        )
    if not item.is_deferred():
        raise CarriedError(
            f"{item_id} is a `{item.kind}` row, not a `{DEFERRED_KIND}`. Only a deferred finding is "
            f"re-carried; answer this one with `discharge`."
        )
    here = _phase_number(phase_dir)
    owner_number = _owner_number(item.owner or "")
    if owner_number <= here:
        raise CarriedError(
            f"{item_id} is owned by {item.owner}, which is this phase or behind it. This phase "
            f"answers it: `discharge {item_id} --as built|tested --by ...`, or `--as declined "
            f"--reason-file <f>`. Re-carrying is only for a finding owed to a LATER phase."
        )
    where = layout(Path(phase_dir))
    previous = prior_phase(where[0], here) if where else None
    source = (
        next((d for d in deferrals(previous) if d.get("id") == item_id), None)
        if previous
        else None
    )
    if source is None:
        raise CarriedError(f"{item_id}: no deferral record on the prior phase to carry")
    ledger = load(phase_dir)
    if any(d.get("id") == item_id for d in ledger["deferrals"]):
        raise CarriedError(f"{item_id} is already carried in {path_for(phase_dir)}")
    _decide_or_refuse(phase_dir, list(source.get("spec_ids") or []), f"{item_id}")
    carried = dict(source)
    carried["recarried_by"] = Path(phase_dir).name
    carried["recarried_at"] = _now()
    ledger["deferrals"].append(carried)
    ledger["discharges"] = [d for d in ledger["discharges"] if d.get("item") != item_id]
    ledger["discharges"].append(
        {
            "item": item_id,
            "from_phase": item.phase,
            "as": "declined",
            "by": None,
            "reason": RECARRY_REASON.format(owner=item.owner),
            "at": _now(),
        }
    )
    ledger["phase"] = Path(phase_dir).name
    save(phase_dir, ledger)
    _emit_deferrals(phase_dir)
    return carried


def deferral_problems(phase_dir: Path) -> list[str]:
    """Where the card, the ledger and the verdict disagree about what this phase deferred.

    Three comparisons, all presence: every ledger deferral has a `deferred-finding` row on this
    card and every such row has a record (the row is the read path, the record is the measurement);
    every `status: deferred` verdict finding has a record naming it (a stamp with nothing behind it
    is open, and `open_findings` already reads it so - this names WHICH); and a verdict finding's
    `deferred_to`, when it says one, agrees with the record's owner. A phase with no card yet is
    checked on the ledger and the verdict alone.
    """
    from verifier_attempts import (
        verdict_records,
    )  # lazy: verifier_attempts imports this module

    out: list[str] = []
    records = {str(d.get("id")): d for d in deferrals(phase_dir)}
    items, _says_none, present = declared(phase_dir)
    if present:
        rows = {item.id for item in items if item.is_deferred()}
        for identifier in sorted(set(records) - rows):
            out.append(
                f"{identifier} is deferred in {FILENAME} but handover.md has no `{DEFERRED_KIND}` "
                f"row for it - a deferral off the card is off the read path, and the next phase "
                f"is never asked about it."
            )
        for identifier in sorted(rows - set(records)):
            out.append(
                f"{identifier} is a `{DEFERRED_KIND}` row on handover.md with no record in "
                f"{FILENAME} - no owner, no measurement. Record it with `defer`, or change its kind."
            )
    backed = {str(d.get("finding")): d for d in records.values() if d.get("finding")}
    for path in verdict_records(Path(phase_dir)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CarriedError(f"cannot read {path}: {exc}") from exc
        for finding in (
            payload.get("findings") if isinstance(payload, dict) else None
        ) or []:
            if not isinstance(finding, dict):
                continue
            if str(finding.get("status") or "").lower() != "deferred":
                continue
            identifier = str(finding.get("id") or "")
            record = backed.get(identifier)
            if record is None:
                out.append(
                    f"{path.name}: finding {identifier} is `status: deferred` with no deferral in "
                    f"{FILENAME} naming it. A stamp with nothing behind it is an OPEN finding: "
                    f"record the deferral (`defer ... --finding {identifier}`) or fix the finding."
                )
                continue
            stated = str(finding.get("deferred_to") or "").strip()
            if stated and _owner_number(stated) != _owner_number(
                str(record.get("owner"))
            ):
                out.append(
                    f"{path.name}: finding {identifier} says `deferred_to: {stated}` while "
                    f"{FILENAME} records its owner as {record.get('owner')}. Two statements of "
                    f"who owns it disagree; the ledger is the one gated on the Done when."
                )
    return out


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
    item = available[item_id]
    if (
        how == "declined"
        and item.is_deferred()
        and _owner_number(item.owner or "") > _phase_number(phase_dir)
    ):
        raise CarriedError(
            f"{item_id} is a deferred finding owned by {item.owner}, a later phase. It is not "
            f"declined here; it is RE-CARRIED with its measurement unchanged: "
            f"scripts/carried_items.py recarry {phase_dir} {item_id}. Fixing it early is fine "
            f"(`--as built|tested --by ...`); dropping it on the way is what a deferral exists to "
            f"make impossible."
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
        f"{phase_dir}/handover.md: {item.id} is a {item.kind} on the LAST card (next: "
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
    out.extend(f"{phase_dir}: {line}" for line in deferral_problems(phase_dir))
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
    except DeferralRefused as exc:
        # Decided, and the answer is no: the finding blocks this phase's Done when. Exit 1, not 2 -
        # nothing is unreadable, and the remedy is to fix the finding here.
        print(f"[carried_items] {exc}", file=sys.stderr)
        return OWED
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

    p_defer = sub.add_parser("defer")
    p_defer.add_argument("phase_dir", type=Path)
    p_defer.add_argument("item_id")
    p_defer.add_argument(
        "--to",
        required=True,
        help="the LATER phase that owns the finding, as <n>-<slug> or <n>",
    )
    p_defer.add_argument(
        "--record-file",
        required=True,
        help="path to a file whose first line is the row's one-line title and whose remainder is "
        "the finding's MEASUREMENT, carried forward verbatim. Both are author-written prose, and "
        "prose belongs in a file the command reads - never on a command line.",
    )
    p_defer.add_argument(
        "--finding",
        help="the verdict finding id this deferral is about, when it is one",
    )
    p_defer.add_argument(
        "--spec-id",
        action="append",
        default=[],
        help="requirement id(s) the finding is against; the Done when gate decides on these",
    )
    p_defer.add_argument(
        "--no-requirement",
        action="store_true",
        help="the finding belongs to no requirement of this phase - said explicitly",
    )

    p_recarry = sub.add_parser("recarry")
    p_recarry.add_argument("phase_dir", type=Path)
    p_recarry.add_argument("item_id")

    p_deferred = sub.add_parser("deferred")
    p_deferred.add_argument("phase_dir", type=Path)

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
        mismatched = deferral_problems(args.phase_dir)
        if mismatched:
            print(
                f"[carried_items] {args.phase_dir}: the card, the ledger and the verdict disagree "
                f"about what this phase deferred:",
                file=sys.stderr,
            )
            for line in mismatched:
                print(f"  x {line}", file=sys.stderr)
            return OWED
        return OK

    if args.action == "defer":
        try:
            text = Path(args.record_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"[carried_items] cannot read the record file: {exc}", file=sys.stderr
            )
            return ERROR
        record = defer(
            args.phase_dir,
            args.item_id,
            args.to,
            text,
            finding=args.finding,
            spec_ids=args.spec_id,
            no_requirement=args.no_requirement,
        )
        print(
            f"{record['id']} deferred to {record['owner']} (Done when {', '.join(record['done_when']['checked'])} checked, clear)"
        )
        print(
            f"  Add the row to this phase's handover.md `## {SECTION_HEADING}`:\n"
            f"  | {record['id']} | {DEFERRED_KIND} | {record['title']} | {FILENAME}#deferrals (owner {record['owner']}) |"
            + (
                f"\n  and stamp verdict finding {record['finding']} `status: deferred`, "
                f"`deferred_to: {record['owner']}`."
                if record["finding"]
                else ""
            ),
            file=sys.stderr,
        )
        return OK

    if args.action == "recarry":
        record = recarry(args.phase_dir, args.item_id)
        print(
            f"{record['id']} re-carried, owned by {record['owner']}, measurement unchanged"
        )
        print(
            f"  Add the row to this phase's handover.md `## {SECTION_HEADING}`:\n"
            f"  | {record['id']} | {DEFERRED_KIND} | {record['title']} | {FILENAME}#deferrals (owner {record['owner']}) |",
            file=sys.stderr,
        )
        return OK

    if args.action == "deferred":
        problems = deferral_problems(args.phase_dir)
        if not problems:
            records = deferrals(args.phase_dir)
            print(
                f"[carried_items] {args.phase_dir}: {len(records)} deferral(s), every one backed "
                f"by a record, a row and a verdict stamp that agree.",
                file=sys.stderr,
            )
            return OK
        print(
            f"{len(problems)} disagreement(s) about what {args.phase_dir.name} deferred - a "
            f"`status: deferred` finding is resolved only by a deferral that backs it:",
            file=sys.stderr,
        )
        for line in problems:
            print(f"  x {line}", file=sys.stderr)
        return OWED

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
        "own card; a deferred-finding owned by a later phase is re-carried with "
        "`scripts/carried_items.py recarry <phase-dir> <id>`, measurement unchanged.",
        file=sys.stderr,
    )
    return OWED


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
