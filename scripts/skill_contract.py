#!/usr/bin/env python3
"""Which skills a stage's contract depends on — derived from the agent definitions, not restated.

The pipeline delegates core behaviour to `skills/`, but an agent definition only *instructs* an
agent to load one. That instruction is not a load, so a stage can run on its own judgement with
nobody aware. Recording what was actually loaded (scripts/hook_skill_load.sh) only answers that if
something also says what SHOULD have been loaded.

That "should" is read out of `agents/<stage>.md` itself. A hand-maintained table would be a second
statement of the same fact, and the two would drift — the failure this file exists to avoid is a
document claiming a requirement the agent no longer has. Adding `skills/<name>` to an agent's
declared line is therefore enough to make it required; there is no list to remember.

**The reading is the DECLARED LINE, not the whole file.** Every canonical agent carries one
`Required skills` line in its header and says of it, in as many words, that the line is the
contract. Scanning the whole document instead made prose load-bearing, and prose says things a
contract does not: `agents/avenger-verifier.md` names `skills/mutation-interpret` *in order to say
it applies only if the project turned the mutation gate on*, and both implementers name
`skills/ponytail` *in order to say it is injected automatically* — a sentence explaining that an
off-switchable hook delivers a skill was read as requiring the stage to load it, which turned
`PONYTAIL_OFF=1` into a phase wedge. One statement of the contract, in the one place three
documents already say it lives.

An agent with no such line has an EMPTY contract. That is the honest answer rather than a guessed
one, and it is stated rather than silently assumed: `required_skills.py verify` fails on a canonical
agent that declares no line, because an omission there silently shrinks a stage's contract.

`<name>` must be an existing directory under `skills/`, so a declared mention of a removed skill is
not a requirement nobody can satisfy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: THE declared line. One per agent, in its header, and each agent's own text calls it the contract.
CONTRACT_LINE = re.compile(r"^.*\bRequired skills\b.*$", re.MULTILINE)

#: `skills/<name>` as that line spells it.
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


def contract_line(stage: str, root: Path | None = None) -> str | None:
    """The stage's declared `Required skills` line, or None when it declares none."""
    path = agent_file(stage, root)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = CONTRACT_LINE.search(text)
    return match.group(0) if match else None


def required_skills(stage: str, root: Path | None = None) -> frozenset[str]:
    """The skills `stage`'s DECLARED line names. Empty for a stage that declares none.

    Deliberately not the whole file: a prose mention is not a declaration, and reading it as one made
    every sentence that merely *names* a skill part of the contract.
    """
    line = contract_line(stage, root)
    if line is None:
        return frozenset()
    existing = available_skills(root)
    return frozenset(name for name in SKILL_REFERENCE.findall(line) if name in existing)


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
