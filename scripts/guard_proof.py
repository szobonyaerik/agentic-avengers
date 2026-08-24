#!/usr/bin/env python3
"""Break the thing a guard guards, and confirm it goes red. A guard is not proven by passing.

## The defect

Issue #69 is one class with many symptoms: **a component reports success while doing nothing, and
nothing notices.** Its instance list closed one at a time - a stale spec stamp, a context block
whose heading never matched, a metrics recorder that dropped every write, a mutation gate that had
never once executed, a carried-items checker that read an unparseable section as "nothing carried".
Every one of them was a deterministic script that answered yes for months.

The issue names the only test that says the class is fixed, and it is not the checklist emptying:

    pick any check in the pipeline, break the thing it guards, and confirm it goes red.

`tests/` is large and green. That proves the tests pass. It does not prove that any guard would
catch the defect it exists for - a guard whose test asserts a shape the guard no longer produces
goes on passing after the guard stops guarding, which is the same silent yes one layer up.

## What this does

For every guard the inventory declares, this reintroduces the defect the guard exists to catch -
mechanically, as an exact edit to a **throwaway copy** of the tree - runs the tests named as that
guard's proof, and asserts they FAIL. A mutation that leaves them green is a guard that does not
guard, and it is reported by name.

Five outcomes, and only the first is a pass:

* **proven** - the tests were green before the mutation and red after it.
* **unproven** - the defect is back in the tree and the suite does not care. The guard is decoration.
* **unanchored** - the mutation's anchor text is no longer in the file it names. Nothing was proved,
  and the inventory has drifted from the code it describes. This is a finding, never a skip: a
  mutation that cannot be applied is indistinguishable from a guard that was never tested, and
  reading it as "fine" is the failure this whole harness exists to remove.
* **baseline-red** - the named tests fail *before* the mutation, so their going red afterwards says
  nothing about the guard.
* **errored** - the run could not be made at all (the tree could not be copied, the runner could not
  be started, the suite hit its watchdog). Never a pass.

## The undeclared count

The interesting number is not how many declared guards passed. It is how many guards the pipeline
runs that nobody declared, because those are the ones with no evidence at all.

A guard here is mechanical, not a judgement: **the enforcement surfaces are `scripts/gate_ci.sh` and
every hook script `hooks/hooks.json` runs, and the guard universe is those surfaces plus every
`scripts/*.py` and `scripts/*.sh` they invoke.** Each file in that universe is either declared by at
least one inventory entry or carries an `[[exempt]]` entry saying why it decides nothing. There is no
third state, and an exemption that nothing references any more is itself a finding - a stale
exemption outlives its reason exactly the way a stale guard outlives its test.

## The boundary

Diff-scoped through `scripts/applicability.py`, on the same rule as every other check here: a guard
declared today may not retroactively fail a tree written before it. `check` binds what the change
touches and counts the rest by name; `report` audits everything and is what the nightly sweep runs.
When git cannot say what changed the scope is unknowable, so nothing is enforced and it says so.

## What this does not claim

It proves that a named test set NOTICES a named defect. It does not prove the guard is correct, that
the mutation is the only way to reintroduce the defect, or that a guard has no other holes - a
mutation is one hole, closed. And it cannot tell that a guard was never declared **and** never
invoked from an enforcement surface: a check nothing runs is invisible to a harness that discovers
guards by what the pipeline runs.

Scope: this repository. The inventory names this repository's scripts and the tests that must go red
for them, so it is deliberately outside `install.sh`'s vendored surface - a consumer repo vendors the
guards, not the proof of them.

Usage:
    guard_proof.py list                          the inventory, one line per guard
    guard_proof.py discover [--base REF] [--all]  the guard universe: declared, exempt, undeclared
    guard_proof.py prove [--guard ID] [--jobs N]  mutate and run every declared guard; the
                                                  undeclared count is printed, not enforced
    guard_proof.py check [--base REF]             CI: diff-scoped prove + diff-scoped undeclared
    guard_proof.py report [--jobs N]              the full sweep: every guard, every undeclared file
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import applicability  # noqa: E402
from proc_group import run_bounded  # noqa: E402

OK = 0
FINDINGS = 1
ERROR = 2

#: The inventory, relative to the repository root. Plain text, stdlib-parsed (`tomllib`), and it
#: lives beside the code it describes so a guard and its declaration move in one diff.
INVENTORY = "scripts/guards.toml"

#: Seconds one guard's test run may take before its process group is killed. A guard's proof is a
#: handful of test files, not a suite; an unbounded child inside a sweep is a wedge.
DEFAULT_BUDGET_S = 900
BUDGET_ENV = "GUARD_PROOF_BUDGET_S"

#: How many guards are proved at once. Each one is a pytest process against its own copy of the tree.
DEFAULT_JOBS = 4
JOBS_ENV = "GUARD_PROOF_JOBS"

#: The runner, overridable so a test can point this at something that cannot start and watch the
#: harness report `errored` rather than a pass.
PYTEST_ENV = "GUARD_PROOF_PYTEST"

PROVEN = "proven"
UNPROVEN = "unproven"
UNANCHORED = "unanchored"
BASELINE_RED = "baseline-red"
ERRORED = "errored"

#: Every status that is not a proof. Each one is a finding, and none of them is a skip.
FINDING_STATUSES = (UNPROVEN, UNANCHORED, BASELINE_RED, ERRORED)

#: The files whose contents define what the pipeline enforces. Everything they invoke is a guard
#: until the inventory says otherwise.
GATE_CI = "scripts/gate_ci.sh"
HOOKS_JSON = "hooks/hooks.json"

#: How a shell file in this repository names a sibling script. The same three spellings
#: `tests/test_install_manifest.py` derives its expectation from, for the same reason: adding a
#: script and calling it is enough to move the check, with no second list to remember.
_REFERENCE_PREFIXES = (
    "$SCRIPT_DIR/",
    "$SD/",
    "${CLAUDE_PLUGIN_ROOT}/scripts/",
    "scripts/",
)

#: The keys an inventory entry may carry. A closed schema, so a misspelled key is a loud refusal
#: rather than a field that silently does nothing - which is how a mutation stops being applied.
_GUARD_KEYS = {"id", "implements", "defect", "tests", "mutation"}
_EDIT_KEYS = {"file", "find", "replace", "count"}
_EXEMPT_KEYS = {"file", "reason"}


class GuardProofError(Exception):
    """A malformed inventory, or a tree that cannot be copied. Always fails the caller closed."""


# --- the inventory -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """One exact textual change that reintroduces a defect."""

    file: str
    find: str
    replace: str
    count: int

    def apply(self, tree: Path) -> str | None:
        """Make the edit inside `tree`, or say why it could not be made."""
        target = tree / self.file
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return f"{self.file}: cannot read it in the copied tree ({exc})"
        found = text.count(self.find)
        if found != self.count:
            return (
                f"{self.file}: the mutation's anchor appears {found} time(s), not the {self.count} "
                f"the inventory declares. The code moved and the declaration did not follow it, so "
                f"nothing was proved. Anchor: {self.find.strip()[:120]!r}"
            )
        target.write_text(text.replace(self.find, self.replace), encoding="utf-8")
        return None


@dataclass(frozen=True)
class Guard:
    """One declared guard: what it catches, how to break it, and what must notice."""

    id: str
    implements: str
    defect: str
    tests: tuple[str, ...]
    mutation: tuple[Edit, ...]

    @property
    def files(self) -> tuple[str, ...]:
        """Every path whose change puts this guard back in scope for a diff-scoped run."""
        seen = [self.implements, *(edit.file for edit in self.mutation)]
        seen += [test.split("::", 1)[0] for test in self.tests]
        return tuple(dict.fromkeys(seen))


@dataclass(frozen=True)
class Exempt:
    """A file the enforcement surfaces invoke that decides nothing, and why."""

    file: str
    reason: str


@dataclass(frozen=True)
class Inventory:
    path: Path
    guards: tuple[Guard, ...]
    exempt: tuple[Exempt, ...]

    def declared_files(self) -> set[str]:
        return {guard.implements for guard in self.guards} | {
            edit.file for guard in self.guards for edit in guard.mutation
        }

    def exempt_files(self) -> set[str]:
        return {entry.file for entry in self.exempt}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardProofError(message)


def _edit(raw: object, guard_id: str) -> Edit:
    _require(
        isinstance(raw, dict),
        f"guard {guard_id!r}: every mutation must be a table of its own",
    )
    assert isinstance(raw, dict)
    unknown = set(raw) - _EDIT_KEYS
    _require(
        not unknown,
        f"guard {guard_id!r}: a mutation carries unknown key(s) {sorted(unknown)}. The schema is "
        f"closed on purpose - a misspelled key is a mutation that quietly stops being applied.",
    )
    for key in ("file", "find", "replace"):
        _require(
            isinstance(raw.get(key), str),
            f"guard {guard_id!r}: a mutation needs a string {key!r}",
        )
    _require(
        raw["find"] != raw["replace"],
        f"guard {guard_id!r}: a mutation whose replacement equals its anchor changes nothing, so it "
        f"would report every guard proven or unproven for the wrong reason",
    )
    count = raw.get("count", 1)
    _require(
        isinstance(count, int) and count >= 1,
        f"guard {guard_id!r}: a mutation's `count` must be a positive integer",
    )
    return Edit(
        file=str(raw["file"]),
        find=str(raw["find"]),
        replace=str(raw["replace"]),
        count=int(count),
    )


def _guard(raw: object) -> Guard:
    _require(isinstance(raw, dict), "every [[guard]] must be a table")
    assert isinstance(raw, dict)
    identifier = raw.get("id")
    _require(
        isinstance(identifier, str) and identifier.strip(),
        "every [[guard]] needs a non-empty `id`",
    )
    unknown = set(raw) - _GUARD_KEYS
    _require(
        not unknown,
        f"guard {identifier!r} carries unknown key(s) {sorted(unknown)}; the schema is closed",
    )
    for key in ("implements", "defect"):
        _require(
            isinstance(raw.get(key), str) and str(raw[key]).strip(),
            f"guard {identifier!r} needs a non-empty {key!r}",
        )
    tests = raw.get("tests")
    _require(
        isinstance(tests, list)
        and tests
        and all(isinstance(t, str) and t.strip() for t in tests),
        f"guard {identifier!r} must name at least one test that has to go red. A guard with no "
        f"named proof is a guard nothing would notice losing.",
    )
    mutation = raw.get("mutation")
    _require(
        isinstance(mutation, list) and bool(mutation),
        f"guard {identifier!r} must declare at least one mutation - the edit that puts the defect "
        f"back. A declaration with no mutation asserts a guard works and proves nothing.",
    )
    assert isinstance(tests, list) and isinstance(mutation, list)
    return Guard(
        id=str(identifier),
        implements=str(raw["implements"]),
        defect=str(raw["defect"]),
        tests=tuple(str(t) for t in tests),
        mutation=tuple(_edit(entry, str(identifier)) for entry in mutation),
    )


def _exempt(raw: object) -> Exempt:
    _require(isinstance(raw, dict), "every [[exempt]] must be a table")
    assert isinstance(raw, dict)
    unknown = set(raw) - _EXEMPT_KEYS
    _require(
        not unknown, f"an [[exempt]] entry carries unknown key(s) {sorted(unknown)}"
    )
    for key in ("file", "reason"):
        _require(
            isinstance(raw.get(key), str) and str(raw[key]).strip(),
            f"every [[exempt]] entry needs a non-empty {key!r} - an exemption with no reason is a "
            f"guard switched off with nobody's name on it",
        )
    return Exempt(file=str(raw["file"]), reason=str(raw["reason"]))


def load(path: Path) -> Inventory:
    """The declared inventory, validated. A malformed inventory is an error, never an empty one."""
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise GuardProofError(
            f"cannot read the guard inventory at {path}: {exc}"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise GuardProofError(f"{path} is not readable TOML: {exc}") from exc

    guards = tuple(_guard(entry) for entry in raw.get("guard", []))
    exempt = tuple(_exempt(entry) for entry in raw.get("exempt", []))

    seen: set[str] = set()
    for guard in guards:
        _require(
            guard.id not in seen,
            f"two guards both declare the id {guard.id!r}; ids address a guard on the command line "
            f"and in the report, so they must be unique",
        )
        seen.add(guard.id)
    return Inventory(path=Path(path), guards=guards, exempt=exempt)


def missing_paths(root: Path, inventory: Inventory) -> list[str]:
    """Declared paths that are not files in the tree - a declaration already pointing at nothing."""
    wanted: set[str] = set()
    for guard in inventory.guards:
        wanted.update(guard.files)
    wanted.update(inventory.exempt_files())
    return sorted(name for name in wanted if not (root / name).is_file())


# --- discovery: what the pipeline actually enforces -----------------------------------------------


def _hook_scripts(root: Path) -> list[str]:
    """Every `scripts/*.sh` the hook configuration runs, read out of `hooks/hooks.json` itself."""
    target = root / HOOKS_JSON
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GuardProofError(
            f"cannot read {HOOKS_JSON}: {exc}. The hook configuration is half the guard universe, "
            f"so an unreadable one is refused rather than read as no hooks at all."
        ) from exc
    found: list[str] = []
    for command in _strings(data):
        for token in _script_tokens(command):
            if token.endswith(".sh"):
                found.append(token)
    return sorted(dict.fromkeys(found))


def _strings(node: object) -> list[str]:
    """Every string anywhere in a decoded JSON document."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for value in node for s in _strings(value)]
    return []


def _script_tokens(text: str) -> list[str]:
    """`scripts/<name>.(py|sh)` for every sibling-script reference in `text`, in any spelling."""
    found: list[str] = []
    for prefix in _REFERENCE_PREFIXES:
        start = 0
        while True:
            at = text.find(prefix, start)
            if at < 0:
                break
            start = at + len(prefix)
            name = ""
            for char in text[start:]:
                if char.isalnum() or char in "_.-":
                    name += char
                else:
                    break
            if name.endswith(".py") or name.endswith(".sh"):
                found.append(f"scripts/{name}")
    return found


def _module_imports(root: Path, name: str) -> list[str]:
    """Sibling `scripts/*.py` modules that `name` imports, flat - the same shape install.sh needs."""
    try:
        text = (root / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        module = ""
        if stripped.startswith("from ") and " import " in stripped:
            module = stripped[5:].split(" import ", 1)[0].strip()
        elif stripped.startswith("import "):
            module = stripped[7:].split(" as ", 1)[0].strip()
        if not module or "." in module or " " in module:
            continue
        if (root / "scripts" / f"{module}.py").is_file():
            found.append(f"scripts/{module}.py")
    return found


def guard_universe(root: Path) -> dict[str, set[str]]:
    """Every file that must be declared or exempt, mapped to what puts it on the enforcement path.

    The surfaces are `scripts/gate_ci.sh` and every hook script `hooks/hooks.json` runs; the universe
    is those plus every sibling script they invoke, **and every sibling module those import**, to a
    fixed point. Derived rather than listed, so adding a check and wiring it in is enough to move
    this - which is the whole point of the undeclared count.

    The import half is not decoration. `gate_plausibility.py` - the guard issue #69's own last
    instance produced - is reached by no shell line at all: `gate_runner.py` imports it. A universe
    built from shell references alone would have declared the pipeline fully covered while the
    newest guard in it had no proof of any kind.
    """
    surfaces = [GATE_CI, *_hook_scripts(root)]
    universe: dict[str, set[str]] = {}
    for surface in surfaces:
        if not (root / surface).is_file():
            continue
        universe.setdefault(surface, set()).add(
            HOOKS_JSON if surface != GATE_CI else "the pre-commit and CI gate floor"
        )
        try:
            text = (root / surface).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for token in _script_tokens(text):
            if (root / token).is_file():
                universe.setdefault(token, set()).add(surface)

    pending = [name for name in universe if name.endswith(".py")]
    while pending:
        name = pending.pop()
        for imported in _module_imports(root, name):
            if imported == name:
                continue
            known = universe.setdefault(imported, set())
            if not known:
                pending.append(imported)
            known.add(f"{name} (import)")
    return universe


# --- the throwaway copy --------------------------------------------------------------------------


def _git_lines(root: Path, *args: str) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


_COPY_SKIP = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".ruff_cache",
    ".pytest_cache",
}


def materialize(root: Path, dest: Path) -> str:
    """Copy the tree's WORKING STATE into `dest`, and say how it was resolved.

    The file list comes from git so the copy is the tree under test rather than the checkout plus
    every build artifact beside it. With no git answer it falls back to a filtered walk and says so:
    a copy that silently became something else would make every verdict here describe another tree.
    """
    dest.mkdir(parents=True, exist_ok=True)
    tracked = _git_lines(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "--full-name"
    )
    if tracked is None:
        _copy_walk(root, dest)
        return "filtered walk (git could not list the tree)"
    for name in tracked:
        source = root / name
        if not source.is_file() or _COPY_SKIP & set(Path(name).parts):
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return "git ls-files"


def _copy_walk(root: Path, dest: Path) -> None:
    for source in root.rglob("*"):
        relative = source.relative_to(root)
        if _COPY_SKIP & set(relative.parts) or not source.is_file():
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


# --- running one guard's proof --------------------------------------------------------------------


def budget_s() -> int:
    raw = (os.environ.get(BUDGET_ENV) or "").strip()
    if not raw:
        return DEFAULT_BUDGET_S
    try:
        value = int(raw)
    except ValueError as exc:
        raise GuardProofError(
            f"{BUDGET_ENV}={raw!r} is not an integer number of seconds"
        ) from exc
    if value <= 0:
        raise GuardProofError(
            f"{BUDGET_ENV}={raw!r} must be a positive number of seconds"
        )
    return value


def jobs() -> int:
    raw = (os.environ.get(JOBS_ENV) or "").strip()
    if not raw:
        return DEFAULT_JOBS
    try:
        value = int(raw)
    except ValueError as exc:
        raise GuardProofError(f"{JOBS_ENV}={raw!r} is not an integer") from exc
    if value <= 0:
        raise GuardProofError(f"{JOBS_ENV}={raw!r} must be a positive integer")
    return value


def _runner() -> list[str]:
    declared = (os.environ.get(PYTEST_ENV) or "").strip()
    if declared:
        return declared.split()
    return [sys.executable, "-m", "pytest"]


@dataclass(frozen=True)
class Run:
    returncode: int
    output: str
    timed_out: bool
    started: bool


def run_tests(tree: Path, tests: tuple[str, ...], budget: int) -> Run:
    """Run one guard's named tests inside `tree`. Never raises: a run that could not start is a Run."""
    argv = [*_runner(), "-q", "-x", "-p", "no:cacheprovider", *tests]
    try:
        result = run_bounded(argv, budget, cwd=str(tree))
    except FileNotFoundError as exc:
        return Run(
            returncode=ERROR,
            output=f"the test runner could not be started: {exc}",
            timed_out=False,
            started=False,
        )
    return Run(
        returncode=result.returncode,
        output=(result.stdout or "") + (result.stderr or ""),
        timed_out=result.timed_out,
        started=True,
    )


@dataclass(frozen=True)
class Proof:
    guard_id: str
    status: str
    detail: str

    @property
    def is_finding(self) -> bool:
        return self.status in FINDING_STATUSES


def _tail(output: str, lines: int = 12) -> str:
    kept = [line for line in (output or "").splitlines() if line.strip()][-lines:]
    return "\n".join(f"      {line}" for line in kept)


def prove(guard: Guard, root: Path, budget: int, baseline: Run | None = None) -> Proof:
    """Break what `guard` guards in a throwaway copy, and report whether its tests noticed."""
    work = Path(tempfile.mkdtemp(prefix=f"guard-proof-{guard.id.replace('/', '_')}-"))
    try:
        tree = work / "tree"
        try:
            materialize(root, tree)
        except OSError as exc:
            return Proof(guard.id, ERRORED, f"the tree could not be copied: {exc}")

        if baseline is None:
            baseline = run_tests(tree, guard.tests, budget)
        if not baseline.started:
            return Proof(guard.id, ERRORED, baseline.output)
        if baseline.timed_out:
            return Proof(
                guard.id,
                ERRORED,
                f"the unmutated run hit its {budget}s watchdog, so there is no baseline to compare "
                f"against",
            )
        if baseline.returncode != 0:
            return Proof(
                guard.id,
                BASELINE_RED,
                "these tests already fail WITHOUT the mutation, so their going red proves nothing "
                "about the guard:\n" + _tail(baseline.output),
            )

        for edit in guard.mutation:
            problem = edit.apply(tree)
            if problem is not None:
                return Proof(guard.id, UNANCHORED, problem)

        mutated = run_tests(tree, guard.tests, budget)
        if not mutated.started:
            return Proof(guard.id, ERRORED, mutated.output)
        if mutated.timed_out:
            return Proof(
                guard.id,
                ERRORED,
                f"the mutated run hit its {budget}s watchdog. A killed run is not a red one - there "
                f"is no result to read.",
            )
        if mutated.returncode == 0:
            return Proof(
                guard.id,
                UNPROVEN,
                "the defect is back in the tree and the named tests stayed GREEN. This guard is not "
                "guarded by anything: " + ", ".join(guard.tests),
            )
        return Proof(guard.id, PROVEN, f"red at exit {mutated.returncode}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- scope ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """What this run is responsible for. `all` audits; `changed` binds the diff; `unknowable` binds
    nothing and says so, because falling back to the whole tree is the hostage failure."""

    mode: str
    paths: frozenset[Path]

    def covers(self, path: Path) -> bool:
        if self.mode == "all":
            return True
        if self.mode == "unknowable":
            return False
        return applicability.touched(path, set(self.paths))

    def covers_guard(self, guard: Guard, root: Path) -> bool:
        """A guard is in scope when the change touches anything its proof depends on."""
        return any(self.covers(root / name) for name in guard.files)


def resolve_scope(root: Path, *, changed_only: bool, base: str | None) -> Scope:
    if not changed_only:
        return Scope("all", frozenset())
    paths = applicability.changed_paths(root, base)
    if paths is None:
        return Scope("unknowable", frozenset())
    return Scope("changed", frozenset(paths))


# --- the report -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    scope: str
    proofs: tuple[Proof, ...]
    skipped: tuple[str, ...]
    undeclared: tuple[tuple[str, tuple[str, ...]], ...]
    undeclared_unenforced: tuple[str, ...]
    stale_exemptions: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def findings(self) -> tuple[Proof, ...]:
        return tuple(proof for proof in self.proofs if proof.is_finding)

    @property
    def proven(self) -> int:
        return sum(1 for proof in self.proofs if proof.status == PROVEN)

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "declared": len(self.proofs) + len(self.skipped),
            "proved": self.proven,
            "undeclared": len(self.undeclared),
            "proofs": [
                {"guard": p.guard_id, "status": p.status, "detail": p.detail}
                for p in self.proofs
            ],
            "not_enforced": list(self.skipped),
            "undeclared_files": {
                name: list(callers) for name, callers in self.undeclared
            },
            "undeclared_not_enforced": list(self.undeclared_unenforced),
            "stale_exemptions": list(self.stale_exemptions),
            "declared_paths_missing": list(self.missing),
        }


def sweep(
    root: Path,
    inventory: Inventory,
    *,
    scope: Scope,
    only: tuple[str, ...] = (),
    budget: int | None = None,
    parallel: int | None = None,
) -> Report:
    """Prove every guard in scope, and name every guard in the code that nobody declared."""
    limit = budget if budget is not None else budget_s()
    workers = parallel if parallel is not None else jobs()

    selected: list[Guard] = []
    skipped: list[str] = []
    for guard in inventory.guards:
        if only and guard.id not in only:
            continue
        if only or scope.covers_guard(guard, root):
            selected.append(guard)
        else:
            skipped.append(guard.id)

    baselines = _baselines(root, selected, limit, workers)
    proofs: list[Proof] = []
    if selected:
        with ThreadPoolExecutor(
            max_workers=max(1, min(workers, len(selected)))
        ) as pool:
            proofs = list(
                pool.map(
                    lambda guard: prove(guard, root, limit, baselines.get(guard.tests)),
                    selected,
                )
            )

    universe = guard_universe(root)
    declared = inventory.declared_files()
    exempt = inventory.exempt_files()
    undeclared: list[tuple[str, tuple[str, ...]]] = []
    undeclared_unenforced: list[str] = []
    for name, callers in sorted(universe.items()):
        if name in declared or name in exempt:
            continue
        if scope.covers(root / name):
            undeclared.append((name, tuple(sorted(callers))))
        else:
            undeclared_unenforced.append(name)

    stale = tuple(
        sorted(entry.file for entry in inventory.exempt if entry.file not in universe)
    )
    return Report(
        scope=scope.mode,
        proofs=tuple(proofs),
        skipped=tuple(skipped),
        undeclared=tuple(undeclared),
        undeclared_unenforced=tuple(undeclared_unenforced),
        stale_exemptions=stale,
        missing=tuple(missing_paths(root, inventory)),
    )


def _baselines(
    root: Path, guards: list[Guard], budget: int, workers: int
) -> dict[tuple[str, ...], Run]:
    """One unmutated run per distinct test set, shared by every guard that names it.

    Guards that name the same tests would otherwise pay for the same green run several times, and
    the answer cannot differ - the tree is identical.
    """
    sets = sorted({guard.tests for guard in guards})
    if not sets:
        return {}
    work = Path(tempfile.mkdtemp(prefix="guard-proof-baseline-"))
    try:
        tree = work / "tree"
        materialize(root, tree)
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sets)))) as pool:
            runs = list(pool.map(lambda tests: run_tests(tree, tests, budget), sets))
        return dict(zip(sets, runs))
    except OSError:
        return {}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- printing ---------------------------------------------------------------------------------


def render(report: Report, inventory: Inventory) -> str:
    lines: list[str] = []
    total = len(report.proofs) + len(report.skipped)
    lines.append(
        f"guard-proof: {total} guard(s) declared, {report.proven} proved, "
        f"{len(report.undeclared)} undeclared"
    )
    if report.scope == "unknowable":
        lines.append(
            "  scope UNKNOWABLE - git cannot say what this change touches, so nothing was "
            "enforced. Run `guard_proof.py report` to audit the whole tree."
        )
    for proof in report.proofs:
        mark = "✓" if proof.status == PROVEN else "✗"
        lines.append(f"  {mark} {proof.guard_id} [{proof.status}]")
        if proof.is_finding:
            for line in proof.detail.splitlines():
                lines.append(f"      {line}" if not line.startswith("    ") else line)
    for name, callers in report.undeclared:
        lines.append(
            f"  ✗ {name} is invoked by {', '.join(callers)} and no inventory entry declares or "
            f"exempts it — nothing proves it guards anything"
        )
    for name in report.stale_exemptions:
        lines.append(
            f"  ✗ {name} is exempted in {inventory.path.name} but no enforcement surface invokes it "
            f"any more — drop the exemption rather than carrying a stale one"
        )
    for name in report.missing:
        lines.append(
            f"  ✗ {name} is named by the inventory but is not a file in the tree"
        )
    return "\n".join(lines)


def _report_unenforced(report: Report) -> None:
    applicability.report_unenforced(
        "guard-proof",
        len(report.skipped),
        "guards whose implementation this change does not touch: "
        + ", ".join(report.skipped),
    )
    applicability.report_unenforced(
        "guard-proof",
        len(report.undeclared_unenforced),
        "undeclared guards this change does not touch: "
        + ", ".join(report.undeclared_unenforced),
    )


def verdict(report: Report, *, undeclared_is_a_finding: bool = True) -> int:
    """The exit code. `prove` reports the undeclared count without failing on it; `check` and
    `report` fail on it.

    The split is the applicability boundary, not a softening: an undeclared guard is a rule added
    after the tree it runs on, so it binds what the change TOUCHES (`check`) and is audited
    deliberately (`report`). `prove` answers one question - do the declared guards guard? - and
    still prints what nobody has declared, because a count that stops being printed is a count
    nobody acts on.
    """
    findings = len(report.findings) + len(report.stale_exemptions) + len(report.missing)
    if undeclared_is_a_finding:
        findings += len(report.undeclared)
    return FINDINGS if findings else OK


# --- CLI --------------------------------------------------------------------------------------


def _root(args: argparse.Namespace) -> Path:
    return Path(
        getattr(args, "root", None) or Path(__file__).resolve().parents[1]
    ).resolve()


def _inventory_path(args: argparse.Namespace, root: Path) -> Path:
    declared = getattr(args, "inventory", None)
    return Path(declared).resolve() if declared else root / INVENTORY


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--root", default=None, help="the tree to prove (default: this repo)"
        )
        target.add_argument(
            "--inventory", default=None, help=f"default: <root>/{INVENTORY}"
        )

    listing = sub.add_parser("list", help="the inventory, one line per guard")
    common(listing)

    discover = sub.add_parser(
        "discover", help="the guard universe and what declares each file"
    )
    common(discover)
    discover.add_argument(
        "--base", default=None, help="scope to the diff against this commit"
    )
    discover.add_argument(
        "--all",
        action="store_true",
        help="audit every file, not only what the diff touches",
    )

    for name, help_text in (
        ("prove", "mutate and run every declared guard"),
        ("check", "CI: diff-scoped prove plus diff-scoped undeclared"),
        ("report", "the full sweep: every guard, every undeclared file"),
    ):
        parser_ = sub.add_parser(name, help=help_text)
        common(parser_)
        parser_.add_argument(
            "--jobs", type=int, default=None, help="guards proved at once"
        )
        parser_.add_argument(
            "--json", action="store_true", help="machine-readable report"
        )
        if name != "report":
            parser_.add_argument(
                "--base", default=None, help="scope to the diff against this commit"
            )
        if name == "prove":
            parser_.add_argument(
                "--guard",
                action="append",
                default=[],
                help="prove only this id (repeatable)",
            )
            parser_.add_argument(
                "--changed", action="store_true", help="only guards this change touches"
            )
    return parser.parse_args(argv)


def _do_list(inventory: Inventory) -> int:
    for guard in inventory.guards:
        print(f"{guard.id}\t{guard.implements}\t{', '.join(guard.tests)}")
    for entry in inventory.exempt:
        print(f"(exempt)\t{entry.file}\t{entry.reason}")
    return OK


def _do_discover(root: Path, inventory: Inventory, args: argparse.Namespace) -> int:
    scope = resolve_scope(root, changed_only=not args.all, base=args.base)
    universe = guard_universe(root)
    declared = inventory.declared_files()
    exempt = inventory.exempt_files()
    findings = 0
    unenforced: list[str] = []
    for name, callers in sorted(universe.items()):
        state = (
            "declared"
            if name in declared
            else "exempt"
            if name in exempt
            else "UNDECLARED"
        )
        if state == "UNDECLARED" and not scope.covers(root / name):
            unenforced.append(name)
            state = "undeclared (not enforced)"
        elif state == "UNDECLARED":
            findings += 1
        print(f"{state}\t{name}\t{', '.join(sorted(callers))}")
    applicability.report_unenforced(
        "guard-proof",
        len(unenforced),
        "undeclared guards this change does not touch: " + ", ".join(unenforced),
    )
    return FINDINGS if findings else OK


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    root = _root(args)
    try:
        inventory = load(_inventory_path(args, root))
        if args.action == "list":
            return _do_list(inventory)
        if args.action == "discover":
            return _do_discover(root, inventory, args)

        changed_only = args.action == "check" or bool(getattr(args, "changed", False))
        scope = resolve_scope(
            root, changed_only=changed_only, base=getattr(args, "base", None)
        )
        report = sweep(
            root,
            inventory,
            scope=scope,
            only=tuple(getattr(args, "guard", []) or ()),
            parallel=args.jobs,
        )
    except GuardProofError as exc:
        print(f"[guard-proof] {exc}", file=sys.stderr)
        return ERROR

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(render(report, inventory))
    _report_unenforced(report)
    return verdict(report, undeclared_is_a_finding=args.action != "prove")


if __name__ == "__main__":
    raise SystemExit(main())
