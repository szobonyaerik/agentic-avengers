#!/usr/bin/env python3
"""A spec's Interfaces block is a claim about code. Compare the two.

## The defect

clickup-agents phase 12: spec 12.2's `## Interfaces / contracts` prose said the poll loop polls and
then sleeps. The shipped code had to do the reverse - polling first opened a second concurrent
database session inside whichever test had booted the agent, which failed locked phase-8 tests
intermittently and once hung the suite to its 30-second watchdog. The prose was then simply wrong
about shipped behaviour. It was handled well by hand: recorded as a named divergence rather than by
editing an already-gated spec, and pinned with a test. Phase 13 produced a second instance - a spec
body that over-claimed what the code did - found only because its implementer went looking.

**Both were caught by somebody choosing to look.** Nothing mechanically compared an Interfaces block
to the code it describes, which is the same shape `scripts/fixture_shapes.py` closed for fixtures:
the judgement looks un-automatable in general, and the DECIDABLE HALF of it is not.

## What is decided here

**A call signature the Interfaces block names must exist in the code.** That is the half a static
rule can answer without a model and without a project declaration: `poll_once(` in the block and no
`poll_once` anywhere in the source tree is a spec describing code that does not exist. It is the
same question `carried_items.py` asks of a discharge's `--by` - does this name resolve to anything -
asked of the section whose whole job is naming things.

## WHAT IT DOES NOT DECIDE, said rather than implied

* **Order of operations.** Phase 12's own instance is invisible here: `poll_once` and
  `sleep_then_poll` both exist, and which one runs first is not a fact about names.
  `tests/test_interface_drift.py` pins that as a test, so the limit is measured rather than claimed.
* **Behaviour, schemas and error modes.** A function that exists and does the opposite of what the
  block says passes. So does a wrong field, a wrong status code, a wrong retry rule.
* **A dotted call is skipped.** `json.dumps()` names something in a dependency, and demanding a
  local definition for it would report a finding nobody can act on.
* **It cannot see a claim the block does not name.** An Interfaces section written as pure prose
  claims nothing this can check, and this module never asks for one to be written.
* **A call site counts as an existence proof.** The check asks whether the name exists in the tree,
  not whether it is defined exactly once at exactly the right layer. Narrower would be noisier.

## Where it is asked

On the `spec-done` trigger (`scripts/hook_verifier.sh`), the first moment the code exists AND the
implementer who wrote both the spec's block and that code still owns them - the same seam
`fixture_shapes.py` is asked at, for the same reason. Also swept diff-scoped by `gate_ci.sh`.

**Diff-scoped**, on the applicability boundary (`scripts/applicability.py`): a spec this change
touches blocks; one it does not is COUNTED and NAMED, never blocked. A check added after the tree,
run repository-wide, refused every spec write of one measured phase over files it had never opened.
When git cannot say what changed, the scope is unknowable, so nothing is enforced and it says so.

Exit codes:
    0  CLEAN   - every signature the blocks in scope name exists in the source tree.
    1  DRIFT   - at least one does not; each is printed as `path:line: name`.
    2  ERROR   - a spec could not be read, or no source root exists. Fail closed: a tree the checker
                 cannot read is a tree it cannot clear, and a scan of nothing is not a clean result.

Usage:
    interface_drift.py [spec.md ...]        # default: every spec under docs/features/
    interface_drift.py --source <dir>       # repeatable; default $INTERFACE_SOURCE_PATHS, else the
                                            # repository root minus docs/ and the usual noise
    interface_drift.py --all                # enforce every spec, not only the ones this change touched
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
import guard_scope  # noqa: E402
from md_section import slice_section  # noqa: E402

CLEAN = 0
DRIFT = 1
ERROR = 2

#: The section this reads. `allow_trailing` covers every spelling in use - `## Interfaces /
#: contracts` (the shipped spec template) and `## Interfaces & contracts` (what clickup-agents
#: actually wrote). Pinning one spelling would make the check silently skip the other, which is the
#: failure mode `spec_gate_context.py` hit when an overview used a heading nobody had checked for.
SECTION_HEADING = "Interfaces"

#: Where the environment may point the source scan, for a project whose code is not at the root.
SOURCE_PATHS_ENV = "INTERFACE_SOURCE_PATHS"

#: Directories a source scan never descends into. `docs/` above all: the specs themselves live there
#: and a spec naming a signature would otherwise prove its own claim.
SKIP_DIRS = {
    ".git",
    ".opencode",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "docs",
    "node_modules",
    "target",
    "venv",
}

#: The file types scanned for a definition. Anything else is not read at all.
SOURCE_SUFFIXES = {
    ".c",
    ".cs",
    ".go",
    ".h",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".cjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}

#: A call signature inside a code span: a BARE identifier followed by `(`. Dotted names are excluded
#: by the lookbehind, which is what keeps `json.dumps()` out of the judged set.
SIGNATURE = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

#: Inline code spans and fenced blocks - the only parts of the section that are read. Prose around
#: them is a description, not a signature, and reading it turns every sentence into a finding.
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")

#: Names that are never a claim about this project's code: control flow that happens to be followed
#: by a paren, and the handful of builtins a signature block legitimately mentions. Deliberately
#: SHORT - a long denylist would start hiding real drift, and an unknown name being judged is the
#: safe direction, since the remedy is to correct the block or the code.
NOT_A_CLAIM = {
    "and",
    "as",
    "assert",
    "async",
    "await",
    "bool",
    "bytes",
    "case",
    "catch",
    "class",
    "const",
    "def",
    "del",
    "dict",
    "do",
    "elif",
    "else",
    "enum",
    "except",
    "export",
    "float",
    "for",
    "from",
    "func",
    "function",
    "if",
    "import",
    "in",
    "int",
    "interface",
    "is",
    "lambda",
    "len",
    "let",
    "list",
    "match",
    "new",
    "not",
    "or",
    "print",
    "raise",
    "return",
    "set",
    "str",
    "switch",
    "throw",
    "try",
    "tuple",
    "type",
    "typeof",
    "var",
    "while",
    "with",
    "yield",
}


class DriftError(Exception):
    """A spec or a source tree that could not be read. Always fails the caller closed."""


def _definition_patterns(name: str) -> list[re.Pattern[str]]:
    """Every shape that counts as this name existing in source.

    Language-agnostic on purpose: the pipeline's canonical agents are project-agnostic (`CLAUDE.md`
    § 8), so a check that only knew Python would be silently blind in every repo that is not one.
    """
    escaped = re.escape(name)
    return [
        # A declaration in any of the languages scanned.
        re.compile(
            rf"\b(?:def|function|func|fn|sub|class|struct|interface|type)\s+{escaped}\b"
        ),
        # An assigned callable: `const poll = () => …`, `poll: async function …`, `poll = lambda …`.
        re.compile(rf"\b{escaped}\s*[:=]\s*(?:async\s+)?(?:function\b|\(|lambda\b|<)"),
        # A method or a call site. The name existing in the tree IS the claim being checked; a
        # narrower rule would report drift for every method defined in a shape this list forgot.
        re.compile(rf"(?<![\w.]){escaped}\s*\("),
    ]


def code_spans(section: str) -> list[tuple[int, str]]:
    """(line offset, text) for every fenced block and inline code span in the section."""
    spans: list[tuple[int, str]] = []
    for match in FENCE.finditer(section):
        spans.append((section[: match.start()].count("\n"), match.group(1)))
    prose = FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), section)
    for match in INLINE_CODE.finditer(prose):
        spans.append((prose[: match.start()].count("\n"), match.group(1)))
    return spans


def signatures(spec_text: str) -> list[tuple[str, int]]:
    """(name, 1-based line in the spec) for every call signature the Interfaces block names.

    Deduplicated on the name, keeping its first mention: a block that names one function three times
    has made one claim, and reporting it three times is three lines a reader has to reconcile.
    """
    found = slice_section(spec_text, SECTION_HEADING, min_level=2, allow_trailing=True)
    if found is None:
        return []
    heading, body = found
    base = spec_text[: spec_text.index(heading)].count("\n") + 1
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for offset, span in code_spans(body):
        for match in SIGNATURE.finditer(span):
            name = match.group(1)
            if name in NOT_A_CLAIM or name in seen:
                continue
            seen.add(name)
            out.append((name, base + offset + span[: match.start()].count("\n") + 1))
    return out


def source_files(roots: list[Path]) -> list[Path]:
    """Every scanned file under the given roots. An empty result is the caller's problem, not a pass."""
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return files


def _corpus(roots: list[Path]) -> str:
    """The scanned source, read once. A spec names a handful of names; re-reading the tree per name
    would make the check quadratic in the size of the thing it is protecting."""
    chunks: list[str] = []
    for path in source_files(roots):
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            raise DriftError(f"cannot read {path}: {exc}") from exc
    return "\n".join(chunks)


def drift(spec: Path, roots: list[Path]) -> list[tuple[str, int]]:
    """The signatures this spec's Interfaces block names that the source tree does not have."""
    try:
        text = Path(spec).read_text(encoding="utf-8")
    except OSError as exc:
        raise DriftError(f"cannot read {spec}: {exc}") from exc
    named = signatures(text)
    if not named:
        return []
    corpus = _corpus(roots)
    return [
        (name, line)
        for name, line in named
        if not any(pattern.search(corpus) for pattern in _definition_patterns(name))
    ]


def default_roots(root: Path) -> list[Path]:
    """Where source is looked for when the caller names nowhere.

    `INTERFACE_SOURCE_PATHS` is the project's declaration, exactly as `SUBPROC_CHECK_PATHS` is for
    the cost gate: a generic check, scoped by the project that knows where its code lives.
    """
    declared = (os.environ.get(SOURCE_PATHS_ENV) or "").strip()
    if declared:
        return [Path(part) for part in re.split(r"[:,]", declared) if part.strip()]
    return [root]


def specs_under(root: Path) -> list[Path]:
    return sorted(Path(root).glob("docs/features/*/phases/*/specs/*/spec.md"))


def _report(spec: Path, found: list[tuple[str, int]]) -> None:
    for name, line in found:
        print(
            f"{spec}:{line}: the Interfaces block names `{name}(`, which no scanned source file "
            f"has. Either the block describes code that was never written, or the code was renamed "
            f"and the block was not - and a spec's Interfaces block is read as a description of "
            f"SHIPPED behaviour.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs", nargs="*", type=Path)
    parser.add_argument("--source", action="append", default=[], type=Path)
    parser.add_argument("--root", default=Path("."), type=Path)
    parser.add_argument(
        "--all",
        action="store_true",
        help="every spec, not only the ones this change touched",
    )
    args = parser.parse_args(argv)

    roots = args.source or default_roots(args.root)
    if not source_files(roots):
        print(
            f"[interface_drift] no source files under {', '.join(str(r) for r in roots)} - nothing "
            f"was compared. A scan of nothing is not a clean result; point the check at the "
            f"project's code with {SOURCE_PATHS_ENV} or --source.",
            file=sys.stderr,
        )
        return ERROR

    targets = [Path(s) for s in args.specs] or specs_under(args.root)
    if not targets:
        print(
            f"[interface_drift] no specs under {args.root}/docs/features - nothing to check",
            file=sys.stderr,
        )
        return CLEAN

    scope: set[Path] | None = None
    if not args.all:
        scope = applicability.changed_paths(Path(args.root))
        if scope is None:
            print(
                "[interface_drift] git cannot say what changed, so the scope is unknowable and no "
                "spec is checked. Run with --all for a full audit.",
                file=sys.stderr,
            )
            return CLEAN

    blocked = 0
    counted = 0
    try:
        for spec in targets:
            found = drift(spec, roots)
            if not found:
                continue
            if args.all or applicability.touched(spec, scope):  # type: ignore[arg-type]
                _report(spec, found)
                blocked += len(found)
            else:
                counted += len(found)
    except DriftError as exc:
        print(f"[interface_drift] {exc}", file=sys.stderr)
        return ERROR

    if counted:
        print(
            f"[interface_drift] {counted} signature(s) in specs this change did not touch name "
            f"nothing in the source tree; counted, not blocked. Run with --all to audit them.",
            file=sys.stderr,
        )
    if blocked:
        return DRIFT
    print(
        f"[interface_drift] clean - every signature named by {len(targets)} spec Interfaces "
        f"block(s) in scope exists in the source tree.",
        file=sys.stderr,
    )
    return CLEAN


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
