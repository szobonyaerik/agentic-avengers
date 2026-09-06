#!/usr/bin/env python3
"""A phase whose `work_kind` says behaviour is preserved has to PROVE it, not assert it.

## The defect

grid-bot-platform, okx-migration phase 1, 27 Aug 2026. The phase was mandated as zero behaviour
change - a package split and a rename, `work_kind: migration` on both specs - and it shipped three
changes to live trading behaviour, every one of which passed verification and a 283-test suite:

    run_engine.py          rem_orders = max_open_orders - open_orders_now   became  +
                           (disabled the MAX_OPEN_ORDERS capacity guard: 12 open against a cap of
                           10 computed 22 instead of -2 and would have placed ten more live orders)
    binance_connector.py   balance["free"].get(base_currency, 0)          became  1
                           (the 0 was the insufficient-balance sentinel; with 1 the guard could not
                           fire and the bot would market-sell an asset it did not hold)
    run_engine.py          balances["free"].get("ETH", 0)                 became  1  (two of four sites)
                           (an absent balance recorded as a fabricated 1 ETH holding)

**Nothing compared behaviour against the declared no-change contract.** The diff was wide and
shallow by design, review attention went where behaviour was expected to change, and no automated
layer asked the claim itself. `skills/tdd` already said what `migration` and `refactor` mean -
behaviour preserved, "any intentional behavior change is greenfield work and must be specified
explicitly" - so the rule existed and nothing enforced it, which is this repository's recurring
shape: a documented claim with no mechanism behind it.

## What is decided here

**When any spec in a phase declares `work_kind: migration` or `work_kind: refactor`, every change to
the phase's SEMANTIC SURFACE must cite what authorised it.** The surface - literals, operators and
control-flow edges, reduced to ATOMS with no identifier in them - is what `scripts/behaviour_atoms.py`
extracts; it is what a behaviour change has to pass through and a rename, a move or a reformat never
touches.

The two sides are compared DEFINITION BY DEFINITION, never as one pool. Netting every changed file
into one multiset was measured first and it hid two of the three defects above: the `0`s that became
`1`s disappeared against `0`s the same phase legitimately added elsewhere, because a removal here and
an addition there is indistinguishable from a move. Per hunk was the second cut and it still hid the
same shape, because a hunk is a property of the DIFF: git coalesces adjacent changes into one hunk
even at `-U0`, so a rewritten statement and an unrelated new line under it arrive together and the
new line's `0` cancels the rewritten one's. So each `-U0` hunk is cut into the top-level definitions
it touches (`split_by_owner`) and each piece accounts for its own atoms, old lines against new.

Only the shapes a refactor actually produces cancel across those pieces: a block CUT here and PASTED
there (a pure removal against a pure addition whose LINES read alike - a move, even if a literal
changed in transit, in which case only that literal remains), a helper EXTRACTED (a pure addition of
at least `MIN_BLOCK` atoms whose every atom came out of one edited piece) and a helper INLINED (the
reverse). An edited piece never cancels against another edited piece. A rename changes no atom; a
reformat changes no AST; so both are free, which is the property the contract has to keep.

**A change is cited or it blocks.** An added atom is cited by a `# behaviour: R<n>.<k>.<m>` comment on
the statement (or compound-statement header) that carries it; a removed atom is cited by that
comment on any new line of the hunk that removed it, which is where a person would write "this
guard is gone on purpose"; a NEW file may cite once in its header, before its first statement, for
every atom it adds. The id must be one a spec in the phase DECLARES - `requirement_cap.declared_ids`,
the reader the cap and the precheck already share - so a citation of nothing is a void citation and
says so. The other route is the disclosed-exception ledger (`scripts/applicability.py record
--rule behaviour-change`), subject either one atom key (`+op:Add`, `-literal:int:0`) or the phase
itself, which is how a phase in flight when this rule landed opts out on the record rather than by
silence. An uncited change is a BLOCKING finding, never a warning: a warning on a phase that claims
to be safe is the state the three guards shipped in.

## What a clean result does NOT establish, said rather than implied

* **A move.** A block cut from one place and pasted in another cancels when the two read alike,
  down to a single atom moving with its line. Swapping two whole branches is a move.
* **Both halves of a change inside ONE definition.** Cancellation is per definition, so an atom
  removed and an IDENTICAL atom added in the same definition in one hunk cancel each other. The
  change still blocks - a `0` that becomes a `1` beside a new `0` reports `+literal:int:1`, since
  only equal keys cancel - but the reader is shown one half of it, not the pair.
* **Call targets, argument order, attribute names.** `fetch(a, b)` becoming `fetch(b, a)`, or
  `.total` becoming `.subtotal`, is a change of identifiers, and identifiers are exactly what a
  rename is allowed to change. A behaviour change carried entirely by a name passes.
* **Semantic equivalence.** `if not x: return` inverted into `if x:` is flagged, though equivalent;
  the guard judges the surface and asks for a citation, it does not prove or refute equivalence.
* **Anything that is not Python.** Other files in the change are counted and named, never parsed.
* **The base.** In session the comparison is against HEAD, which is the phase's base only while the
  phase commits nothing before its close (the pipeline's rule; `BEHAVIOUR_BASE` names another).

## Where it is asked

`spec-done` and `handover` in `scripts/hook_verifier.sh` - the first moment the code exists and the
implementer still owns it, and the phase close - and `gate_ci.sh`, diff-scoped on the applicability
boundary (`scripts/applicability.py`): a phase this change does not touch is counted and named,
never blocked; a spec already `status: done` at HEAD has shipped and is not re-bound (the hook's own
transition check); a phase whose ledger excepts it is CLOSED. In CI the comparison base is the
branch, so it runs only when every touched phase is under the contract - a pull request mixing a
greenfield phase with a no-change one cannot be attributed file by file, and it says so rather than
holding the greenfield work to a contract it never declared. In session HEAD isolates the phase.

Exit codes:
    0  CLEAN   - every atom that changed is cited, or the phase declares no contract, or the scope
                 is not one this check may bind (each said on stderr, never silently).
    1  DRIFT   - at least one uncited change; each is printed with where it is and how to cite it.
    2  ERROR   - a file, a spec or the ledger could not be read, or a Python file does not parse.
                 Fail closed: a tree the guard cannot read is a tree it cannot clear.

Usage:
    behaviour_drift.py check [<phase-dir> ...] [--root .] [--base REF] [--all]
    behaviour_drift.py declared <phase-dir>          which specs put the phase under the contract
    behaviour_drift.py compare --old REF --new REF [--root .] [--ids-from <phase-dir>]
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
import guard_scope  # noqa: E402
import spec_gate_state  # noqa: E402
from behaviour_atoms import Atom, AtomError, atoms, citations, owners  # noqa: E402
from requirement_cap import declared_ids  # noqa: E402

CLEAN = 0
DRIFT = 1
ERROR = 2

#: The rule an exception on the phase's ledger names. Read here and nowhere else.
RULE = "behaviour-change"

#: The `work_kind` values that carry the contract. `greenfield` is the one mode in which behaviour
#: is expected to change; `skills/tdd` defines the other two as behaviour-preserving, and this is
#: that definition enforced rather than a new axis beside it.
CONTRACT_KINDS = frozenset({"migration", "refactor"})
WORK_KIND_FIELD = "work_kind"

#: Where the environment narrows the source scan to production code, and names another base.
SOURCE_PATHS_ENV = "BEHAVIOUR_SOURCE_PATHS"
BASE_ENV = "BEHAVIOUR_BASE"

#: Never compared: the pipeline's own documents, and the places tests live. A migration or
#: refactor phase ADDS characterization tests by its own procedure, and a test's literals are the
#: expected values of the behaviour being pinned, not the behaviour.
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
    "test",
    "tests",
    "venv",
}
TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest)\.py$")
OTHER_SOURCE = {".js", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".kt", ".cs"}

#: `@@ -<old>[,<len>] +<new>[,<len>] @@` - the one line of a `-U0` diff that maps old to new.
HUNK_LINE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class BehaviourDriftError(Exception):
    """A file, spec or ledger that could not be read or parsed. Always fails the caller closed."""


# --- the contract ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    phase_dir: Path
    kinds: tuple[tuple[str, str], ...]  # (spec directory name, work_kind as written)

    @property
    def binding(self) -> tuple[str, ...]:
        return tuple(name for name, kind in self.kinds if kind in CONTRACT_KINDS)

    @property
    def declared(self) -> bool:
        return bool(self.binding)

    def reason(self) -> str:
        if not self.kinds:
            return "the phase has no specs, so no spec declares a work_kind"
        if self.declared:
            return (
                f"{len(self.binding)} spec(s) declare a behaviour-preserving work_kind: "
                + ", ".join(f"{n} ({k})" for n, k in self.kinds if k in CONTRACT_KINDS)
            )
        return (
            "no spec declares `migration` or `refactor` ("
            + ", ".join(f"{n}: {k or 'absent'}" for n, k in self.kinds)
            + ")"
        )


def contract(phase_dir: Path) -> Contract:
    """Read `work_kind` off every spec in the phase - the field the implementer already reads."""
    kinds: list[tuple[str, str]] = []
    for spec in sorted(Path(phase_dir).glob("specs/*/spec.md")):
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError as exc:
            raise BehaviourDriftError(f"cannot read {spec}: {exc}") from exc
        kind = spec_gate_state.frontmatter(text).get(WORK_KIND_FIELD, "").lower()
        kinds.append((spec.parent.name, kind))
    return Contract(Path(phase_dir), tuple(kinds))


def phase_requirement_ids(phase_dirs: list[Path]) -> set[str]:
    """Every requirement id a spec in these phases declares - what a citation may name."""
    ids: set[str] = set()
    for phase_dir in phase_dirs:
        for spec in sorted(Path(phase_dir).glob("specs/*/spec.md")):
            try:
                declared, _ = declared_ids(spec.read_text(encoding="utf-8"))
            except OSError as exc:
                raise BehaviourDriftError(f"cannot read {spec}: {exc}") from exc
            ids.update(declared)
    return ids


# --- the two sides of the change -----------------------------------------------------------------


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int

    def old_lines(self) -> range:
        return range(self.old_start, self.old_start + self.old_count)

    def new_lines(self) -> range:
        return range(self.new_start, self.new_start + self.new_count)

    def label(self) -> str:
        if self.new_count == 0:
            return f"-{self.old_start}" + (
                f"..{self.old_start + self.old_count - 1}" if self.old_count > 1 else ""
            )
        return f"{self.new_start}" + (
            f"..{self.new_start + self.new_count - 1}" if self.new_count > 1 else ""
        )


@dataclass
class ChangedFile:
    """One Python file the change touches: its text at the base, its text now, and the hunks."""

    rel: str
    old: str | None
    new: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return self.old is None and self.new is not None


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
        )
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


def _show(root: Path, ref: str, rel: str) -> str | None:
    rc, listed = _git(root, "ls-tree", "--name-only", ref, "--", rel)
    if rc != 0:
        raise BehaviourDriftError(f"git cannot read {ref}")
    if not listed.strip():
        return None
    rc, text = _git(root, "show", f"{ref}:{rel}")
    if rc != 0:
        raise BehaviourDriftError(f"git cannot show {ref}:{rel}")
    return text


def _hunks(root: Path, old_ref: str, new_ref: str | None, rel: str) -> list[Hunk]:
    args = ["diff", "--no-relative", "-U0", old_ref]
    if new_ref is not None:
        args.append(new_ref)
    rc, out = _git(root, *args, "--", f":(literal){rel}")
    if rc != 0:
        raise BehaviourDriftError(f"git cannot diff {rel} against {old_ref}")
    return _parse_hunks(out)


def _hunks_no_index(root: Path, old: str, new_path: Path) -> list[Hunk]:
    """Hunks between a base text and a file on disk, for a pair git did not see as one file."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(old)
        name = tmp.name
    try:
        rc, out = _git(root, "diff", "--no-index", "-U0", "--", name, str(new_path))
    finally:
        os.unlink(name)
    if rc not in (0, 1):
        raise BehaviourDriftError(f"git cannot diff {new_path} against its base text")
    return _parse_hunks(out)


#: How alike two files' atom multisets must be for a deleted file and a new file to be read as ONE
#: renamed file. Below it they stay a pure removal and a pure addition, which `cancel_moves` still
#: nets down to their difference; above it they get real hunks, so a literal flipped inside a
#: renamed file is its own finding instead of one atom in the residual of a whole new file.
RENAME_SIMILARITY = 0.5


def pair_renames(
    root: Path, files: list[ChangedFile], new_text_at: dict[str, Path]
) -> list[ChangedFile]:
    """Fold a deleted file and a new file that hold mostly the same atoms into one renamed file.

    git reports a rename only between two TRACKED paths; in session the new path is untracked, and
    at the measured phase the renamed connector arrived as a whole-file removal plus a whole-file
    addition. Read that way, the `0` that became `1` inside it was one atom among a new file's
    hundreds. Paired, it is a hunk of its own.
    """
    gone = [f for f in files if f.new is None and f.old]
    arrived = [f for f in files if f.old is None and f.new]
    if not gone or not arrived:
        return files
    counts = {
        f.rel: Counter(a.key for a in atoms(f.old or f.new or "", f.rel))
        for f in [*gone, *arrived]
    }
    taken: set[str] = set()
    merged: list[ChangedFile] = []
    for old_file in gone:
        best, score = None, 0.0
        for new_file in arrived:
            if new_file.rel in taken:
                continue
            a, b = counts[old_file.rel], counts[new_file.rel]
            total = max(sum(a.values()), sum(b.values()), 1)
            ratio = sum((a & b).values()) / total
            if ratio > score:
                best, score = new_file, ratio
        if best is None or score < RENAME_SIMILARITY:
            continue
        taken.update({old_file.rel, best.rel})
        merged.append(
            ChangedFile(
                f"{old_file.rel} -> {best.rel}",
                old_file.old,
                best.new,
                _hunks_no_index(root, old_file.old or "", new_text_at[best.rel]),
            )
        )
    return [f for f in files if f.rel not in taken] + merged


def _whole(old: str | None, new: str | None) -> list[Hunk]:
    """The single hunk of a file that exists on one side only."""
    if old is None and new:
        return [Hunk(0, 0, 1, len(new.splitlines()))]
    if new is None and old:
        return [Hunk(1, len(old.splitlines()), 0, 0)]
    return []


def _source_roots() -> list[str] | None:
    declared = (os.environ.get(SOURCE_PATHS_ENV) or "").strip()
    if not declared:
        return None
    return [p.strip().strip("/") for p in re.split(r"[:,]", declared) if p.strip()]


def is_source(rel: str, roots: list[str] | None = None) -> bool:
    """Whether a changed path is production Python this guard compares."""
    parts = Path(rel).parts
    if Path(rel).suffix != ".py" or any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    if TEST_FILE.match(parts[-1]):
        return False
    if roots:
        return any(rel == r or rel.startswith(r + "/") for r in roots)
    return True


def working_tree_change(
    root: Path, base: str | None
) -> tuple[list[ChangedFile], list[str], str] | None:
    """The Python files this change touches, against HEAD or the merge-base with `base`.

    Returns (files, other source-looking paths that were NOT compared, the base ref), or None when
    git cannot say what changed - unknowable, and the caller says so.
    """
    root = Path(root).resolve()
    rc, top = _git(root, "rev-parse", "--show-toplevel")
    if rc != 0 or not top.strip():
        return None
    top_path = Path(top.strip()).resolve()
    old_ref = "HEAD"
    if base is not None:
        if not base.strip():
            return None
        rc, forked = _git(root, "merge-base", base, "HEAD")
        if rc != 0 or not forked.strip():
            return None
        old_ref = forked.strip()
    scope = applicability.changed_paths(root, base)
    if scope is None:
        return None
    roots = _source_roots()
    files: list[ChangedFile] = []
    skipped: list[str] = []
    for path in sorted(scope):
        try:
            rel = path.relative_to(top_path).as_posix()
        except ValueError:
            continue
        if not is_source(rel, roots):
            if Path(rel).suffix in OTHER_SOURCE:
                skipped.append(rel)
            continue
        old = _show(top_path, old_ref, rel)
        try:
            new = path.read_text(encoding="utf-8") if path.is_file() else None
        except (OSError, UnicodeDecodeError) as exc:
            raise BehaviourDriftError(f"cannot read {rel}: {exc}") from exc
        if old is None and new is None:
            continue
        hunks = (
            _hunks(top_path, old_ref, None, rel)
            if old is not None
            else _whole(old, new)
        )
        files.append(ChangedFile(rel, old, new, hunks or _whole(old, new)))
    on_disk = {f.rel: top_path / f.rel for f in files if f.new is not None}
    return pair_renames(top_path, files, on_disk), skipped, old_ref


def committed_change(root: Path, old_ref: str, new_ref: str) -> list[ChangedFile]:
    """The Python files that differ between two refs - for measuring a change already landed.

    Read from `--name-status -M`, never `--name-only`: with rename detection on, the latter prints
    a renamed file under its NEW path alone, so the old side silently leaves the comparison and a
    renamed file reads as a whole new one. A rename git detects gets real hunks between its two
    blobs; one it does not is paired here by atom similarity, exactly as in session.
    """
    root = Path(root).resolve()
    rc, out = _git(
        root, "diff", "--name-status", "-M", "--no-relative", "-z", old_ref, new_ref
    )
    if rc != 0:
        raise BehaviourDriftError(f"git cannot diff {old_ref}..{new_ref}")
    roots = _source_roots()
    files: list[ChangedFile] = []
    fields = [f for f in out.split("\0") if f != ""]
    i = 0
    while i < len(fields):
        status, path = fields[i], fields[i + 1]
        i += 2
        old_rel, new_rel = path, path
        if status.startswith(("R", "C")):
            new_rel = fields[i]
            i += 1
        if not (is_source(old_rel, roots) or is_source(new_rel, roots)):
            continue
        old = _show(root, old_ref, old_rel) if not status.startswith("A") else None
        new = _show(root, new_ref, new_rel) if not status.startswith("D") else None
        if old is None and new is None:
            continue
        if old is not None and new is not None:
            hunks = _hunks_between(root, f"{old_ref}:{old_rel}", f"{new_ref}:{new_rel}")
        else:
            hunks = _whole(old, new)
        rel = old_rel if old_rel == new_rel else f"{old_rel} -> {new_rel}"
        files.append(ChangedFile(rel, old, new, hunks))
    return _pair_committed(root, files)


def _hunks_between(root: Path, old_spec: str, new_spec: str) -> list[Hunk]:
    """Hunks between two blob specs (`ref:path`), so a rename diffs as one file."""
    rc, out = _git(root, "diff", "--no-relative", "-U0", old_spec, new_spec)
    if rc not in (0, 1):
        raise BehaviourDriftError(f"git cannot diff {old_spec} against {new_spec}")
    return _parse_hunks(out)


def _parse_hunks(out: str) -> list[Hunk]:
    return [
        Hunk(
            int(m.group(1)),
            int(m.group(2)) if m.group(2) is not None else 1,
            int(m.group(3)),
            int(m.group(4)) if m.group(4) is not None else 1,
        )
        for m in (HUNK_LINE.match(line) for line in out.splitlines())
        if m
    ]


def _pair_committed(root: Path, files: list[ChangedFile]) -> list[ChangedFile]:
    """`pair_renames` over a landed change: the new side is read out of git into scratch files."""
    import tempfile

    arrived = [f for f in files if f.old is None and f.new]
    if not arrived or not any(f.new is None for f in files):
        return files
    with tempfile.TemporaryDirectory() as scratch:
        on_disk: dict[str, Path] = {}
        for f in arrived:
            target = Path(scratch) / f.rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.new or "", encoding="utf-8")
            on_disk[f.rel] = target
        return pair_renames(root, files, on_disk)


# --- the comparison -------------------------------------------------------------------------------


@dataclass
class Account:
    """One hunk's own accounting: what it removed and added, net of what it kept."""

    file: ChangedFile
    hunk: Hunk
    old_atoms: list[Atom]
    new_atoms: list[Atom]
    removed: Counter
    added: Counter
    old_text: list[str] = field(default_factory=list)
    new_text: list[str] = field(default_factory=list)

    @property
    def pure_addition(self) -> bool:
        return self.hunk.old_count == 0

    @property
    def pure_removal(self) -> bool:
        return self.hunk.new_count == 0

    def where(self) -> str:
        return f"{self.file.rel}:{self.hunk.label()}"


def _in(atom: Atom, lines: range) -> bool:
    start, end = atom.span
    return start <= lines.stop - 1 and end >= lines.start and len(lines) > 0


#: The owner of every line outside a top-level definition. Two module-level statements share it,
#: which is the one place inside a file where an addition can still cancel a removal.
MODULE_OWNER = "<module>"


def _runs(lines: range, owner: dict[int, str]) -> list[tuple[str, int, int]]:
    """The contiguous runs of one owner inside a hunk's lines: (owner, first line, count)."""
    out: list[tuple[str, int, int]] = []
    for line in lines:
        who = owner.get(line, MODULE_OWNER)
        if out and out[-1][0] == who and out[-1][1] + out[-1][2] == line:
            name, start, count = out[-1]
            out[-1] = (name, start, count + 1)
        else:
            out.append((who, line, 1))
    return out


def split_by_owner(
    hunk: Hunk, old_owner: dict[int, str], new_owner: dict[int, str]
) -> list[Hunk]:
    """Cut one hunk into the definitions it touches, so a change in one cannot cancel another.

    Git coalesces adjacent changes into a single hunk even at `-U0`, so a statement rewritten on one
    line and an unrelated line added under it arrive as one hunk - and the `0` in the added line
    then cancels the `0` leaving the rewritten one, which is issue #107's own masking one level
    down. Runs of the same owner are paired across the two sides in order; a definition present on
    only one side becomes a pure addition or a pure removal, which `cancel_moves` still nets
    against its counterpart elsewhere. A hunk touching one definition on each side is untouched.
    """
    old_runs = _runs(hunk.old_lines(), old_owner)
    new_runs = _runs(hunk.new_lines(), new_owner)
    if len(old_runs) <= 1 and len(new_runs) <= 1:
        return [hunk]
    out: list[Hunk] = []
    for who in dict.fromkeys([r[0] for r in old_runs] + [r[0] for r in new_runs]):
        mine_old = [r for r in old_runs if r[0] == who]
        mine_new = [r for r in new_runs if r[0] == who]
        for i in range(max(len(mine_old), len(mine_new))):
            old = mine_old[i] if i < len(mine_old) else None
            new = mine_new[i] if i < len(mine_new) else None
            out.append(
                Hunk(
                    old[1] if old else hunk.old_start,
                    old[2] if old else 0,
                    new[1] if new else hunk.new_start,
                    new[2] if new else 0,
                )
            )
    return sorted(out, key=lambda h: (h.new_start, h.old_start))


def _text(source: str | None, lines: range) -> list[str]:
    if not source or not len(lines):
        return []
    body = source.splitlines()
    return [body[n - 1] for n in lines if 0 < n <= len(body)]


def accounts(files: list[ChangedFile]) -> list[Account]:
    """Every hunk of every file, cut into the definitions it touches, with its own residual."""
    out: list[Account] = []
    for changed in files:
        old_atoms = atoms(changed.old, f"{changed.rel}@base") if changed.old else []
        new_atoms = atoms(changed.new, changed.rel) if changed.new else []
        old_owner = owners(changed.old, f"{changed.rel}@base") if changed.old else {}
        new_owner = owners(changed.new, changed.rel) if changed.new else {}
        for whole in changed.hunks:
            for hunk in split_by_owner(whole, old_owner, new_owner):
                mine_old = [a for a in old_atoms if _in(a, hunk.old_lines())]
                mine_new = [a for a in new_atoms if _in(a, hunk.new_lines())]
                old_c = Counter(a.key for a in mine_old)
                new_c = Counter(a.key for a in mine_new)
                out.append(
                    Account(
                        changed,
                        hunk,
                        mine_old,
                        mine_new,
                        old_c - new_c,
                        new_c - old_c,
                        _text(changed.old, hunk.old_lines()),
                        _text(changed.new, hunk.new_lines()),
                    )
                )
    return out


#: How much a pure addition has to carry before an edited hunk will read it as a helper EXTRACTED
#: out of itself (and the reverse, for an inline). One `0` arriving in a new statement and one `0`
#: leaving an edited one is exactly what a flipped sentinel looks like from the outside, and it is
#: what the first cut of this module read as an extraction. A block extracts free; a one-liner
#: costs a citation. A plain MOVE is not held to this - it is held to `MOVE_SIMILARITY`, which is
#: evidence rather than a size, so a one-line function moved between files stays free.
MIN_BLOCK = 2

#: How alike the two sides of a cut-and-paste have to read for it to BE one. A move is the one
#: shape whose evidence cannot be the atoms themselves: an addition and a removal that merely hold
#: the same literal are indistinguishable from a flip beside an unrelated new statement, which is
#: the defect this guard exists for. So the lines are compared, the way git's own rename detection
#: compares them - and a rename inside the moved block only lowers the ratio, since identifiers are
#: most of what differs. Judging the FINDING is still the atoms' job; this only pairs candidates.
MOVE_SIMILARITY = 0.6

#: Characters per side the move comparison reads. A whole new file is a legitimate paste target and
#: `SequenceMatcher` is quadratic; a paste that agrees for this long agreed.
MOVE_COMPARE_MAX = 8000


def _alike(left: list[str], right: list[str]) -> float:
    """How alike two blocks of lines read, indentation and blank lines set aside."""
    one = "\n".join(line.strip() for line in left if line.strip())[:MOVE_COMPARE_MAX]
    two = "\n".join(line.strip() for line in right if line.strip())[:MOVE_COMPARE_MAX]
    if not one or not two:
        return 0.0
    # autojunk OFF: it treats any character in more than 1% of a sequence over 200 long as junk,
    # which on source text is most of the alphabet, and the ratio then says little about the code.
    return difflib.SequenceMatcher(None, one, two, autojunk=False).ratio()


def _contained(inner: Counter, outer: Counter) -> bool:
    return sum(inner.values()) >= MIN_BLOCK and all(
        outer[k] >= n for k, n in inner.items()
    )


def cancel_moves(ledger: list[Account]) -> None:
    """Cancel the three shapes a refactor produces across hunks, and nothing else.

    Containment first - a helper extracted (a pure addition whose every atom came out of ONE edited
    hunk) or inlined (the reverse) - then plain moves, a pure removal against a pure addition, down
    to their intersection so a literal changed in transit is what remains. An edited hunk never
    cancels against another edited hunk: that is the shape the measured `0` -> `1` flips hid in.
    """
    adds = [a for a in ledger if a.pure_addition]
    rems = [a for a in ledger if a.pure_removal]
    mods = [a for a in ledger if not a.pure_addition and not a.pure_removal]
    for p in adds:
        for q in mods:
            if _contained(p.added, q.removed):
                q.removed.subtract(p.added)
                q.removed = +q.removed
                p.added = Counter()
                break
    for q in rems:
        for p in mods:
            if _contained(q.removed, p.added):
                p.added.subtract(q.removed)
                p.added = +p.added
                q.removed = Counter()
                break
    for p in adds:
        for q in rems:
            common = p.added & q.removed
            if common and _alike(q.old_text, p.new_text) >= MOVE_SIMILARITY:
                p.added.subtract(common)
                q.removed.subtract(common)
                p.added, q.removed = +p.added, +q.removed


@dataclass(frozen=True)
class Finding:
    where: str
    removed: tuple[str, ...]  # keys, one per uncited occurrence
    added: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]
    compared: int
    changed: int  # atoms that changed, before citation
    void_ids: tuple[str, ...]


def _valid(ids: list[str], declared: set[str], void: set[str]) -> bool:
    void.update(i for i in ids if i not in declared)
    return any(i in declared for i in ids)


def compare(
    files: list[ChangedFile], declared: set[str], excepted: set[str] = frozenset()
) -> Report:
    """Account per hunk, cancel moves, then hold every remaining change to a citation."""
    ledger = accounts(files)
    cancel_moves(ledger)
    void: set[str] = set()
    findings: list[Finding] = []
    changed = 0
    cites = {f.rel: citations(f.new) for f in files if f.new is not None}
    for account in ledger:
        changed += sum(account.removed.values()) + sum(account.added.values())
        by_line, header = cites.get(account.file.rel, ({}, []))
        hunk_ids = [
            rid for line in account.hunk.new_lines() for rid in by_line.get(line, [])
        ]
        if account.file.is_new:
            hunk_ids = [*hunk_ids, *header]
        removed_uncited: list[str] = []
        for key, count in sorted(account.removed.items()):
            if f"-{key}" in excepted or (hunk_ids and _valid(hunk_ids, declared, void)):
                continue
            removed_uncited.extend([key] * count)
        added_uncited: list[str] = []
        for key, count in sorted(account.added.items()):
            if f"+{key}" in excepted:
                continue
            sites = [a for a in account.new_atoms if a.key == key]
            cited = 0
            for atom in sites:
                ids = [
                    rid
                    for line in range(atom.cite[0], atom.cite[1] + 1)
                    for rid in by_line.get(line, [])
                ]
                if account.file.is_new:
                    ids = [*ids, *header]
                if ids and _valid(ids, declared, void):
                    cited += 1
            if cited < count:
                added_uncited.extend([key] * (count - cited))
        if removed_uncited or added_uncited:
            findings.append(
                Finding(account.where(), tuple(removed_uncited), tuple(added_uncited))
            )
    return Report(tuple(findings), len(files), changed, tuple(sorted(void)))


# --- reporting ------------------------------------------------------------------------------------


def _display(keys: tuple[str, ...], sign: str) -> str:
    counted = Counter(keys)
    return "  ".join(
        f"{sign}{k if len(k) <= 60 else k[:57] + '...'}" + (f" x{n}" if n > 1 else "")
        for k, n in sorted(counted.items())
    )


def report_findings(report: Report, phase: str) -> None:
    for finding in report.findings:
        parts = [
            p
            for p in (_display(finding.removed, "-"), _display(finding.added, "+"))
            if p
        ]
        print(f"  ✗ {finding.where}: {'  '.join(parts)}", file=sys.stderr)
    if report.void_ids:
        print(
            f"  ✗ cited but declared by no spec in {phase}: {', '.join(report.void_ids)} - a "
            f"citation of an id nobody declared authorises nothing.",
            file=sys.stderr,
        )
    total = sum(len(f.removed) + len(f.added) for f in report.findings)
    print(
        f"[behaviour_drift] {phase} declares behaviour preserved and {total} change(s) to its "
        f"semantic surface in {len(report.findings)} hunk(s) cite nothing. Each is a BLOCKING "
        f"finding. Cite the requirement that authorises it with `# behaviour: R<n>.<k>.<m>` on the "
        f"changed statement (a removal: on any new line of the hunk that removed it; a NEW file: "
        f"once in its header), or record a disclosed exception: `scripts/applicability.py record "
        f"<phase-dir> --rule {RULE} --subject=<+key|-key|phase> --reason-file <f>`. A change "
        f"with no requirement and no exception is a behaviour change the phase said it would not "
        f"make.",
        file=sys.stderr,
    )


def _clean_line(report: Report, base: str, skipped: list[str]) -> str:
    line = (
        f"[behaviour_drift] clean - {report.compared} Python file(s) compared against {base}; "
        f"{report.changed} atom(s) changed, every one cited or excepted."
    )
    if skipped:
        line += f" NOT compared, not Python: {', '.join(skipped[:5])}" + (
            f" (+{len(skipped) - 5})" if len(skipped) > 5 else ""
        )
    return line


def _ledger(phase_dirs: list[Path]) -> tuple[set[str], list[Path]]:
    """Atom subjects excepted on these phases' ledgers, and the phases excepted whole."""
    subjects: set[str] = set()
    whole: list[Path] = []
    for phase_dir in phase_dirs:
        try:
            records = applicability.exceptions(phase_dir)
        except applicability.ApplicabilityError as exc:
            raise BehaviourDriftError(str(exc)) from exc
        for record in records:
            if record.rule != RULE:
                continue
            if record.subject == Path(phase_dir).name:
                whole.append(Path(phase_dir))
            else:
                subjects.add(record.subject)
    return subjects, whole


# --- CLI ------------------------------------------------------------------------------------------


def _phases_in_scope(
    args: argparse.Namespace, base: str | None
) -> tuple[list[Path], str] | None:
    root = Path(args.root)
    if args.phases:
        return [Path(p) for p in args.phases], "the phase(s) named on the command line"
    every = sorted(p.parent for p in root.glob("docs/features/*/phases/*/specs"))
    if args.all:
        return every, "--all: every phase under docs/features/"
    scope = applicability.changed_paths(root, base)
    if scope is None:
        return None
    touched = [p for p in every if applicability.touched(p, scope)]
    return touched, "diff-scoped: the phases this change touches"


def _check(args: argparse.Namespace) -> int:
    base = args.base if args.base is not None else (os.environ.get(BASE_ENV) or None)
    scoped = _phases_in_scope(args, base)
    if scoped is None:
        print(
            "[behaviour_drift] git cannot say what changed, so the scope is unknowable and "
            "nothing is compared. Run with --all or name a phase for a full check.",
            file=sys.stderr,
        )
        return CLEAN
    phases, mode = scoped
    contracts = []
    for phase_dir in phases:
        if not phase_dir.is_dir():
            print(
                f"[behaviour_drift] no such phase directory: {phase_dir}",
                file=sys.stderr,
            )
            return ERROR
        contracts.append(contract(phase_dir))
    bound = [c for c in contracts if c.declared]
    print(
        f"  behaviour-drift scope - {mode} ({len(phases)} phase(s), {len(bound)} under the "
        f"contract)",
        file=sys.stderr,
    )
    for c in contracts:
        print(f"  {c.phase_dir.name}: {c.reason()}", file=sys.stderr)
    if not bound:
        print(
            "[behaviour_drift] no phase in scope declares `work_kind: migration` or `refactor`, so "
            "no behaviour contract binds and nothing is compared.",
            file=sys.stderr,
        )
        return CLEAN
    if len(bound) < len(contracts) and not args.phases:
        unbound = ", ".join(c.phase_dir.name for c in contracts if not c.declared)
        print(
            f"[behaviour_drift] NOT CHECKED - the change also touches phase(s) that declare no "
            f"contract ({unbound}), and a diff cannot be attributed file by file to one phase. "
            f"Holding greenfield work to a contract it never declared would be a wedge; the "
            f"contract binds in session, where HEAD isolates the phase.",
            file=sys.stderr,
        )
        return CLEAN

    subjects, whole = _ledger([c.phase_dir for c in bound])
    if whole:
        print(
            f"[behaviour_drift] {len(whole)} phase(s) EXCEPTED whole on their ledger (rule "
            f"{RULE}, subject = the phase): {', '.join(p.name for p in whole)}. Counted and named, "
            f"never blocked - CLOSED by disclosed exception (CLAUDE.md §3a).",
            file=sys.stderr,
        )
        bound = [c for c in bound if c.phase_dir not in whole]
        if not bound:
            return CLEAN

    change = working_tree_change(Path(args.root), base)
    if change is None:
        print(
            "[behaviour_drift] git cannot state this change against its base, so the scope is "
            "unknowable and nothing is compared.",
            file=sys.stderr,
        )
        return CLEAN
    files, skipped, old_ref = change
    declared = phase_requirement_ids([c.phase_dir for c in bound])
    report = compare(files, declared, subjects)
    if report.findings:
        report_findings(report, ", ".join(c.phase_dir.name for c in bound))
        return DRIFT
    print(_clean_line(report, old_ref, skipped), file=sys.stderr)
    return CLEAN


def _declared(args: argparse.Namespace) -> int:
    found = contract(Path(args.phase_dir))
    print(("under contract: " if found.declared else "no contract: ") + found.reason())
    return CLEAN


def _compare(args: argparse.Namespace) -> int:
    files = committed_change(Path(args.root), args.old, args.new)
    declared = phase_requirement_ids([Path(args.ids_from)]) if args.ids_from else set()
    report = compare(files, declared)
    if report.findings:
        report_findings(report, f"{args.old}..{args.new}")
        return DRIFT
    print(_clean_line(report, args.old, []), file=sys.stderr)
    return CLEAN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)

    p_check = sub.add_parser(
        "check", help="enforce the contract on the phases in scope"
    )
    p_check.add_argument("phases", nargs="*")
    p_check.add_argument("--root", default=".", type=Path)
    p_check.add_argument(
        "--base", default=None, help="compare against the merge-base with REF"
    )
    p_check.add_argument("--all", action="store_true")
    p_check.set_defaults(run=_check)

    p_declared = sub.add_parser(
        "declared", help="which specs put the phase under the contract"
    )
    p_declared.add_argument("phase_dir")
    p_declared.set_defaults(run=_declared)

    p_compare = sub.add_parser(
        "compare", help="measure a landed change between two refs"
    )
    p_compare.add_argument("--old", required=True)
    p_compare.add_argument("--new", required=True)
    p_compare.add_argument("--root", default=".", type=Path)
    p_compare.add_argument(
        "--ids-from", default=None, help="phase dir whose ids citations may name"
    )
    p_compare.set_defaults(run=_compare)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (BehaviourDriftError, AtomError) as exc:
        print(f"[behaviour_drift] {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
