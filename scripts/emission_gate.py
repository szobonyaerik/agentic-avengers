#!/usr/bin/env python3
"""A producer that stopped producing must not read as a clean result.

Two facts about a phase are emitted by a stage as it runs, and both went silently missing across two
measured phases of one build. Neither absence was detectable, because in both cases the record simply
held nothing, and nothing is what a phase with nothing to report also holds.

* **Defects.** Phase 12 recorded 1 defect against at least 5 it produced; phase 13 recorded 0 against
  at least 2. Four of phase 12's and both of phase 13's were found by the Verifier BY EXECUTING CODE
  and described in full in the phase status log and the PR body. `found_by` is the one field in
  firstmate's record that cannot be reconstructed afterwards, so a record written by hand at retro
  time is a measurement of the operator's attention rather than of the pipeline.
* **The close stamp.** Issue #46 correctly moved `closed` from implementation-finish to landing.
  Nothing emitted it at landing, so a premature stamp was replaced by no stamp: phase 12 landed on
  2026-08-21T11:15:08Z with `closed`, `elapsed_minutes`, `tests_before`, `tests_after` and
  `verification_attempts` all still null, and all six were entered by hand hours later. The
  hypothesis measuring this reads "zero close-correction overrides", which looked like success and
  actually described a producer that had stopped.

The emissions themselves live where the fact is decided (`pipeline_metrics.py`, driven from
`hook_verifier.sh` on every verdict write and from `hook_phase_close.sh` when a commit lands). THIS
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

`close` asks whether a LANDED phase carries a null `closed`. It reads only phases that already have
a record of this project, for the same reason: a phase this pipeline never measured has no producer
to have stopped. It cannot tell a phase closed under a cap from one closed cleanly, and it says
nothing about whether the value stamped is correct — only that something stamped one.

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


def landed_phases(root: Path) -> list[Path]:
    """Every phase directory under `root` that has landed — its artifacts all committed."""
    found = []
    for handover in sorted(root.glob("docs/features/*/phases/*/handover.md")):
        phase_dir = handover.parent
        if metrics.phase_landed(phase_dir) is True:
            found.append(phase_dir)
    return found


def check_close(root: Path) -> tuple[int, list[str]]:
    """A landed phase must not carry a null `closed`.

    Read the trap this exists for before narrowing it: the metric that watched this reads "count of
    overrides correcting a close stamp", and it reported zero — not because the stamp got correct,
    but because no stamp was emitted at all. A check that only looks for a WRONG value can never see
    a producer that stopped.
    """
    problems: list[str] = []
    checked = 0
    for phase_dir in landed_phases(root):
        try:
            record = _record(str(phase_dir))
        except Undecidable as exc:
            return UNDECIDABLE, [str(exc)]
        if record is None:
            continue
        checked += 1
        if record.get("closed") is None:
            problems.append(
                f"{phase_dir.relative_to(root)}: this phase has LANDED and its record still carries "
                f"closed=null. Nothing emitted the close stamp, so `elapsed_minutes`, `tests_after` "
                f"and the phase's headline cost are missing too."
            )
    if problems:
        problems.append(
            "  Stamp it where it lands: pipeline_metrics.py phase-close <phase-dir>, which "
            "`scripts/hook_phase_close.sh` runs after the commit that lands the phase."
        )
        return GAP, problems
    if checked == 0:
        return CLEAN, [
            "NOT CHECKED: no landed phase of this project has a metrics record — nothing measured "
            "these phases, so nothing here can say the measurement is missing."
        ]
    return CLEAN, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    defects = sub.add_parser("defects", help="the record carries every defect this phase concluded")
    defects.add_argument("phase_dir", nargs="?", help="one phase; omit with --root to sweep")
    defects.add_argument("--root", default=None, help="sweep the phases this change touches")
    close = sub.add_parser("close", help="no landed phase carries a null `closed`")
    close.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    if args.command == "defects":
        if args.phase_dir:
            code, lines = check_defects(args.phase_dir)
        elif args.root:
            code, lines = sweep_defects(Path(args.root).resolve())
        else:
            parser.error("defects needs a phase directory or --root")
    else:
        code, lines = check_close(Path(args.root).resolve())
    for line in lines:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
