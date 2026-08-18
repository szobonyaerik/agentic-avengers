#!/usr/bin/env python3
"""Makes `status: done` mean what it says, mechanically, instead of trusting the writer.

Issue #68: an implementer stamps its spec's frontmatter `status: done` and then KEEPS WORKING —
`test-mapping.md`, `test-evidence.md` and the phase's mutation gate all come after the write. In
phase 11 a worker had armed a wedge guard on that stamp, watching for it to mean "safe to dispatch
the next spec's implementer into this worktree." It fired at 24 minutes while the agent was still
running. Had the guard been trusted, two implementers would have run in one worktree against one
database — forbidden outright, and phase 9 measured why (a stash from one swallowed the other's
uncommitted work; the shared database produced foreign-key violations and a spurious lint failure).

Nothing here tells anyone not to trust the stamp — that sentence has already been written five
times in two days and enforces nothing. Instead the stamp is made SELF-CORRECTING: the moment it is
written, `scripts/hook_verifier.sh` checks whether the spec is actually done (its own tests green,
its `test-mapping.md` non-empty), and if not, this module REVERTS `status: done` back to
`status: in-progress` before the hook fails. A premature stamp does not survive contact with the
gate that reads it — it is undone, not merely complained about.

`test-mapping.md` completeness is checked here directly (a spec's own mapping file must carry at
least one row past its header) rather than by re-deriving the full per-requirement traceability
rule in `scripts/verifier_precheck.py` — that check is phase-level, reads `binding:` tiers, and is
off-limits to this change. This check is narrower and spec-scoped: a spec cannot be done while its
mapping is still empty, which is exactly the ordering issue #68 names.

Usage:
    spec_done_guard.py mapping-complete <spec.md>   exit 0 = has rows, 1 = empty/missing, 2 = error
    spec_done_guard.py revert <spec.md>             flip status: done -> status: in-progress;
                                                     exit 0 whether or not a change was needed,
                                                     2 if the file cannot be read/written
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

OK = 0
NOT_DONE = 1
ERROR = 2

STATUS_FIELD = "status"
DONE = "done"
IN_PROGRESS = "in-progress"

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
STATUS_LINE = re.compile(rf"^{STATUS_FIELD}:.*$", re.MULTILINE)


def frontmatter(text: str) -> dict[str, str]:
    """Flat key/value view of a spec's YAML frontmatter; empty when there is none."""
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.split("#")[0].strip()
    return fields


def mapping_complete(spec_path: Path) -> bool:
    """`test-mapping.md` beside the spec carries at least one row past its header separator.

    A missing or template-only mapping means not one test has been recorded yet for this spec,
    which cannot be true of a spec that is genuinely done.
    """
    mapping = spec_path.parent / "test-mapping.md"
    if not mapping.is_file():
        return False
    try:
        lines = mapping.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    rows = [
        line for line in lines if line.strip().startswith("|") and "---" not in line
    ]
    # The first `|...|` line is the header row itself; a real mapping has at least one more.
    return len(rows) >= 2


def revert(spec_path: Path) -> bool:
    """Flip `status: done` back to `status: in-progress`. Returns True iff it changed anything.

    Raises ValueError when the file has no frontmatter or no `status:` field — a spec that could
    stamp `done` in the first place has both, so either case means something else is already wrong
    and reverting would silently paper over it.
    """
    text = spec_path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("no YAML frontmatter")
    block = match.group(1)
    if not STATUS_LINE.search(block):
        raise ValueError("no status: field in frontmatter")
    new_block = STATUS_LINE.sub(f"{STATUS_FIELD}: {IN_PROGRESS}", block)
    if new_block == block:
        return False
    spec_path.write_text(
        f"---\n{new_block}\n---" + text[match.end() :], encoding="utf-8"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "mapping-complete":
        path = Path(args[1])
        if not path.is_file():
            print(f"[spec_done_guard] no such spec: {path}", file=sys.stderr)
            return ERROR
        return OK if mapping_complete(path) else NOT_DONE
    if len(args) == 2 and args[0] == "revert":
        path = Path(args[1])
        try:
            changed = revert(path)
        except (OSError, ValueError) as exc:
            print(f"[spec_done_guard] cannot revert {path}: {exc}", file=sys.stderr)
            return ERROR
        state = "reverted" if changed else "no change needed for"
        print(
            f"[spec_done_guard] {state} {path}: status -> {IN_PROGRESS}",
            file=sys.stderr,
        )
        return OK
    print(__doc__, file=sys.stderr)
    return ERROR


if __name__ == "__main__":
    raise SystemExit(main())
