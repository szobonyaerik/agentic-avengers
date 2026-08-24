#!/usr/bin/env python3
"""Check test fixtures against the shapes a real deployment actually produces (issue #33).

One measured defect: **1,009 tests passed** against Telegram supergroup ids around 970 million while
real ids are an order of magnitude larger. The column was `sa.Integer()` (int32), so a `/setkey`
credential refusal raised `DataError: value out of int32 range` before it could fire — a security
control shipped **non-functional behind a green suite**. Nothing in the pipeline caught it; the
delivery gate driving a real id end to end did.

Fixture realism shipped as INSTRUCTION — `skills/tdd` names it as a TDD anti-pattern,
`skills/verifier-triage` carries it in the Verifier's pattern list, `agents/avenger-spec-writer` is
told to pin realistic example values — because no static rule can decide "a shape a real deployment
produces" *generically*. That is true, and it is not a reason to leave it unenforced. **The shape is
per-project knowledge, so the PROJECT declares it and the check is generic** — the same split
`SUBPROC_CHECK_PATHS` already uses to scope a generic cost gate to a project's real test root.

    # fixture-shapes.toml, at the repository root
    [telegram-chat-id]
    names = ["chat_id", "supergroup_id"]   # identifiers whose LITERAL values are checked
    min   = 1000000000000                  # inclusive integer bounds …
    max   = 9999999999999
    why   = "real supergroup ids are ~1e12; a 9-digit fixture fits int32 and hides the overflow"

    [stripe-customer]
    names   = ["customer_id"]
    pattern = "cus_[A-Za-z0-9]{14,}"       # … or a regex the whole string must match
    why     = "a cus_1 fixture hides a column too narrow for a real id"

**`why` is mandatory**, for the same reason `@pytest.mark.subprocess("<why>")`'s is: the declaration
alone is a rubber stamp, the sentence is what a reviewer weighs — and it is what the violation
prints, so the person who broke it reads the reasoning rather than a bound. A table that names no
identifier, or that constrains nothing, is a hard ERROR: a declaration that LOOKS enforced and checks
nothing is this issue's own defect one level up.

WHAT IT DOES NOT DECIDE, said rather than implied:

* **It cannot tell that a declaration is MISSING.** An identifier the project has not declared is not
  checked. Guessing a shape would be worse than not checking — inventing "a real id looks like this"
  is exactly the generic rule that does not exist.
* **A computed fixture is skipped.** Only literals are judged: `chat_id = make_id()` cannot be read,
  and reporting it would be a finding nobody can act on.
* **It reads fixtures, not columns.** The int32 overflow itself is not detected; what is detected is
  the unrealistic value that let the overflow hide.

**Diff-scoped**, on the applicability boundary (`scripts/applicability.py`): a fixture in a file this
change touches blocks; one in a file it does not is COUNTED and NAMED, never blocked. This is the
same rule `subprocess_check.py` runs on, and for the same measured reason — a check added after the
tree, run repository-wide, refused every spec write of one phase over files it had never opened.
`--all` audits everything. When git cannot say what changed, the scope is unknowable, so nothing is
enforced and the check says so out loud.

Exit codes:
    0  CLEAN       — no fixture in scope contradicts a declared shape.
    1  VIOLATIONS  — at least one in scope; each is printed as `path:line: reason`.
    2  ERROR       — a declaration or a test file could not be read or parsed. Fail closed: a file
                     the checker cannot read is a file it cannot clear.

No declaration file is CLEAN but never silent — reported on stderr. A project that has declared no
shapes must still be able to work, but a permanently green gate with no output is the invisible pass
this whole class of defect is about.

Usage:
    fixture_shapes.py [path ...]     # default: $SUBPROC_CHECK_PATHS, else tests/
    fixture_shapes.py --all          # enforce every file, not only the ones this change touches
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
import subprocess_check  # noqa: E402 — one owner of "which roots hold this project's tests"

CLEAN = 0
VIOLATIONS = 1
ERROR = 2

PREFIX = "[fixture_shapes]"

#: Where the per-project declaration lives. An env override for the same reason every other
#: per-machine path here has one; the repo root is the default because the shapes describe the
#: project, not one spec.
CONFIG_ENV = "FIXTURE_SHAPES"
DEFAULT_CONFIG = "fixture-shapes.toml"


class ShapeError(Exception):
    """The declaration itself is unusable. Always fatal: fail closed, never scan with half a rule."""


@dataclass(frozen=True)
class Shape:
    """One external identifier class, and the shape a real deployment produces for it."""

    key: str
    names: frozenset[str]
    why: str
    minimum: int | None = None
    maximum: int | None = None
    pattern: re.Pattern[str] | None = None

    def violation(self, value: int | str) -> str | None:
        """Why `value` contradicts this shape, or None when it satisfies it."""
        if isinstance(value, bool):
            return None  # `True` is an int in Python and never an external identifier
        if isinstance(value, int):
            if self.minimum is not None and value < self.minimum:
                return f"{value} is below the declared minimum {self.minimum}"
            if self.maximum is not None and value > self.maximum:
                return f"{value} is above the declared maximum {self.maximum}"
            return None
        if isinstance(value, str) and self.pattern is not None:
            return None if self.pattern.fullmatch(value) else (
                f"{value!r} does not match the declared pattern {self.pattern.pattern!r}"
            )
        return None


@dataclass(frozen=True)
class Violation:
    """One fixture contradicting a declared shape, located well enough to fix without searching."""

    path: Path
    line: int
    name: str
    key: str
    reason: str
    why: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.name} ({self.key}): {self.reason} — {self.why}"


def config_path(root: Path | None = None) -> Path:
    """The declaration file: `$FIXTURE_SHAPES`, else `fixture-shapes.toml` at the project root."""
    override = os.environ.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override)
    base = root if root is not None else Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    return Path(base) / DEFAULT_CONFIG


def load(path: Path) -> dict[str, Shape]:
    """Parse the declaration. `{}` when the file is absent; raises `ShapeError` when it is unusable.

    Absent and unusable are deliberately different: a project with nothing declared is an ordinary
    state, while a declaration that cannot be read is a rule nobody can rely on.
    """
    if not Path(path).is_file():
        return {}
    try:
        parsed = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise ShapeError(f"{path} could not be parsed: {exc}") from exc

    shapes: dict[str, Shape] = {}
    for key, table in parsed.items():
        if not isinstance(table, dict):
            raise ShapeError(f"{path}: [{key}] is not a table")
        names = table.get("names")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise ShapeError(
                f"{path}: [{key}] declares no `names` — a shape with no identifier to check is a "
                f"rule that binds nothing"
            )
        why = table.get("why")
        if not isinstance(why, str) or not why.strip():
            raise ShapeError(
                f"{path}: [{key}] declares no `why`. The declaration alone is a rubber stamp; the "
                f"sentence is what a reviewer weighs, and it is what the violation prints"
            )
        minimum, maximum = table.get("min"), table.get("max")
        raw_pattern = table.get("pattern")
        if minimum is None and maximum is None and raw_pattern is None:
            raise ShapeError(
                f"{path}: [{key}] constrains nothing — no `min`, no `max`, no `pattern`. A "
                f"declaration that looks enforced and checks nothing is the defect this file exists "
                f"to remove"
            )
        try:
            pattern = re.compile(raw_pattern) if raw_pattern is not None else None
        except re.error as exc:
            raise ShapeError(f"{path}: [{key}] has an invalid `pattern`: {exc}") from exc
        shapes[key] = Shape(
            key=key,
            names=frozenset(names),
            why=why.strip(),
            minimum=minimum if isinstance(minimum, int) else None,
            maximum=maximum if isinstance(maximum, int) else None,
            pattern=pattern,
        )
    return shapes


def _literal(node: ast.expr) -> int | str | None:
    """The literal `node` is, or None when it is not one this check can judge.

    A negative number is `UnaryOp(USub, Constant)` in the AST, and reading it as its magnitude would
    clear `-970000000` against a positive bound — the sign is part of the shape.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, str)):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _bindings(tree: ast.Module) -> list[tuple[str, ast.expr]]:
    """Every (identifier, value) a fixture can be written as: assignment, keyword, dict entry."""
    found: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.append((target.id, node.value))
                elif isinstance(target, ast.Attribute):
                    found.append((target.attr, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                found.append((node.target.id, node.value))
            elif isinstance(node.target, ast.Attribute):
                found.append((node.target.attr, node.value))
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg:
                    found.append((keyword.arg, keyword.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((key.value, value))
    return found


def scan_source(source: str, path: Path, shapes: dict[str, Shape]) -> list[Violation]:
    """Every fixture in `source` contradicting a declared shape. Raises SyntaxError on bad input."""
    tree = ast.parse(source, filename=str(path))
    by_name: dict[str, list[Shape]] = {}
    for shape in shapes.values():
        for name in shape.names:
            by_name.setdefault(name, []).append(shape)

    out: list[Violation] = []
    for name, node in _bindings(tree):
        for shape in by_name.get(name, ()):
            value = _literal(node)
            if value is None:
                continue  # computed, not written: nothing here can judge it
            reason = shape.violation(value)
            if reason is not None:
                out.append(
                    Violation(path=path, line=getattr(node, "lineno", 0), name=name,
                              key=shape.key, reason=reason, why=shape.why)
                )
    return out


def scan_path(
    root: Path, shapes: dict[str, Shape]
) -> tuple[list[Violation], list[tuple[Path, str]], list[Path]]:
    """(violations, unreadable files with their reason, roots that do not exist)."""
    root = Path(root)
    if not root.exists():
        return [], [], [root]
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts
    )
    violations: list[Violation] = []
    unreadable: list[tuple[Path, str]] = []
    for path in files:
        try:
            violations.extend(scan_source(path.read_text(encoding="utf-8"), path, shapes))
        except (OSError, SyntaxError, ValueError) as exc:
            unreadable.append((path, str(exc)))
    return violations, unreadable, []


def main(argv: list[str] | None = None) -> int:
    """Scan the requested paths against the project's declaration and return the gate's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=[], type=Path,
                        help="files or directories (default: the project's test roots)")
    parser.add_argument("--all", action="store_true", dest="enforce_all",
                        help="enforce every file, not only the ones this change touches")
    args = parser.parse_args(argv)

    try:
        declaration = config_path()
        shapes = load(declaration)
    except ShapeError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        print(f"{PREFIX} fail closed: a declaration this cannot read is a rule nothing can rely on.",
              file=sys.stderr)
        return ERROR

    if not shapes:
        # Never silent. A project that has declared no shapes must still be able to work, but a
        # permanently green gate with no output is the invisible pass this check exists to remove.
        print(
            f"{PREFIX} no fixture-shapes declared ({declaration} is absent or empty) — fixture "
            f"realism is NOT checked mechanically in this project, only by reading. Declare the "
            f"external identifiers whose shape a real deployment fixes.",
            file=sys.stderr,
        )
        return CLEAN

    roots = args.paths or subprocess_check.requested_paths([])
    scope = subprocess_check.enforcement_scope(bool(args.paths), args.enforce_all)
    print(f"{PREFIX} scope — {scope.mode}", file=sys.stderr)

    violations: list[Violation] = []
    unreadable: list[tuple[Path, str]] = []
    missing: list[Path] = []
    for root in roots:
        found, bad, gone = scan_path(root, shapes)
        violations.extend(found)
        unreadable.extend(bad)
        missing.extend(gone)

    for root in missing:
        print(f"{PREFIX} {root} does not exist — nothing scanned there.", file=sys.stderr)
    for path, reason in unreadable:
        print(f"{PREFIX} {path}: could not be parsed ({reason})", file=sys.stderr)

    enforced = [v for v in violations if scope.binds(v.path)]
    counted = len(violations) - len(enforced)
    if counted:
        applicability.report_unenforced(
            "fixture_shapes", counted,
            "fixture(s) contradict a declared shape outside what this change is responsible for",
        )
    for violation in enforced:
        print(f"{PREFIX} {violation.render()}", file=sys.stderr)

    if unreadable:
        return ERROR
    return VIOLATIONS if enforced else CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
