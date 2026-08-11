#!/usr/bin/env python3
"""The spec gate's blocking set - closed, and the verdict derived from it rather than asked for.

## Why this file exists at all

`prompts/fidelity-rubric.md` told its reviewer to *"judge - without charity ... assume gaps until the
spec proves otherwise"*, and ended with **"When unsure between REVIEW and NO-GO, choose NO-GO."**
Seven dimensions all asked "is everything covered?" and nothing anywhere asked "is this too much".
A model told to be conservative follows that literally, so the only answer a rejected spec had was
more text: one measured spec went 25k -> 51k characters across four rejected rounds, another
40k -> 57k, and the gate then flagged both for excess surface. Two rubrics asked overlapping
questions of the same document at the same moment, and one spec passed one and failed the other on
**byte-identical text**.

The replacement is two passes and one table:

  1. **Observe** (`prompts/spec-gate-observe.md`) - report every observation, with **no verdict
     pressure at all**. The observe pass cannot block anything; it does not know how.
  2. **Triage** (`prompts/spec-gate-triage.md`) - classify each observation against the closed set
     below. It emits classifications, **not a verdict**.
  3. **This module** turns classifications into the verdict, deterministically. No model decides
     whether a spec is blocked.

That third step is the load-bearing one. A closed set stated only in prose is a suggestion: the
model that drifts it back into a ratchet is the same model being asked to obey it. Here the set is
data, the verdict is `len(blocking) == 0`, and a category the table does not know is a **hard
failure** rather than a judgement call - so drift shows up as a fail-closed error naming the invented
category, not as a quietly stricter gate.

## The closed set

Exactly four things block. Everything else is a note, and **notes never block** - they land in the
spec's known-open list (`spec-notes.md`) where the implementer reads them once.

Adding a fifth category is a deliberate change to this table, reviewed as such. It is not something a
rubric edit or a well-argued observation can do at run time.

Usage:
    spec_gate_triage.py categories                       print the closed set (one per line)
    spec_gate_triage.py decide <observations> <classifications>
                                                         deterministic verdict as JSON on stdout
                                                         exit 0 = approved, 1 = blocked, 2 = error
    spec_gate_triage.py report <decision.json>           the blocking findings, as the author reads
                                                         them
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The filter's own arithmetic — how many observations became blockers and how many became notes — is
# the number this redesign is judged by, so it is recorded where the decision is actually derived.
# Measurement, never a gate: a runtime vendored without the metrics modules records nothing, and
# nothing below this line can change a verdict. Nothing is written to stdout either; the decision
# JSON is the only thing on it.
try:
    from pipeline_metrics import record_triage_decision
except ImportError:  # pragma: no cover - vendored without the metrics modules
    def record_triage_decision(**_kwargs):
        return False

APPROVED = 0
BLOCKED = 1
ERROR = 2

#: The closed blocking set. Four entries, each one a defect that makes a spec unbuildable as written.
#: The wording is the contract the triage prompt restates; `tests/test_spec_gate_triage.py` asserts
#: the prompt and this table name the same categories, so the two cannot drift apart silently.
BLOCKING: dict[str, str] = {
    "missing-requirement": (
        "a behavior the spec's own scope or goal commits to, that no requirement states"
    ),
    "contradiction": (
        "two statements in this spec that cannot both hold, or one that contradicts a binding "
        "contract the overview or the prior phase's card declares"
    ),
    "untestable-criterion": (
        "an acceptance criterion with no observable pass/fail condition at a seam - nobody can "
        "write the test it asks for"
    ),
    "unhandled-critical-edge-case": (
        "a boundary, failure, duplicate or unauthorized path on a critical surface that the spec "
        "neither handles nor consciously excludes"
    ),
}

#: Everything the triage pass sees that is not one of the four above. A note is recorded, read once
#: by the implementer, and blocks nothing. This is the whole counterweight to the ratchet: an
#: observation no longer has to be either "blocking" or "unsaid".
NOTE = "note"

CATEGORIES: tuple[str, ...] = (*BLOCKING, NOTE)


class TriageError(Exception):
    """A triage result this module refuses to turn into a verdict.

    Every instance fails the gate closed. The gate must never approve a spec because the triage pass
    answered in a shape nobody can read - that is the same fail-open as a killed hook reporting
    nothing.
    """

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause

    def render(self) -> str:
        return f"[spec-gate-triage] cause={self.cause} {self}"


@dataclass(frozen=True)
class Decision:
    """The gate's verdict, and the two lists it is derived from."""

    blocking: tuple[dict, ...]
    notes: tuple[dict, ...]

    @property
    def approved(self) -> bool:
        """Approved exactly when nothing landed in the closed blocking set."""
        return not self.blocking

    def as_json(self) -> str:
        return json.dumps(
            {
                "verdict": "APPROVED" if self.approved else "BLOCKED",
                "blocking": list(self.blocking),
                "notes": list(self.notes),
            },
            indent=2,
        )


def _load(path: Path, key: str) -> list[dict]:
    """Read one pass's JSON output and return its list under `key`."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TriageError("io", f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise TriageError("non-json", f"{path} is not JSON: {exc}") from exc
    items = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise TriageError(
            "shape",
            f"{path} has no `{key}` array - the pass did not answer in the shape the gate reads.",
        )
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise TriageError(
                "shape", f"{path}: every entry under `{key}` needs a non-empty `id`."
            )
    return items


def decide(observations: list[dict], classifications: list[dict]) -> Decision:
    """Turn classified observations into the gate's verdict. Fails closed on anything unexpected.

    Three ways this refuses rather than guesses, each one a route back to the ratchet if it guessed:

    - **an unknown category** - the triage pass invented one. Guessing "blocking" reinstates the
      ratchet; guessing "note" silently deletes a finding. Neither is a verdict.
    - **an unclassified observation** - the pass reported something and then never said what it was.
      Dropping it is a fail-open.
    - **a classification for an observation nobody made** - the two passes disagree about what is
      being judged, so neither list can be trusted.
    """
    by_id = {str(o["id"]).strip(): o for o in observations}
    seen: set[str] = set()
    blocking: list[dict] = []
    notes: list[dict] = []

    for entry in classifications:
        oid = str(entry["id"]).strip()
        category = str(entry.get("category", "")).strip().lower()
        if category not in CATEGORIES:
            raise TriageError(
                "unknown-category",
                f"observation {oid!r} was classified {category!r}, which is not in the closed set "
                f"({', '.join(CATEGORIES)}). The blocking set is closed on purpose: a category "
                f"invented at run time is how a filter drifts back into the ratchet it replaced.",
            )
        if oid not in by_id:
            raise TriageError(
                "unmatched-classification",
                f"classification {oid!r} matches no observation - the two passes are not judging "
                f"the same list.",
            )
        if oid in seen:
            raise TriageError(
                "duplicate-classification", f"observation {oid!r} was classified twice."
            )
        seen.add(oid)
        record = {**by_id[oid], "category": category, "why": entry.get("why", "")}
        (notes if category == NOTE else blocking).append(record)

    missing = sorted(set(by_id) - seen)
    if missing:
        raise TriageError(
            "unclassified",
            f"{len(missing)} observation(s) were never classified ({', '.join(missing[:5])}"
            f"{', …' if len(missing) > 5 else ''}). An unclassified observation is one the gate "
            f"would silently drop.",
        )

    return Decision(tuple(blocking), tuple(notes))


def report(decision: dict) -> str:
    """The blocking findings as the spec writer reads them, plus the note count.

    It lives here rather than in the hook because the hook is bash: the first version of this was an
    inline `python3 -c` whose quoting made it produce nothing at all, so a block arrived with an
    empty report — the precise defect this whole pass exists to remove, reintroduced by the shape of
    the caller. Prose belongs in a file and rendering belongs in the module that owns the data.
    """
    lines: list[str] = []
    for finding in decision.get("blocking") or []:
        ref = finding.get("spec_ref") or "-"
        lines.append(f"[{finding.get('category')}] {ref}: {finding.get('statement')}")
        if finding.get("why"):
            lines.append(f"    why: {finding['why']}")
    notes = len(decision.get("notes") or [])
    if notes:
        lines.append(
            f"({notes} non-blocking note(s) recorded in spec-notes.md — they do not block.)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["categories"]:
        print("\n".join(CATEGORIES))
        return APPROVED
    if len(args) == 2 and args[0] == "report":
        try:
            print(report(json.loads(Path(args[1]).read_text(encoding="utf-8"))))
        except (OSError, ValueError) as exc:
            print(f"[spec-gate-triage] cannot render the report: {exc}", file=sys.stderr)
            return ERROR
        return APPROVED
    if len(args) != 3 or args[0] != "decide":
        print(__doc__, file=sys.stderr)
        return ERROR
    try:
        observations = _load(Path(args[1]), "observations")
        decision = decide(observations, _load(Path(args[2]), "classifications"))
    except TriageError as exc:
        print(exc.render(), file=sys.stderr)
        return ERROR
    print(decision.as_json())
    record_triage_decision(
        spec_path=os.environ.get("AVENGER_METRICS_SPEC_PATH"),
        observations=len(observations),
        blocking=len(decision.blocking),
        notes=len(decision.notes),
        approved=decision.approved,
    )
    return APPROVED if decision.approved else BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
