#!/usr/bin/env python3
"""What reasoning effort each stage runs at — declared where the harness reads it, not in prose.

`commands/avenger-run.md` carried a per-stage effort table and called reasoning effort "the largest
lever on cost that does not change what any gate checks". It then told the orchestrator to **pass**
`effort` when it spawned a stage. Nothing could obey that: the delegation tool exposes
`description`, `prompt`, `subagent_type`, `model` and `isolation`, and no effort parameter. So every
stage of every phase ran at the session default, the string `effort` appears in no phase metrics
record, and the single largest cost lever the pipeline believed it had was decoration. Any claim
that a low-effort profile held quality was a claim about a profile that was never in force.

**The mechanism that does exist is the agent definition's own `effort:` frontmatter key.** The
harness parses it for `.claude/agents/*.md` and for plugin agent files alike, validates it against
the named levels below, and applies it when the stage is spawned — no caller has to remember
anything, and there is no wrapper in between that could accept a value and drop it.

So the table is not restated here. `declared()` READS it out of `agents/<stage>.md`, exactly as
`skill_contract.py` reads the required-skills contract out of the same files, and for the same
reason: a second statement of a fact is the copy that drifts, and a drifted copy is worse than none.
This module owns no allocation of its own. What it owns is the three ways the defect can come back:

1. **A stage declares no effort.** It runs at the session default, which is the state this
   repository shipped for ten phases. `check` names it.
2. **A document states a pair the definitions do not.** Either it disagrees with the mechanism, or
   it names a stage no definition backs — a table with nothing behind it, which is the state that
   made the original claim unsupportable. Both are named. This is deliberately scoped to sections
   *about effort*, so `criticality: high` in a neighbouring table is not a false positive.
3. **An instruction to supply effort at spawn time comes back.** It is unobeyable by construction,
   and it is what made the original table look enforced. Two such instructions were live when this
   was written: "Pass `effort` when you spawn a subagent" and "Invoke … at `effort: high`".

**What this module does NOT do, stated because the alternative is how the original defect read.** It
does not observe what any stage actually ran at. A model that does not support a level is silently
downgraded, and only the harness sees that, so a green `check` says the allocation exists and agrees
everywhere it is written down — never that the lever was pulled. Nothing here may be read as a
measurement in a retrospective. The observed half is tracked as `fm-metrics-stage-efforts-field`:
firstmate's phase metrics record is a closed schema with no field for a resolved effort, and a writer
that accepted one and dropped it would look fixed, which is worse than the gap.

CLI:
    stage_effort.py table [--root <r>]     the declared allocation, rendered from the definitions
    stage_effort.py check [--root <r>]     exit 1 with every problem named on stderr
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: The named levels the harness accepts. It also accepts a bare integer; the pipeline's allocation is
#: written in named levels, and an integer here would be a number nobody could compare to a table.
LEVELS = ("low", "medium", "high", "xhigh", "max")

#: Documents a stage or an operator reads. The same set `doc_read_path.py` guards, for the same
#: reason: a claim that came back one caller at a time is caught wherever it was added.
SOURCE_DIRS = ("agents", "commands", "skills", "prompts")

#: `effort: <level>` on its own line inside a definition's frontmatter. Anchored, so the prose
#: instruction the guard below refuses cannot be mistaken for the mechanism.
FRONTMATTER_EFFORT = re.compile(r"^effort:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

#: Plugin- and namespace-qualified stage names ("plan-build-verify:avenger-verifier") name the same
#: definition file as the bare form.
_QUALIFIER = re.compile(r"^.*:")

#: A markdown heading, and whether it is about effort. Rows are attributed to the section they are
#: under, so the pair check binds tables that mean effort and no others.
_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_ABOUT_EFFORT = re.compile(r"\beffort\b", re.I)

#: A backticked token, as both the stage column and the level column of such a table spell theirs.
_TICKED = re.compile(r"`([^`]+)`")

#: The instruction nothing can obey. Two shapes, both live when this was written:
#:   "Pass `effort` when you spawn a subagent"      — a parameter the delegation tool does not have
#:   "Invoke `…avenger-breaker` at `effort: high`"  — the same parameter, supplied inline
#: The frontmatter key is anchored to the start of its line, so it never matches either.
#:
#: The first shape requires the BACKTICKS, deliberately. A parameter is named in code voice; a
#: sentence *about* the rule is not, and the first draft of this guard fired on the runbook paragraph
#: explaining it. A guard that cannot be described without tripping gets described less precisely,
#: which is the wrong trade. The second shape needs no backticks because `effort: high` and
#: `effort=high` are already code voice whether or not anyone marked them up.
UNOBEYABLE = (
    re.compile(r"\b(?:pass|passing|supply|set|give)\s+(?:an?\s+)?`effort`", re.I),
    re.compile(r"\S[ \t]+`?effort`?[ \t]*[:=][ \t]*`?(?:" + "|".join(LEVELS) + r")\b", re.I),
)


def repo_root() -> Path:
    """The canonical-source repository this module ships from."""
    return Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter(text: str) -> str:
    """The definition's frontmatter block, or "" when it has none.

    Read separately from the body on purpose: `effort:` in the frontmatter is the mechanism, and
    the same characters in the body are the unobeyable instruction. One regex over the whole file
    could not tell them apart, and would have to choose which of the two guards to break.
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def agent_paths(root: Path | None = None) -> list[Path]:
    base = (root or repo_root()) / "agents"
    return sorted(base.glob("*.md")) if base.is_dir() else []


def agent_file(stage: str, root: Path | None = None) -> Path | None:
    """The definition file for `stage`, or None when the stage is not a canonical agent."""
    name = _QUALIFIER.sub("", (stage or "").strip())
    if not name:
        return None
    path = (root or repo_root()) / "agents" / f"{name}.md"
    return path if path.is_file() else None


def declared_effort(stage: str, root: Path | None = None) -> str | None:
    """The effort a stage's own definition declares, or None when it declares none.

    None is the honest answer and never a guessed default: a stage that declares nothing runs at
    whatever the session is set to, and calling that "medium" would hide the exact state this item
    exists to end.
    """
    path = agent_file(stage, root)
    if path is None:
        return None
    found = FRONTMATTER_EFFORT.search(_frontmatter(_read(path)))
    return found.group(1) if found else None


def declared(root: Path | None = None) -> dict[str, str | None]:
    """Every canonical stage and the effort it declares, in name order."""
    return {p.stem: declared_effort(p.stem, root) for p in agent_paths(root)}


def render_table(root: Path | None = None) -> str:
    """The allocation, rendered from the definitions — the one place a reader should look."""
    lines = ["| stage | effort |", "|---|---|"]
    for stage, level in declared(root).items():
        lines.append(f"| `{stage}` | " + (f"`{level}` |" if level else "**undeclared** |"))
    return "\n".join(lines)


# --- check ---------------------------------------------------------------------------------------

def _effort_rows(text: str) -> list[tuple[int, str]]:
    """Table rows sitting under a heading about effort, with their line numbers.

    Attribution by section rather than by content is what keeps the pair check narrow: a row means
    an effort allocation because of the heading it is under, not because it happens to contain the
    word `high`.
    """
    rows: list[tuple[int, str]] = []
    in_effort_section = False
    for lineno, line in enumerate(text.splitlines(), 1):
        heading = _HEADING.match(line)
        if heading:
            in_effort_section = bool(_ABOUT_EFFORT.search(heading.group(1)))
            continue
        if in_effort_section and line.lstrip().startswith("|"):
            rows.append((lineno, line))
    return rows


def _check_definitions(root: Path) -> list[str]:
    problems: list[str] = []
    for stage, level in declared(root).items():
        if level is None:
            problems.append(
                f"agents/{stage}.md: declares no `effort:`, so this stage runs at whatever the "
                f"session is set to. Add `effort: <{'|'.join(LEVELS)}>` to its frontmatter — that "
                f"key is the mechanism; a table in a document is not."
            )
        elif level not in LEVELS:
            problems.append(
                f"agents/{stage}.md: declares effort '{level}', which is not one of "
                f"{', '.join(LEVELS)}. The harness refuses it and the stage falls back to the "
                f"session default."
            )
    return problems


def _check_documents(root: Path) -> list[str]:
    problems: list[str] = []
    known = set(declared(root))
    for source_dir in SOURCE_DIRS:
        base = root / source_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            text = _read(path)
            problems += _check_rows(rel, text, known, root)
            problems += _check_instructions(rel, text)
    return problems


def _check_rows(rel: str, text: str, known: set[str], root: Path) -> list[str]:
    problems: list[str] = []
    for lineno, row in _effort_rows(text):
        tokens = _TICKED.findall(row)
        stages = [_QUALIFIER.sub("", t) for t in tokens if _QUALIFIER.sub("", t).startswith("avenger-")]
        levels = [t for t in tokens if t.lower() in LEVELS]
        if not stages:
            continue
        for stage in stages:
            if stage not in known:
                problems.append(
                    f"{rel}:{lineno}: names `{stage}` in an effort table, but no `agents/{stage}.md` "
                    f"declares one. A row no definition backs is a table with nothing behind it."
                )
                continue
            level = declared_effort(stage, root)
            if level is None:
                problems.append(
                    f"{rel}:{lineno}: states an effort for `{stage}`, which declares no `effort:` "
                    f"in `agents/{stage}.md`. This is the documented-but-never-applied state: fix "
                    f"the definition, or delete the row."
                )
            elif levels and level not in {x.lower() for x in levels}:
                problems.append(
                    f"{rel}:{lineno}: says `{levels[0]}` for `{stage}`, which declares `{level}`. "
                    f"The definition is the mechanism; change it there, or make this row agree."
                )
    return problems


def _frontmatter_lines(text: str) -> int:
    """How many leading lines the frontmatter block occupies, so the body scan can skip them.

    Skipped rather than filtered by content: `effort: high` is the MECHANISM inside frontmatter and
    the unobeyable instruction outside it, and the same characters cannot be told apart any other
    way. Line numbers stay those of the whole file, so a reported problem is one a reader can open.
    """
    front = _frontmatter(text)
    return front.count("\n") + 2 if front else 0


def _check_instructions(rel: str, text: str) -> list[str]:
    problems: list[str] = []
    skip = _frontmatter_lines(text)
    for lineno, line in enumerate(text.splitlines(), 1):
        if lineno <= skip:
            continue
        for pattern in UNOBEYABLE:
            if pattern.search(line):
                problems.append(
                    f"{rel}:{lineno}: instructs a caller to supply effort at spawn time, which "
                    f"cannot be obeyed — the delegation tool has no effort parameter. Declare it in "
                    f"the stage's own `agents/<stage>.md` frontmatter instead: {line.strip()[:90]}"
                )
                break
    return problems


def check(root: Path | None = None) -> list[str]:
    """Every way the documented-but-never-applied state can come back, named."""
    base = root or repo_root()
    return _check_definitions(base) + _check_documents(base)


# --- CLI -----------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("table", "check"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else repo_root()

    if args.cmd == "table":
        print(render_table(root))
        return 0

    stages = declared(root)
    if not stages:
        # Said out loud rather than passing invisibly, the same way subprocess_check.py reports an
        # absent test root. A vendored repo receives the generated runtimes and no `agents/`, so this
        # check has nothing to read there — and "nothing to read" must never look like "checked, and
        # every stage declares its effort", which is the shape of every silent gate in this repo.
        print(
            f"stage effort: no agents/ under {root} — nothing checked. The declared allocation "
            f"lives in the canonical repository's agent definitions.",
            file=sys.stderr,
        )
        return 0

    problems = check(root)
    for problem in problems:
        print(f"  ✗ {problem}", file=sys.stderr)
    if problems:
        print(
            f"stage effort: {len(problems)} problem(s). The per-stage table is real only while "
            f"every stage declares its effort where the harness reads it.",
            file=sys.stderr,
        )
        return 1
    print(f"stage effort: {len(declared(root))} stages declare their effort.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
