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

## What this may bind (the applicability boundary, `scripts/applicability.py`)

**Only the TRANSITION into `done` binds.** `hook_verifier.sh` fires on any tool write to a
`spec.md` that merely *contains* `status: done`, which cannot by itself tell a stamp that has just
landed from one that has sat there since the phase closed. Acting on the second is the wedge this
repository has now built four times: a consumer repo vendored through `scripts/install.sh` carries
specs stamped `done` long before this rule existed, and any later edit to one of them — an
amendment (§4d) to a verified phase included — would rewrite `status: in-progress` into a SHIPPED
spec. That stamp is the *only* evidence `applicability.spec_shipped` reads, so removing it flips
`requirement_cap` from counting a shipped spec to blocking it with a SPLIT it cannot take, and
re-routes a completed spec back to `stage: implementer` in `pipeline_state.py`. Re-stamping `done`
re-fires the hook and reverts again; `spec-done` is not in `applicability.RULES`, so no disclosed
exception can be recorded for it either. There is no way out of that loop, which is exactly what
"a rule whose remedy is unavailable is not a gate, it is a wedge" names.

So `stamp_is_new` compares the worktree against the file's **committed HEAD** version, and the
three answers are the boundary:

- the committed version says anything other than `done`, or there is no committed version at all →
  the stamp is NEW, the spec is still OPEN, and the rule BINDS;
- the committed version already says `done` → the spec is CLOSED by the *shipped* evidence, and it
  is COUNTED and NAMED (`applicability.report_unenforced`), never blocked and never rewritten;
- git cannot say → the scope is UNKNOWABLE, so nothing is enforced and it is said out loud, the
  same direction every other check on this boundary takes rather than enforcing everything.

`test-mapping.md` completeness is checked here directly (a spec's own mapping file must carry at
least one RECORDED row past its header separator) rather than by re-deriving the full
per-requirement traceability rule in `scripts/verifier_precheck.py` — that check is phase-level,
reads `binding:` tiers, and is off-limits to this change. This check is narrower and spec-scoped: a
spec cannot be done while its mapping is still empty, which is exactly the ordering issue #68 names.

**Counting rows is not the same as checking they say anything.** `docs/templates/`'s mapping
template ships a header, a separator and THREE placeholder rows, and `skills/tdd` points every
implementer at it as the starting shape — so copy-then-stamp is the expected flow, not an edge
case, and a row-count check would pass exactly the state this issue is about ("test-mapping.md was
still empty" when the stamp landed). A row is a PLACEHOLDER while any cell still carries the
template's angle-bracket syntax (`R<n>.<k>.<m>`, `test_<name>`, `<seam>`); completeness needs one
row that is not. One genuinely filled row is enough, however many placeholders sit beside it.

## Exit 1 means the answer, never a crash

`NOT_DONE` and `OUT_OF_SCOPE` are both exit 1, wired to two different branches of
`hook_verifier.sh` — and Python exits 1 on an uncaught exception too. Left uncaught, a
`UnicodeDecodeError` from a non-UTF-8 mapping (a `ValueError`, so no `except OSError` sees it)
would arrive as the mapping obligation and revert a correctly-stamped spec, while the same crash in
`stamp_is_new` would arrive as the boundary and silently stop enforcing. So every unexpected
failure is ERROR and names its own cause, the rule CLAUDE.md §4c states for `verifier_attempts.py`.
An unreadable mapping is the same shape one layer in: it is UNDECIDABLE, never `empty`.

Usage:
    spec_done_guard.py stamp-is-new <spec.md>       exit 0 = a NEW `done` stamp, the rule binds;
                                                     1 = out of scope (already done at HEAD, or the
                                                     scope is unknowable); 2 = error
    spec_done_guard.py mapping-complete <spec.md>   exit 0 = a recorded row, 1 = empty/missing/only
                                                     placeholders, 2 = error
    spec_done_guard.py revert <spec.md>             flip status: done -> status: in-progress;
                                                     exit 0 whether or not a change was needed,
                                                     2 if the file cannot be read/written
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
import spec_gate_state  # noqa: E402

OK = 0
NOT_DONE = 1
#: Same code as `NOT_DONE`, different question: the rule does not bind this spec at all.
OUT_OF_SCOPE = 1
ERROR = 2

STATUS_FIELD = "status"
DONE = "done"
IN_PROGRESS = "in-progress"

CHECK = "spec-done"

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
STATUS_LINE = re.compile(rf"^{STATUS_FIELD}:.*$", re.MULTILINE)

#: A markdown table's separator row and nothing else: pipes, colons, whitespace, and at least one
#: dash. Detecting it by "the line contains `---`" dropped any real data row whose own text happens
#: to contain a dash run, which reads as a header-only mapping and reverts a correct stamp.
SEPARATOR_ROW = re.compile(r"\|[\s:|-]*-[\s:|-]*\|")

#: The template's own placeholder syntax, in any cell of a row. Every placeholder cell the mapping
#: template ships carries it (`R<n>.<k>.<m>`, `test_<journey>`, `<seam>`, `<mandatory: …>`), and a
#: filled-in row does not — real requirement ids, test names and prose have no bare `<…>` token.
PLACEHOLDER_CELL = re.compile(r"<[^>]+>")


class UndecidableMapping(Exception):
    """The mapping file exists but could not be read, so completeness is UNKNOWN.

    Deliberately not `False`. Answering "no rows recorded" here would reach the hook as the
    obligation branch and rewrite a spec on the strength of a check that never ran — the same
    conflation the shell-level exit-code split removes one layer out.
    """

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path}: {type(cause).__name__}: {cause}")
        self.path = path


def _git(root: Path, *args: str) -> tuple[int, str]:
    """git's exit code and raw stdout. Deliberately not `applicability._git`, which answers "which
    paths changed" by dropping blank lines — fine for a path list, wrong for file content."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
        )
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


def stamp_is_new(spec_path: Path) -> bool | None:
    """Whether `status: done` in the worktree is a TRANSITION rather than a pre-existing stamp.

    True binds the rule, False means the spec is already CLOSED by the *shipped* evidence, and None
    means git could not say and therefore nothing may be enforced. See the module docstring.
    """
    path = Path(spec_path).resolve()
    rc, out = _git(path.parent, "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        return None
    top = Path(out.strip().splitlines()[0]).resolve()
    try:
        rel = path.relative_to(top)
    except ValueError:
        return None
    rc, text = _git(top, "show", f"HEAD:{rel.as_posix()}")
    if rc != 0:
        # No committed version — an unborn branch, or a spec written this session. Either way the
        # stamp cannot predate this change, so it is new.
        return True
    return spec_gate_state.frontmatter(text).get(STATUS_FIELD) != DONE


def mapping_complete(spec_path: Path) -> bool:
    """`test-mapping.md` beside the spec carries at least one RECORDED row past its separator.

    A row still carrying the template's `<…>` placeholder syntax in any cell has recorded nothing,
    so the template copied verbatim reads exactly like the missing file it came from. A missing,
    header-only or placeholder-only mapping means not one test has been recorded yet for this spec,
    which cannot be true of a spec that is genuinely done.

    Raises `UndecidableMapping` when the file exists but cannot be read — unknown is not empty.
    """
    mapping = spec_path.parent / "test-mapping.md"
    if not mapping.is_file():
        return False
    try:
        lines = mapping.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        raise UndecidableMapping(mapping, exc) from exc
    seen_separator = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if not seen_separator:
            seen_separator = bool(SEPARATOR_ROW.fullmatch(stripped))
            continue
        if not PLACEHOLDER_CELL.search(stripped):
            return True
    return False


def revert(spec_path: Path) -> bool:
    """Flip `status: done` back to `status: in-progress`. Returns True iff it changed anything.

    Only when the field currently reads exactly `done`. Between the hook's grep and this call sits
    the phase's whole suite run, so another lane may have written `blocked` or some later status in
    the meantime, and rewriting whatever value it finds would be a mutation nobody asked for.

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
    if spec_gate_state.frontmatter(text).get(STATUS_FIELD) != DONE:
        return False
    new_block = STATUS_LINE.sub(f"{STATUS_FIELD}: {IN_PROGRESS}", block)
    if new_block == block:
        return False
    spec_path.write_text(
        f"---\n{new_block}\n---" + text[match.end() :], encoding="utf-8"
    )
    return True


def _stamp_is_new_cli(path: Path) -> int:
    verdict = stamp_is_new(path)
    if verdict is True:
        return OK
    if verdict is None:
        print(
            f"[{CHECK}] scope UNKNOWABLE — git cannot say what this change touches, so the "
            f"`done` stamp on {path} is not enforced either way.",
            file=sys.stderr,
        )
        return OUT_OF_SCOPE
    applicability.report_unenforced(
        CHECK,
        1,
        f"{path} was already stamped `status: {DONE}` at HEAD — the spec has SHIPPED, so its "
        f"stamp is counted, never reverted (a rule whose remedy is unavailable is a wedge).",
    )
    return OUT_OF_SCOPE


def main(argv: list[str] | None = None) -> int:
    """Exit 1 means the answer — the obligation or the boundary. Everything else is ERROR.

    The catch-all is the point, not the belt: guarding one `read_text` fixes one crash, while an
    uncaught exception of ANY kind exiting 1 arrives at `hook_verifier.sh` as a verdict it never
    reached — reverting a correct stamp on the mapping branch, or silently standing down on the
    boundary branch. `SystemExit` is a `BaseException` and passes through untouched.
    """
    try:
        return _dispatch(argv)
    except UndecidableMapping as exc:
        print(
            f"[{CHECK}] cannot read the mapping: {exc}\n"
            f"  This is NOT an empty mapping and recording rows cannot fix it — the check never\n"
            f"  ran, so the stamp is left exactly as written. Repair {exc.path}, then run again.",
            file=sys.stderr,
        )
        return ERROR
    except Exception as exc:  # noqa: BLE001 — an unexpected failure is an ERROR, never a verdict
        print(
            f"[{CHECK}] unexpected failure deciding the `status: {DONE}` stamp: "
            f"{type(exc).__name__}: {exc}\n"
            f"  Reported as an error rather than a verdict: nothing was judged.",
            file=sys.stderr,
        )
        return ERROR


def _dispatch(argv: list[str] | None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] in ("stamp-is-new", "mapping-complete"):
        path = Path(args[1])
        if not path.is_file():
            print(f"[spec_done_guard] no such spec: {path}", file=sys.stderr)
            return ERROR
        if args[0] == "stamp-is-new":
            return _stamp_is_new_cli(path)
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
