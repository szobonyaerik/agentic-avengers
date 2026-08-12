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

Usage:
    verifier_attempts.py check <phase-dir> [--max N]   exit 0 = within cap, 1 = at/over, 2 = error
    verifier_attempts.py series <phase-dir>            print attempt -> new findings, one per line
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

WITHIN = 0
CAPPED = 1
ERROR = 2

#: Attempts a phase gets before the loop stops and the remainder is carried, waived or escalated.
DEFAULT_MAX_ATTEMPTS = 3

ARCHIVE = re.compile(r"\Averdict-attempt-(\d+)\.json\Z")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def attempts(phase_dir: Path) -> list[tuple[int, int, str]]:
    """(attempt number, finding count, verdict) for every attempt on record, in order."""
    seen: dict[int, tuple[int, str]] = {}
    for path in sorted(Path(phase_dir).glob("verdict-attempt-*.json")):
        if not ARCHIVE.match(path.name):
            continue
        data = _read(path)
        if data is None:
            continue
        number = int(data.get("attempt") or ARCHIVE.match(path.name).group(1))
        seen[number] = (len(data.get("findings") or []), str(data.get("verdict") or "?"))
    live = _read(Path(phase_dir) / "verdict.json")
    if live is not None:
        number = int(live.get("attempt") or (max(seen) + 1 if seen else 1))
        seen[number] = (len(live.get("findings") or []), str(live.get("verdict") or "?"))
    return [(n, *seen[n]) for n in sorted(seen)]


def current(phase_dir: Path) -> int:
    """The highest attempt number on record; 0 when the phase has never been verified."""
    records = attempts(phase_dir)
    return records[-1][0] if records else 0


def main(argv: list[str] | None = None) -> int:
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
        for number, findings, verdict in records:
            print(f"attempt {number}: {findings} finding(s), verdict {verdict}")
        return WITHIN

    latest = records[-1] if records else (0, 0, "?")
    if latest[0] < args.max:
        return WITHIN
    if latest[0] == args.max and latest[2] == "pass" and latest[1] == 0:
        # At the cap but clean: the cap is on the LOOP, not on a phase that finished on its LAST
        # ALLOWED attempt. Failing here would turn a successful third attempt into a stop.
        #
        # Deliberately `== args.max`, not `>=`: an attempt PAST the cap is not an allowed attempt, so
        # a clean fourth verdict does not get the exemption a clean third does. Read as `>=` this
        # exemption was the cap's own escape hatch — every attempt after the third could clear it by
        # eventually passing, which is precisely the unbounded loop the cap exists to stop.
        return WITHIN
    print(
        f"verifier: phase {args.phase_dir.name} is at attempt {latest[0]} of a cap of {args.max}, "
        f"and the latest verdict is '{latest[2]}' with {latest[1]} finding(s).",
        file=sys.stderr,
    )
    for number, findings, verdict in records:
        print(f"    attempt {number}: {findings} finding(s), verdict {verdict}", file=sys.stderr)
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
