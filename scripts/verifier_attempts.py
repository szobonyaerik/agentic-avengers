#!/usr/bin/env python3
"""The verification attempt cap, and the trickle it exists to stop.

Measured across 8 phases of one feature: **28 verification attempts, 20 of them re-attempts, and 16
of those 20 caused by a finding the Verifier itself generated.** 80% of the loop was the gate feeding
itself. One phase's attempt-by-attempt new-finding counts were **6, 2, 8, 4, 2, 1, 0, 6** - a gate
disclosing a subset of what it could already see, one round at a time, each round costing a full
re-verification.

Two things follow, and this module is both of them.

**The cap.** Three attempts per phase. At the cap the loop stops: whatever is still open is carried
as a **known-open finding** in the phase `handover.md`, waived explicitly, or escalated to a human -
all three are honest, and a fourth silent attempt is not. The cap is a real trade and it is named:
some findings are carried rather than fixed. The measured phase already demonstrated that is
survivable and recordable.

**Bundled route-backs.** A gate must route back everything it can see in one bundle. A drop in new
findings per attempt is not evidence of convergence when the same gate later produces six more, so
this prints the per-attempt series alongside the cap: a trickle is visible in the number rather than
inferred from a feeling. It cannot *prove* a route-back was bundled - no script can know what a model
saw and withheld - so it makes the shape legible and leaves the judgement where judgement lives.

Attempt counts come from `verdict.json`'s `attempt` field and the `verdict-attempt-<n>.json` archives
beside it, which the Verifier already writes.

**Exit 1 means the cap and nothing else.** An uncaught exception also exits 1, so an unguarded
`int()` over a malformed `attempt` field made a *crash* arrive at `hook_verifier.sh` as a cap: the
handover was refused with "a further attempt is refused — carry, waive or escalate", for a phase that
might be on attempt 1, whose actual problem was an unreadable file that none of those three remedies
can fix. A failure indistinguishable from a judgement is the defect class this whole overhaul exists
to remove, so every unexpected error here exits **2** with its own cause, the same
`0 = within / 1 = capped / 2 = error` convention every sibling script uses.

Usage:
    verifier_attempts.py check <phase-dir> [--max N]   exit 0 = within cap, 1 = at/over,
                                                       2 = error (unreadable record, or any
                                                       unexpected failure — never the cap)
    verifier_attempts.py series <phase-dir>            print attempt -> new findings, one per line
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# "Still open" is ONE rule — `status: open` and not waived by break-glass — and `verdict_findings`
# owns it. Restating it here as "the findings array is empty" made the cap unclearable by the very
# remedy its own message prescribes: waive the remainder, the Verifier writes `pass` with
# `bypassed: true` and the waived findings still in the array, and CI stayed red with no action left
# that could clear it. (It used to live in `verifier_bundle_scope`, which scoped the cross-family
# review bundle; that pass is gone and the rule moved house rather than being copied.)
from verdict_findings import open_findings  # noqa: E402

WITHIN = 0
CAPPED = 1
ERROR = 2

#: Attempts a phase gets before the loop stops and the remainder is carried, waived or escalated.
DEFAULT_MAX_ATTEMPTS = 3

ARCHIVE = re.compile(r"\Averdict-attempt-(\d+)\.json\Z")


class UnreadableVerdict(Exception):
    """A verdict record whose attempt this module cannot read.

    Raised rather than guessed at: an attempt number invented for a malformed record would be the
    cap deciding on a fact it does not have. It is caught in `main` and reported as ERROR with its
    own cause, never as the cap.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _attempt_number(value: object) -> int | None:
    """`attempt` as a whole number, or None when the record does not carry a readable one.

    Guarded the way `_read` guards the JSON around it. `bool` is excluded deliberately: `True` is an
    `int` in Python and an attempt of "true" is a malformed record, not attempt 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value.strip())
    return None


def _numbered(data: dict, path: Path, fallback: int) -> int:
    """The record's attempt number, its `fallback` when it declares none, or a hard error.

    An absent, empty or zero `attempt` falls back, exactly as before — attempts are 1-based, so those
    all mean "not declared". A DECLARED attempt that is not a whole number is an unreadable record.
    """
    raw = data.get("attempt")
    if raw is None or raw == "" or raw == 0:
        return fallback
    number = _attempt_number(raw)
    if number is None:
        raise UnreadableVerdict(
            path, f"`attempt` is {raw!r}, which is not a whole number of attempts"
        )
    return number or fallback


class Attempt(NamedTuple):
    """One verification attempt on record.

    `findings` is every finding it raised — that is the trickle series, and a finding that was later
    fixed or waived still happened. `unresolved` is the subset still open and unwaived, which is the
    only count a verdict can be judged clean against.
    """

    number: int
    findings: int
    verdict: str
    unresolved: int


def _attempt(number: int, data: dict) -> Attempt:
    findings = [f for f in (data.get("findings") or []) if isinstance(f, dict)]
    return Attempt(
        number=number,
        findings=len(data.get("findings") or []),
        verdict=str(data.get("verdict") or "?"),
        unresolved=len(open_findings(findings)),
    )


def attempts(phase_dir: Path) -> list[Attempt]:
    """Every attempt on record, in order.

    An ARCHIVE with an unreadable `attempt` does not stop the walk: its filename already carries the
    number authoritatively, so falling back to it invents nothing, and losing the whole series to one
    malformed archive would hide the trickle this module exists to make visible. The LIVE
    `verdict.json` has no such second source — it is the record the cap decides on — so an unreadable
    one raises rather than being guessed at or skipped.
    """
    seen: dict[int, Attempt] = {}
    for path in sorted(Path(phase_dir).glob("verdict-attempt-*.json")):
        archived = ARCHIVE.match(path.name)
        if not archived:
            continue
        data = _read(path)
        if data is None:
            continue
        from_name = int(archived.group(1))
        try:
            number = _numbered(data, path, from_name)
        except UnreadableVerdict as exc:
            print(
                f"  (archive {path.name}: {exc.reason} — using the number in its filename)",
                file=sys.stderr,
            )
            number = from_name
        seen[number] = _attempt(number, data)
    live_path = Path(phase_dir) / "verdict.json"
    live = _read(live_path)
    if live is None and live_path.exists():
        raise UnreadableVerdict(live_path, "the file is not readable JSON describing an object")
    if live is not None:
        number = _numbered(live, live_path, max(seen) + 1 if seen else 1)
        seen[number] = _attempt(number, live)
    return [seen[n] for n in sorted(seen)]


def current(phase_dir: Path) -> int:
    """The highest attempt number on record; 0 when the phase has never been verified."""
    records = attempts(phase_dir)
    return records[-1].number if records else 0


def main(argv: list[str] | None = None) -> int:
    """Exit 1 means the cap. Everything unexpected is ERROR, and says which.

    The catch-all is the point, not the belt: guarding one `int()` fixes one crash, while an
    uncaught exception of ANY kind exiting 1 makes the next one arrive at `hook_verifier.sh` as a cap
    again, prescribing three remedies that cannot apply. `SystemExit` from argparse is a
    `BaseException` and passes through untouched.
    """
    try:
        return _check(argv)
    except UnreadableVerdict as exc:
        print(
            f"[verifier_attempts] cannot read the verification record: {exc}\n"
            f"  This is NOT the attempt cap and the phase may be well under it — carrying, waiving\n"
            f"  or escalating findings cannot fix it. Repair {exc.path} so its `attempt` is the\n"
            f"  whole number the Verifier writes, then run this again.",
            file=sys.stderr,
        )
        return ERROR
    except Exception as exc:  # noqa: BLE001 — an unexpected failure is an ERROR, never a cap
        print(
            f"[verifier_attempts] unexpected failure deciding the attempt cap: "
            f"{type(exc).__name__}: {exc}\n"
            f"  Reported as an error rather than a cap: the loop was never judged.",
            file=sys.stderr,
        )
        return ERROR


def _check(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "series"))
    parser.add_argument("phase_dir", type=Path)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_ATTEMPTS)
    args = parser.parse_args(argv)

    if not args.phase_dir.is_dir():
        print(f"[verifier_attempts] no such phase directory: {args.phase_dir}", file=sys.stderr)
        return ERROR

    records = attempts(args.phase_dir)
    if args.action == "series":
        for record in records:
            print(
                f"attempt {record.number}: {record.findings} finding(s), verdict {record.verdict}"
            )
        return WITHIN

    latest = records[-1] if records else Attempt(0, 0, "?", 0)
    if latest.number < args.max:
        return WITHIN
    if latest.verdict == "pass" and latest.unresolved == 0:
        # At the cap but RESOLVED: the cap is on the LOOP, and a phase whose latest verdict passes
        # with nothing left open has ended the loop. Stopping it would refuse the phase for its own
        # history, with no action left that could ever clear it.
        #
        # "Resolved" is `open_findings` — every finding either `fixed` or waived — and not "the
        # findings array is empty", because waiving the remainder is one of the three remedies this
        # cap's own message prescribes, and the Verifier records a waiver by leaving the finding in
        # place with `break_glass`. A check its prescribed remedy cannot satisfy is a wedge.
        #
        # The cap still binds where it matters: a verdict of `fail` at or past the cap stops here,
        # which is what refuses a further attempt.
        return WITHIN
    print(
        f"verifier: phase {args.phase_dir.name} is at attempt {latest.number} of a cap of "
        f"{args.max}, and the latest verdict is '{latest.verdict}' with {latest.unresolved} "
        f"unresolved finding(s) of {latest.findings} raised.",
        file=sys.stderr,
    )
    for record in records:
        print(
            f"    attempt {record.number}: {record.findings} finding(s), verdict {record.verdict}",
            file=sys.stderr,
        )
    print(
        "\n  The loop stops here. 80% of re-attempts measured across one feature were the Verifier\n"
        "  routing back to ITSELF, and a per-attempt trickle of new findings is a gate disclosing a\n"
        "  subset of what it can already see. Choose one, and say which in handover.md:\n"
        "    - carry the remaining findings as KNOWN-OPEN in the phase handover, or\n"
        "    - waive them explicitly (scripts/bypass_log.sh verifier <finding-id> <who>), or\n"
        "    - escalate to a human.\n"
        "  A fourth attempt is not one of the three.",
        file=sys.stderr,
    )
    return CAPPED


if __name__ == "__main__":
    raise SystemExit(main())
