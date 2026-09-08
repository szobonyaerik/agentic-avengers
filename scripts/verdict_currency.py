#!/usr/bin/env python3
"""A passing verdict must not stand over a tree that has since changed (issue #51).

A phase passes verification and the pipeline writes `verdict.json`. A later fix pass — the feature
close ship gate, which owns both findings and fixes while it runs — then changes production code the
verdict covers, and touches no phase artifact. The verdict still asserts a named source file is
byte-identical, quoting a `git diff`, after the fix commit changed it. The phase is no longer
verified as recorded, and nothing anywhere notices or says so.

**The obvious remedy is the forbidden one.** Letting anything rewrite `verdict.json` would restate a
verification nobody performed over the changed code. Two separate phase workers refused to do that
and both were right. The correct remedy already exists (`scripts/amendments.py`): open an amendment
naming the requirement ids the fixes touch, re-verify only those, then correct the artifacts. What
was missing is the TRIGGER that makes it mandatory — the rule, not the mechanism.

WHAT IS DECIDABLE HERE, AND WHAT IS NOT. The pipeline does not know which production file belongs to
which phase, and inventing that mapping would be a rule whose remedy nobody could apply. So this
check anchors on the one moment where the question has a single answer: the NEWEST verdict in the
feature. Every commit after it is post-verification by construction — the e2e author, a ship-gate fix
pass, a stray edit — because a phase's own implementation commits always precede its own verdict.
Before that anchor there is nothing to decide: an implementer changing source while an earlier
phase's verdict stands is ordinary phase work, and holding it would block the pipeline's normal
operation.

Two path classes are excluded, each for a stated reason:

* `docs/` — the pipeline's own artifacts. The handover, the ledgers and the verdict itself land after
  verification by design, and holding a phase for writing its own card would be a wedge.
* the feature's own `tests/e2e/<feature>/` — written once after the final phase is green, by design
  (CLAUDE.md §4b), and deliberately excluded from the phase verifier.

**A verdict not yet committed anchors on the HEAD its evidence was recorded at** (issue #97, folded
#122). The anchor above only exists once `verdict.json` has LANDED, so at phase close - the handover,
where the verdict is on disk and not yet in any commit - this check used to answer "no committed
verdict to anchor on" and step aside, which is the one moment the folded instance happened in: a fix
round committed and moved the head while a verification was open, and only a human reading two
heads side by side saw it. `verifier_evidence.py` now records `head` on every run, so an uncommitted
verdict anchors on the newest recorded head of the phases carrying one, and `hook_verifier.sh` asks
this at the handover. A committed verdict keeps the commit it landed in as its anchor, and
deliberately not its evidence head: the phase's own commit lands AFTER its verification and carries
the verified source, so reading it as post-verification would owe every phase an amendment for
having been committed.

The two are RANKED by commit time rather than tried in order. Preferring any committed verdict made
the recorded head reachable in phase 1 alone - from phase 2 onward a feature always carries an
earlier committed verdict, so a phase closing at its own handover anchored on its PREDECESSOR and
every one of its own implementation commits read as post-verification change. Taking the later of
the two leaves a closed phase anchored exactly where it was: a verdict's landing commit is always
newer than the head its own evidence recorded.

**It fails OPEN on anything git cannot answer**, exactly as `applicability.changed_paths` does: no
git, not a repository, no verdict to anchor on - committed or recorded. An unknowable scope enforces
nothing and says so on stderr rather than passing invisibly.

    verdict_currency.py check <feature-dir>     exit 0 current · 1 an amendment is owed · 2 usage
"""

from __future__ import annotations

import subprocess  # noqa: S404 — git is the only thing that can answer "changed since"
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402
import verifier_evidence  # noqa: E402 - the record that names which head a run stood on

CURRENT = 0
OWED = 1
ERROR = 2

PREFIX = "[verdict_currency]"

#: Never counted as a post-verification change. See the module docstring for why each is here.
IGNORED_PREFIXES = ("docs/",)


def _git(root: Path, *args: str) -> str | None:
    """git output, or None when git cannot answer — no binary, not a repository, a failed call."""
    try:
        result = subprocess.run(  # noqa: S603 — fixed executable, list argv, no shell
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def anchor(root: Path, feature_dir: Path) -> str | None:
    """The NEWEST moment this feature was verified at, or None when unknowable.

    `git log -1` over every phase's verdict at once: the newest of them is the moment after which a
    source change is post-verification. A feature with no committed verdict has nothing to be stale
    against, which is not a finding — it is a feature that has not been verified yet.

    **The two anchors are RANKED, never ordered by which exists.** Reading the committed verdict
    first and the recorded head only in its absence makes the recorded head reachable in phase 1
    alone: from phase 2 onward the feature always carries an earlier committed verdict, so a phase
    closing at its own handover anchored on its PREDECESSOR's verdict commit and `changed_since`
    swept up every one of its own implementation commits. That is ordinary phase work — the state
    `test_ordinary_phase_work_before_the_newest_verdict_is_not_held` pins for the committed case —
    reported as a stale verdict, with a remedy (an amendment naming the ids the change touched)
    scoped to the wrong phase. So the LATER of the two wins. The stated intent survives unchanged: a
    committed verdict's own landing commit always comes after the head its evidence recorded, so a
    closed phase still anchors on its verdict commit.

    Later is asked as ANCESTRY (`git merge-base --is-ancestor`), not as commit time: both anchors
    are commits on the history under HEAD, and a phase's commits routinely share a timestamp to the
    second, where a `>` on `%ct` silently keeps the wrong one. Two commits on neither's ancestry
    cannot be ranked at all — a recorded head from another branch — and that keeps the committed
    verdict, the answer this check has always given, said out loud rather than resolved silently.
    """
    try:
        rel = feature_dir.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None
    out = _git(root, "log", "-1", "--format=%H", "--", f"{rel}/phases/*/verdict.json")
    committed = (out.strip() or None) if out is not None else None
    recorded = recorded_anchor(root, feature_dir)
    if committed is None:
        return recorded
    if recorded is None or recorded == committed:
        return committed
    if _is_ancestor(root, committed, recorded):
        return recorded
    if not _is_ancestor(root, recorded, committed):
        print(
            f"{PREFIX} {Path(feature_dir).name}: the newest committed verdict ({committed[:8]}) "
            f"and the newest recorded head ({recorded[:8]}) are on neither's ancestry, so which is "
            f"LATER cannot be stated - anchoring on the committed verdict, which is what this "
            f"check has always done",
            file=sys.stderr,
        )
    return committed


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    """Whether `earlier` is reachable from `later`. False for unrelated commits and for git failing.

    `_git` returns None on a non-zero exit, which `--is-ancestor` also uses for its NO — so both
    arrive here as False, and the caller treats "not an ancestor" and "git could not say" the same
    way on purpose: it falls back to the committed verdict either way.
    """
    return _git(root, "merge-base", "--is-ancestor", earlier, later) is not None


def recorded_anchor(root: Path, feature_dir: Path) -> str | None:
    """The newest head recorded by the evidence of a phase whose verdict is on disk, or None.

    Only phases that HAVE a `verdict.json` are read - a run recorded for a phase still being
    verified anchors nothing, because there is no verdict yet for the tree to have moved past. A
    record this cannot read is skipped and named: an unreadable transcript is `verifier_evidence
    check`'s finding, not a reason for this check to invent an anchor or to crash.
    """
    best: tuple[int, str] | None = None
    for verdict in sorted(Path(feature_dir).glob("phases/*/verdict.json")):
        phase = verdict.parent
        try:
            head = verifier_evidence.latest_head(phase)
        except verifier_evidence.EvidenceError as exc:
            print(
                f"{PREFIX} {phase.name}: its evidence record could not be read ({exc}) - no "
                f"recorded head taken from it",
                file=sys.stderr,
            )
            continue
        if head is None:
            continue
        rank = _commit_time(root, head)
        if rank is None:
            print(
                f"{PREFIX} {phase.name}: recorded head {head[:8]} is not a commit this repository "
                f"knows - not used as an anchor",
                file=sys.stderr,
            )
            continue
        if best is None or rank > best[0]:
            best = (rank, head)
    return best[1] if best else None


def _commit_time(root: Path, commit: str) -> int | None:
    """When `commit` was made, for ranking recorded heads; None when git does not know it."""
    out = _git(root, "log", "-1", "--format=%ct", commit, "--")
    try:
        return int(out.strip()) if out and out.strip() else None
    except ValueError:
        return None


def _e2e_prefix(root: Path, feature: str) -> str:
    """Where this feature's own e2e suite lives, relative to the repository root."""
    return f"tests/e2e/{feature}/"


def changed_since(root: Path, commit: str, feature: str) -> list[str] | None:
    """Tracked paths a commit after `commit` changed, excluding the classes named above.

    None when git cannot say — the same fail-open every other check on this boundary uses.
    """
    out = _git(root, "diff", "--name-only", f"{commit}..HEAD")
    if out is None:
        return None
    ignored = (*IGNORED_PREFIXES, _e2e_prefix(root, feature))
    return sorted(
        path
        for path in (line.strip() for line in out.splitlines())
        if path and not path.startswith(ignored)
    )


def amended_since(root: Path, commit: str, feature_dir: Path) -> bool:
    """Whether any phase in the feature recorded an amendment after `commit`.

    The ledger is the artifact the remedy produces, so its presence in a later commit — or as an
    uncommitted change, since the resolver runs against a working tree — IS the discharge. What the
    amendment then owes is `amendments.py due`'s question, not this one: this check asks only
    whether the correction was opened at all.
    """
    try:
        rel = feature_dir.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return False
    pattern = f"{rel}/phases/*/amendments.json"
    logged = _git(root, "log", "--format=%H", f"{commit}..HEAD", "--", pattern)
    if logged and logged.strip():
        return True
    status = _git(root, "status", "--porcelain", "--", pattern)
    return bool(status and status.strip())


def check(root: Path, feature_dir: Path) -> str | None:
    """The finding, or None when every passing verdict still describes the tree it judged."""
    feature = Path(feature_dir).name
    commit = anchor(root, feature_dir)
    if commit is None:
        print(
            f"{PREFIX} {feature}: no committed verdict and no recorded head to anchor on (or git "
            f"cannot say) — verdict currency NOT checked",
            file=sys.stderr,
        )
        return None
    changed = changed_since(root, commit, feature)
    if changed is None:
        print(
            f"{PREFIX} {feature}: git could not say what changed since {commit[:8]} — verdict "
            f"currency NOT checked",
            file=sys.stderr,
        )
        return None
    if not changed:
        return None
    if amended_since(root, commit, feature_dir):
        return None
    shown = ", ".join(changed[:5]) + (
        f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
    )
    return (
        f"{feature}: {len(changed)} tracked file(s) changed after the newest verdict landed "
        f"({commit[:8]}), and no phase in this feature has recorded an amendment: {shown}. The "
        f"verdict describes a tree that no longer exists. Do NOT rewrite verdict.json — that would "
        f"restate a verification nobody performed. Open an amendment naming the requirement ids the "
        f"change touches (scripts/amendments.py open <phase-dir> --id <A1> --requirements <ids> "
        f"--reason-file <f>), re-verify only those, then correct the artifacts."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "check":
        print(__doc__, file=sys.stderr)
        return ERROR
    feature_dir = Path(args[1])
    if not feature_dir.is_dir():
        print(f"{PREFIX} no such feature directory: {feature_dir}", file=sys.stderr)
        return ERROR
    root = feature_dir.resolve().parents[2]
    finding = check(root, feature_dir)
    if finding is None:
        return CURRENT
    print(f"{PREFIX} {finding}", file=sys.stderr)
    return OWED


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
