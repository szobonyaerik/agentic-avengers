#!/usr/bin/env python3
"""The Verifier's bookkeeping, done by a script instead of by a model, on every commit.

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

Usage:
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

#: A requirement declaration line, and the `binding:` it carries when it declares one on the same
#: line - the shape `docs/templates/spec.template.md` emits.
DECLARATION = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?(R\d+\.\d+\.\d+)\b(?P<rest>.*)$")
BINDING = re.compile(r"binding:[ \t`]*([a-z0-9-]+)", re.IGNORECASE)
ACCEPTANCE_HEADING = re.compile(r"^##+[ \t]*Acceptance criteria\b", re.IGNORECASE | re.MULTILINE)


def bound_requirements(spec: Path) -> tuple[list[str], list[str]]:
    """(requirements owed a trace, requirements exempt) for one spec.

    A requirement whose declaration says `binding: none` is exempt. One that declares no binding at
    all is **owed a trace**, not exempt: a missing binding is a spec defect, and treating it as
    exempt would let the absence of a declaration buy the absence of a test.
    """
    try:
        text = spec.read_text(encoding="utf-8")
    except OSError:
        return [], []
    owed: list[str] = []
    exempt: list[str] = []
    for line in text.splitlines():
        match = DECLARATION.match(line)
        if not match:
            continue
        rid = match.group(1)
        if rid in owed or rid in exempt:
            continue
        binding = BINDING.search(match.group("rest"))
        (exempt if binding and binding.group(1).lower() == "none" else owed).append(rid)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phases", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="every phase under docs/features/")
    parser.add_argument("--root", default=".", type=Path)
    args = parser.parse_args(argv)

    targets = phase_dirs(args.root) if args.all else list(args.phases)
    if not targets:
        if args.all:
            print("  (no phases with specs — nothing to pre-check)", file=sys.stderr)
            return CLEAN
        parser.error("give a phase directory, or --all")

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
