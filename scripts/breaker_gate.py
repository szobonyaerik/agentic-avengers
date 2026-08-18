#!/usr/bin/env python3
"""The Breaker gate - a phase that declares `criticality: critical` does not close without a record.

## The defect (issue #45)

All four phase-8 specs and all four phase-9 specs of one measured feature declared
`criticality: critical`, which is what `commands/avenger-run.md` §4 reads as the signal to run the
Breaker. It was owed twice and ran neither time - there are zero occurrences of "breaker" anywhere in
that feature's docs or tests. This was never explained by a wedged stage resolver (that reading was
withdrawn); the resolver was never asked the question in the first place, because nothing enforced it.

The Breaker is not decorative: it found phase 8's credential leaks by constructing inputs nothing else
would. Whatever it would have found in phases 8 and 9 was never looked for, and nothing noticed -
a stage that emits nothing is indistinguishable from a stage that never ran.

## The fix

Documentation already said the Breaker should run; that sentence enforced nothing, which is the exact
defect shape this fixes. The Breaker now persists a **record** (`breaker.json`, beside `verdict.json`)
naming its verdict and what it actually attacked, and a phase that OWES one - any spec in it declares
`criticality: critical` - does not close without a valid one on disk. Same shape as
`carried_items.py`: a mechanical, diff-scoped check run from `hook_verifier.sh` on every handover and
from `gate_ci.sh` in CI, plus a state the resolver (`pipeline_state.py`) reports so `/avenger-run`
routes to the Breaker itself rather than a human remembering to.

A record is not proof the Breaker probed anything real - that is still the agent's job
(`agents/avenger-breaker.md`) - but a `clean` verdict naming nothing attacked, or a `found` verdict
naming no counterexample, is refused here: "a clean Breaker report with no attempts described is not
acceptable" was already the agent's own instruction, and this is what makes it checkable.

An owed phase may be excepted (`applicability.py record --rule breaker`) for a disclosed reason - no
critical path reachable, a captain-ordered cap - the same as every other rule this pipeline enforces.

Usage:
    breaker_gate.py owed <phase-dir>    exit 1 when this phase declares criticality: critical
    breaker_gate.py due <phase-dir>     exit 1 when owed and no valid record exists
    breaker_gate.py check [--root .] [--all]
                                        the `due` obligation over every phase, for CI. Diff-scoped by
                                        default; `--all` audits every phase.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import applicability  # noqa: E402
import spec_gate_state  # noqa: E402

OK = 0
OWED = 1
ERROR = 2

FILENAME = "breaker.json"
VERDICTS = ("clean", "found")
RULE = "breaker"


class BreakerGateError(Exception):
    """A record or ledger this cannot read. Always fails the caller closed."""


def _spec_files(phase_dir: Path) -> list[Path]:
    return sorted(Path(phase_dir).glob("specs/*/spec.md"))


def owed(phase_dir: Path) -> bool:
    """Whether any spec in this phase declares `criticality: critical` - what routes the Breaker.

    Read through `spec_gate_state.frontmatter`, the repository's one strict reader of a spec's
    stamps, so this asks the same question `pipeline_state._phase_criticality` does. An unreadable
    spec is not critical by construction - under-report here just means one more spec checked once
    it is readable, never a Breaker run silently waived.
    """
    for spec_file in _spec_files(phase_dir):
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if spec_gate_state.frontmatter(text).get("criticality") == "critical":
            return True
    return False


def record_path(phase_dir: Path) -> Path:
    return Path(phase_dir) / FILENAME


def _validate(data: object, path: Path) -> str | None:
    """The reason this record does not satisfy the obligation, or None when it does."""
    if not isinstance(data, dict):
        return f"{path} is not a JSON object"
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        return f"{path} has no readable verdict (must be one of {VERDICTS}, got {verdict!r})"
    if verdict == "clean":
        attacked = data.get("attacked")
        if not isinstance(attacked, list) or not attacked:
            return (
                f"{path} verdict is 'clean' but names nothing attacked - a clean report with no "
                f"attempts described is not evidence the path was probed"
            )
    else:
        counterexamples = data.get("counterexamples")
        if not isinstance(counterexamples, list) or not counterexamples:
            return (
                f"{path} verdict is 'found' but names no counterexample (test path/id) routed to "
                f"the implementer"
            )
    return None


def satisfied(phase_dir: Path) -> str | None:
    """The reason this phase's Breaker obligation is not met, or None when it is.

    Distinct from `owed()`: a phase that owes nothing is trivially satisfied. A missing record and a
    malformed one both return a reason - the caller does not need to tell them apart to act, only to
    report a phase without one as not silently passing.
    """
    path = record_path(phase_dir)
    if not path.is_file():
        return (
            f"{phase_dir} declares criticality: critical and has no {FILENAME} - the Breaker is "
            f"owed and has left no record it ran"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"{path} could not be read as JSON ({exc})"
    return _validate(data, path)


def due(phase_dir: Path) -> str | None:
    """The reason this phase does not close, or None when the Breaker obligation is clear.

    A phase that does not owe a Breaker run is clear by construction, before any exception is
    consulted - `excepted()` exists for a phase that DOES owe one and was deliberately not run.
    """
    if not owed(phase_dir):
        return None
    reason = satisfied(phase_dir)
    if reason is None:
        return None
    exception = _excepted(phase_dir)
    if exception is not None:
        return None
    return reason


def _excepted(phase_dir: Path) -> applicability.Exception_ | None:
    try:
        record = applicability.excepted(Path(phase_dir), RULE, Path(phase_dir).name)
    except applicability.ApplicabilityError as exc:
        print(
            f"[breaker_gate] {phase_dir} has an exception ledger this cannot read ({exc}). "
            f"No exception is granted - the Breaker run is still owed.",
            file=sys.stderr,
        )
        return None
    if record is not None:
        print(
            f"[breaker_gate] {phase_dir} - `{RULE}` is not owed: {record.describe()}",
            file=sys.stderr,
        )
    return record


def closed_phases(root: Path) -> list[Path]:
    """Every phase directory holding at least one spec, in path order."""
    return sorted(
        {spec.parents[2] for spec in Path(root).glob("docs/features/*/phases/*/specs/*/spec.md")}
    )


def check(root: Path, *, enforce_all: bool = False) -> list[str]:
    """The Breaker obligation across the repository. **Diff-scoped unless `enforce_all`.**

    Same rationale as `carried_items.check`: this obligation lands on a phase directory tree every
    consumer repo already has on disk, so a full audit would fail CI over phases that closed before
    this rule existed. `hook_verifier.sh` enforces it on the phase being closed - which is a phase the
    diff touches by construction - so nothing is lost by scoping the sweep; `--all` is the audit for
    anyone who wants it.
    """
    phases = closed_phases(root)
    if not phases:
        print(f"[breaker_gate] no phases with specs under {root} - nothing to check", file=sys.stderr)
        return []

    scope: set[Path] | None = None
    if not enforce_all:
        scope = applicability.changed_paths(Path(root))
        if scope is None:
            print(
                f"[breaker_gate] git cannot say what changed under {root}, so the scope is "
                f"unknowable and no phase is checked. Run `check --all` for a full audit.",
                file=sys.stderr,
            )
            return []

    problems: list[str] = []
    unenforced = 0
    for phase in phases:
        reason = due(phase)
        if reason is None:
            continue
        if enforce_all or applicability.touched(phase, scope):  # type: ignore[arg-type]
            problems.append(reason)
        else:
            unenforced += 1
    if unenforced:
        print(
            f"[breaker_gate] {unenforced} phase(s) predate this rule or are untouched and are not "
            f"enforced - they are checked when you next change them.",
            file=sys.stderr,
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    """The CLI. **Exit 1 means the obligation, and exit 2 means it could not be DECIDED.**"""
    try:
        return _dispatch(_parse(argv))
    except BreakerGateError as exc:
        print(f"[breaker_gate] {exc}", file=sys.stderr)
        return ERROR
    except Exception as exc:  # noqa: BLE001 - an undecidable check is never an owed item
        print(f"[breaker_gate] the check could not be decided: {exc!r}", file=sys.stderr)
        return ERROR


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    for name in ("owed", "due"):
        p = sub.add_parser(name)
        p.add_argument("phase_dir", type=Path)

    p_check = sub.add_parser("check")
    p_check.add_argument("--root", default=".", type=Path)
    p_check.add_argument("--all", action="store_true", help="every phase, not just changed ones")

    return parser.parse_args(argv)


def _dispatch(args: argparse.Namespace) -> int:
    if args.action == "owed":
        is_owed = owed(args.phase_dir)
        print("owed" if is_owed else "not owed")
        return OWED if is_owed else OK

    if args.action == "due":
        reason = due(args.phase_dir)
        if reason is None:
            print(f"[breaker_gate] {args.phase_dir} - Breaker obligation clear")
            return OK
        print(
            "breaker: this phase declares criticality: critical, which is what routes the Breaker "
            "(commands/avenger-run.md §4) - and it has no valid record that it ran:",
            file=sys.stderr,
        )
        print(f"  x {reason}", file=sys.stderr)
        print(
            "  Run plan-build-verify:avenger-breaker over the phase's critical/security paths; it "
            f"persists {FILENAME} with `verdict: clean|found` and what it attacked. Or, if the run "
            "is deliberately waived, record why: applicability.py record <phase-dir> --rule breaker "
            "--subject <phase> --reason-file <f> --recorded-by <who>.",
            file=sys.stderr,
        )
        return OWED

    problems = check(args.root, enforce_all=args.all)
    if not problems:
        return OK
    print(
        "breaker: a phase that declares criticality: critical does not close without a Breaker "
        "record - the Breaker found real defects nothing else caught, and a phase with no record is "
        "indistinguishable from one nobody ever ran:",
        file=sys.stderr,
    )
    for line in problems:
        print(f"  x {line}", file=sys.stderr)
    return OWED


if __name__ == "__main__":
    raise SystemExit(main())
