#!/usr/bin/env python3
"""Decide whether a spec still needs gating, by hashing its body.

Both spec gates fire on every `spec.md` write. Without this, marking a spec `status: done` — a
frontmatter-only edit made by the implementer, long after the spec was approved — re-runs the
fidelity gate and the automated spec-review. That costs a paid model call each, and worse, the
fidelity gate re-stamps `fidelity_verdict` from a fresh nondeterministic call: an approved spec can
flip to NO-GO because a word landed differently the second time.

The gates judge the spec's *content*. Frontmatter carries verdicts and status — the gates' own
output plus workflow bookkeeping — so a change confined to it cannot change a verdict that was
honestly reached. This hashes the body (everything after the closing `---`) and records it in the
frontmatter as `gated_hash`. Body unchanged -> the stored verdict still stands, skip. Body changed
-> re-gate, which is exactly right: the spec is no longer the one that was approved.

Each gate gets its OWN key (`<gate>_gated_hash`). A shared key would mean the first gate to stamp it
makes every later gate skip — the composed quality wall would collapse to whichever gate ran first.

Fails toward gating: any parse problem, missing hash, or unreadable file means "gate it". A gate
that skips when unsure is a gate that does not exist.

**`stamp` also keeps the body itself**, not just its digest, so a later re-gate can be scoped to what
actually changed. Re-judging a whole spec that was already approved and built is how one spec drew
three different verdicts from the same model on the same rubric — the third a NO-GO over text the
first two had passed — and a verdict is a sample from a distribution, not a fact about the artifact.
The digest answers "did it change"; the body answers "what changed", and only the second lets the
re-gate stay inside the diff. Git cannot stand in for this: a spec is typically approved, implemented
and re-gated well before the phase's commit, so there is no committed predecessor to diff against.

The kept bodies are a rebuildable cache, not an artifact: losing one costs a full re-gate, which is
the safe direction. `.avenger-gate-cache/` is gitignored for that reason.

**The hash is recorded WITH ITS VERDICT.** Only GO and REVIEW used to stamp, so a NO-GO left no
trace of having been reached: the spec kept whatever hash it had, and the rejection existed only as
one line of stderr that the run had already scrolled past. An unexplained rejection either stalls
the phase or triggers blind rewriting — one 25k spec reached 51k that way. `<gate>_gated_verdict`
now sits next to `<gate>_gated_hash` in the frontmatter, so `check` can say what the stored verdict
WAS rather than assuming a stored hash meant a pass. A stamp with no recorded verdict predates this
and is read as GO, which is what stamping used to mean.

The verdict lives in the frontmatter, not in the cache, on purpose: the cache is rebuildable scratch
and losing it must not turn a recorded NO-GO into an assumed pass. The gate's *report* is kept in the
cache alongside the body — losing that only costs a less informative replay.

A rejection records its hash, its verdict and its report, but does NOT replace the kept **body**.
That body is the reference the next re-gate diffs against under the heading "PREVIOUSLY APPROVED",
and rejected text is not approved text: overwriting it would show the author changes-since-rejection
while telling them they were changes-since-approval.

Usage:
    spec_gate_cache.py check <spec.md> <gate>      exit 0 = needs gating, 1 = unchanged (prints the
                                                   stored verdict on stdout)
    spec_gate_cache.py stamp <spec.md> <gate> [verdict] [report-file]
                                                   record the current body hash + verdict + body
    spec_gate_cache.py previous <spec.md> <gate>   print the body that gate last judged (1 = none kept)
    spec_gate_cache.py report <spec.md> <gate>     print the report that gate last emitted (1 = none)
    spec_gate_cache.py body <spec.md>              print the current body, for diffing against it
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

NEEDS_GATING = 0
UNCHANGED = 1
ERROR = 2

GATES = ("fidelity", "review")

ACTIONS = ("check", "stamp", "previous", "body", "report")

#: What a stamp that recorded no verdict meant. Before verdicts were recorded, only GO and REVIEW
#: stamped at all, so a bare hash is a pass — stated once here rather than inferred at each caller.
LEGACY_VERDICT = "GO"

#: Verdicts that make the judged body the new reference for "what this gate last approved".
PASSING = {"GO", "REVIEW", "PASS"}

CACHE_DIR = ".avenger-gate-cache"

FRONTMATTER = re.compile(r"^---\n(.*?\n)---\n(.*)\Z", re.DOTALL)


def hash_line(gate: str) -> re.Pattern[str]:
    """Matcher for one gate's recorded hash. Per-gate so gates never skip each other."""
    return re.compile(
        rf"^{gate}_gated_hash:[ \t]*([0-9a-f]{{64}})[ \t]*$", re.MULTILINE
    )


def verdict_line(gate: str) -> re.Pattern[str]:
    """Matcher for the verdict recorded alongside that gate's hash."""
    return re.compile(rf"^{gate}_gated_verdict:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def split_spec(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Raises ValueError when there is no YAML frontmatter."""
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("no YAML frontmatter")
    return match.group(1), match.group(2)


def body_hash(body: str) -> str:
    """Stable hash of the spec's content. Trailing whitespace is not a content change."""
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def stored_hash(frontmatter: str, gate: str) -> str | None:
    """The hash recorded by that gate's last successful run, if any."""
    match = hash_line(gate).search(frontmatter)
    return match.group(1) if match else None


def needs_gating(text: str, gate: str) -> bool:
    """True when the body differs from the one this gate last judged (or it never ran)."""
    frontmatter, body = split_spec(text)
    previous = stored_hash(frontmatter, gate)
    return previous is None or previous != body_hash(body)


def stored_verdict(frontmatter: str, gate: str) -> str:
    """The verdict recorded with that gate's hash, or the legacy meaning of a bare hash."""
    match = verdict_line(gate).search(frontmatter)
    return match.group(1).strip().upper() if match else LEGACY_VERDICT


def _set_line(frontmatter: str, pattern: re.Pattern[str], line: str) -> str:
    """Replace one frontmatter line, or append it when the key is not there yet."""
    if pattern.search(frontmatter):
        return pattern.sub(line, frontmatter)
    return frontmatter.rstrip("\n") + f"\n{line}\n"


def stamp(text: str, gate: str, verdict: str = LEGACY_VERDICT) -> str:
    """Return the spec with this gate's hash AND the verdict it was reached with, recorded.

    Both keys move together: a hash without its verdict is what let a NO-GO leave no trace, and a
    verdict without its hash could not be tied to the text it judged.
    """
    frontmatter, body = split_spec(text)
    digest = body_hash(body)
    token = (verdict or LEGACY_VERDICT).strip().upper()
    frontmatter = _set_line(frontmatter, hash_line(gate), f"{gate}_gated_hash: {digest}")
    frontmatter = _set_line(frontmatter, verdict_line(gate), f"{gate}_gated_verdict: {token}")
    return f"---\n{frontmatter}---\n{body}"


def cache_root() -> Path:
    """Where kept bodies live. The project dir when a hook supplies one, else the working dir."""
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())


def cache_path(spec: Path, gate: str, kind: str = "md") -> Path:
    """The kept-body (`md`) or kept-report (`report`) file for one spec and gate.

    Keyed by a digest of the resolved spec path so sibling specs never collide and the layout stays
    flat — the cache is scratch, and nothing reads it by browsing.
    """
    key = hashlib.sha256(str(spec.resolve()).encode("utf-8")).hexdigest()[:16]
    suffix = "md" if kind == "md" else "report.md"
    return cache_root() / CACHE_DIR / gate / f"{key}.{suffix}"


def normalized(body: str) -> str:
    """A body ending in exactly one newline, so a re-gate diff shows only real edits.

    `previous` and `body` are diffed against each other by the hook, and a shell reads one of them
    through a command substitution, which eats every trailing newline. Without a single shared
    convention the two sides disagree about the end of the file and every re-gate opens with a
    phantom hunk — noise handed to the reviewer, which is the exact failure the diff exists to
    prevent. Normalising in one place beats each caller remembering to.
    """
    return body.rstrip("\n") + "\n"


def keep(spec: Path, gate: str, body: str, kind: str = "md") -> None:
    """Store what a gate just judged (or said). Best-effort: a miss only costs a fuller re-gate."""
    target = cache_path(spec, gate, kind)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalized(body), encoding="utf-8")
    except OSError as exc:
        print(f"[spec_gate_cache] could not keep {target}: {exc}", file=sys.stderr)


def previous(spec: Path, gate: str, kind: str = "md") -> str | None:
    """The body (or report) that gate last produced, or None when nothing was kept."""
    try:
        return normalized(cache_path(spec, gate, kind).read_text(encoding="utf-8"))
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    """Dispatch `check` / `stamp` / `previous` / `body` for one spec path and gate."""
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in ACTIONS:
        print(__doc__, file=sys.stderr)
        return ERROR

    # `body` is gate-agnostic — it reads the spec, not a gate's record of it.
    # `stamp` takes an optional verdict and an optional file holding that verdict's report.
    wanted = 2 if args[0] == "body" else 3
    ok_len = len(args) in ((wanted, wanted + 1, wanted + 2) if args[0] == "stamp" else (wanted,))
    if not ok_len or (wanted == 3 and args[2] not in GATES):
        print(__doc__, file=sys.stderr)
        return ERROR

    action, path = args[0], Path(args[1])
    gate = args[2] if wanted == 3 else ""
    verdict = args[3] if action == "stamp" and len(args) > 3 else LEGACY_VERDICT
    report_file = args[4] if action == "stamp" and len(args) > 4 else ""

    if action in ("previous", "report"):
        kept = previous(path, gate, "md" if action == "previous" else "report")
        if kept is None:
            return UNCHANGED  # nothing kept -> the caller gates the whole spec / has no report
        sys.stdout.write(kept)
        return NEEDS_GATING

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[spec_gate_cache] cannot read {path}: {exc}", file=sys.stderr)
        return ERROR

    try:
        if action == "body":
            sys.stdout.write(normalized(split_spec(text)[1]))
            return NEEDS_GATING
        if action == "check":
            if needs_gating(text, gate):
                return NEEDS_GATING
            # Unchanged: hand the caller the verdict this body already earned, so a recorded NO-GO
            # is replayed rather than skipped past. Silence here would be a fail-open.
            print(stored_verdict(split_spec(text)[0], gate))
            return UNCHANGED
        path.write_text(stamp(text, gate, verdict), encoding="utf-8")
        # The kept BODY is the reference the next re-gate diffs against, under the heading
        # "PREVIOUSLY APPROVED". A rejection is not an approval, so it records its hash and its
        # verdict but leaves that reference alone: overwriting it would label the rejected text as
        # approved, and the author would be shown changes-since-rejection while being told they were
        # changes-since-approval. With nothing kept at all, the whole spec is gated — safe either way.
        if (verdict or LEGACY_VERDICT).strip().upper() in PASSING:
            keep(path, gate, split_spec(text)[1])
        if report_file:
            try:
                keep(path, gate, Path(report_file).read_text(encoding="utf-8"), "report")
            except OSError as exc:
                print(f"[spec_gate_cache] could not read report {report_file}: {exc}",
                      file=sys.stderr)
        return NEEDS_GATING
    except ValueError as exc:
        # No frontmatter -> cannot reason about it -> gate it. `body` has nothing to hand back, so
        # it errors instead, and its caller falls back to gating the whole spec.
        print(f"[spec_gate_cache] {path}: {exc} — gating anyway", file=sys.stderr)
        return ERROR if action == "body" else NEEDS_GATING
    except OSError as exc:
        print(f"[spec_gate_cache] cannot write {path}: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    sys.exit(main())
