#!/usr/bin/env python3
"""Flag tests that spawn a subprocess without declaring it.

A test that shells out costs a process launch every run, and a test that shells out to the *suite*
costs a whole extra suite run. Four such tests once survived spec review, fidelity checking,
cross-family review and per-phase verification across five phases of one feature — one suite run was
really five, and they accounted for roughly five-sixths of its runtime. None of those stages could
have caught them: every one reads for **correctness**, and a subprocess is not incorrect. It is
expensive, and cost is not something a reader sees.

This is the cheap complement to those stages: an AST walk, no model, under a second, run at
spec-review time so the cost is visible while the spec is still cheap to change.

**It is a declaration requirement, not a ban.** Some tests genuinely must drive a real binary. Those
carry `@pytest.mark.subprocess("<why>")` — or, when every test in the file spawns,
`pytestmark = pytest.mark.subprocess("<why>")` — and the justification is mandatory, because the
marker alone is a rubber stamp while the sentence is what a reviewer weighs.

Deliberately NOT a wall-clock budget. Seven runs of one unchanged suite spanned 66.43s to 137.76s on
one machine — a 2.1x swing with zero code change — so a runtime gate would fail green suites at
random and teach everyone to bypass it. This counts something that does not move with machine load.

Exit codes:
    0  CLEAN       — no undeclared spawner.
    1  VIOLATIONS  — at least one; each is printed as `path:line: reason`.
    2  ERROR       — a file could not be read or parsed. Fail closed: a file the checker cannot
                     read is a file it cannot clear.

Usage:
    subprocess_check.py [path ...]        # default: tests/
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

CLEAN = 0
VIOLATIONS = 1
ERROR = 2

# The spawners, by the module that owns them. Matched after alias resolution, so `import subprocess
# as sp` and `from subprocess import run as go` resolve to the same entries.
SPAWNERS: dict[str, frozenset[str]] = {
    "subprocess": frozenset({"run", "Popen", "check_output", "check_call", "call"}),
    "asyncio": frozenset({"create_subprocess_exec", "create_subprocess_shell"}),
}

MARKER = "subprocess"


@dataclass(frozen=True)
class Violation:
    """One undeclared spawner, located well enough to fix without searching."""

    path: Path
    line: int
    reason: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


def spawner_aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve this module's import aliases for the spawning modules and their functions.

    Returns (modules, functions): local name -> owning module. `import subprocess as sp` puts
    `sp -> subprocess` in the first; `from subprocess import run as go` puts `go -> subprocess` in
    the second. Without this, renaming an import would silently defeat the check.
    """
    modules: dict[str, str] = {}
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in SPAWNERS:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            owned = SPAWNERS.get(node.module or "")
            if owned:
                for alias in node.names:
                    if alias.name in owned:
                        functions[alias.asname or alias.name] = node.module or ""
    return modules, functions


def spawner_name(call: ast.Call, modules: dict[str, str], functions: dict[str, str]) -> str | None:
    """The `module.function` a call resolves to, or None when it is not a spawner."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = modules.get(func.value.id)
        if module and func.attr in SPAWNERS[module]:
            return f"{func.value.id}.{func.attr}"
    elif isinstance(func, ast.Name):
        module = functions.get(func.id)
        if module:
            return f"{module}.{func.id}"
    return None


def justified(decorator: ast.expr) -> bool | None:
    """Whether a decorator is the subprocess marker, and if so whether it carries a justification.

    None  — not the marker.
    False — the marker, but with no non-empty reason.
    True  — the marker with a justification.
    """
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call else decorator
    if not (isinstance(target, ast.Attribute) and target.attr == MARKER):
        return None
    if call is None:
        return False
    reasons = [arg for arg in call.args] + [kw.value for kw in call.keywords]
    return any(
        isinstance(r, ast.Constant) and isinstance(r.value, str) and r.value.strip()
        for r in reasons
    )


def module_marker(tree: ast.Module) -> bool | None:
    """The file-wide `pytestmark` declaration, read the same way as a decorator.

    Both the single-marker and list forms are accepted — a file whose every test drives a real
    binary should say so once, not once per test.
    """
    verdict: bool | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        value = node.value
        marks = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        for mark in marks:
            found = justified(mark)
            if found is True:
                return True
            if found is False:
                verdict = False
    return verdict


def declaration(scopes: list[ast.AST], file_wide: bool | None) -> bool | None:
    """The nearest applicable marker for a call, innermost scope first, then the module."""
    for scope in reversed(scopes):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in scope.decorator_list:
                found = justified(decorator)
                if found is not None:
                    return found
    return file_wide


def scan_source(source: str, path: Path) -> list[Violation]:
    """Every undeclared spawner in one module. Raises SyntaxError on unparseable input."""
    tree = ast.parse(source)
    modules, functions = spawner_aliases(tree)
    if not modules and not functions:
        return []

    file_wide = module_marker(tree)
    found: list[Violation] = []

    def walk(node: ast.AST, scopes: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                name = spawner_name(child, modules, functions)
                if name:
                    declared = declaration(scopes, file_wide)
                    if declared is not True:
                        found.append(
                            Violation(
                                path=path,
                                line=child.lineno,
                                reason=(
                                    f"{name}() spawns a process; the enclosing test carries the "
                                    f"@pytest.mark.{MARKER} marker but no justification — state in "
                                    "one sentence why a real process is required"
                                    if declared is False
                                    else f"{name}() spawns a process. Drive the behaviour in-process, "
                                    f"or mark the test @pytest.mark.{MARKER}(\"<why a real process "
                                    'is required>")'
                                ),
                            )
                        )
            walk(child, scopes + [child])

    walk(tree, [tree])
    return sorted(found, key=lambda v: v.line)


def scan_path(root: Path) -> tuple[list[Violation], list[str]]:
    """Scan one file or directory tree. Returns (violations, unreadable-file errors)."""
    if not root.exists():
        return [], []  # a project with no tests yet is not a failure
    files = sorted(root.rglob("*.py")) if root.is_dir() else [root]

    found: list[Violation] = []
    errors: list[str] = []
    for path in files:
        try:
            found.extend(scan_source(path.read_text(encoding="utf-8"), path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {exc}")
    return found, errors


def main(argv: list[str] | None = None) -> int:
    """Scan the given paths (default `tests/`) and return the gate's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", default=["tests"], type=Path, help="files or directories (default: tests)"
    )
    args = parser.parse_args(argv)

    found: list[Violation] = []
    errors: list[str] = []
    for path in args.paths or [Path("tests")]:
        path_found, path_errors = scan_path(Path(path))
        found.extend(path_found)
        errors.extend(path_errors)

    for error in errors:
        print(f"[subprocess_check] unreadable: {error}", file=sys.stderr)
    for violation in found:
        print(f"[subprocess_check] {violation.render()}", file=sys.stderr)

    if errors:
        return ERROR
    if found:
        print(
            f"[subprocess_check] {len(found)} undeclared subprocess call(s). Each one costs a "
            "process launch on every run of the suite.",
            file=sys.stderr,
        )
        return VIOLATIONS
    return CLEAN


if __name__ == "__main__":
    sys.exit(main())
