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
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# "Still open" is ONE rule — `status: open` and not waived by break-glass — and `verifier_bundle_scope`
# already owns it, because it is what keeps a spec in the review set. Restating it here as "the
# findings array is empty" made the cap unclearable by the very remedy its own message prescribes:
# waive the remainder, the Verifier writes `pass` with `bypassed: true` and the waived findings still
# in the array, and CI stayed red with no action left that could clear it.
from verifier_bundle_scope import open_findings  # noqa: E402

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
    """Every attempt on record, in order."""
    seen: dict[int, Attempt] = {}
    for path in sorted(Path(phase_dir).glob("verdict-attempt-*.json")):
        if not ARCHIVE.match(path.name):
            continue
        data = _read(path)
        if data is None:
            continue
        number = int(data.get("attempt") or ARCHIVE.match(path.name).group(1))
        seen[number] = _attempt(number, data)
    live = _read(Path(phase_dir) / "verdict.json")
    if live is not None:
        number = int(live.get("attempt") or (max(seen) + 1 if seen else 1))
        seen[number] = _attempt(number, live)
    return [seen[n] for n in sorted(seen)]


def current(phase_dir: Path) -> int:
    """The highest attempt number on record; 0 when the phase has never been verified."""
    records = attempts(phase_dir)
    return records[-1].number if records else 0


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
