#!/usr/bin/env python3
"""Which phases have finished implementing, and whether their artifact set is complete.

This is the KEY of the Stop-hook artifact sweep (`scripts/hook_artifact_check.sh`), and it exists
because that key used to be a file nothing was instructed to write.

The sweep was keyed to `implementation-report.md`: `find docs/features -name implementation-report.md`
gave it the list of phases that had finished implementing. But no template, no agent, no skill, no
command and no script ever told anyone to produce that file. Its only trace of a writer instruction
was a parenthetical list inside `skills/ponytail`, naming it among artifacts to write "to their
template every time" - a template that does not exist - and ponytail reaches only the two
implementers, and not at all under `PONYTAIL_OFF=1`. So whether the key existed at all depended on
whether an implementer happened to invent an undocumented file, and a sweep keyed to that mostly
does not run. That is issue #29's `implementation-report.md` outcome: the class is removed, and the
key is replaced by a signal the pipeline actually produces.

**A phase has finished implementing when every spec it holds is stamped `status: done`.** That is
the pipeline's own completion stamp for a spec: the implementer writes it, `hook_verifier.sh` fires
its `spec-done` trigger on it, and `scripts/spec_done_guard.py` reverts one that is not backed by a
recorded mapping row and a green suite (issue #68). It is the same stamp `applicability.py` reads to
call a spec shipped. Nothing new is written to make this check possible, which is the point.

**What it requires is ONE thing: `test-mapping.md` beside each spec, keyed to that spec's own
`status: done`.** That is where the stamp is the right key: the implementer owes a mapping row the
instant it stamps a spec done, which is exactly what `spec_done_guard.py` enforces at the stamp
itself. The old sweep looked for this file in the PHASE directory, which has not been its home since
specs became `<n>.<k>` directories, so that half asked for a file at a path the pipeline never
writes.

**It does NOT ask for `handover.md`, and that is a decision rather than an omission.**
`hook_verifier.sh` already owns whether a handover may be written, and it refuses on a passing
verdict plus six further checks: `verifier_precheck.py`, `required_skills.py audit`,
`verifier_evidence.py check`, `breaker_gate.py due`, the carried-items gate and
`emission_gate.py defects`. Any condition this sweep could ask is strictly WEAKER than that set, so
a phase always exists where the Stop hook says "create handover.md before stopping" and the handover
trigger then refuses to let anyone write it - a required skill with no observed load, a critical
phase whose Breaker never ran, a verdict with no execution transcript - and in each case the
prescribed remedy is unavailable to a stage that has ended, leaving only `GATE_BYPASS`. Duplicating
the six checks here was considered and rejected for the same reason it is rejected everywhere else
in this repository: a second, weaker copy of a rule is not extra safety, it is the drift defect.
`hook_verifier.sh` is the authoritative point for that artifact, and `doc_read_path.py check` - two
lines later in this same hook - holds the card's byte cap and its `readers:` line once it exists.

A spec that owes no mapping row at all is exempt by construction, read from
`spec_done_guard.mapping_owed`: every requirement `binding: none` gets no test and no row (§4a), and
demanding one asks the author to invent a row the rules forbid. Undecidable is not exempt - a spec
whose requirements cannot be read, or that declares none, is reported rather than skipped, the same
reading `spec_done_guard` takes.

A phase with no spec directory at all has not started, not finished: it is not swept.

**It binds only what this change is responsible for** (§3a, `scripts/applicability.py`). A phase the
diff does not touch is CLOSED to this sweep: it is counted and named on stderr through the
boundary's one spelling, never blocked. Without that, a consumer repo installing this release with
phases built under the older layout would fail every Stop from then on over locked artifacts whose
only remedy is rewriting shipped phases - the hostage failure the cost gate and the requirement cap
were scoped to remove. `check --all` is the full audit somebody runs deliberately. When git cannot
say what changed the scope is unknowable, so nothing is enforced and that is said out loud.

**What it does not decide**, stated rather than implied: it reads stamps and file presence, never
content, so a `test-mapping.md` with no real row satisfies it here - `spec_done_guard.py` holds
that at the stamp. It says nothing whatsoever about a phase's handover, its verdict, its evidence or
its Breaker record; every one of those belongs to `hook_verifier.sh`. And a phase abandoned
mid-verification is indistinguishable to it from one still being verified, which costs nothing now
that the only thing it asks for is owed at the stamp rather than at the close.

**Every report carries its own REMEDY, never one taken from a shared header.** Only one of the
three shapes `_spec_problems` can produce is a missing file; telling an author to create one for
the other two prescribes a fix that silences the report and leaves the defect exactly where it was,
because the `test-mapping.md` presence test short-circuits ahead of both.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applicability import changed_paths, report_unenforced, touched  # noqa: E402
from spec_done_guard import (  # noqa: E402
    NoRequirementsDeclared,
    UndecidableRequirements,
    mapping_owed,
)
from spec_gate_state import frontmatter  # noqa: E402

DONE = "done"

CLEAN = 0
OWED = 1  # a phase genuinely owes an artifact. The remedy is to write it.
UNDECIDABLE = 2  # the sweep could not answer at all. §6: every stop names which it is.


def spec_paths(phase_dir: Path) -> list[Path]:
    """Every `spec.md` under a phase, in a stable order."""
    return sorted(phase_dir.glob("specs/*/spec.md"))


def _status(spec_path: Path) -> str:
    """A spec's own `status:` stamp. Unreadable is not `done` - under-report, never over.

    `ValueError` is caught beside `OSError` because a spec that is not valid UTF-8 raises the
    former, and a decode error reaching the Stop hook as a traceback would fail the session with a
    stack trace instead of leaving the phase alone - which is the wrong answer twice over, since an
    unreadable spec is exactly a phase that has NOT finished.
    """
    try:
        return (
            frontmatter(spec_path.read_text(encoding="utf-8"))
            .get("status", "")
            .strip()
            .lower()
        )
    except (OSError, ValueError):
        return ""


def finished_implementing(phase_dir: Path) -> bool:
    """Whether every spec this phase holds is stamped `status: done`."""
    specs = spec_paths(phase_dir)
    return bool(specs) and all(_status(spec) == DONE for spec in specs)


def phase_dirs(root: Path) -> list[Path]:
    """Every phase directory under the artifact tree, in a stable order.

    An absent tree scans nothing. That is CLEAN, and it is said out loud rather than passing
    invisibly, because `doc_read_path.check_artifacts` says exactly that two lines later in the same
    Stop hook and two checks giving different answers to the same absence is how one of them stops
    meaning anything. Same discipline `subprocess_check.py` uses for an absent test root.
    """
    features = root / "docs" / "features"
    if not features.is_dir():
        print(f"[phase_artifacts] no {features} - nothing to check", file=sys.stderr)
        return []
    return sorted(path for path in features.glob("*/phases/*") if path.is_dir())


def _spec_problems(spec_path: Path) -> list[str]:
    """What one `status: done` spec still owes, or why that cannot be decided.

    Each string carries ITS OWN remedy, because only one of the three shapes is a missing file. The
    caller used to supply one header for all three - "create them before stopping" - and for the two
    undecidable shapes that prescription was not merely wrong, it WORKED: the `mapping.is_file()`
    short-circuit above returns clean the moment any `test-mapping.md` exists, so an agent obeying
    the header wrote an empty one and silenced a report about an unreadable spec while leaving the
    spec unreadable.
    """
    mapping = spec_path.parent / "test-mapping.md"
    if mapping.is_file():
        return []
    try:
        if not mapping_owed(spec_path):
            return []
    except NoRequirementsDeclared:
        return [
            f"{spec_path}: is `status: done` and declares no requirement at all. That is not the "
            f"`binding: none` exemption; a spec with nothing to require has nothing to have "
            f"finished. REMEDY: declare this spec's requirements, or take the stamp back off it. "
            f"Writing a `test-mapping.md` silences this report and changes nothing."
        ]
    except UndecidableRequirements as exc:
        return [
            f"{spec_path}: is `status: done` and its requirements cannot be read ({exc}), so "
            f"whether it owes a `test-mapping.md` cannot be decided - unknown is not exempt. "
            f"REMEDY: repair the requirement layout in the spec itself. Writing a "
            f"`test-mapping.md` silences this report and leaves the spec just as unreadable."
        ]
    return [
        f"{mapping}: missing. This spec is `status: done` and owes a mapping row. "
        f"REMEDY: write the row that traces its requirements to their tests."
    ]


def phase_problems(phase_dir: Path) -> list[str]:
    """Every artifact this one phase owes, or nothing when it has not finished implementing."""
    if not finished_implementing(phase_dir):
        return []
    found: list[str] = []
    for spec_path in spec_paths(phase_dir):
        found += _spec_problems(spec_path)
    return found


def problems(root: Path, *, enforce_all: bool = False) -> list[str]:
    """Every artifact a phase that finished implementing still owes, inside the change's scope.

    Diff-scoped by default (§3a): a phase this change did not touch is counted and named through
    `applicability.report_unenforced`, never blocked.
    """
    scope: set[Path] | None = None
    if not enforce_all:
        scope = changed_paths(root)
        if scope is None:
            print(
                f"[phase_artifacts] git cannot say what changed under {root}, so the scope is "
                f"unknowable and no phase is enforced. Run `check --all` for a full audit.",
                file=sys.stderr,
            )
            return []

    found: list[str] = []
    unenforced = 0
    untouched: list[str] = []
    for phase_dir in phase_dirs(root):
        owed = phase_problems(phase_dir)
        if not owed:
            continue
        if enforce_all or touched(phase_dir, scope or set()):
            found += owed
            continue
        unenforced += len(owed)
        untouched.append(_name(root, phase_dir))

    report_unenforced(
        "phase_artifacts",
        unenforced,
        f"{len(untouched)} phase(s) this change did not touch ({', '.join(untouched)}) - they are "
        f"checked when you next change them, and `check --all` audits them now",
    )
    return found


def _name(root: Path, phase_dir: Path) -> str:
    try:
        return phase_dir.relative_to(root).as_posix()
    except ValueError:
        return str(phase_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", help="every phase that finished implementing has its artifacts"
    )
    check.add_argument(
        "root", nargs="?", default=".", help="repository root (default: .)"
    )
    check.add_argument(
        "--all",
        action="store_true",
        dest="enforce_all",
        help="audit every phase, not only the ones this change touches",
    )

    listing = sub.add_parser(
        "list", help="print the phases that have finished implementing"
    )
    listing.add_argument(
        "root", nargs="?", default=".", help="repository root (default: .)"
    )

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "list":
        for phase_dir in phase_dirs(root):
            if finished_implementing(phase_dir):
                print(phase_dir.relative_to(root).as_posix())
        return CLEAN

    try:
        found = problems(root, enforce_all=args.enforce_all)
    except Exception as exc:  # noqa: BLE001 - the split IS the point, see below
        print(
            f"[phase_artifacts] could not decide what {root} owes: "
            f"{type(exc).__name__}: {exc}. Nothing is claimed missing. This is a defect in the "
            f"sweep or in an artifact it reads, not a document anyone forgot to write, so the "
            f"remedy is not 'create it'.",
            file=sys.stderr,
        )
        return UNDECIDABLE
    if found:
        print(
            "Phase artifacts owed or undecidable (each line states its own remedy):",
            file=sys.stderr,
        )
        for problem in found:
            print(f"  - {problem}", file=sys.stderr)
        return OWED
    return CLEAN


if __name__ == "__main__":
    sys.exit(main())
