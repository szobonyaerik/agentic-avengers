#!/usr/bin/env python3
"""One number held three species of override, and it could not tell them apart.

The metric counted "any override naming a record correction". Three different things answer to that
description, they have nothing in common, and reading them as one number says nothing:

* a **corrupted measurement** — the record held a value that was wrong, and it was rewritten;
* an **account corrected because reality differed** — nothing measured was wrong, but the story the
  record told about what happened was, and better evidence arrived;
* an **authorised waiver** — nothing is wrong at all, a rule was deliberately set aside by somebody
  entitled to set it aside.

Phase 12 of one measured build carried two firstmate self-corrections plus a captain waiver; phase
13 carried a single deliberate break-glass. It has been named in five consecutive retros.

## Where the class is recorded

**On the override's own `scope`, as a tag: `<class>: <text>`.** Not as a new field, because
firstmate owns the record's schema and it is CLOSED at both levels — a new key is their decision and
would be refused by their `validate` on the way in (`docs/pipeline-metrics.md`, and CLAUDE.md §6d:
"this repo owns no part of that schema"). `scope` is a free string that `validate` already accepts,
and it is the field whose whole job is saying what the override permitted.

## Nothing is inferred

`class_of` reads the tag or answers None. It never guesses from the prose, and the prose is exactly
where the guessing went wrong: phase 12's account corrections open with the word "CORRECTED" and a
text match is what put them in the same number as a captain's waiver. A tag is the whole prefix up
to the first colon, compared against the CLOSED set — free text routinely contains a colon, so a
leading word that is not one of the three is not a class, it is a sentence.

For the same reason `count` reports **`unclassified`** as its own line rather than folding those
into any class. An override whose class nobody stated is not a waiver by default and not a
correction by default; it is an override nobody classified, and saying so is the answer.

`tag` refuses a class the set does not know, naming what was invented — the same discipline as
`spec_gate_triage.BLOCKING`. A fourth species is a deliberate edit to `CLASSES`.

## What this is not

**It is not a gate.** It is the metric, and it counts. Nothing here fails a phase over an
unclassified override: the classes exist so a hypothesis can be settled, and blocking delivery on a
retrospective vocabulary would be the tail wagging the dog. What it will not do is quietly report
three species as one number again.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_sink as sink  # noqa: E402

#: The closed set, with what each one MEANS — the distinction is the whole point, so it is stated
#: here rather than left to the name.
CLASSES: dict[str, str] = {
    "waiver": (
        "a rule was deliberately set aside by somebody entitled to set it aside. Nothing in the "
        "record is wrong; the pipeline did less than its rules ask, on purpose and on the record. "
        "Break-glass, a cross-family waiver, a disclosed exception, a captain-ordered close."
    ),
    "measurement-correction": (
        "a value the record already held was WRONG and was rewritten. The producer misfired — a "
        "close stamped before landing, a suite counted two different ways."
    ),
    "account-correction": (
        "nothing measured was wrong; the ACCOUNT the record gave of what happened was, and better "
        "evidence arrived. Kept rather than deleted, because a corrected record is evidence."
    ),
}

#: What a `count` line is called when the override states no class. Deliberately not one of the
#: three: an override nobody classified is not any of them by default.
UNCLASSIFIED = "unclassified"

SEPARATOR = ": "


class UnknownClass(Exception):
    """A class the closed set does not know. Named rather than accepted or silently dropped."""


class NoRecord(Exception):
    """There is no record to count. Not the same answer as a record holding no overrides."""


def tag(name: str, text: str) -> str:
    """Render an override's `scope` carrying its class."""
    if name not in CLASSES:
        raise UnknownClass(
            f"'{name}' is not one of the override classes ({', '.join(CLASSES)}). A fourth species "
            f"is a deliberate edit to override_classes.CLASSES, never a word a caller invents: the "
            f"whole defect this replaces is three species sharing one number."
        )
    return f"{name}{SEPARATOR}{text}"


def class_of(scope: str | None) -> str | None:
    """The class this scope states, or None when it states none. Never inferred from the prose."""
    if not isinstance(scope, str) or SEPARATOR not in scope:
        return None
    head = scope.split(SEPARATOR, 1)[0]
    return head if head in CLASSES else None


def scope_text(scope: str) -> str:
    """The scope with its class tag removed, for a reader that wants only what it permitted."""
    return scope.split(SEPARATOR, 1)[1] if class_of(scope) else scope


def count(phase: str) -> dict[str, list[str]]:
    """The phase's overrides, grouped by class, each group naming its override ids."""
    if not sink.enabled():
        raise NoRecord(
            f"phase {phase}: no metrics writer is configured, so there is no record to count. Zero "
            f"overrides and no record are different answers, and reading one as the other is the "
            f"failure this metric exists to stop."
        )
    record = sink.show(phase)
    if record is None:
        raise NoRecord(f"phase {phase}: no record exists to count.")
    grouped: dict[str, list[str]] = {name: [] for name in (*CLASSES, UNCLASSIFIED)}
    for override in record.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        grouped[class_of(override.get("scope")) or UNCLASSIFIED].append(
            str(override.get("id") or "(no id)")
        )
    return grouped


def render(phase: str, grouped: dict[str, list[str]]) -> str:
    total = sum(len(ids) for ids in grouped.values())
    lines = [f"phase {phase}: {total} override(s)"]
    for name, ids in grouped.items():
        lines.append(f"  {name:<24}{len(ids):>3}  {', '.join(ids)}".rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="count a phase's overrides by class")
    sub = parser.add_subparsers(dest="command", required=True)
    counted = sub.add_parser("count", help="one line per class, plus unclassified")
    counted.add_argument("phase")
    tagged = sub.add_parser("tag", help="render a scope carrying its class")
    tagged.add_argument("name")
    tagged.add_argument("text")
    sub.add_parser("classes", help="the closed set and what each class means")
    args = parser.parse_args(argv)

    if args.command == "classes":
        for name, meaning in CLASSES.items():
            print(f"{name}\n  {meaning}")
        return 0
    if args.command == "tag":
        try:
            print(tag(args.name, args.text))
        except UnknownClass as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    try:
        grouped = count(args.phase)
    except NoRecord as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render(args.phase, grouped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
