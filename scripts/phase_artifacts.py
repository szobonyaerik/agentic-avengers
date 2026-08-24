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

**The two halves it then requires are keyed to DIFFERENT moments, because they are owed at
different moments.**

  * `test-mapping.md` beside each spec - PER SPEC, not per phase - is keyed to `status: done`, where
    it is correct: the implementer owes a mapping row the instant it stamps a spec done, which is
    exactly what `spec_done_guard.py` enforces at the stamp itself. The old sweep looked for this
    file in the phase directory, which has not been its home since specs became `<n>.<k>`
    directories, so that half asked for a file at a path the pipeline never writes.
  * `handover.md` beside `verdict.json`, per phase, is keyed to a **passing verdict**, never to
    `status: done`. Between the last spec being stamped and the Verifier passing the phase lies the
    entire verification stage - up to 3 attempts plus implementer route-backs - and `hook_verifier.sh`
    REFUSES the handover write until a passing `verdict.json` exists. Keyed on the stamp, this
    check demanded a document the pipeline's own rules forbid writing yet, and `/avenger-run` is
    resumable across sessions, so a session ending inside that window is ordinary usage rather than
    an unfinished artifact set.

"Passing verdict" is not re-derived here. `verifier_attempts.attempts` owns what the verdict record
is (the live `verdict.json` plus its `verdict-attempt-<n>.json` archives) and `verdict_findings`
owns what "still open" means, so this asks them: the latest attempt says `pass` and carries no
finding still marked open, break-glass waiver included. That is the STRICTER of the two readings
that module owns, and it is the one `hook_verifier.sh` applies before it will let a handover be
written - see `verified`, where the difference is stated. A second reading here would be a second
answer to a question the pipeline has already settled twice.

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
content, so a `handover.md` that says nothing and a `test-mapping.md` with no real row both satisfy
it here - `doc_read_path.py` holds the first and `spec_done_guard.py` the second. It cannot tell a
phase abandoned mid-verification from one still being verified, so a phase with no passing verdict
is simply not asked for a handover, however long it has stood that way.
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
from verifier_attempts import UnreadableVerdict, attempts  # noqa: E402

DONE = "done"
PASS = "pass"


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


def verified(phase_dir: Path) -> bool:
    """Whether the phase's latest verdict PASSES carrying no finding still marked open.

    Taken from the modules that own each half rather than restated: `verifier_attempts.attempts` for
    what the verdict record is, and `verdict_findings` for what open means. Which of that module's
    two readings applies is the whole question here, and this asks for LESS than the attempt cap
    does. `Attempt.unresolved` counts `open_findings`, which treats a break-glass waiver as
    resolved, because the cap asks whether the loop has ended and a waiver ends it.
    `Attempt.flagged` counts `status: open` literally, and that is the reading `hook_verifier.sh`
    takes before it will let a `handover.md` be written: a `pass` carrying a waived-but-open finding
    fails closed there. Answering with `unresolved` made this sweep demand a handover the hook then
    refused to let anyone write - a phase with no reachable end state, reached through the documented
    remedy at the attempt cap.

    So the two gates are aligned at the strict end, deliberately: between a sweep that asks for a
    document too early and one that asks a little late, only the first can wedge a phase. An
    unreadable or absent record is NOT a pass for the same reason - under-report, so the sweep asks
    for nothing it cannot show is owed.
    """
    try:
        records = attempts(phase_dir)
    except (UnreadableVerdict, OSError, ValueError):
        return False
    if not records:
        return False
    latest = records[-1]
    return latest.verdict == PASS and latest.flagged == 0


def phase_dirs(root: Path) -> list[Path]:
    """Every phase directory under the artifact tree, in a stable order."""
    features = root / "docs" / "features"
    if not features.is_dir():
        return []
    return sorted(path for path in features.glob("*/phases/*") if path.is_dir())


def _spec_problems(spec_path: Path) -> list[str]:
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
            f"finished."
        ]
    except UndecidableRequirements as exc:
        return [
            f"{spec_path}: is `status: done` and its requirements cannot be read ({exc}), so "
            f"whether it owes a `test-mapping.md` cannot be decided - unknown is not exempt."
        ]
    return [f"{mapping}: missing (this spec is `status: done` and owes a mapping row)"]


def phase_problems(phase_dir: Path) -> list[str]:
    """Every artifact this one phase owes, or nothing when it has not finished implementing."""
    if not finished_implementing(phase_dir):
        return []
    found: list[str] = []
    handover = phase_dir / "handover.md"
    if verified(phase_dir) and not handover.is_file():
        found.append(
            f"{handover}: missing (this phase has a passing verdict.json and every spec in it is "
            f"`status: done`)"
        )
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
        return 0

    found = problems(root, enforce_all=args.enforce_all)
    if found:
        print("Phase artifacts missing (create them before stopping):", file=sys.stderr)
        for problem in found:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
