#!/usr/bin/env python3
"""Which skills a stage's contract depends on — derived from the agent definitions, not restated.

The pipeline delegates core behaviour to `skills/`, but an agent definition only *instructs* an
agent to load one. That instruction is not a load, so a stage can run on its own judgement with
nobody aware. Recording what was actually loaded (scripts/hook_skill_load.sh) only answers that if
something also says what SHOULD have been loaded.

That "should" is read out of `agents/<stage>.md` itself. A hand-maintained table would be a second
statement of the same fact, and the two would drift — the failure this file exists to avoid is a
document claiming a requirement the agent no longer has. Adding `skills/<name>` to an agent is
therefore enough to make it required; there is no list to remember.

The reading is deliberately literal: a `skills/<name>` reference in the agent's own definition, with
`<name>` an existing directory under `skills/`. An agent that names no skill has an empty contract,
which is the honest answer rather than a guessed one.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: `skills/<name>` as every agent definition spells it.
SKILL_REFERENCE = re.compile(r"skills/([a-z0-9][a-z0-9-]*)")

#: Plugin- and namespace-qualified agent types ("plan-build-verify:avenger-verifier") name the same
#: definition file as the bare form; so does a stage recorded as "avenger-verifier".
_QUALIFIER = re.compile(r"^.*:")


def repo_root() -> Path:
    """The canonical-source repository this module ships from."""
    return Path(__file__).resolve().parent.parent


def agent_file(stage: str, root: Path | None = None) -> Path | None:
    """The definition file for `stage`, or None when the stage is not a canonical agent."""
    base = root or repo_root()
    name = _QUALIFIER.sub("", (stage or "").strip())
    if not name:
        return None
    path = base / "agents" / f"{name}.md"
    return path if path.is_file() else None


def available_skills(root: Path | None = None) -> set[str]:
    """Every skill that actually exists, so a prose mention of a removed one is not a requirement.

    Existence is the DIRECTORY, deliberately, not a readable `SKILL.md` inside it. Keying on the file
    would make a skill whose body went missing quietly stop being required — and a required skill
    that is absent is not a lighter version of the rules, it is no rules. That case has to reach
    delivery as a LOUD BLOCKER (`scripts/hook_skills.sh`) and the audit as an unmet requirement,
    which it cannot do if this filter has already deleted it from the contract.
    """
    skills = (root or repo_root()) / "skills"
    if not skills.is_dir():
        return set()
    return {p.name for p in skills.iterdir() if p.is_dir()}


def required_skills(stage: str, root: Path | None = None) -> frozenset[str]:
    """The skills `stage`'s own definition names. Empty for a stage with no definition."""
    path = agent_file(stage, root)
    if path is None:
        return frozenset()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    existing = available_skills(root)
    return frozenset(name for name in SKILL_REFERENCE.findall(text) if name in existing)


def main(argv: list[str] | None = None) -> int:
    """CLI: print one required skill per line for the named stage."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: skill_contract.py <stage>", file=sys.stderr)
        return 2
    for name in sorted(required_skills(args[0])):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
