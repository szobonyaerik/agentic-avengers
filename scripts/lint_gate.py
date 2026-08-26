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

#: What `ruff format --check` emits per drifted file. Parsed rather than re-derived, so the scoping
#: happens over ruff's own answer instead of over a second opinion about which files exist - and
#: parsed in BOTH shapes ruff has used, because this line is a version-dependent human output and
#: reading only one of them made a newer ruff's drift arrive here as an empty list, i.e. as clean.
DRIFT_PREFIX = "Would reformat:"  # ruff <= 0.15
DRIFT_ARROW = "-->"  # ruff >= 0.16, the diagnostic form: `--> path:row:col`

#: `ruff format --check` exits 1 for "some file would be reformatted" and 2 for "ruff itself could
#: not run". Anything else is a contract this gate does not know.
_FORMAT_OK = 0
_FORMAT_DRIFT = 1


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603


def rules(paths: list[str], cwd: Path) -> tuple[int, str]:
    """`ruff check`, unscoped. Returns (exit code, what it said)."""
    proc = _run(["ruff", "check", *paths], cwd)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _drift_path(line: str) -> str | None:
    """The file named by one line of `ruff format --check` output, in either shape."""
    line = line.strip()
    if line.startswith(DRIFT_PREFIX):
        return line[len(DRIFT_PREFIX) :].strip() or None
    if line.startswith(DRIFT_ARROW):
        # `--> path/to/file.py:1:10` - the trailing row:col is ruff's, never part of the path.
        located = line[len(DRIFT_ARROW) :].strip()
        return located.rsplit(":", 2)[0] or None
    return None


def drifted(paths: list[str], cwd: Path) -> tuple[list[Path], str, int]:
    """Every file `ruff format` would rewrite, absolute, plus what ruff said and how it exited."""
    proc = _run(["ruff", "format", "--check", *paths], cwd)
    said = (proc.stdout or "") + (proc.stderr or "")
    named = [_drift_path(line) for line in said.splitlines()]
    files: list[Path] = []
    for name in named:
        if name is None:
            continue
        resolved = (cwd / name).resolve()
        if resolved not in files:
            files.append(resolved)
    return files, said, proc.returncode


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

    files, said, code = drifted(paths, cwd)
    if code not in (_FORMAT_OK, _FORMAT_DRIFT) or (code == _FORMAT_DRIFT and not files):
        # ruff answered in a shape this gate cannot read. Reporting the empty list as "no drift"
        # is the silent pass this gate exists to remove, so it is named and the gate stops.
        print(said.rstrip(), file=sys.stderr)
        print(
            "lint-gate: FORMAT — `ruff format --check` exited "
            f"{code} and this gate could not read which files it named, so the format dimension "
            "verified NOTHING. Check the installed ruff version against this gate's parser.",
            file=sys.stderr,
        )
        return ERROR

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
