#!/usr/bin/env python3
"""What must hold around `cosmic-ray exec`: the tree it leaves, and whether it can finish (issue #95).

## The defect

`cosmic-ray exec` mutates source files IN PLACE and reverts each mutant in a `finally`. The mutation
hook launches it in its own session and, when the harness's timeout arrives, SIGTERMs then SIGKILLs
the whole process group - and neither signal runs a `finally`. The mutant applied at that moment
stayed on disk, indistinguishable from an authored edit (`-` to `+`, `0` to `1`, `10` to `9`), and
was committed as one: four times in one measured phase, three of them under a `verdict: pass`, in
live trading code. The hook's own comments cite overruns of 569s, 3818s and 4276s against a 300s
budget, so the kill path is the EXPECTED path on any real suite, not an edge case.

## What this decides

Three commands, each one thing the hook asks at the point it is decided:

* `snapshot` - before exec. Copies every file the session can mutate (the distinct `module_path`
  of its work items) into a scratch directory, with a sha256 per file and `git status --porcelain`
  over those paths for the record. The set comes from the SESSION, not from the config: it is the
  exact set cosmic-ray will write to, and it exists the moment `init` has run.
* `restore` - after exec, and on the kill path. Rehashes every snapshotted file, and for each one
  that differs (or is gone) copies the snapshot back and says so. Exit 0 only when NOTHING differed:
  a tree that had to be restored is a tree that was corrupted, and the caller fails the hook on it
  regardless of policy - a corrupted tree is never advisory. Restoring from the snapshot rather than
  from `git checkout` is deliberate: the pipeline verifies UNCOMMITTED work, and HEAD is not the
  state the implementer left.
* `budget` - before exec. The one estimate that is available up front on every run, with no prior
  record needed: the measured wall clock of `cosmic-ray baseline` (one run of the test command on
  unmutated code) times the number of mutants still pending after scoping, against the seconds the
  hook has left. Over budget, the hook refuses to START exec rather than start it and be killed with
  a mutant applied.

## What it does not decide

The content hash decides; the porcelain is recorded and shown, never compared, because the index can
legitimately change under a hook (an implementer staging a file) and cosmic-ray never touches it. A
test command that itself rewrites a file under `module-path` reads as corruption - the check cannot
tell who wrote the bytes, only that they changed. The budget is a LOWER bound: a mutant that hangs
runs to cosmic-ray's own `timeout`, longer than the baseline, so a run that fits the estimate can
still overrun; that is why `restore` runs on the kill path too rather than trusting the estimate.
And nothing here can act when the hook ITSELF is SIGKILLed - there is no code left to run - which is
what the budget refusal is for. What a clean result does not establish is stated in this module's
runtime output (`scripts/guard_scope.py`), because a later stage reads output and not source.

Usage:
    mutation_exec_guard.py snapshot --root DIR --session S OUT_DIR
    mutation_exec_guard.py restore  --root DIR SNAP_DIR
    mutation_exec_guard.py budget   --session S --baseline-s B --budget-s X

Exit codes:
    0  snapshot written / tree intact / exec fits the budget
    1  restore: the tree DIFFERED and was put back / budget: exec would not fit
    2  error - could not read the session, a file, could not restore, or an unexpected failure.
       Fail closed. Nothing escapes as a traceback: 1 is the code the caller reads as "the tree
       differed AND every file is back", and a crash is not that claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402

OK = 0
DIFFERED = 1
ERROR = 2

MANIFEST = "manifest.json"
FILES_DIR = "files"
TAG = "[mutation_exec_guard]"


def _sha256(path: Path) -> str | None:
    """The file's content hash, or None when it cannot be read - never an empty-string hash."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _porcelain(root: Path, paths: list[Path]) -> str | None:
    """`git status --porcelain` over exactly the in-scope paths. None when git has no answer."""
    if not paths:
        return ""
    try:
        proc = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *map(str, paths),
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def session_files(session: Path, root: Path) -> list[Path]:
    """Every file the session can write to: the distinct module_path of its work items, resolved."""
    from cosmic_ray.work_db import WorkDB, use_db

    seen: dict[Path, None] = {}
    with use_db(str(session), WorkDB.Mode.open) as db:
        for item in db.work_items:
            for mutation in item.mutations:
                raw = Path(mutation.module_path)
                resolved = raw if raw.is_absolute() else root / raw
                seen.setdefault(resolved.resolve(), None)
    return list(seen)


def pending_mutants(session: Path) -> int:
    """How many mutants `exec` would still run: work items with no result yet."""
    from cosmic_ray.work_db import WorkDB, use_db

    with use_db(str(session), WorkDB.Mode.open) as db:
        return max(db.num_work_items - db.num_results, 0)


def snapshot(root: Path, session: Path, out: Path) -> int:
    root = root.resolve()
    try:
        files = session_files(session, root)
    except ImportError as exc:
        print(f"{TAG} cosmic-ray is not importable: {exc}", file=sys.stderr)
        return ERROR
    except Exception as exc:  # noqa: BLE001 - any unreadable session refuses the snapshot
        print(f"{TAG} cannot read the session {session}: {exc}", file=sys.stderr)
        return ERROR

    entries: list[dict[str, str]] = []
    try:
        (out / FILES_DIR).mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(files):
            digest = _sha256(path)
            if digest is None:
                print(f"{TAG} cannot read an in-scope file: {path}", file=sys.stderr)
                return ERROR
            copy = out / FILES_DIR / str(index)
            shutil.copyfile(path, copy)
            entries.append({"path": str(path), "sha256": digest, "copy": copy.name})
        manifest = {
            "root": str(root),
            "files": entries,
            "porcelain": _porcelain(root, files),
        }
        (out / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"{TAG} cannot write the snapshot under {out}: {exc}", file=sys.stderr)
        return ERROR
    print(f"{TAG} snapshot: {len(entries)} in-scope file(s) recorded before exec")
    return OK


def _load_manifest(snap: Path) -> dict | None:
    try:
        return json.loads((snap / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def restore(root: Path, snap: Path) -> int:
    root = root.resolve()
    manifest = _load_manifest(snap)
    if manifest is None or not isinstance(manifest.get("files"), list):
        print(
            f"{TAG} no readable snapshot at {snap}: cannot say what exec left behind",
            file=sys.stderr,
        )
        return ERROR

    differed: list[Path] = []
    unrestored: list[str] = []
    for entry in manifest["files"]:
        path = Path(entry["path"])
        now = _sha256(path)
        if now == entry["sha256"]:
            continue
        differed.append(path)
        was = "missing" if now is None else now[:12]
        try:
            shutil.copyfile(snap / FILES_DIR / entry["copy"], path)
        except OSError as exc:
            unrestored.append(f"{path}: {exc}")
            continue
        if _sha256(path) != entry["sha256"]:
            unrestored.append(f"{path}: content still differs after the copy back")
            continue
        print(
            f"{TAG} RESTORED {path} (found {was}, snapshot {entry['sha256'][:12]})",
            file=sys.stderr,
        )

    if not differed:
        print(f"{TAG} tree intact: every in-scope file hashes as it did before exec")
        return OK

    print(
        f"{TAG} TREE DIFFERED after exec: {len(differed)} in-scope file(s) were not as cosmic-ray "
        f"found them. A mutant, or part of one, was left applied.",
        file=sys.stderr,
    )
    before = manifest.get("porcelain")
    after = _porcelain(root, [Path(e["path"]) for e in manifest["files"]])
    if before is not None and after is not None and before != after:
        print(f"{TAG} git status before exec:\n{before.rstrip()}", file=sys.stderr)
        print(f"{TAG} git status after restore:\n{after.rstrip()}", file=sys.stderr)
    if unrestored:
        for line in unrestored:
            print(f"{TAG} NOT RESTORED {line}", file=sys.stderr)
        return ERROR
    return DIFFERED


def estimate_seconds(pending: int, baseline_s: float) -> int:
    """The lower bound `exec` will take: one test-command run per pending mutant."""
    return math.ceil(pending * max(baseline_s, 0.0))


def budget(session: Path, baseline_s: float, budget_s: float) -> int:
    try:
        pending = pending_mutants(session)
    except ImportError as exc:
        print(f"{TAG} cosmic-ray is not importable: {exc}", file=sys.stderr)
        return ERROR
    except Exception as exc:  # noqa: BLE001 - an unreadable session is an unknown estimate
        print(f"{TAG} cannot read the session {session}: {exc}", file=sys.stderr)
        return ERROR
    estimate = estimate_seconds(pending, baseline_s)
    line = (
        f"{TAG} exec estimate: {pending} pending mutant(s) x {baseline_s:g}s baseline = "
        f"{estimate}s, against {budget_s:g}s left in the hook's budget"
    )
    if estimate <= budget_s:
        print(line)
        return OK
    print(line, file=sys.stderr)
    print(
        f"{TAG} OVER BUDGET: exec would be killed mid-mutant. Refusing to start it. Remedies: raise "
        f"MUTATION_HOOK_BUDGET_S and the hook's timeout in hooks/hooks.json together, narrow the "
        f"scope, or make the test command faster.",
        file=sys.stderr,
    )
    return DIFFERED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)

    snap = sub.add_parser("snapshot", help="record every in-scope file before exec")
    snap.add_argument("out", type=Path, help="directory to write the snapshot into")
    snap.add_argument("--root", type=Path, default=Path("."), help="repository root")
    snap.add_argument(
        "--session", type=Path, required=True, help="the cosmic-ray session"
    )

    rest = sub.add_parser("restore", help="put back every in-scope file that differs")
    rest.add_argument("snap", type=Path, help="the snapshot directory")
    rest.add_argument("--root", type=Path, default=Path("."), help="repository root")

    bud = sub.add_parser(
        "budget", help="whether exec can finish inside the seconds left"
    )
    bud.add_argument(
        "--session", type=Path, required=True, help="the cosmic-ray session"
    )
    bud.add_argument(
        "--baseline-s", type=float, required=True, help="measured baseline seconds"
    )
    bud.add_argument(
        "--budget-s", type=float, required=True, help="seconds the hook has left"
    )

    args = parser.parse_args(argv)
    if args.action == "snapshot":
        if not args.session.is_file():
            print(f"{TAG} session not found: {args.session}", file=sys.stderr)
            return ERROR
        return snapshot(args.root, args.session, args.out)
    if args.action == "restore":
        return restore(args.root, args.snap)
    if not args.session.is_file():
        print(f"{TAG} session not found: {args.session}", file=sys.stderr)
        return ERROR
    return budget(args.session, args.baseline_s, args.budget_s)


def main_guarded(argv: list[str] | None = None) -> int:
    """`main` with nothing escaping as a traceback: an unexpected failure is ERROR, never DIFFERED."""
    try:
        return main(argv)
    except Exception as exc:  # noqa: BLE001 - any crash is an unknown tree, and that is fail-closed
        print(f"{TAG} unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main_guarded))
