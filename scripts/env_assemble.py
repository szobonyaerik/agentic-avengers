#!/usr/bin/env python3
"""Assemble a project `.env` from example files, and refuse a placeholder that reaches a run.

The documented setup line used to be:

    cat .env.example .env.pipeline.example > .env

A `cat` joins BYTES. When the first file does not end in a newline, its last key fuses onto the
first line of the second file and the parser reads the merged line as a different value. Measured
(issue #108, grid-bot-platform, 27-29 Aug 2026): `DRY_RUN=true` was read as `DRY_RUN=false` on every
worktree assembled that way, silently, for the life of the task. Nobody chose it, nothing reported
it, and it was found only during live-fire checks. That account was a demo. The same instruction
against a live-configured worktree places real orders with nobody aware.

Two lines of defence, because the first one alone is a claim:

* **Join with an explicit separator.** Every source contributes a text that ends in a newline, so
  two keys can never share a line.
* **Read the result back.** Every key each source declared must parse out of the assembled file
  holding the value that source declared - the last source to declare a key wins, exactly as a
  concatenation would. Anything else is an `AssemblyError` naming the key, and NOTHING is written:
  a half-assembled `.env` is the state this module exists to prevent.

The verification is deliberately not a list of safety-critical key names. `DRY_RUN` belongs to one
project; the property that matters - a value survived the join unchanged - is the same for every key
in every project, and a hardcoded list would be silent about the next one.

A third refusal lives here because it is about the same file: a value still carrying the templates'
`REPLACE_ME` marker must stop a run rather than become its configuration. `gate_runner.py` asks
before any provider call, so the refusal lands at the first gate with the key named, instead of as
an authentication error from a provider that was handed a placeholder.

**The LAST source to declare a key wins.** That one rule decides the source ORDER everywhere, and
getting it backwards is not a cosmetic mistake: the shipped `docs/templates/env.example` declares
`OPENROUTER_API_KEY`, `GATE_MODEL`, `GATE_PROVIDER`, `AUTHOR_FAMILY` and `MUTATION_POLICY`
uncommented, so a template placed after an operator's own file replaces their real API key with
`sk-or-v1-REPLACE_ME` and reverts their chosen model, provider, author family and mutation policy to
its defaults - a wrong config that never errors, which is the same class of harm as the `DRY_RUN`
inversion above.

**So a worktree that already has a live `.env` merges IN PLACE by naming that file as its own LAST
source.** `assemble` reads every source before it writes anything, so `--out .env <template> .env
--force` is not the destructive `--force` it looks like: the live file is read first, every key it
declares wins over the template's default for the same key, every pipeline key it does not have is
added, and the read-back then proves every key it declared survived into the result. A `.env`
written any other way is how the pipeline's keys end up somewhere no gate reads, since
`env_file.find_env_file` looks for `.env` and nothing else. The greenfield form - a template into a
`.env` that does not exist yet - has no such conflict and is unaffected.

There is no prompt and no conflict-resolution mode; the order IS the resolution. Re-merging is not
idempotent either: the template's text is appended again each time, which parses correctly because
the last declaration still wins, and de-duplicating on write would break this module's contract that
the bytes are its sources joined.

    python3 scripts/env_assemble.py assemble --out .env .env.example .env.pipeline.example
    python3 scripts/env_assemble.py assemble --out .env .env.pipeline.example .env --force
    python3 scripts/env_assemble.py check [--root .]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_file import ENV_FILENAME, find_env_file, load_into, parse  # noqa: E402
from env_file import read as read_env  # noqa: E402

#: The marker the shipped templates put in every value the operator must fill in.
PLACEHOLDER_MARKER = "REPLACE_ME"


class AssemblyError(Exception):
    """A `.env` could not be assembled such that every declared value survived."""


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AssemblyError(f"cannot read {path}: {exc}") from exc


def join_text(texts: list[str]) -> str:
    """Join source texts so no source's last line can fuse onto the next source's first.

    An empty source contributes nothing; every other one contributes exactly one trailing newline.
    """
    return "".join(
        text if text.endswith("\n") else text + "\n" for text in texts if text
    )


def declared_in(texts: list[str]) -> dict[str, str]:
    """Every key `texts` declare, with the value the LAST text to declare it gives it.

    That is the resolution a concatenation has always had; what this module changes is that the
    resolution is now stated and checked rather than being whatever the bytes produced. It takes
    the texts rather than the paths so `assemble` can derive the expectation and the joined result
    from ONE read of each source: reading twice is a seam where a file edited in between makes the
    read-back report "declared as X, assembled as Y" about a file that was simply rewritten - a
    misleading cause on the one step whose whole purpose is a legible config failure.
    """
    values: dict[str, str] = {}
    for text in texts:
        values.update(parse(text))
    return values


def declared(sources: list[Path]) -> dict[str, str]:
    """`declared_in`, reading each source. The answer for callers that hold paths, not texts."""
    return declared_in([_read(source) for source in sources])


def verify(assembled: str, expected: dict[str, str]) -> list[str]:
    """Problems reading `expected` back out of the assembled text; empty when it all survived."""
    got = parse(assembled)
    problems = []
    for key, value in expected.items():
        if key not in got:
            problems.append(
                f"{key}: declared as {value!r}, absent from the assembled file"
            )
        elif got[key] != value:
            problems.append(f"{key}: declared as {value!r}, assembled as {got[key]!r}")
    return problems


def assemble(sources: list[Path], out: Path, force: bool = False) -> dict[str, str]:
    """Write `out` from `sources`, or raise an `AssemblyError` naming the cause.

    An existing `out` is never overwritten without `force`: a live `.env` holds the operator's real
    credentials, and the greenfield assumption that it does not is half of issue #108.

    Every check runs BEFORE the write, so a refusal leaves `out` exactly as it was. The one thing
    that guarantee does not cover is the write itself: a disk that fills part-way through leaves a
    truncated file, because the read-back that would notice has already run. That failure names its
    own cause rather than escaping as a traceback, which is the most this module can offer there.
    """
    out = Path(out)
    if out.exists() and not force:
        raise AssemblyError(
            f"{out} already exists - refusing to overwrite it. It may hold live credentials; "
            f"pass --force only when you mean to replace it."
        )

    texts = [_read(Path(source)) for source in sources]
    expected = declared_in(texts)
    assembled = join_text(texts)

    problems = verify(assembled, expected)
    if problems:
        raise AssemblyError(
            "assembled .env does not hold what the sources declared - nothing was written:\n  "
            + "\n  ".join(problems)
        )

    try:
        out.write_text(assembled, encoding="utf-8")
    except OSError as exc:
        raise AssemblyError(f"cannot write {out}: {exc}") from exc
    return expected


#: Variables whose value is author-written PROSE rather than configuration (CLAUDE.md section 6).
#: A bypass reason legitimately NAMES the key it is bypassing - `GATE_BYPASS="OPENROUTER_API_KEY
#: still REPLACE_ME in CI, proceeding"` - and reporting that sentence as an unfilled config value
#: is a wrong message on the exact path an operator uses to break glass past this refusal. Stated
#: here, where the rule lives, so no caller has to remember it.
PROSE_VALUED_KEYS = frozenset({"GATE_BYPASS", "GATE_SAME_FAMILY_WAIVER"})


def placeholder_keys(values: dict[str, str]) -> list[str]:
    """The keys whose value still carries the templates' fill-me-in marker."""
    return sorted(
        key
        for key, value in values.items()
        if key not in PROSE_VALUED_KEYS and PLACEHOLDER_MARKER in value
    )


#: The pipeline's own variables. Every one of them is consumed on every provider path: the gate
#: variables by the runner itself, and `OPENROUTER_API_KEY` by BOTH - `call_openrouter` sends it as
#: a bearer token, and the `opencode` child inherits it verbatim (`proc_group.run_bounded` passes no
#: `env=`), which is the documented way to authenticate opencode (AGENTS.md, docs/USAGE.md). So
#: there is no provider under which one of these is unread, and nothing here is scoped by provider.
PIPELINE_KEY_PREFIXES = ("GATE_", "OPENROUTER_", "AUTHOR_FAMILY", "MUTATION_", "SPEC_")


def run_placeholders(env: dict[str, str], root: Path | str = ".") -> list[str]:
    """The keys a RUN must refuse to start on, with their EFFECTIVE values.

    Scoped deliberately: the project `.env`'s own keys - the operator wrote that file and every key
    in it is theirs to fill in - plus the pipeline's own variables from the real environment.
    Scanning the whole environment instead would let an unrelated tool's scaffold variable wedge
    every gate with a remedy that is not the operator's to apply - a wedge, not a gate. The values
    are read from `env`, never from the file, because the real environment wins over the file and it
    is the effective value that reaches the provider.
    """
    keys = set(read_env(Path(root)))
    keys |= {key for key in env if key.startswith(PIPELINE_KEY_PREFIXES)}
    return placeholder_keys({key: env[key] for key in sorted(keys) if key in env})


def _cmd_assemble(args: argparse.Namespace) -> int:
    try:
        values = assemble(args.sources, Path(args.out), force=args.force)
    except AssemblyError as exc:
        print(f"env-assemble: {exc}", file=sys.stderr)
        return 1
    print(
        f"env-assemble: wrote {args.out} with {len(values)} keys from {len(args.sources)} sources"
    )
    left = placeholder_keys(values)
    if left:
        print(
            "env-assemble: fill these in before the first gate call - a run is refused while they "
            f"carry {PLACEHOLDER_MARKER}: {', '.join(left)}",
            file=sys.stderr,
        )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Ask on demand exactly what a run asks, through the one function that answers it.

    `check` used to parse `<root>/.env` alone, which is a second, weaker copy of the rule: it
    disagreed with a run in both directions - it refused a placeholder the real environment already
    overrode, and it passed a pipeline variable exported as a placeholder with no file at all.
    """
    root = Path(args.root)
    path = find_env_file(root)
    if path is None:
        # Absent is not a violation: the real environment may carry everything. Said out loud
        # rather than passing invisibly, the rule this repository applies to every unscoped check.
        print(
            f"env-assemble: no {ENV_FILENAME} at or above {root} - checking the real "
            f"environment alone",
            file=sys.stderr,
        )

    left = run_placeholders(load_into(dict(os.environ), root), root)
    if left:
        print(
            f"env-assemble: these values still carry {PLACEHOLDER_MARKER}: {', '.join(left)}. "
            f"A placeholder must not become a run's configuration - fill them in (the project "
            f"{ENV_FILENAME}, or the real environment).",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser(
        "assemble", help="join example files into a .env and verify the result"
    )
    build.add_argument(
        "--out", default=ENV_FILENAME, help=f"file to write (default {ENV_FILENAME})"
    )
    build.add_argument(
        "--force", action="store_true", help="replace an existing output file"
    )
    build.add_argument(
        "sources", nargs="+", help="example files, in order; the last one wins"
    )
    build.set_defaults(func=_cmd_assemble)

    look = sub.add_parser("check", help="refuse a placeholder value reaching a run")
    look.add_argument(
        "--root", default=".", help="project root holding the .env (default: cwd)"
    )
    look.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
