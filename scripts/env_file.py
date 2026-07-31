#!/usr/bin/env python3
"""Load pipeline configuration from a project's `.env` file.

The gates read their model ids, provider and API key from the environment, and hooks inherit whatever
environment Claude Code was launched with — which is nothing useful when the app is started from a
desktop icon or an IDE. This module fills that gap from a `.env` file next to the project.

Two rules govern it:

* **The real environment always wins.** A variable already set — an exported shell value, a CI secret
  — is never overwritten, so a committed default cannot shadow what CI was configured with.
* **The file is parsed, never sourced.** Sourcing would execute arbitrary shell from a file people
  paste secrets into. Values such as `$(...)` stay literal strings.

Shell callers use `scripts/load_env.sh`, which evals the `--export` output of this module. There is
one implementation of the parsing, here.

    python3 scripts/env_file.py --export [--root .]
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
from pathlib import Path

ENV_FILENAME = ".env"
IDENTIFIER = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def parse(text: str) -> dict[str, str]:
    """Parse `.env` text into a flat mapping, skipping comments and malformed lines."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key, sep, value = line.partition("=")
        if not sep:
            continue

        key = key.strip()
        if not IDENTIFIER.match(key):
            continue

        # A trailing `# comment` is NOT stripped: secrets legitimately contain '#', and truncating a
        # key at one would fail in a way that looks like a bad credential.
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        values[key] = value
    return values


def find_env_file(start: Path) -> Path | None:
    """The nearest `.env` at or above `start`, or None when there is none."""
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def read(root: Path) -> dict[str, str]:
    """Values from the nearest `.env`; empty when absent or unreadable."""
    path = find_env_file(root)
    if path is None:
        return {}
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def load_into(env: dict[str, str], root: Path | None = None) -> dict[str, str]:
    """Fill `env` in place with any `.env` values it does not already define."""
    for key, value in read(Path(root) if root is not None else Path.cwd()).items():
        if key not in env:
            env[key] = value
    return env


def export_lines(values: dict[str, str], existing: dict[str, str]) -> list[str]:
    """Shell `export` statements for the values `existing` does not already define.

    Every value is quoted with `shlex.quote`, so evaluating these lines cannot execute the file's
    contents.
    """
    return [
        f"export {key}={shlex.quote(value)}"
        for key, value in values.items()
        if key not in existing
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: emit shell exports for `scripts/load_env.sh` to eval."""
    parser = argparse.ArgumentParser(description="Load pipeline config from .env")
    parser.add_argument(
        "--export", action="store_true", help="print shell export statements"
    )
    parser.add_argument(
        "--root", default=".", help="where to start looking (default: cwd)"
    )
    args = parser.parse_args(argv)

    values = read(Path(args.root))
    if args.export:
        for line in export_lines(values, dict(os.environ)):
            print(line)
    else:
        for key in sorted(values):
            print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
