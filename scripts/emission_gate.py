#!/usr/bin/env python3
"""A producer that stopped producing must not read as a clean result.

A fact about a phase is emitted by the stage that observes it, and when that stage stops emitting,
the record simply holds nothing - which is also what a phase with nothing to report holds. Neither
absence below was detectable for exactly that reason.

* **Defects.** Phase 12 recorded 1 defect against at least 5 it produced; phase 13 recorded 0 against
  at least 2. Four of phase 12's and both of phase 13's were found by the Verifier BY EXECUTING CODE
  and described in full in the phase status log and the PR body. `found_by` is the one field in
  firstmate's record that cannot be reconstructed afterwards, so a record written by hand at retro
  time is a measurement of the operator's attention rather than of the pipeline.
The emissions themselves live where the fact is decided (`pipeline_metrics.py`, driven from
`hook_verifier.sh` on every verdict write). THIS
module is the other half: the check that makes their absence visible. It decides nothing about
whether a phase is good — only whether the record says as much as the phase's own artifacts do.

## What each check compares, and what it CANNOT see

`defects` compares the number of distinct finding ids across the phase's whole verdict history —
`verdict.json` plus every `verdict-attempt-<n>.json` — against the defects recorded with
`found_by: verifier`. Limits, stated rather than implied:

* **Only the Verifier's own `findings[]` count.** A defect described only in a PR body, a phase
  status log, a chat transcript or a commit message is invisible here, and phase 12's five included
  such cases. This check would have caught four of that phase's five, not all of them.
* **No other stage is covered.** The implementer, the Breaker, the spec gate and mutation each
  conclude defects, and nothing here compares their output to the record. The mutation gate has its
  own emission (`record_mutation_survivors`); the others do not, and that gap is real.
* **It counts entries, not defects.** One finding may describe two defects and the check still
  passes; it is a floor, never an equality.
* **A phase with no metrics record is NOT CHECKED**, and says so on stderr. That is the standing
  state of any repository with no firstmate writer configured, and blocking there would make a
  measurement layer into a delivery outage.

Exit codes follow this repository's rule that every stop names which: **0** clean or not checked,
**1** the obligation (the record says less than the artifacts do), **2** undecidable — a verdict that
will not parse, a phase path that resolves to no phase. "Emit the defects" cannot repair malformed
JSON, so the two never share a message.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
import metrics_sink as sink  # noqa: E402
import pipeline_metrics as metrics  # noqa: E402
import verifier_attempts  # noqa: E402

CLEAN = 0
GAP = 1
UNDECIDABLE = 2

#: The `found_by` value this pipeline attributes a Verifier finding to, and the id prefix
#: `record_verifier_findings` gives it. Read here rather than restated: a defect the Verifier
#: emitted is the only thing this check counts, and a second copy of that rule is the one that drifts.
VERIFIER = "verifier"


class Undecidable(Exception):
    """The check could not answer its own question. Never reported as a gap."""


def _record(phase_dir: str) -> dict | None:
    """This project's metrics record for `phase_dir`, or None when there is none to read.

    Scoped to the project, because a firstmate home holds one filename namespace for every pipeline
    it runs: phase 3 of another project is a different phase, and reading it here would judge this
    phase against somebody else's defects.
    """
    phase = metrics.resolve_phase(phase_dir)
    if phase is None:
        raise Undecidable(f"{phase_dir}: no phase number could be derived from this path")
    if not sink.enabled():
        return None
    record = sink.show(phase)
    if record is None:
        return None
    name = sink.project()
    if name is not None and record.get("project") != name:
        return None
    return record


def described_findings(phase_dir: Path) -> set[str]:
    """Every finding id the Verifier has concluded in this phase, across every attempt.

    The live verdict is only the last attempt and a passing one carries no findings at all, so the
    archives are where a phase's history actually lives. An archive that will not parse is
    UNDECIDABLE rather than empty: read as empty it would lower the bar this check enforces, which
    is the silent pass the whole module exists to remove.
    """
    ids: set[str] = set()
    for path in verifier_attempts.verdict_records(phase_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Undecidable(f"{path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise Undecidable(f"{path}: the verdict is not a JSON object")
        for finding in payload.get("findings") or []:
            if isinstance(finding, dict) and str(finding.get("id") or "").strip():
                ids.add(str(finding["id"]).strip())
    return ids


def recorded_verifier_defects(record: dict) -> set[str]:
    """The defects in the record that the Verifier is credited with."""
    return {
        str(defect.get("id"))
        for defect in record.get("defects") or []
        if isinstance(defect, dict) and defect.get("found_by") == VERIFIER and defect.get("id")
    }


def check_defects(phase_dir: str) -> tuple[int, list[str]]:
    """A phase must not close with fewer recorded defects than its own verdicts describe."""
    try:
        record = _record(phase_dir)
        described = described_findings(Path(phase_dir))
    except Undecidable as exc:
        return UNDECIDABLE, [str(exc)]
    if record is None:
        return CLEAN, [
            f"NOT CHECKED: {phase_dir} has no metrics record of this project — nothing measured "
            f"this phase, so nothing here can say the measurement is missing."
        ]
    recorded = recorded_verifier_defects(record)
    if len(recorded) >= len(described):
        return CLEAN, []
    missing = sorted(f"{VERIFIER}-{fid}" for fid in described)
    return GAP, [
        f"{phase_dir}: the Verifier concluded {len(described)} finding(s) across this phase's "
        f"verdicts and the record carries {len(recorded)} defect(s) found_by={VERIFIER}.",
        f"  concluded: {', '.join(sorted(described))}",
        f"  recorded:  {', '.join(sorted(recorded)) or '(none)'}",
        "  `found_by` is the one field that cannot be reconstructed after the run, so a phase that "
        "closes here closes it forever.",
        f"  Emit them: pipeline_metrics.py verifier-findings {phase_dir} "
        f"{Path(phase_dir) / 'verdict.json'}",
        f"  (expected ids: {', '.join(missing)})",
    ]


def sweep_defects(root: Path) -> tuple[int, list[str]]:
    """Every phase the current change touches, checked for the defects it never recorded.

    DIFF-SCOPED, on the applicability boundary (CLAUDE.md §3a) and for the reason every check here
    is: this rule was added after the phases it runs on, so repository-wide it would ask "does the
    whole tree satisfy a rule we added later" instead of "does what this change is responsible for".
    When git cannot say what changed the scope is unknowable, so nothing is enforced and it is said
    out loud rather than passed over.
    """
    scope = applicability.changed_paths(root)
    if scope is None:
        return CLEAN, [
            "NOT CHECKED: git could not say what this change touches, so no phase is in scope."
        ]
    worst, lines = CLEAN, []
    for verdict in sorted(root.glob("docs/features/*/phases/*/verdict.json")):
        phase_dir = verdict.parent
        if not applicability.touched(phase_dir, scope):
            continue
        code, said = check_defects(str(phase_dir))
        worst = max(worst, code)
        lines.extend(said)
    return worst, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    defects = sub.add_parser("defects", help="the record carries every defect this phase concluded")
    defects.add_argument("phase_dir", nargs="?", help="one phase; omit with --root to sweep")
    defects.add_argument("--root", default=None, help="sweep the phases this change touches")
    args = parser.parse_args(argv)

    if args.phase_dir:
        code, lines = check_defects(args.phase_dir)
    elif args.root:
        code, lines = sweep_defects(Path(args.root).resolve())
    else:
        parser.error("defects needs a phase directory or --root")
    for line in lines:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
