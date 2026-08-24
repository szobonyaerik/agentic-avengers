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

What it then requires of such a phase is what the old sweep required, corrected for where those
files actually live today:

  * `handover.md` beside `verdict.json`, per phase; and
  * `test-mapping.md` beside each spec - PER SPEC, not per phase. The old sweep looked for it in
    the phase directory, which has not been its home since specs became `<n>.<k>` directories, so
    that half asked for a file at a path the pipeline never writes.

A spec that owes no mapping row at all is exempt by construction, read from
`spec_done_guard.mapping_owed`: every requirement `binding: none` gets no test and no row (§4a), and
demanding one asks the author to invent a row the rules forbid. Undecidable is not exempt - a spec
whose requirements cannot be read, or that declares none, is reported rather than skipped, the same
reading `spec_done_guard` takes.

A phase with no spec directory at all has not started, not finished: it is not swept.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec_done_guard import (  # noqa: E402
    NoRequirementsDeclared,
    UndecidableRequirements,
    mapping_owed,
)
from spec_gate_state import frontmatter  # noqa: E402

DONE = "done"


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


def problems(root: Path) -> list[str]:
    """Every artifact a phase that finished implementing still owes."""
    found: list[str] = []
    for phase_dir in phase_dirs(root):
        if not finished_implementing(phase_dir):
            continue
        handover = phase_dir / "handover.md"
        if not handover.is_file():
            found.append(
                f"{handover}: missing (every spec in this phase is `status: done`)"
            )
        for spec_path in spec_paths(phase_dir):
            found += _spec_problems(spec_path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", help="every phase that finished implementing has its artifacts"
    )
    check.add_argument(
        "root", nargs="?", default=".", help="repository root (default: .)"
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

    found = problems(root)
    if found:
        print("Phase artifacts missing (create them before stopping):", file=sys.stderr)
        for problem in found:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
