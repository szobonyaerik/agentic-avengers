#!/usr/bin/env python3
"""Flag tests that spawn a subprocess without declaring it.

A test that shells out costs a process launch every run, and a test that shells out to the *suite*
costs a whole extra suite run. Four such tests once survived spec review, fidelity checking
and per-phase verification across five phases of one feature — one suite run was
really five, and they accounted for roughly five-sixths of its runtime. None of those stages could
have caught them: every one reads for **correctness**, and a subprocess is not incorrect. It is
expensive, and cost is not something a reader sees.

This is the cheap complement to those stages: an AST walk, no model, under a second, run at
spec-review time so the cost is visible while the spec is still cheap to change.

**It is a declaration requirement, not a ban.** Some tests genuinely must drive a real binary. Those
carry `@pytest.mark.subprocess("<why>")` — on the test, or on its class — or, when every test in the
file spawns, `pytestmark = pytest.mark.subprocess("<why>")` at module or class level. The
justification is mandatory, because the marker alone is a rubber stamp while the sentence is what a
reviewer weighs. The nearest declaration wins: method over class, class over module.

A file-wide or class-wide declaration is the accepted cost of supporting the module-level helper
shape (`def run_hook(...): return subprocess.run(...)`, which belongs to no test function): it also
covers spawners added later that its one sentence was never written about.

Deliberately NOT a wall-clock budget. Seven runs of one unchanged suite spanned 66.43s to 137.76s on
one machine — a 2.1x swing with zero code change — so a runtime gate would fail green suites at
random and teach everyone to bypass it. This counts something that does not move with machine load.

**It is diff-scoped**, on the applicability boundary (`scripts/applicability.py`): a spawner in a file
this change touches blocks; one in a file it does not is COUNTED and NAMED, never blocked. This ran
repository-wide when it shipped, and the first phase to meet it on a real repository was refused
EVERY spec write over 17 undeclared spawners in locked phase-1 and phase-7 tests that the phase had
never opened — the gate rejected before any model saw a body, and the only way forward was a logged
break-glass over work nobody was doing. That is the hostage failure, and it is the same rule
`doc_read_path.py`, `verifier_precheck.py`, `verifier_evidence.py`, the spec re-gate cache and the
mutation gate already run on. `--all` audits the whole tree; it is deliberately NOT wired into CI,
because a full audit on every commit would reinstate the hostage one layer out. When git cannot say
what changed the scope is unknowable, so nothing is enforced and the check says so out loud.

Exit codes:
    0  CLEAN       — no undeclared spawner in scope.
    1  VIOLATIONS  — at least one in scope; each is printed as `path:line: reason`.
    2  ERROR       — a file could not be read or parsed. Fail closed: a file the checker cannot
                     read is a file it cannot clear.

A root that does not exist is CLEAN but never silent: it is reported on stderr. A project with no
tests yet must still be able to write a spec, but a project whose tests live somewhere else would
otherwise get a permanently green gate with no output — invisible is the defect, not permissive.
Point it at the real root with `SUBPROC_CHECK_PATHS` (os.pathsep-separated) when tests are not at
`tests/`; explicit arguments win over it.

Usage:
    subprocess_check.py [path ...]        # default: $SUBPROC_CHECK_PATHS, else tests/
    subprocess_check.py --all             # enforce every file, not only the ones this change touches
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402

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

# Where to look when no path is given. The env override exists because a project whose tests are at
# `packages/api/tests/` would otherwise get a gate that passes without ever reading a file.
PATHS_ENV = "SUBPROC_CHECK_PATHS"
DEFAULT_PATHS = ("tests",)


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


def pytestmark_marker(body: list[ast.stmt]) -> bool | None:
    """The `pytestmark` declaration in one body — a module's or a class's — read as a decorator is.

    Both the single-marker and list forms are accepted — a file (or a class) whose every test drives
    a real binary should say so once, not once per test.
    """
    verdict: bool | None = None
    for node in body:
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


def module_marker(tree: ast.Module) -> bool | None:
    """The file-wide `pytestmark` declaration."""
    return pytestmark_marker(tree.body)


def scope_marker(scope: ast.AST) -> bool | None:
    """The marker a single scope declares, or None when it declares nothing.

    A class counts as a scope: `@pytest.mark.subprocess("why")` above `class TestCli:` and a
    `pytestmark` assignment in its body are both idiomatic pytest, and reading only functions here
    reported a correctly-declared test as a violation.
    """
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        for decorator in scope.decorator_list:
            found = justified(decorator)
            if found is not None:
                return found
    if isinstance(scope, ast.ClassDef):
        return pytestmark_marker(scope.body)
    return None


def declaration(scopes: list[ast.AST], file_wide: bool | None) -> bool | None:
    """The nearest applicable marker for a call: innermost scope out, then the module.

    Nearest wins, so a method's own marker overrides its class's and a class's overrides the file's.
    """
    for scope in reversed(scopes):
        found = scope_marker(scope)
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


def scan_path(root: Path) -> tuple[list[Violation], list[tuple[Path, str]], list[Path]]:
    """Scan one file or directory tree.

    Returns (violations, unreadable files as (path, why), absent roots). An absent root is not an
    error — a project with no tests yet must still be able to write a spec — but it is reported,
    because a gate that reads nothing and says nothing is indistinguishable from a gate that passed.

    An unreadable file carries its own path because the applicability boundary applies to it too: a
    syntax error in a locked phase's test is fail-closed for whoever changes that file, not for every
    spec written anywhere in the repository afterwards.
    """
    if not root.exists():
        return [], [], [root]
    files = sorted(root.rglob("*.py")) if root.is_dir() else [root]

    found: list[Violation] = []
    errors: list[tuple[Path, str]] = []
    for path in files:
        try:
            found.extend(scan_source(path.read_text(encoding="utf-8"), path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append((path, str(exc)))
    return found, errors, []


def requested_paths(argv_paths: list[Path]) -> list[Path]:
    """The roots to scan: explicit arguments, else `$SUBPROC_CHECK_PATHS`, else `tests/`."""
    if argv_paths:
        return argv_paths
    override = os.environ.get(PATHS_ENV, "").strip()
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p.strip()]
    return [Path(p) for p in DEFAULT_PATHS]


@dataclass(frozen=True)
class Enforcement:
    """What this run may block on, and the mode it decided that in.

    `paths is None` means every file it scanned. `unknowable` is deliberately distinct from an empty
    scope: a clean working tree legitimately touches nothing, while git being unable to answer is a
    check that did not run — and the two must not print the same sentence.
    """

    paths: set[Path] | None
    mode: str
    unknowable: bool = False

    def binds(self, path: Path) -> bool:
        return self.paths is None or applicability.touched(path, self.paths)


def enforcement_scope(explicit: bool, enforce_all: bool) -> Enforcement:
    """Decide what this run may block on.

    Three modes, the same three every scoped check in this pipeline has: `--all` audits everything,
    paths named on the command line are enforced whole because the caller named them, and the
    default — the spec gate's own call — is scoped to what this change touched. The mode is always
    printed: a silent fallback is how a check comes to mean something other than what its caller
    believes.
    """
    if enforce_all:
        return Enforcement(None, "--all: every file scanned")
    if explicit:
        return Enforcement(None, "the paths named on the command line")
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    scope = applicability.changed_paths(root)
    if scope is None:
        return Enforcement(
            set(),
            f"git cannot say what changed under {root}, so the scope is unknowable and nothing is "
            f"enforced. Run `--all` for a full audit.",
            unknowable=True,
        )
    return Enforcement(scope, "diff-scoped: the files this change touches")


def main(argv: list[str] | None = None) -> int:
    """Scan the requested paths and return the gate's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        type=Path,
        help=f"files or directories (default: ${PATHS_ENV}, else {DEFAULT_PATHS[0]})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="enforce_all",
        help="enforce every file scanned, not only the ones this change touches (full audit)",
    )
    args = parser.parse_args(argv)

    found: list[Violation] = []
    errors: list[tuple[Path, str]] = []
    absent: list[Path] = []
    for path in requested_paths(args.paths):
        path_found, path_errors, path_absent = scan_path(Path(path))
        found.extend(path_found)
        errors.extend(path_errors)
        absent.extend(path_absent)

    for root in absent:
        print(
            f"[subprocess_check] no such test root: {root} — nothing was scanned. Set "
            f"${PATHS_ENV} if this project's tests live elsewhere.",
            file=sys.stderr,
        )

    enforcement = enforcement_scope(bool(args.paths), args.enforce_all)
    print(f"  subprocess check scope — {enforcement.mode}", file=sys.stderr)

    blocking = [v for v in found if enforcement.binds(v.path)]
    blocking_errors = [(p, e) for p, e in errors if enforcement.binds(p)]

    for path, error in blocking_errors:
        print(f"[subprocess_check] unreadable: {path}: {error}", file=sys.stderr)
    for violation in blocking:
        print(f"[subprocess_check] {violation.render()}", file=sys.stderr)

    # Counted and named. This is the whole point of the boundary: 17 spawners in locked phases
    # nobody had touched refused every spec write of a phase that had not opened one of them.
    carried = (len(found) - len(blocking)) + (len(errors) - len(blocking_errors))
    applicability.report_unenforced(
        "subprocess_check",
        carried,
        "undeclared spawner(s) or unreadable file(s) the scope did not cover"
        + (
            " (the scope is unknowable, so nothing was enforced)"
            if enforcement.unknowable
            else " in files this change did not touch — they are checked when you next change "
            "them, and `--all` audits them now"
        ),
    )

    if blocking_errors:
        return ERROR
    if blocking:
        print(
            f"[subprocess_check] {len(blocking)} undeclared subprocess call(s). Each one costs a "
            "process launch on every run of the suite.",
            file=sys.stderr,
        )
        return VIOLATIONS
    return CLEAN


if __name__ == "__main__":
    sys.exit(main())
