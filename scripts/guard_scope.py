#!/usr/bin/env python3
"""What a CLEAN result from each guard does NOT establish - said in the guard's own output.

## The defect

Four guards in this pipeline reported CLEAN while missing something real (issue #97). A
containment guard whose AST scan stopped at the first dot; a mutation gate blind to new files; a
conformance check degraded to presence-only by an unannotated decorator; a drift guard that never
watched membership operators. In every case the reader took the clean result for a stronger claim
than the check could support, and in every case the reader was being reasonable, because **nothing
told them otherwise**.

Three of those four had their limitation written down - in a module docstring. That is the part
this module exists for, and the distinction came from an operator rather than a design review:

    A later stage reads OUTPUT, not source.

A limitation documented only in the source is invisible to the reader who needs it. So every guard
emits, on a clean result, one line naming what it proves and what that clean result does not
establish.

## Why the statements live in ONE file

The alternative is a hand-written sentence at each guard's clean branch, and thirty-odd sentences
maintained separately are the drift defect this repository refuses everywhere else: a second
statement of a fact goes stale silently, and a stale scope statement is worse than none because it
is read as current. `scripts/guard_scope.toml` is the single source; the guard supplies only its
own filename. `check` holds the two ends together - a declared guard with no statement is a finding,
and a statement naming no guard is a finding.

## What emitting can never do

It cannot change a verdict. `clean()` returns the exit code it was handed, whatever happens inside
it: a guard whose scope statement could fail its own check would be a guard weakened by
documentation. An inventory that is missing, unreadable, or silent about this guard produces a
NOTICE saying exactly that - never silence, because an absent statement read as "no limits" is the
defect one layer down.

## Allowlist growth

A drift guard was once quietened with nine allowlist entries instead of a fix to its extraction
layer. Any allowlist-based guard invites that, so `allowlists` counts each declared allowlist now
against its size at the merge-base and REPORTS growth. It is deliberately a report and never a
gate: growth is often legitimate, and a blocking check would be answered with a bypass rather than
with a reviewer's attention, which is what this exists to buy.

Usage:
    guard_scope.py emit <file|name>       the scope statement for one guard, on stderr
    guard_scope.py list                   every declared statement
    guard_scope.py check                  moved: `guard_proof.py scope` owns it, beside guards.toml
    guard_scope.py allowlists [--base R]  allowlist sizes, and their growth against REF
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OK = 0
FINDINGS = 1
ERROR = 2

#: The one string a later stage greps for. Every emission opens with it, so a reader that wants
#: only the claims can find them, and `check` can ask whether a guard emits at all.
SCOPE_MARKER = "SCOPE OF A CLEAN RESULT"

#: What an emission says when the inventory cannot answer. Never silence: an absent statement read
#: as "this guard has no limits" is the very reading issue #97 is about.
UNAVAILABLE = "unavailable"

INVENTORY = "guard_scope.toml"

_GUARD_KEYS = {"proves", "limits"}
_ALLOWLIST_KEYS = {"id", "file", "glob", "marker", "guard", "what"}


class GuardScopeError(Exception):
    """A malformed inventory. Only ever raised on the `check`/`list` paths, never while emitting."""


@dataclass(frozen=True)
class Statement:
    """One guard's claim: what a clean result proves, and what it does not establish."""

    name: str
    proves: str
    limits: tuple[str, ...]

    def render(self) -> str:
        """The single line a later stage reads. One line, so it survives a tail and a grep."""
        return (
            f"[{self.name}] {SCOPE_MARKER}: proves {self.proves} "
            f"Does NOT establish: {'; '.join(self.limits)}."
        )


@dataclass(frozen=True)
class Allowlist:
    """One list a guard can be quietened with, and how to count it."""

    id: str
    marker: str
    what: str
    guard: str
    file: str | None = None
    glob: str | None = None

    def paths(self, root: Path) -> list[Path]:
        if self.file:
            target = root / self.file
            return [target] if target.is_file() else []
        return sorted(p for p in root.glob(self.glob or "") if p.is_file())


@dataclass(frozen=True)
class Inventory:
    statements: dict[str, Statement]
    allowlists: tuple[Allowlist, ...]


def _name(path_or_name: str) -> str:
    """The key a statement is filed under: the bare filename, however the caller spelled it."""
    return Path(str(path_or_name)).name


def load(path: Path) -> Inventory:
    """Parse the inventory. Raises on anything malformed - a half-read inventory declares nothing."""
    # Imported HERE, never at module scope. Every guard in the pipeline imports this module now,
    # and the hooks run under whatever `python3` is on PATH - which on macOS is the system 3.9,
    # where `tomllib` does not exist. A module-level import made that an ImportError inside guards
    # that had worked for years, and the guard then delivered its own failure as its result. An
    # emission may not decide anything, and it may not decide whether its host can start either.
    try:
        import tomllib
    except ImportError as exc:  # pragma: no cover - only on a runtime older than 3.11
        raise GuardScopeError(
            f"tomllib is unavailable on this interpreter ({exc}), so no scope statement can be "
            f"read. Nothing else about the guard changes."
        ) from exc
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise GuardScopeError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise GuardScopeError(f"{path} is not readable TOML: {exc}") from exc

    statements: dict[str, Statement] = {}
    for key, entry in (raw.get("guard") or {}).items():
        if not isinstance(entry, dict):
            raise GuardScopeError(f"[guard.{key}] must be a table")
        unknown = set(entry) - _GUARD_KEYS
        if unknown:
            raise GuardScopeError(
                f"[guard.{key}] carries unknown key(s) {sorted(unknown)}; the schema is closed so a "
                f"misspelled key cannot become a statement that silently says nothing"
            )
        proves = entry.get("proves")
        limits = entry.get("limits")
        if not isinstance(proves, str) or not proves.strip():
            raise GuardScopeError(f"[guard.{key}] needs a non-empty `proves`")
        if (
            not isinstance(limits, list)
            or not limits
            or not all(isinstance(item, str) and item.strip() for item in limits)
        ):
            raise GuardScopeError(
                f"[guard.{key}] must name at least one thing a clean result does NOT establish. A "
                f"guard that claims no limits is claiming total coverage, which is the assertion "
                f"issue #97 exists to stop being made by silence."
            )
        statements[_name(key)] = Statement(
            name=_name(key), proves=proves.strip(), limits=tuple(s.strip() for s in limits)
        )

    allowlists: list[Allowlist] = []
    for entry in raw.get("allowlist") or []:
        if not isinstance(entry, dict):
            raise GuardScopeError("every [[allowlist]] must be a table")
        unknown = set(entry) - _ALLOWLIST_KEYS
        if unknown:
            raise GuardScopeError(
                f"an [[allowlist]] entry carries unknown key(s) {sorted(unknown)}"
            )
        for key in ("id", "marker", "what", "guard"):
            if not isinstance(entry.get(key), str) or not str(entry[key]).strip():
                raise GuardScopeError(f"every [[allowlist]] entry needs a non-empty {key!r}")
        if not (entry.get("file") or entry.get("glob")):
            raise GuardScopeError(
                f"allowlist {entry['id']!r} must name a `file` or a `glob` to count in"
            )
        allowlists.append(
            Allowlist(
                id=str(entry["id"]),
                marker=str(entry["marker"]),
                what=str(entry["what"]),
                guard=str(entry["guard"]),
                file=entry.get("file"),
                glob=entry.get("glob"),
            )
        )
    return Inventory(statements=statements, allowlists=tuple(allowlists))


def inventory_path(root: Path | None = None) -> Path:
    """Beside this module, so a vendored copy carries its statements with it."""
    return (Path(root) if root else Path(__file__).resolve().parent) / INVENTORY


def resolve(name: str, root: Path | None = None) -> tuple[Statement | None, str]:
    """The statement for one guard, or None AND WHY. Never raises: emitting must not be able to fail.

    The reason is returned rather than flattened because the two absences prescribe different
    remedies - write the entry, versus this interpreter cannot read the inventory at all - and a
    notice naming the wrong one sends its reader to fix something that is not broken.
    """
    try:
        found = load(inventory_path(root)).statements.get(_name(name))
    except GuardScopeError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - emission is never allowed to raise
        return None, f"the inventory could not be read ({exc!r})"
    if found is None:
        return None, f"no entry in {INVENTORY}"
    return found, ""


def statement(name: str, root: Path | None = None) -> Statement | None:
    """The statement for one guard, or None. Never raises."""
    return resolve(name, root)[0]


def notice(name: str, why: str) -> str:
    """What is printed when no statement can be produced. An absence, named as one."""
    return (
        f"[{_name(name)}] {SCOPE_MARKER}: {UNAVAILABLE} - {why}. This is a MISSING declaration, "
        f"not a claim of full coverage."
    )


def clean(name: str, code: int, *, ok: int = OK, stream=None) -> int:
    """Emit the scope statement when `code` is clean, and return `code` UNCHANGED.

    The return is the whole contract: this never decides anything. A guard whose exit code could be
    moved by its own documentation would be a guard weakened to make a rule easier to satisfy, which
    is the one thing issue #97 puts out of scope.

    It emits only on a CLEAN result on purpose. A failing guard already tells the reader exactly what
    it found; it is the clean one that gets over-read.
    """
    if code != ok:
        return code
    target = stream if stream is not None else sys.stderr
    try:
        found, why = resolve(name)
        print(found.render() if found else notice(name, why), file=target)
    except Exception:  # noqa: BLE001 - an emission failure may never become a verdict
        pass
    return code


def run(name: str, entry, *, ok: int = OK) -> int:
    """Call a guard's `main` and emit its scope statement on a clean result. Never changes the code.

    Two shapes are in use in this repository and both must emit: a `main()` that RETURNS an exit
    code, and one that calls `sys.exit()` from inside. Wiring only the first would have left the
    gate runner - which exits inline at every one of its own outcomes - silently unwired while
    looking wired, which is this issue's own failure mode applied to its own fix.
    """
    try:
        code = entry()
    except SystemExit as exc:
        code = exc.code
    if code is None:
        code = OK
    if not isinstance(code, int):
        return code
    return clean(name, code, ok=ok)


def emits(text: str) -> bool:
    """Whether a guard's source actually reaches this module, however it spells the call.

    Asked of the SOURCE because the alternative is running every guard to see whether it printed,
    and a guard whose clean branch nothing exercised would then read as compliant.
    """
    return "guard_scope" in text


# --- allowlists: growth as a signal ---------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _count(text: str, marker: str) -> int:
    return text.count(marker)


def sizes(root: Path, inventory: Inventory) -> dict[str, int]:
    """How many entries each declared allowlist holds right now."""
    found: dict[str, int] = {}
    for entry in inventory.allowlists:
        total = 0
        for path in entry.paths(root):
            try:
                total += _count(path.read_text(encoding="utf-8"), entry.marker)
            except (OSError, UnicodeDecodeError):
                continue
        found[entry.id] = total
    return found


def growth(root: Path, inventory: Inventory, base: str) -> dict[str, tuple[int, int]] | None:
    """(before, now) per allowlist against `base`, or None when git cannot answer.

    None is UNKNOWABLE and the caller says so. Read as "no growth" it would report every quietening
    as routine, which is the whole thing this is watching for.
    """
    forked = _git(root, "merge-base", base, "HEAD")
    if not forked or not forked.strip():
        return None
    ref = forked.strip().splitlines()[0]
    now = sizes(root, inventory)
    before: dict[str, int] = {}
    for entry in inventory.allowlists:
        total = 0
        for path in entry.paths(root):
            relative = path.relative_to(root).as_posix()
            text = _git(root, "show", f"{ref}:{relative}")
            if text:
                total += _count(text, entry.marker)
        before[entry.id] = total
    return {key: (before.get(key, 0), now.get(key, 0)) for key in now}


def _render_growth(
    inventory: Inventory, measured: dict[str, tuple[int, int]] | None, base: str
) -> list[str]:
    if measured is None:
        return [
            f"[guard_scope] allowlist growth against {base!r} is UNKNOWABLE - git could not resolve "
            f"a merge-base. Nothing was compared."
        ]
    by_id = {entry.id: entry for entry in inventory.allowlists}
    lines: list[str] = []
    for key, (before, now) in sorted(measured.items()):
        entry = by_id[key]
        delta = now - before
        if delta > 0:
            lines.append(
                f"[guard_scope] ALLOWLIST GREW: {key} +{delta} ({before} -> {now}) - {entry.what}. "
                f"An allowlist is how {entry.guard} gets quietened; a fix at its extraction layer is "
                f"the alternative this line exists to make somebody weigh."
            )
        else:
            lines.append(f"[guard_scope] allowlist {key}: {now} entr(ies), no growth.")
    return lines


# --- CLI ------------------------------------------------------------------------------------------


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)

    emit = sub.add_parser("emit", help="one guard's scope statement, on stderr")
    emit.add_argument("name", help="the guard's file name, however it is spelled")

    sub.add_parser("list", help="every declared statement")

    lists = sub.add_parser("allowlists", help="allowlist sizes, and growth against a base")
    lists.add_argument("--base", default=None, help="compare against the merge-base with REF")
    lists.add_argument("--root", default=None)

    args = parser.parse_args(argv)
    root = Path(getattr(args, "root", None) or _root()).resolve()

    if args.action == "emit":
        # Always exit 0. A shell guard calls this on its own clean path, and an emission that could
        # fail a gate would be documentation with a veto.
        found, why = resolve(args.name)
        print(found.render() if found else notice(args.name, why), file=sys.stderr)
        return OK

    try:
        inventory = load(inventory_path(root / "scripts"))
    except GuardScopeError as exc:
        print(f"[guard_scope] {exc}", file=sys.stderr)
        return ERROR

    if args.action == "list":
        for name in sorted(inventory.statements):
            print(inventory.statements[name].render())
        return OK

    base = args.base
    if base is None:
        for line in sorted(
            f"[guard_scope] allowlist {key}: {value} entr(ies)."
            for key, value in sizes(root, inventory).items()
        ):
            print(line)
        return OK
    for line in _render_growth(inventory, growth(root, inventory, base), base):
        print(line)
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
