#!/usr/bin/env python3
"""The Verifier's bookkeeping, done by a script instead of by a model, on every commit, diff-scoped.

A scout measured all 46 Verifier findings across 8 phases of one feature. **Twelve of them (26%)
were bookkeeping** - about the pipeline's own gate stamps, traceability rows and spec headings. On
the worst phase that was 45%, and **attempts 2 and 5 produced nothing but bookkeeping**: roughly 70
minutes of wall clock and ~410k subagent tokens for four stamp-freshness observations.

All twelve were mechanically decidable, and several said so in their own text - one names its
detection method as a `grep`, and four are `spec_gate_cache.py check`, which already existed and
already returned an exit code. The Verifier was shelling out to it by hand, once per phase, and
calling the result a finding.

So they move here. Three checks, no model, run from `hook_verifier.sh` and from CI:

  1. **Traceability** - every requirement id a spec declares appears in some `test-mapping.md` row
     for its phase. `binding: none` requirements are exempt by construction: the tiered-binding rule
     says they get no test, so demanding a row for one would hand the suite back the per-id
     multiplier that rule removed.
  2. **Stamp freshness** - `spec_gate_cache.py check` is fresh for every spec in the phase. The
     defect this catches recurred **twice, six attempts apart, in one phase**, precisely because
     nothing checked it continuously.
  3. **Structure** - every spec still has its `## Acceptance criteria` heading. One amendment
     deleted one, and the finding that caught it noted that no pipeline script parses the heading,
     so nothing broke - which is exactly the argument for a script that does.

What this deliberately does NOT do is judge anything. Coverage judged per `binding:` and adversarial
execution against secrets, resource lifetimes and concurrency invariants stay with the Verifier: they
are the two jobs no script can do, and they produced all three of its user-visible defects. The third
job it used to have - a dedicated reader for gamed tests - is gone with the cross-family reading pass
that carried it, and nothing inherits it.

**Scope: you are responsible for what you change.** Run with no target it checks the phases the
current diff touches, which is what lets it run on *every* commit rather than only on `--full` - a
check that runs once at the end is exactly the "nothing checked it continuously" this replaces.
`--all` audits every phase in the repository, which is what CI's `--full` does. The distinction
matters to a consumer repo upgrading to this version: a full audit would hard-fail its CI over
locked, pre-rule phases nobody touched. It is the same rule as `scripts/doc_read_path.py` (whose
`changed_paths()` this reuses rather than re-implementing), `verifier_evidence.py`, the spec re-gate
cache and the mutation gate - and it behaves the same way at the edge: when git cannot say what
changed, the scope is unknowable, so **nothing is enforced and the check says so out loud**. Falling
back to enforcing everything is the hostage failure the scoping removes.

The mode it ran in is always printed, because a silent fallback is how a check comes to mean
something other than what its caller believes.

Usage:
    verifier_precheck.py [--root .]                        the phases the current diff touches
    verifier_precheck.py <phase-dir> [<phase-dir> ...]     exit 0 = clean, 1 = findings, 2 = error
    verifier_precheck.py --all [--root .]                  every phase under docs/features/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CLEAN = 0
FINDINGS = 1
ERROR = 2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ONE declaration regex and ONE diff-scope mechanism, both imported rather than copied. This module
# held its own copy of the declaration regex, and both copies went blind on table-formatted specs at
# the same moment: the cap read `0/12` and this check reported zero ids owed a trace, so it passed
# vacuously on the very spec it exists to hold. A second copy of a rule is the copy that drifts.
# The diff scope comes from `applicability.py`, which owns the whole boundary this check sits on.
from applicability import (  # noqa: E402
    ApplicabilityError,
    changed_paths,
    excepted,
    report_unenforced,
    touched,
)
from requirement_cap import declared_bindings  # noqa: E402
import spec_gate_state  # noqa: E402 — the one place "is this spec gated" is decided

ACCEPTANCE_HEADING = re.compile(
    r"^##+[ \t]*Acceptance criteria\b", re.IGNORECASE | re.MULTILINE
)


def bound_requirements(spec: Path) -> tuple[list[str], list[str]]:
    """(requirements owed a trace, requirements exempt) for one spec.

    A requirement whose declaration says `binding: none` is exempt. One that declares no binding at
    all is **owed a trace**, not exempt: a missing binding is a spec defect, and treating it as
    exempt would let the absence of a declaration buy the absence of a test.

    Both the layout and where the binding sits inside it come from `requirement_cap`, which owns
    them. This module used to re-derive the second half from the declaration line alone, so widening
    the shared regex to accept headings and ordered lists silently moved a block later and gave it a
    wronger message.
    """
    try:
        text = spec.read_text(encoding="utf-8")
    except OSError:
        return [], []
    owed: list[str] = []
    exempt: list[str] = []
    for rid, binding in declared_bindings(text):
        if rid in owed or rid in exempt:
            continue
        (exempt if binding == "none" else owed).append(rid)
    return owed, exempt


def traced_ids(phase_dir: Path) -> set[str]:
    """Every requirement id mentioned anywhere in the phase's `test-mapping.md` tables.

    Mentioned, not parsed into columns: a journey row lists several ids in one cell, and a mapping
    format this over-fits would fail on a table that is perfectly legible to the reader.
    """
    found: set[str] = set()
    for mapping in phase_dir.glob("specs/*/test-mapping.md"):
        try:
            found.update(
                re.findall(r"R\d+\.\d+\.\d+", mapping.read_text(encoding="utf-8"))
            )
        except OSError:
            continue
    return found


#: A test name a row may point at. Deliberately the pytest convention and nothing wider: a cell
#: holding prose would otherwise yield "names" nobody wrote.
TEST_NAME = re.compile(r"\btest_[A-Za-z0-9_]+")

#: A skip decorator directly above a test. Python-specific, and that limit is stated in
#: `named_tests`' docstring rather than implied.
SKIP_DECORATOR = re.compile(r"^[ \t]*@(?:pytest\.mark\.)?skip(?:if)?\b", re.MULTILINE)


def _test_root(phase_dir: Path) -> Path | None:
    """`tests/<feature>/<n>-<slug>`, or the older `tests/<n>-<slug>` — whichever exists.

    The same resolution `verifier_evidence` and `hook_verifier.sh` use, so a row is held against
    exactly the tree the gate then runs. A project laid out otherwise resolves to None and is not
    held at all, which is the fail-open every check on this boundary uses.
    """
    phase = Path(phase_dir).resolve()
    if len(phase.parents) < 2:
        return None
    root = phase.parents[3] if len(phase.parents) >= 4 else Path.cwd()
    feature = phase.parents[1].name
    for candidate in (root / "tests" / feature / phase.name, root / "tests" / phase.name):
        if candidate.is_dir():
            return candidate
    return None


def named_tests(phase_dir: Path) -> list[tuple[str, str]]:
    """(test name, the mapping that names it) for every test a row points at.

    Only `test_*` tokens count. A cell holding `n/a`, a dash or the template's own placeholder names
    no test, and inventing one out of prose would report findings nobody could act on.
    """
    out: list[tuple[str, str]] = []
    for mapping in sorted(phase_dir.glob("specs/*/test-mapping.md")):
        try:
            text = mapping.read_text(encoding="utf-8")
        except OSError:
            continue
        for name in TEST_NAME.findall(text):
            out.append((name, mapping.parent.name))
    return out


def _defined_tests(tests: Path) -> dict[str, bool]:
    """Every test defined under `tests`, mapped to whether it is skipped.

    Definition and skip detection are Python-specific, and that is the honest bound of this check:
    a project whose tests are written in another language resolves no definitions here, so every
    row would read as naming a missing test. `check_phase` therefore holds a row only when the tree
    yielded at least one definition — the scope is unreadable otherwise, not violated.
    """
    found: dict[str, bool] = {}
    for path in sorted(tests.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"[ \t]*(?:async +)?def +(test_[A-Za-z0-9_]+)", line)
            if not match:
                continue
            above = "\n".join(lines[max(0, index - 6):index])
            found[match.group(1)] = bool(SKIP_DECORATOR.search(above))
    return found


def trace_claims(phase_dir: Path) -> list[str]:
    """Findings for rows whose named test does not exist, or exists and is skipped (issue #52).

    The precheck used to confirm only that a requirement id appeared in SOME row, never that the
    row's claim matched the test it names — so a row was free to assert anything and the trace was
    decorative. One measured row asserted the exact opposite of its own test and every check passed.

    **What this does NOT do, said rather than implied:** it does not read a row's prose against a
    test's assertions. Generating the claim from the test is the better fix and this is not it; a
    row whose words contradict its existing, running test still passes here.
    """
    rows = named_tests(phase_dir)
    if not rows:
        return []
    tests = _test_root(phase_dir)
    defined = _defined_tests(tests) if tests is not None else {}
    if not defined:
        print(
            f"[verifier_precheck] {phase_dir.name}: no readable test definitions under its test "
            f"tree — trace-row claims not checked",
            file=sys.stderr,
        )
        return []
    out: list[str] = []
    for name, spec_name in sorted(set(rows)):
        if name not in defined:
            out.append(
                f"{spec_name}/test-mapping.md names {name}, and there is no test by that name in "
                f"this phase's tests ({tests}). A row that names nothing is a trace that proves "
                f"nothing."
            )
        elif defined[name]:
            out.append(
                f"{spec_name}/test-mapping.md names {name}, which is SKIPPED. A skipped test is "
                f"green output over a requirement nothing exercises."
            )
    return out


def stamp_fresh(spec: Path) -> bool | None:
    """True when the spec's body is unchanged since the gate judged it; None when undecidable.

    Delegated to `spec_gate_state`, which owns what "gated" means (issue #42). This module held its
    own implementation of the question while the stage resolver asked a weaker one, so one spec was
    gated and ungated at the same time depending on which part of the pipeline asked. A second copy
    of a rule is the copy that drifts - the same reason the declaration regex above is imported.
    """
    return spec_gate_state.stamp_fresh(spec)


def excepted_stamp(phase_dir: Path, spec: Path) -> str | None:
    """Why a stale spec-gate stamp is not blocking, or None when it still blocks.

    A stamp goes stale whenever the spec body no longer matches what the gate judged — the same
    condition whether the body changed because it needs re-gating or because the gate provider was
    unreachable when it should have been re-run. Re-gating is the remedy that clears it under
    ordinary circumstances; a disclosed **exception** (`applicability.py`, rule `spec-gate`) is the
    one that still clears it when the provider is down, since recording an exception makes no gate
    call at all. An amendment does not: it re-verifies requirement ids at the Verifier, and never
    touches the spec-gate hash this check reads.

    An unreadable exception ledger grants nothing and says so — under-report, exactly as the
    resolver does everywhere else on this boundary.
    """
    try:
        record = excepted(phase_dir, "spec-gate", spec.resolve().parent.name)
    except ApplicabilityError as exc:
        print(
            f"[verifier_precheck] {phase_dir.name} has an exception ledger this cannot read "
            f"({exc}). No exception is granted.",
            file=sys.stderr,
        )
        return None
    return record.describe() if record else None


def check_phase(phase_dir: Path) -> list[str]:
    """Every mechanical finding for one phase, as lines. Empty means clean."""
    out: list[str] = []
    specs = sorted(phase_dir.glob("specs/*/spec.md"))
    if not specs:
        return out
    traced = traced_ids(phase_dir)
    out.extend(trace_claims(phase_dir))

    for spec in specs:
        owed, _exempt = bound_requirements(spec)
        untraced = [rid for rid in owed if rid not in traced]
        if untraced:
            out.append(
                f"{spec}: {len(untraced)} requirement id(s) appear in no test-mapping.md row for "
                f"this phase: {', '.join(untraced)}. (binding: none ids are exempt and not counted.)"
            )

        try:
            body = spec.read_text(encoding="utf-8")
        except OSError as exc:
            out.append(f"{spec}: unreadable ({exc})")
            continue
        if not ACCEPTANCE_HEADING.search(body):
            out.append(
                f"{spec}: no `## Acceptance criteria` heading. Nothing parses it, which is exactly "
                f"why an edit can delete it and no gate notice."
            )

        fresh = stamp_fresh(spec)
        if fresh is False:
            why = excepted_stamp(phase_dir, spec)
            if why is not None:
                report_unenforced(
                    "verifier_precheck",
                    1,
                    f"{spec} stamp is stale but a recorded exception covers it — {why}",
                )
            else:
                out.append(
                    f"{spec}: the spec body has changed since the gate judged it — the spec is "
                    f"UNGATED at this commit. Write it again to re-gate, or record a disclosed "
                    f"exception (scripts/applicability.py record {phase_dir} --rule spec-gate "
                    f"--subject {spec.resolve().parent.name} --reason-file <f>)."
                )
        elif fresh is None:
            out.append(
                f"{spec}: gate-stamp freshness could not be decided (fail closed)."
            )
    return out


def phase_dirs(root: Path) -> list[Path]:
    return sorted(
        p.parent
        for p in Path(root).glob("docs/features/*/phases/*/specs")
        if p.is_dir()
    )


def changed_phase_dirs(root: Path) -> list[Path] | None:
    """The phases the current diff touches, or None when git cannot say what changed.

    None is not "nothing changed" and must never be read as one: the scope is unknowable, so the
    caller enforces nothing and says so. Falling back to every phase would hold a repository hostage
    to history it has not touched, which is the whole reason this is scoped.
    """
    scope = changed_paths(Path(root))
    if scope is None:
        return None
    return [phase for phase in phase_dirs(root) if touched(phase, scope)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phases", nargs="*", type=Path)
    parser.add_argument(
        "--all", action="store_true", help="every phase under docs/features/"
    )
    parser.add_argument("--root", default=".", type=Path)
    args = parser.parse_args(argv)

    if args.all:
        targets, mode = phase_dirs(args.root), "--all: every phase under docs/features/"
    elif args.phases:
        targets, mode = (
            list(args.phases),
            "the phase directories named on the command line",
        )
    else:
        scoped = changed_phase_dirs(args.root)
        if scoped is None:
            print(
                f"[verifier_precheck] git cannot say what changed under {args.root}, so the scope "
                f"is unknowable and no phase is pre-checked. Run `--all` for a full audit.",
                file=sys.stderr,
            )
            return CLEAN
        targets, mode = scoped, "diff-scoped: the phases this diff touches"

    print(
        f"  verifier pre-check scope — {mode} ({len(targets)} phase(s))",
        file=sys.stderr,
    )
    if not targets:
        print(
            "  (no phases with specs in scope — nothing to pre-check)", file=sys.stderr
        )
        return CLEAN

    findings: list[str] = []
    for phase in targets:
        if not Path(phase).is_dir():
            print(
                f"[verifier_precheck] no such phase directory: {phase}", file=sys.stderr
            )
            return ERROR
        findings.extend(check_phase(Path(phase)))

    if not findings:
        return CLEAN
    print(
        "verifier pre-check: mechanical findings (these are bookkeeping, not judgement — the "
        "Verifier used to raise them by hand, once per phase, and 26% of everything it produced "
        "was this class):",
        file=sys.stderr,
    )
    for line in findings:
        print(f"  ✗ {line}", file=sys.stderr)
    return FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
