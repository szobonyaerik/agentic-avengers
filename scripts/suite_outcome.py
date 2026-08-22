#!/usr/bin/env python3
"""Did a test suite RUN, or did it merely stop? One decision, in one place.

## The defect

clickup-agents phase 12 shipped a defect that made the suite fail intermittently on one run and
HANG to its 30-second watchdog on the next. The first suite run after that code landed was clean at
1298 tests with the defect already present, and it was caught only because the implementer chose to
run the suite twice, having been fooled once before. **Nothing mechanically distinguished a hung
suite from a passing one**, which is why a single green run is not evidence in this pipeline.

An exit code cannot say it on its own:

* A watchdog kill drains whatever the child wrote and reports whatever the corpse reported.
  `verifier_evidence.record` has stored `timed_out` on every entry since it was written, and
  **nothing ever read it** - a measurement that describes the right thing and decides nothing.
* A suite that dies mid-run - a segfault, an OOM kill, a harness that cut the process off - leaves
  output with no summary in it. Every test runner in use here ends by stating how many tests it ran;
  a run with no such line did not reach the end of its own work, whatever it exited with.

So a completed suite run has to satisfy both: **it stopped on its own, and it said what it did.**

## Where this is decided

Here, and read by both callers rather than restated at either:

* `verifier_evidence.problems()` - the recorded `suite` run a verdict stands on.
* `scripts/hook_verifier.sh` - the gate's own run of the phase's tests, which also runs THROUGH
  `run` below so a hang becomes a measured watchdog kill instead of a wedged hook.

## The remedy for a runner this does not know

`SUITE_SUMMARY_PATTERN` declares the project's own summary as one regular expression and REPLACES
the defaults. That is the whole escape hatch, and it is deliberately not an off switch: a guard with
an off switch is the state this module exists to leave.

Usage:
    suite_outcome.py run [--budget S] -- <argv>...
        Run the suite bounded, stream its combined output to stdout, and exit with the child's own
        code when it COMPLETED, or INCOMPLETE (86) when it did not.

    suite_outcome.py check <log-file> [--exit-code N] [--timed-out]
        Decide over a log already on disk. Exit 0 = a completed run, 1 = not.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from proc_group import run_bounded  # noqa: E402

OK = 0
NOT_COMPLETE = 1
ERROR = 2

#: The exit code `run` reports when the child did not complete its own work. Deliberately outside
#: the range every test runner here uses for a verdict (pytest 0-5, go/cargo 0-2), so a caller can
#: tell "the suite says red" from "there is no suite result to read".
INCOMPLETE = 86

PATTERN_ENV = "SUITE_SUMMARY_PATTERN"

#: What "the runner said what it did" looks like, per runner. A closed default set with a declared
#: override, rather than a heuristic: an unrecognised runner gets ONE env var, not a silent pass.
DEFAULT_PATTERNS = (
    r"\b\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b",  # pytest, jest
    r"\bno tests ran\b",                                                             # pytest, empty
    r"\bRan\s+\d+\s+tests?\b",                                                       # unittest
    r"\btest result:\s*\w+",                                                         # cargo
    r"^(?:ok|FAIL|PASS)\s",                                                          # go test
    r"^(?:OK|FAILED)\b",                                                             # unittest tail
)

#: Seconds a suite run may take before its group is killed. Generous - these are whole test suites -
#: and bounded, because an unbounded suite inside a hook is the hang this module exists to catch.
DEFAULT_BUDGET_S = 1800
BUDGET_ENV = "SUITE_BUDGET_S"


class SuiteOutcomeError(Exception):
    """A declared pattern or budget this cannot use. Always fails the caller closed."""


def _pattern() -> re.Pattern[str]:
    """The regex that recognises a completed run: the project's declaration, or the defaults."""
    declared = (os.environ.get(PATTERN_ENV) or "").strip()
    source = declared if declared else "|".join(DEFAULT_PATTERNS)
    try:
        return re.compile(source, re.MULTILINE)
    except re.error as exc:
        raise SuiteOutcomeError(
            f"{PATTERN_ENV}={declared!r} is not a usable regular expression ({exc}). Nothing is "
            f"judged against a pattern that cannot compile - fix it, or unset it to use the "
            f"defaults."
        ) from exc


def budget_s() -> int:
    raw = (os.environ.get(BUDGET_ENV) or "").strip()
    if not raw:
        return DEFAULT_BUDGET_S
    try:
        value = int(raw)
    except ValueError as exc:
        raise SuiteOutcomeError(f"{BUDGET_ENV}={raw!r} is not an integer number of seconds") from exc
    if value <= 0:
        raise SuiteOutcomeError(f"{BUDGET_ENV}={raw!r} must be a positive number of seconds")
    return value


def summary_line(output: str) -> str | None:
    """The line on which the runner stated what it ran, or None when it never got there."""
    match = _pattern().search(output or "")
    if not match:
        return None
    line = (output or "")[: match.end()].rsplit("\n", 1)[-1]
    return line.strip() or match.group(0)


def problems(*, exit_code: int, timed_out: bool, output: str) -> list[str]:
    """Every reason this run is not a COMPLETED suite run. Empty list = it is one.

    Deliberately silent about `exit_code` being non-zero: a suite that ran and reported failures HAS
    completed, and its callers already know what to do with a red one. The question here is only
    whether there is a result to read at all.
    """
    found: list[str] = []
    if timed_out:
        found.append(
            "the run exited on its WATCHDOG rather than finishing - its process group was killed "
            "after the budget elapsed, so whatever it printed is a partial transcript and its exit "
            f"code ({exit_code}) is what the corpse reported, not a verdict"
        )
    if summary_line(output) is None:
        found.append(
            "the output carries no test-runner SUMMARY - no line stating how many tests ran - so "
            "this run never reached the end of its own work. A suite that did not finish is not a "
            f"suite that passed. If this project's runner reports differently, declare it in "
            f"{PATTERN_ENV}."
        )
    return found


def _report(found: list[str], where: str) -> None:
    print(f"suite-outcome: {where} did NOT complete:", file=sys.stderr)
    for problem in found:
        print(f"  - {problem}", file=sys.stderr)


def run(argv: list[str], budget: int | None = None) -> int:
    """Run the suite in its own process group, print what it produced, and judge whether it ran."""
    if not argv:
        raise SuiteOutcomeError("no command given - `run` needs a command after `--`")
    limit = budget if budget is not None else budget_s()
    try:
        result = run_bounded(argv, limit)
    except FileNotFoundError as exc:
        print(f"suite-outcome: the suite command could not be started: {exc}", file=sys.stderr)
        return ERROR
    output = (result.stdout or "") + (result.stderr or "")
    sys.stdout.write(output)
    sys.stdout.flush()
    found = problems(exit_code=result.returncode, timed_out=result.timed_out, output=output)
    if found:
        _report(found, f"`{' '.join(argv)}` (after {result.elapsed:.1f}s)")
        return INCOMPLETE
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    runner = sub.add_parser("run", help="run a suite bounded and judge whether it completed")
    runner.add_argument("--budget", type=int, default=None, help="seconds before the watchdog fires")
    runner.add_argument("command_argv", nargs=argparse.REMAINDER)

    checker = sub.add_parser("check", help="judge a log already on disk")
    checker.add_argument("log")
    checker.add_argument("--exit-code", type=int, default=0)
    checker.add_argument("--timed-out", action="store_true")

    args = ap.parse_args(argv)
    try:
        if args.command == "run":
            # Only the LEADING `--` is argparse's; a later one belongs to the suite command
            # (`pytest -- path`), and stripping it would silently change what was run.
            words = list(args.command_argv)
            if words and words[0] == "--":
                words = words[1:]
            return run(words, args.budget)
        try:
            text = Path(args.log).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"suite-outcome: cannot read {args.log}: {exc}", file=sys.stderr)
            return ERROR
        found = problems(exit_code=args.exit_code, timed_out=args.timed_out, output=text)
        if found:
            _report(found, args.log)
            return NOT_COMPLETE
        return OK
    except SuiteOutcomeError as exc:
        print(f"suite-outcome: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    sys.exit(main())
