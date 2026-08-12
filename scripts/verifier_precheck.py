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

What this deliberately does NOT do is judge anything. Coverage judged per `binding:`, reading a green
suite for gamed tests, and adversarial execution against secrets, resource lifetimes and concurrency
invariants stay with the Verifier: they are the three jobs no script can do, and they produced all
three of its user-visible defects.

**Scope: you are responsible for what you change.** Run with no target it checks the phases the
current diff touches, which is what lets it run on *every* commit rather than only on `--full` - a
check that runs once at the end is exactly the "nothing checked it continuously" this replaces.
`--all` audits every phase in the repository, which is what CI's `--full` does. The distinction
matters to a consumer repo upgrading to this version: a full audit would hard-fail its CI over
locked, pre-rule phases nobody touched. It is the same rule as `scripts/doc_read_path.py` (whose
`changed_paths()` this reuses rather than re-implementing), the verifier bundle, the spec re-gate
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
import subprocess
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
from doc_read_path import changed_paths  # noqa: E402
from requirement_cap import declared_bindings  # noqa: E402

ACCEPTANCE_HEADING = re.compile(r"^##+[ \t]*Acceptance criteria\b", re.IGNORECASE | re.MULTILINE)


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
            found.update(re.findall(r"R\d+\.\d+\.\d+", mapping.read_text(encoding="utf-8")))
        except OSError:
            continue
    return found


def stamp_fresh(spec: Path) -> bool | None:
    """True when the spec's body is unchanged since the gate judged it; None when undecidable."""
    for gate in ("gate", "review", "fidelity"):
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, str(HERE / "spec_gate_cache.py"), "check", str(spec), gate],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # 1 = unchanged since judged (fresh). 0 = needs gating (stale). 2 = could not decide.
        if result.returncode == 1:
            return True
        if result.returncode == 2:
            return None
    return False


def check_phase(phase_dir: Path) -> list[str]:
    """Every mechanical finding for one phase, as lines. Empty means clean."""
    out: list[str] = []
    specs = sorted(phase_dir.glob("specs/*/spec.md"))
    if not specs:
        return out
    traced = traced_ids(phase_dir)

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
            out.append(
                f"{spec}: the spec body has changed since the gate judged it — the spec is UNGATED "
                f"at this commit. Write it again to re-gate, or amend it explicitly "
                f"(scripts/amendments.py)."
            )
        elif fresh is None:
            out.append(f"{spec}: gate-stamp freshness could not be decided (fail closed).")
    return out


def phase_dirs(root: Path) -> list[Path]:
    return sorted(p.parent for p in Path(root).glob("docs/features/*/phases/*/specs") if p.is_dir())


def changed_phase_dirs(root: Path) -> list[Path] | None:
    """The phases the current diff touches, or None when git cannot say what changed.

    None is not "nothing changed" and must never be read as one: the scope is unknowable, so the
    caller enforces nothing and says so. Falling back to every phase would hold a repository hostage
    to history it has not touched, which is the whole reason this is scoped.
    """
    scope = changed_paths(Path(root))
    if scope is None:
        return None
    touched: list[Path] = []
    for phase in phase_dirs(root):
        resolved = phase.resolve()
        if any(changed == resolved or resolved in changed.parents for changed in scope):
            touched.append(phase)
    return touched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phases", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="every phase under docs/features/")
    parser.add_argument("--root", default=".", type=Path)
    args = parser.parse_args(argv)

    if args.all:
        targets, mode = phase_dirs(args.root), "--all: every phase under docs/features/"
    elif args.phases:
        targets, mode = list(args.phases), "the phase directories named on the command line"
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

    print(f"  verifier pre-check scope — {mode} ({len(targets)} phase(s))", file=sys.stderr)
    if not targets:
        print("  (no phases with specs in scope — nothing to pre-check)", file=sys.stderr)
        return CLEAN

    findings: list[str] = []
    for phase in targets:
        if not Path(phase).is_dir():
            print(f"[verifier_precheck] no such phase directory: {phase}", file=sys.stderr)
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
