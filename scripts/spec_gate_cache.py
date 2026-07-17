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

Usage:
    spec_gate_cache.py check <spec.md> <gate>   exit 0 = needs gating, 1 = unchanged since last run
    spec_gate_cache.py stamp <spec.md> <gate>   record the current body hash for that gate
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

NEEDS_GATING = 0
UNCHANGED = 1
ERROR = 2

GATES = ("fidelity", "review")

FRONTMATTER = re.compile(r"^---\n(.*?\n)---\n(.*)\Z", re.DOTALL)


def hash_line(gate: str) -> re.Pattern[str]:
    """Matcher for one gate's recorded hash. Per-gate so gates never skip each other."""
    return re.compile(
        rf"^{gate}_gated_hash:[ \t]*([0-9a-f]{{64}})[ \t]*$", re.MULTILINE
    )


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


def stamp(text: str, gate: str) -> str:
    """Return the spec with `<gate>_gated_hash` set to the current body hash (added or replaced)."""
    frontmatter, body = split_spec(text)
    digest = body_hash(body)
    line = f"{gate}_gated_hash: {digest}"
    if hash_line(gate).search(frontmatter):
        frontmatter = hash_line(gate).sub(line, frontmatter)
    else:
        frontmatter = frontmatter.rstrip("\n") + f"\n{line}\n"
    return f"---\n{frontmatter}---\n{body}"


def main(argv: list[str] | None = None) -> int:
    """Dispatch `check` / `stamp` for one spec path and gate."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] not in {"check", "stamp"} or args[2] not in GATES:
        print(__doc__, file=sys.stderr)
        return ERROR

    action, path, gate = args[0], Path(args[1]), args[2]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[spec_gate_cache] cannot read {path}: {exc}", file=sys.stderr)
        return ERROR

    try:
        if action == "check":
            return NEEDS_GATING if needs_gating(text, gate) else UNCHANGED
        path.write_text(stamp(text, gate), encoding="utf-8")
        return NEEDS_GATING
    except ValueError as exc:
        # No frontmatter -> cannot reason about it -> gate it.
        print(f"[spec_gate_cache] {path}: {exc} — gating anyway", file=sys.stderr)
        return NEEDS_GATING
    except OSError as exc:
        print(f"[spec_gate_cache] cannot write {path}: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    sys.exit(main())
