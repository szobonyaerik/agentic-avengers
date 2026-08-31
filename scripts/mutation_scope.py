#!/usr/bin/env python3
"""Scope the mutation gate over the WORKING TREE the pipeline actually verifies.

## The defect this replaces

The gate diff-scoped through `cr-filter-git`, and that filter's whole mechanism is
`git diff --relative -U0 <branch> .`. `git diff` reports nothing about an UNTRACKED file, while the
pipeline verifies uncommitted work: the Verifier runs once per phase, over a working copy, before
the phase's commit lands. So a phase whose contribution is NEW FILES had every mutant marked
skipped, `tested` came out zero, and `mutation_score.verdict` returned GO with the line "all N
mutants fall outside the diff". Nothing on disk, in the metrics record or in the phase's artifacts
distinguished that from a gate that ran and found nothing.

That is issue #97's second instance, and it is the one the issue calls sharpest: the gate reported
a clean result over a phase it had not measured a single line of.

## What this scopes on

`git diff -U0` against HEAD (or against the merge-base with `--base`), **plus every line of every
untracked file**. That union is the same question `applicability.changed_paths` answers for every
other check on the boundary - modified, staged, untracked - asked at line granularity, because
mutating whole files a phase merely touched costs a multiple of the runtime for no extra signal.

`--base` widens it to the branch, for CI, where nothing is uncommitted and the working-tree answer
alone is legitimately empty.

## It fails LOUDLY, and that is the point

**An empty scope is not a clean gate.** A session where nothing survived filtering exits
`NOTHING_IN_SCOPE`, never OK: the gate measured no line, so it has no verdict to give, and the
caller records that as `did-not-run` rather than reporting a score of any kind. Git that cannot
answer is an ERROR for the same reason - "unknowable" read as "nothing changed" would skip every
mutant in silence, which is the defect one layer down.

What a clean result does NOT establish is stated in this module's runtime output, not only here
(`scripts/guard_scope.py`), because a later stage reads output and not source.

Usage:
    mutation_scope.py <session.sqlite> [--root .] [--base REF]

Exit codes:
    0  OK               at least one mutant is in scope; the rest are marked skipped.
    2  ERROR            git could not answer, or the session could not be read. Fail closed.
    3  NOTHING_IN_SCOPE nothing to mutate. NEVER a pass - the gate did not run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402

OK = 0
ERROR = 2
NOTHING_IN_SCOPE = 3

#: `@@ -<old> +<start>[,<len>] @@` - the only line of a `-U0` diff that carries new line numbers.
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git_text(root: Path, *args: str) -> str | None:
    """git's answer verbatim, or None when it has none. None is UNKNOWABLE, never 'nothing'."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git(root: Path, *args: str) -> list[str] | None:
    """git's answer as lines, or None when it has none. None is UNKNOWABLE, never 'nothing'."""
    raw = _git_text(root, *args)
    return None if raw is None else raw.splitlines()


def _git_paths(root: Path, *args: str) -> list[str] | None:
    """Paths from git, NUL-separated. The ONE spelling every path-listing call site uses.

    `-z` rather than a newline list, so `core.quotePath` - on by default - cannot C-quote a name
    holding a non-ASCII byte, a quote, a backslash, a tab or a newline into one that joins to
    nothing, and a name containing a newline cannot become two. Both callers go through here so a
    third cannot reacquire it by asking git directly.
    """
    raw = _git_text(root, *args)
    if raw is None:
        return None
    return [name for name in raw.split("\0") if name]


def _changed_files(root: Path, ref: str) -> list[str] | None:
    """The paths `ref` differs from, asked for as data rather than recovered from diff text."""
    return _git_paths(root, "diff", "--name-only", "-z", "--no-relative", ref, "--")


def _file_lines(root: Path, ref: str, name: str) -> set[int] | None:
    """New-side line numbers for ONE file. The path is known from the request, never parsed back.

    `:(literal)` because the trailing argument is a git PATHSPEC, which is fnmatch-matched: `*`, `?`
    and `[...]` are wildcards there, so `app/[slug]/page.tsx` - an ordinary frontend filename -
    matched other files and folded THEIR new-side line numbers into this file's set. A widening
    union cannot hide a change, but it puts mutants on unchanged lines into scope and reports a
    scope that is not the truth.
    """
    output = _git(root, "diff", "--no-relative", "-U0", ref, "--", f":(literal){name}")
    if output is None:
        return None
    lines: set[int] = set()
    for line in output:
        match = HUNK.match(line)
        if not match:
            continue
        start = int(match.group(1))
        length = int(match.group(2)) if match.group(2) is not None else 1
        lines.update(range(start, start + length))
    return lines


def _diff_lines(root: Path, ref: str) -> dict[Path, set[int]] | None:
    """New-side line numbers per file for `git diff -U0 <ref>`, over the working tree.

    Asked ONE FILE AT A TIME, and that is the whole construction. A combined diff has to be
    attributed back to a file by reading its `+++ ` headers, and under `-U0` no line can be
    classified by its own prefix: an ADDED CONTENT line beginning `++ ` renders as `+++ <rest>`,
    which is indistinguishable from a header, repoints the parser at a path that does not exist and
    drops every later hunk of the real file. With any other file keeping the scope non-empty that
    failed SILENTLY NARROW - an OK verdict over a fraction of the change, from the module written to
    stop exactly that. The header prefix is also configurable (`diff.noprefix`,
    `diff.mnemonicPrefix`), so what the parser recognised was a property of the operator's own git
    config. Asking git which files changed first removes both: the path comes from the request, so
    a hunk cannot be attributed to the wrong file at all, and a deleted file simply reports no
    new-side lines rather than needing `+++ /dev/null` to be spotted.
    """
    names = _changed_files(root, ref)
    if names is None:
        return None
    found: dict[Path, set[int]] = {}
    for name in names:
        lines = _file_lines(root, ref, name)
        if lines is None:
            return None
        if lines:
            found[(root / name).resolve()] = lines
    return found


def _untracked_lines(root: Path) -> dict[Path, set[int]] | None:
    """Every line of every untracked file. This is the half `git diff` cannot report at all.

    The two read failures are DIFFERENT answers and only one is safely ignorable. Content that is
    not UTF-8 text is genuinely binary and holds no mutants a source operator could generate, so it
    is skipped - out loud, never in silence. A path git has just reported as untracked that cannot
    be opened AT ALL is a scope this module could not compute, and that belongs with UNKNOWABLE:
    folded into the skip it read as "no mutants here", which is how a brand-new source file left
    the scope with no error and no message.
    """
    names = _git_paths(
        root, "ls-files", "--others", "--exclude-standard", "--full-name", "-z"
    )
    if names is None:
        return None
    found: dict[Path, set[int]] = {}
    for name in names:
        target = (root / name).resolve()
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(
                f"[mutation_scope] skipped untracked {name}: not UTF-8 text, so it holds no "
                f"mutants a source mutation operator could generate.",
                file=sys.stderr,
            )
            continue
        except OSError as exc:
            print(
                f"[mutation_scope] git reported {name} as untracked and it cannot be opened "
                f"({exc}), so what this change is responsible for is UNKNOWABLE. Refusing to "
                f"filter: read as 'no mutants here' this drops a whole new file in silence.",
                file=sys.stderr,
            )
            return None
        count = len(text.splitlines())
        if count:
            found[target] = set(range(1, count + 1))
    return found


def changed_lines(root: Path, base: str | None = None) -> dict[Path, set[int]] | None:
    """Line numbers this change is responsible for, by absolute path, or None when unknowable.

    The union of what the working tree changed against HEAD and every line of every untracked file.
    With `base`, also what this branch changed against its MERGE-BASE with it - the same widening,
    and the same merge-base rather than the base tip, that `applicability.changed_paths` uses: the
    base branch's own later commits are not this change's doing.
    """
    root = Path(root).resolve()
    if _git(root, "rev-parse", "--git-dir") is None:
        return None
    parts: list[dict[Path, set[int]] | None] = [
        _diff_lines(root, "HEAD"),
        _untracked_lines(root),
    ]
    if base is not None:
        if not (base or "").strip():
            return None
        forked = _git(root, "merge-base", base, "HEAD")
        if not forked or not forked[0].strip():
            return None
        parts.append(_diff_lines(root, forked[0].strip()))
    merged: dict[Path, set[int]] = {}
    for part in parts:
        if part is None:
            return None
        for path, lines in part.items():
            merged.setdefault(path, set()).update(lines)
    return merged


def in_scope(
    path: Path, start_line: int, end_line: int, scope: dict[Path, set[int]]
) -> bool:
    """Whether a mutation's line span touches any line this change is responsible for."""
    lines = scope.get(Path(path).resolve())
    if not lines:
        return False
    return bool(lines & set(range(start_line, end_line + 1)))


def verdict(*, kept: int, skipped: int, total: int) -> tuple[int, str]:
    """The exit code and the line to print. An empty scope is NEVER OK."""
    if total == 0:
        return (
            NOTHING_IN_SCOPE,
            "the session holds no mutants at all, so the gate did not run. Check cosmic-ray's "
            "module-path; this is not a score.",
        )
    if kept == 0:
        return (
            NOTHING_IN_SCOPE,
            f"all {total} mutants fall outside the working tree's changed lines, so the gate did "
            f"not run over a single line of this phase. This is NOT a pass and NOT a score: it is "
            f"the absence of a measurement, and it is recorded as one.",
        )
    return (
        OK,
        f"{kept} of {total} mutants are on lines this change is responsible for "
        f"({skipped} skipped).",
    )


def filter_session(session: Path, scope: dict[Path, set[int]]) -> tuple[int, int, int]:
    """Mark every pending mutant outside `scope` as skipped. Returns (kept, skipped, total)."""
    from cosmic_ray.work_db import WorkDB, use_db
    from cosmic_ray.work_item import WorkerOutcome, WorkResult

    dropped: list[str] = []
    kept = 0
    with use_db(str(session), WorkDB.Mode.open) as db:
        total = db.num_work_items
        for item in db.pending_work_items:
            if any(
                in_scope(
                    Path(mutation.module_path),
                    mutation.start_pos[0],
                    mutation.end_pos[0],
                    scope,
                )
                for mutation in item.mutations
            ):
                kept += 1
            else:
                dropped.append(item.job_id)
        if dropped:
            db.set_multiple_results(
                dropped,
                WorkResult(
                    output="filtered: outside the working tree's changed lines",
                    worker_outcome=WorkerOutcome.SKIPPED,
                ),
            )
    return kept, len(dropped), total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("session", type=Path, help="the cosmic-ray session to filter")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    parser.add_argument(
        "--base",
        default=None,
        help="also scope over what this branch changed against its merge-base with REF",
    )
    args = parser.parse_args(argv)

    if not args.session.is_file():
        print(f"[mutation_scope] session not found: {args.session}", file=sys.stderr)
        return ERROR

    scope = changed_lines(args.root, args.base)
    if scope is None:
        print(
            "[mutation_scope] git cannot say what this working tree changed, so the scope is "
            "UNKNOWABLE. Refusing to filter: read as 'nothing changed' this would skip every "
            "mutant in silence.",
            file=sys.stderr,
        )
        return ERROR

    try:
        kept, skipped, total = filter_session(args.session, scope)
    except ImportError as exc:
        print(f"[mutation_scope] cosmic-ray is not importable: {exc}", file=sys.stderr)
        return ERROR
    except (OSError, ValueError) as exc:
        print(f"[mutation_scope] cannot filter the session: {exc}", file=sys.stderr)
        return ERROR

    code, message = verdict(kept=kept, skipped=skipped, total=total)
    print(f"[mutation_scope] {message}", file=sys.stdout if code == OK else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
