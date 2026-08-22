#!/usr/bin/env python3
"""The lint gate, with the dimension it was missing: FORMAT.

## The defect

The gate ran `ruff check .`. `ruff check` judges rules; it says nothing about formatting, so drift
passed it untouched. Two consecutive clickup-agents phases reported "ruff clean" and **neither
statement was evidence about formatting** - nothing in the gate could have failed on it. That is the
same family as a green suite that never ran: a check reporting success while verifying nothing.

## The two dimensions

* **Rules** - `ruff check`, over the whole tree, exactly as before. The tree is clean and stays
  clean, so there is nothing here to scope.
* **Format** - `ruff format --check`, **diff-scoped** on the applicability boundary
  (`applicability.changed_paths`, CLAUDE.md 3a). This repository has 93 files that predate the
  rule; a gate that failed the build over them would be a wedge rather than a gate, which is the
  same reason the read-path check, the cost gate, the spec re-gate cache and the mutation gate are
  all scoped the same way. What the change touches is enforced. The rest is **counted and named on
  stderr**, never blocked. `--all` audits the whole tree and is the thing somebody runs
  deliberately.

When git cannot state what changed, the scope is unknowable: the format dimension enforces
**nothing** and says so out loud, rather than falling back to enforcing everything.

Usage:
    lint_gate.py [--all] [path ...]        default path: the whole tree (`.`)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402

OK = 0
FOUND = 1
ERROR = 2

#: The line `ruff format --check` emits per drifted file. Parsed rather than re-derived, so the
#: scoping happens over ruff's own answer instead of over a second opinion about which files exist.
DRIFT_PREFIX = "Would reformat:"


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603


def rules(paths: list[str], cwd: Path) -> tuple[int, str]:
    """`ruff check`, unscoped. Returns (exit code, what it said)."""
    proc = _run(["ruff", "check", *paths], cwd)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def drifted(paths: list[str], cwd: Path) -> tuple[list[Path], str]:
    """Every file `ruff format` would rewrite, absolute, plus whatever ruff said about it."""
    proc = _run(["ruff", "format", "--check", *paths], cwd)
    said = (proc.stdout or "") + (proc.stderr or "")
    files = [
        (cwd / line[len(DRIFT_PREFIX) :].strip()).resolve()
        for line in said.splitlines()
        if line.startswith(DRIFT_PREFIX)
    ]
    return files, said


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="audit the whole tree instead of what this change touches",
    )
    ap.add_argument("paths", nargs="*", default=None)
    args = ap.parse_args(argv)
    paths = args.paths or ["."]
    cwd = Path.cwd().resolve()

    if not shutil.which("ruff"):
        print(
            "lint-gate: ruff is not on PATH, so neither dimension of this gate can run.",
            file=sys.stderr,
        )
        return ERROR

    verdict = OK

    code, said = rules(paths, cwd)
    if said.strip():
        print(said.rstrip(), file=sys.stderr)
    if code != 0:
        print(
            "lint-gate: RULES — ruff check reported the findings above.",
            file=sys.stderr,
        )
        verdict = FOUND

    files, said = drifted(paths, cwd)
    scope = None if args.all else applicability.changed_paths(cwd)
    if args.all:
        enforced, counted = files, []
    elif scope is None:
        # Unknowable scope. Enforcing everything here would fail a consumer repository's build over
        # files written before this rule existed; enforcing nothing quietly would be the silent pass
        # this gate exists to remove. So: nothing is enforced, and it is said.
        enforced, counted = [], files
        print(
            "lint-gate: FORMAT — git could not state what this change touched, so the format "
            "dimension enforced NOTHING. Run `lint_gate.py --all` to audit the tree.",
            file=sys.stderr,
        )
    else:
        enforced = [f for f in files if applicability.touched(f, scope)]
        counted = [f for f in files if f not in enforced]

    for path in enforced:
        print(
            f"lint-gate: FORMAT — {_rel(path, cwd)} is not formatted. Run `ruff format` on it.",
            file=sys.stderr,
        )
    if enforced:
        verdict = FOUND
    if counted:
        print(
            f"lint-gate: FORMAT — {len(counted)} file(s) this change did not touch are not "
            f"formatted; counted, not blocked: "
            f"{', '.join(_rel(p, cwd) for p in sorted(counted)[:10])}"
            f"{' …' if len(counted) > 10 else ''}",
            file=sys.stderr,
        )

    if verdict == OK:
        print("lint-gate: clean (rules + format).", file=sys.stderr)
    return verdict


def _rel(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
