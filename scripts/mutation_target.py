#!/usr/bin/env python3
"""Does this repo have anything for the mutation gate to mutate?

The mutation gate is allowed exactly one skip: a repo whose `module-path` names a directory that
isn't there (a docs/config-only repo). Every other shape must RUN the gate. That asymmetry is the
whole point of this file — deciding "nothing to mutate" from a value you failed to parse fails
OPEN, and silently disables the gate for everyone who never edited the shipped config.

That is not hypothetical: the shell parser this replaces read `module-path` with grep+sed and
stripped surrounding quotes but not a trailing inline comment, so the repo's own shipped
`module-path = "src"  # your package/dir under test` parsed as `src" # your package...`. No such
path can exist, so the skip branch fired — under MUTATION_POLICY=enforce as much as advisory.

So: parse with `tomllib`, never with a regex. A `#` inside a quoted path is part of the path; a `#`
after an unquoted value is a comment; and TOML that doesn't parse at all is not a licence to skip.

    python3 scripts/mutation_target.py [--root .] cosmic-ray.toml

Exit codes (SKIP is the only one that stops the gate, and it is deliberately the narrow case):
    0  SKIP      module-path is a clean scalar and does not exist under --root; prints the path
    1  RUN_GATE  anything else — path exists, list form, absent key, unparseable TOML, wrong type
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

SKIP = 0
RUN_GATE = 1


def scalar_module_path(config_text: str) -> str | None:
    """The configured `module-path`, but only when it is a plain scalar.

    Returns None for every ambiguous shape — a list of paths, a missing key, a non-string value,
    or TOML that doesn't parse — because the caller turns None into "run the gate". Only a value
    this function is certain about may be used to skip.
    """
    try:
        parsed = tomllib.loads(config_text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    section = parsed.get("cosmic-ray")
    if not isinstance(section, dict):
        return None
    value = section.get("module-path")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def decide(config_text: str, root: Path) -> tuple[int, str | None]:
    """(exit code, resolved path). SKIP only for a clean scalar that is genuinely absent."""
    module_path = scalar_module_path(config_text)
    if module_path is None:
        return RUN_GATE, None
    if (root / module_path).exists():
        return RUN_GATE, module_path
    return SKIP, module_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", help="path to cosmic-ray.toml")
    ap.add_argument("--root", default=".", help="repo root the module-path is relative to")
    args = ap.parse_args(argv)
    try:
        config_text = Path(args.config).read_text(encoding="utf-8")
    except OSError:
        # A missing/unreadable config is the caller's fail-closed case, not a skip.
        return RUN_GATE
    code, module_path = decide(config_text, Path(args.root))
    if code == SKIP and module_path:
        print(module_path)
    return code


if __name__ == "__main__":
    sys.exit(main())
