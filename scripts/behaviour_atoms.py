#!/usr/bin/env python3
"""The EXTRACTION layer of the behaviour-drift guard: Python source -> atoms, and citations.

`scripts/behaviour_drift.py` decides; this module only says what the semantic surface of one file
IS. It is kept apart because an extraction blind spot is how a drift guard fails while reporting
clean (issue #97, and grid-bot-platform's own guard, which read `*` in a keyword-only parameter list
as multiplication until it was moved from the token stream onto the AST). What is and is not an atom
is decided here, in one place, and `tests/test_behaviour_drift.py` pins each decision.

## An atom

A short key with NO IDENTIFIER in it, so that a rename can never change one:

  * `literal:<type>:<repr>`  - every `ast.Constant` except a docstring, a bare string statement
                               and anything inside a type annotation (those describe, they do not run)
  * `op:<Op>` / `op:aug:<Op>` - arithmetic, comparison, membership, identity, boolean, unary
  * `flow:<edge>`            - `if`, `ifexp`, `else`, `loop`, `try`, `except:<builtin|custom|bare>`,
                               `finally`, `early-return`, `raise:<builtin|custom|re-raise>`,
                               `break`, `continue`, `assert`, `with`, `comp-if`, `match`, `case`,
                               `case-guard`

A TAIL return - the last statement of a function body - is deliberately not an atom: extracting a
helper adds one, and extraction is what a refactor is allowed to do. An early return is a control
edge and is one. Exception types are named only when they are BUILTINS, because `except ValueError`
becoming `except Exception` changes what is swallowed while `except OrderError` becoming
`except PlacementError` may be a rename.

## Where a citation may sit

Every atom carries the line range a `# behaviour: R<n>.<k>.<m>` comment may occupy to authorise it:
the whole statement for a simple statement, the HEADER alone for a compound one, so a comment inside
a branch body never authorises the condition above it; for an `else:`/`finally:` edge, the lines
between the block it follows and the block it opens.
"""

from __future__ import annotations

import ast
import builtins
import io
import re
import tokenize
from dataclasses import dataclass

#: A citation, matched inside a COMMENT token only - never inside a string. Both spellings.
CITATION = re.compile(
    r"behaviou?r:\s*(R\d+\.\d+\.\d+(?:\s*,\s*R\d+\.\d+\.\d+)*)", re.IGNORECASE
)
REQUIREMENT_ID = re.compile(r"R\d+\.\d+\.\d+")


class AtomError(Exception):
    """A source that does not parse as Python. The caller fails closed on it."""


@dataclass(frozen=True)
class Atom:
    """One unit of the semantic surface.

    `line` is where a reader is pointed; `span` is the lines the atom occupies (what a hunk is
    matched against); `cite` is the lines a citation for it may sit on.
    """

    key: str
    line: int
    span: tuple[int, int]
    cite: tuple[int, int]


def _builtin_exception(node: ast.expr | None) -> str:
    if node is None:
        return "bare"
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Tuple):
        return "|".join(sorted(_builtin_exception(e) for e in node.elts))
    if isinstance(node, ast.Name):
        found = getattr(builtins, node.id, None)
        if isinstance(found, type) and issubclass(found, BaseException):
            return node.id
    return "custom"


def _end(node: ast.AST) -> int:
    return getattr(node, "end_lineno", None) or node.lineno


def _first_line(stmts: list) -> int | None:
    return min((s.lineno for s in stmts), default=None)


def _last_line(stmts: list) -> int | None:
    return max((_end(s) for s in stmts), default=None)


class _Walker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.atoms: list[Atom] = []
        self._ranges: list[tuple[int, int]] = []
        self._tails: list[ast.stmt | None] = []

    # -- bookkeeping -------------------------------------------------------------------------

    def _add(
        self,
        key: str,
        node: ast.AST,
        *,
        cite: tuple[int, int] | None = None,
        span: tuple[int, int] | None = None,
        line: int | None = None,
    ) -> None:
        cite = cite or (self._ranges[-1] if self._ranges else (node.lineno, _end(node)))
        span = span or (node.lineno, _end(node))
        self.atoms.append(Atom(key, line or node.lineno, span, cite))

    @staticmethod
    def _header(node: ast.AST) -> tuple[int, int]:
        """The lines of a compound statement before its first block starts."""
        blocks = [
            getattr(node, name, None)
            for name in ("body", "handlers", "orelse", "finalbody", "cases")
        ]
        firsts = [f for f in (_first_line(b) for b in blocks if b) if f is not None]
        if not firsts:
            return node.lineno, _end(node)
        return node.lineno, max(node.lineno, min(firsts) - 1)

    @staticmethod
    def _between(before: list, after: list) -> tuple[int, int]:
        """The lines an `else:`/`finally:` keyword can sit on: after one block, before the next.

        An `elif` has no such gap - the next block starts on the keyword's own line - so the edge
        is cited on that header line.
        """
        start, end = _last_line(before), _first_line(after)
        if start is None or end is None or start + 1 > end - 1:
            head = after[0].lineno if after else (end or start or 1)
            return head, head
        return start + 1, end - 1

    def _stmt(self, node: ast.stmt, *, header: bool) -> None:
        self._ranges.append(self._header(node) if header else (node.lineno, _end(node)))
        try:
            self.generic_visit(node)
        finally:
            self._ranges.pop()

    def visit(self, node: ast.AST) -> None:
        method = getattr(self, "visit_" + node.__class__.__name__, None)
        if method is not None:
            method(node)
        elif isinstance(node, ast.stmt):
            self._stmt(node, header=False)
        else:
            self.generic_visit(node)

    # -- literals ---------------------------------------------------------------------------

    def visit_Constant(self, node: ast.Constant) -> None:
        self._add(f"literal:{type(node.value).__name__}:{node.value!r}", node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Constant):
            return  # a docstring, a bare string or a `...` body: describes, does not run
        self._stmt(node, header=False)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._ranges.append((node.lineno, _end(node)))
        try:
            self.visit(node.target)
            if node.value is not None:
                self.visit(node.value)
        finally:
            self._ranges.pop()

    def visit_arguments(self, node: ast.arguments) -> None:
        for default in [*node.defaults, *node.kw_defaults]:
            if default is not None:
                self.visit(default)

    # -- operators --------------------------------------------------------------------------

    def visit_BinOp(self, node: ast.BinOp) -> None:
        self._add(f"op:{type(node.op).__name__}", node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._ranges.append((node.lineno, _end(node)))
        try:
            self._add(f"op:aug:{type(node.op).__name__}", node)
            self.generic_visit(node)
        finally:
            self._ranges.pop()

    def visit_Compare(self, node: ast.Compare) -> None:
        for op in node.ops:
            self._add(f"op:{type(op).__name__}", node)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self._add(f"op:{type(node.op).__name__}", node)
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        self._add(f"op:{type(node.op).__name__}", node)
        self.generic_visit(node)

    # -- control flow -----------------------------------------------------------------------

    def _edge(self, key: str, node: ast.AST, where: tuple[int, int]) -> None:
        self._add(key, node, cite=where, span=where, line=where[0])

    def _orelse(self, node: ast.stmt, before: list) -> None:
        if getattr(node, "orelse", None):
            self._edge("flow:else", node, self._between(before, node.orelse))

    def visit_If(self, node: ast.If) -> None:
        self._edge("flow:if", node, self._header(node))
        self._orelse(node, node.body)
        self._stmt(node, header=True)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._add("flow:ifexp", node)
        self.generic_visit(node)

    def _loop(self, node: ast.stmt) -> None:
        self._edge("flow:loop", node, self._header(node))
        self._orelse(node, node.body)
        self._stmt(node, header=True)

    visit_For = visit_AsyncFor = visit_While = _loop

    def visit_Try(self, node: ast.Try) -> None:
        self._edge("flow:try", node, self._header(node))
        for handler in node.handlers:
            self._edge(
                f"flow:except:{_builtin_exception(handler.type)}",
                handler,
                self._header(handler),
            )
        self._orelse(node, [*node.body, *node.handlers])
        if node.finalbody:
            self._edge(
                "flow:finally",
                node,
                self._between(
                    [*node.body, *node.handlers, *node.orelse], node.finalbody
                ),
            )
        self._stmt(node, header=True)

    visit_TryStar = visit_Try

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._stmt(node, header=True)  # type: ignore[arg-type]

    def visit_With(self, node: ast.With) -> None:
        self._edge("flow:with", node, self._header(node))
        self._stmt(node, header=True)

    visit_AsyncWith = visit_With

    def visit_Match(self, node: ast.Match) -> None:
        self._edge("flow:match", node, self._header(node))
        for case in node.cases:
            first = case.pattern.lineno
            self._edge("flow:case", case.pattern, (first, first))
            if case.guard is not None:
                self._edge("flow:case-guard", case.pattern, (first, first))
        self._stmt(node, header=True)

    def _function(self, node: ast.stmt) -> None:
        self._tails.append(node.body[-1] if node.body else None)
        self._ranges.append(self._header(node))
        try:
            for decorator in node.decorator_list:
                self.visit(decorator)
            self.visit(node.args)
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self._ranges.pop()
            self._tails.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.visit(node.body)

    def visit_Return(self, node: ast.Return) -> None:
        if not (self._tails and self._tails[-1] is node):
            self._edge("flow:early-return", node, (node.lineno, _end(node)))
        self._stmt(node, header=False)

    def visit_Raise(self, node: ast.Raise) -> None:
        kind = "re-raise" if node.exc is None else _builtin_exception(node.exc)
        self._ranges.append((node.lineno, _end(node)))
        try:
            self._add(f"flow:raise:{kind}", node)
            self.generic_visit(node)
        finally:
            self._ranges.pop()

    def _simple_edge(self, key: str, node: ast.stmt) -> None:
        self._ranges.append((node.lineno, _end(node)))
        try:
            self._add(key, node)
            self.generic_visit(node)
        finally:
            self._ranges.pop()

    def visit_Break(self, node: ast.Break) -> None:
        self._simple_edge("flow:break", node)

    def visit_Continue(self, node: ast.Continue) -> None:
        self._simple_edge("flow:continue", node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._simple_edge("flow:assert", node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        for test in node.ifs:
            self._add("flow:comp-if", test)
        self.generic_visit(node)


def atoms(source: str, label: str = "<source>") -> list[Atom]:
    """Every atom in one Python source. A source that does not parse is an ERROR, never empty."""
    try:
        tree = ast.parse(source, filename=label)
    except (SyntaxError, ValueError) as exc:
        raise AtomError(f"{label} does not parse as Python: {exc}") from exc
    walker = _Walker()
    walker.visit(tree)
    return walker.atoms


def citations(source: str) -> tuple[dict[int, list[str]], list[str]]:
    """(citations by line, header citations) - ids named in `# behaviour:` COMMENTS only.

    The header is every citing comment before the first statement that is not a docstring; a NEW
    file cites there once for everything it adds. Read from the token stream, so a `behaviour:`
    inside a string literal cites nothing.
    """
    by_line: dict[int, list[str]] = {}
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return {}, []
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        found = CITATION.search(token.string)
        if found:
            by_line.setdefault(token.start[0], []).extend(
                REQUIREMENT_ID.findall(found.group(1))
            )
    first: int | None = None
    try:
        for stmt in ast.parse(source).body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                continue
            first = stmt.lineno
            break
    except (SyntaxError, ValueError):
        pass
    header = [
        rid
        for line, ids in sorted(by_line.items())
        if first is None or line < first
        for rid in ids
    ]
    return by_line, header


def owners(source: str, label: str = "<source>") -> dict[int, str]:
    """Which top-level definition owns each line - the unit a change is accounted in.

    A git hunk is a property of the DIFF, not of the program: with no context lines at all git
    still coalesces a rewritten statement and an unrelated line next to it into one hunk, and a `0`
    arriving in the second then cancels the `0` leaving the first - the exact masking issue #107
    was filed for, one level down. A definition is the smallest unit that cannot do that while
    still letting a reformat, which rewrites one statement across several lines, cancel. Lines
    outside every top-level `def`/`class` share `<module>`; a nested definition belongs to the
    top-level one that holds it.
    """
    try:
        tree = ast.parse(source, filename=label)
    except (SyntaxError, ValueError) as exc:
        raise AtomError(f"{label} does not parse as Python: {exc}") from exc
    out: dict[int, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        for line in range(start, _end(node) + 1):
            out[line] = node.name
    return out
