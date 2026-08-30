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
`env_file.find_env_file` looks for `.env` and nothing else.

**The CREATE form obeys the same principle: the more operator-owned file goes later.** What decides
which that is, is CONTENT. An example still byte-identical to the shipped `docs/templates/env.example`
carries nobody's decision, so it is named FIRST and supplies only what nothing else declares; one
that has been edited is operator-owned and is named after it. Both halves are load-bearing: named
last, a freshly copied template reverted a team's committed `GATE_MODEL`, `GATE_PROVIDER` and
`MUTATION_POLICY` to its own defaults; named first unconditionally, an operator's filled-in
`.env.pipeline.example` lost every key it shares with `.env.example` - and the two derive from the
same template, so they share all of them. Content and not PRESENCE, because `/pipeline-init` step
2a's own `cp -n` changes presence, so a presence rule answered one way before that copy and the
reverse after it. `pipeline_init.Survey` is the one place that decides both source lists; nothing
else restates them.

There is no prompt and no conflict-resolution mode; the order IS the resolution. Re-merging is not
idempotent either: the template's text is appended again each time, which parses correctly because
the last declaration still wins, and de-duplicating on write would break this module's contract that
the bytes are its sources joined.

    python3 scripts/env_assemble.py assemble --out .env .env.pipeline.example .env.example
    python3 scripts/env_assemble.py assemble --out .env .env.pipeline.example .env --force
    python3 scripts/env_assemble.py check [--root .]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_file import ENV_FILENAME, find_env_file, load_into, parse  # noqa: E402
from env_file import read as read_env  # noqa: E402

#: The marker the shipped templates put in every value the operator must fill in.
PLACEHOLDER_MARKER = "REPLACE_ME"


class AssemblyError(Exception):
    """A `.env` could not be assembled such that every declared value survived."""


def _read(path: Path) -> str:
    """A source's text, or an `AssemblyError` naming the path and the cause.

    `UnicodeDecodeError` is caught beside `OSError` because the merge-in-place form names the live
    `.env` as a source, and a live `.env` is hand-edited - one cp1252 or latin-1 byte pasted into a
    comment or a password makes the operator's own remedy answer with a stack trace instead of a
    named cause, on the one step whose whole purpose is a legible config failure.
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
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
    read-back report a mismatch about a key in a file that was simply rewritten - a
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
    """Problems reading `expected` back out of the assembled text; empty when it all survived.

    A problem names the KEY and the SHAPE of the mismatch, and NEITHER value - not the declared one,
    not the assembled one, not a truncated or masked rendering of either. `assemble` raises these
    verbatim and the CLI prints them to stderr, so on the merge-in-place path - where the live
    `.env` is one of its own sources - an interpolated value is the operator's real credential
    printed into their terminal and into the agent transcript that ran the step. The key plus the
    mismatch kind is the whole diagnostic content: which key, and whether it vanished or changed.
    """
    got = parse(assembled)
    problems = []
    for key in expected:
        if key not in got:
            problems.append(
                f"{key}: declared by a source, absent from the assembled file"
            )
        elif got[key] != expected[key]:
            problems.append(
                f"{key}: declared by a source, assembled with a different value"
            )
    return problems


def assemble(sources: list[Path], out: Path, force: bool = False) -> dict[str, str]:
    """Write `out` from `sources`, or raise an `AssemblyError` naming the cause.

    An existing `out` is never overwritten without `force`: a live `.env` holds the operator's real
    credentials, and the greenfield assumption that it does not is half of issue #108.

    Every check runs BEFORE the write, and the write itself is ATOMIC - the bytes go to a temp file
    beside `out` and are moved over it with `os.replace`, so nothing ever truncates `out` in place.
    On the merge-in-place path the destination IS the live `.env`, holding the operator's real
    credentials, AND one of its own sources: a `write_text` there opens it in truncate mode, so a
    full disk or a signal between the truncate and the completed write destroys a file that is
    gitignored, has no backup, and can no longer be re-merged because one of the sources WAS it.
    `scripts/plugin_release.py` writes the install registry the same way, for a far less valuable
    file. So "nothing half-written is left behind" holds for the write too, not just the checks.
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

    _write_atomically(out, assembled)
    return expected


#: The temp file's name is FIXED rather than derived from the destination, so that ONE gitignore
#: pattern covers every use of this writer. That file holds the fully merged result - on the
#: merge-in-place path, the operator's real credentials - and a name built from `out.name` produced
#: `..env.<rand>.tmp` for a `.env`, which the literal `.env` in a `.gitignore` does not match: `git
#: status` reported it untracked and the `git add -A` the pipeline's own commit steps run would have
#: committed live credentials. `--out` is arbitrary, so no pattern derived from it could be written
#: down in advance; this one can. The pattern lives in this repository's `.gitignore` and in the
#: step-2 list `commands/pipeline-init.md` makes every project write - two places, and no more.
TEMP_PREFIX = ".env-assemble."
TEMP_SUFFIX = ".tmp"
#: What those two places must carry. Stated here, beside the name it describes, so a test can ask
#: git itself rather than a second copy of the literal.
TEMP_GITIGNORE_PATTERN = f"{TEMP_PREFIX}*{TEMP_SUFFIX}"


def _write_atomically(out: Path, text: str) -> None:
    """Write `text` to `out` without ever truncating it: temp file in the same directory, then
    `os.replace`. Same directory so the replace is a rename within one filesystem, which is what
    makes it atomic. An existing file's mode is carried over - a `.env` is often chmod 600 and a
    fresh temp file is not.

    The temp file is removed whenever the `os.replace` did not happen, in a `finally` rather than an
    `except OSError`. `KeyboardInterrupt` and `SystemExit` are `BaseException`, and a Ctrl-C or a
    hook-budget SIGTERM between `mkstemp` and `replace` is the exact scenario this atomic write
    exists for - caught only as `OSError`, it traded a truncated `.env` for a file holding the fully
    merged credentials left in the working tree. Cleanup alone is still not sufficient, since
    SIGKILL and power loss cannot be caught at all, which is why `TEMP_PREFIX` is also gitignored.
    """
    tmp: Path | None = None
    replaced = False
    try:
        handle, tmp_name = tempfile.mkstemp(
            dir=str(out.parent), prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX
        )
        tmp = Path(tmp_name)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if out.exists():
            os.chmod(tmp, out.stat().st_mode & 0o7777)
        os.replace(tmp, out)
        replaced = True
    except OSError as exc:
        raise AssemblyError(f"cannot write {out}: {exc}") from exc
    finally:
        if tmp is not None and not replaced:
            tmp.unlink(missing_ok=True)


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
