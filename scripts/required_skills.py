#!/usr/bin/env python3
"""Which skills each stage REQUIRES - and the evidence that it actually got them.

The pipeline delegates its core behaviour to thirteen skills, and until now it delegated by *asking*:
every implementer prompt says "Load `skills/tdd` before you start". That is an instruction, not a
mechanism. Nothing checked, nothing recorded, and a stage that skipped a required skill fell back
silently to whatever the model already believed - which is the failure mode the whole pipeline exists
to remove from everything else. `skills/ponytail` is the only one genuinely injected, and
`docs/lessons/` shipped with a complete written procedure and **zero invocations** for the same
reason: a directive in a skill reaches only the agents that load that skill.

So a required skill is **injected, not requested**, and every injection is **recorded**:

- `scripts/hook_skills.sh` reads this table on `SubagentStart` and injects each required skill's body
  into the subagent's context. The agent does not have to remember, and cannot decline.
- Each injection appends one line to `.avenger-skill-loads.jsonl` (gitignored scratch): the agent
  type, the skill, its size, whether it was required, and whether it loaded. That file is the
  evidence, and it is what makes "100% of required loads evidenced" a number rather than a hope.
- **A required skill that is missing or unreadable is a LOUD BLOCKER**, not a silent fallback. The
  hook says so in the injected context and records `loaded: false`. The one thing it must never do is
  let the stage proceed believing it has rules it does not have.

The table is keyed by an unanchored, case-insensitive regex over `agent_type`, so plugin-scoped names
like `plan-build-verify:avenger-verifier` match. Order matters only for readability; the first
matching entry wins, so the more specific patterns come first.

Reach is scoped deliberately, and differently per skill for the same reason `hook_ponytail.sh`
excludes the Verifier and `hook_lessons.sh` does not: a skill that fights a stage's job must not
reach it.

Usage:
    required_skills.py for <agent-type>          print the skill dirs that stage requires
    required_skills.py table                     print the whole table
    required_skills.py verify [--root .]         every required skill exists and is readable
                                                 (exit 1 when one does not)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OK = 0
MISSING = 1
ERROR = 2

#: (agent_type pattern, required skill directory names). Every pipeline agent gets
#: `pipeline-conventions` - it is the shared rulebook, and an agent that has not read it is an agent
#: guessing at phases, gates and the ID scheme.
REQUIRED: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Implementers write both the tests and the code, so the TDD procedure is not optional for them.
    (
        r"avenger-(backend-architect|frontend-developer)",
        ("pipeline-conventions", "tdd", "self-improvement"),
    ),
    # The Verifier's triage procedure and verdict schema. `tdd` too, because the anti-patterns it
    # reads a green suite for are defined there and nowhere else.
    (r"avenger-verifier", ("pipeline-conventions", "verifier-triage", "tdd", "self-improvement")),
    (r"avenger-spec-writer", ("pipeline-conventions", "spec-review-checklist", "self-improvement")),
    (r"avenger-(breaker|bug-hunter)", ("pipeline-conventions", "self-improvement")),
    (r"avenger-handover", ("pipeline-conventions", "phase-handover", "self-improvement")),
    (
        r"avenger-(task-analyst|solution-architect|implementation-planner)",
        ("pipeline-conventions", "codemap", "self-improvement"),
    ),
    # Anything else in the family: the rulebook and the lessons procedure, nothing more.
    (r"avenger-", ("pipeline-conventions", "self-improvement")),
)


def required_for(agent_type: str) -> tuple[str, ...]:
    """The skills that stage must have. Empty for an agent this pipeline does not own."""
    name = (agent_type or "").strip()
    if not name:
        return ()
    for pattern, skills in REQUIRED:
        try:
            if re.search(pattern, name, re.IGNORECASE):
                return skills
        except re.error:  # a broken pattern must not inject into every subagent
            return ()
    return ()


def skill_path(root: Path, skill: str) -> Path:
    return Path(root) / "skills" / skill / "SKILL.md"


def missing(root: Path) -> list[tuple[str, str]]:
    """(agent pattern, skill) for every required skill that is absent or unreadable."""
    out: list[tuple[str, str]] = []
    for pattern, skills in REQUIRED:
        for skill in skills:
            path = skill_path(root, skill)
            try:
                if not path.read_text(encoding="utf-8").strip():
                    out.append((pattern, skill))
            except OSError:
                out.append((pattern, skill))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_for = sub.add_parser("for")
    p_for.add_argument("agent_type")
    sub.add_parser("table")
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    args = parser.parse_args(argv)

    if args.action == "for":
        print("\n".join(required_for(args.agent_type)))
        return OK
    if args.action == "table":
        for pattern, skills in REQUIRED:
            print(f"{pattern}\t{','.join(skills)}")
        return OK

    gaps = missing(args.root)
    if not gaps:
        return OK
    print(
        "required_skills: a stage requires a skill that is missing or empty. A required skill that "
        "is not there is a stage running on whatever the model already believed:",
        file=sys.stderr,
    )
    for pattern, skill in gaps:
        print(f"  ✗ {pattern} requires skills/{skill}/SKILL.md", file=sys.stderr)
    return MISSING


if __name__ == "__main__":
    raise SystemExit(main())
