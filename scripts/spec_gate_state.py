#!/usr/bin/env python3
"""Where a spec stands with the one gate - read in exactly one place.

The pipeline used to run two MODEL gates over the same document at the same moment - the Fidelity
Gate and the automated half of spec-review - and a spec carried a stamp from each:
`fidelity_verdict: GO|REVIEW|NO-GO` and `review_status: pending|approved`. They asked overlapping
questions, and one spec passed one and failed the other on byte-identical text. They are now **one
gate** (`scripts/hook_spec_gate.sh`) with **one stamp**:

    spec_gate: pending | approved | blocked

`review_status` survives, and means only what it now is: a **human** sign-off from the `/spec-review`
grill. No model writes it except in unattended mode, where the automated gate is the whole wall by
design. One machine gate plus one human is not two rubrics; two rubrics was the defect.

This module is the only thing that reads the machine stamp. Every caller - `pipeline_state.py`
resolving the next stage, `gate_ci.sh` auditing committed specs, the hook deciding whether to
re-gate - asks here. A status rule copied into three callers is a rule that drifts in two of them,
and the recurrence this whole overhaul is about was guards attached to individual commands instead
of to the invariant.

**Legacy specs still read correctly.** A repository full of specs written before the collapse
carries `fidelity_verdict` and no `spec_gate:` line. Rather than migrating files (which would
rewrite bodies the gate cache is keyed on), the old stamp is *derived* here, once:

    blocked   <- fidelity_verdict: NO-GO
    approved  <- fidelity_verdict: GO | REVIEW | PASS
    pending   <- anything else: `fidelity_verdict: pending`, a value nobody can read, and a spec that
                 carries neither stamp

The bias is the same as everywhere else in the resolver: when a stamp is missing or unreadable, the
spec is **pending**, because the cost of re-gating is one call and the cost of skipping is an
ungated spec reaching the implementer.

It also **writes** the stamp, for the same reason it reads it. The hook had two spellings — a `sed`
for the approved case and an inline Python heredoc for the missing-key case — and only one of them
could insert a key that was not there, so a blocked spec whose frontmatter lacked `spec_gate:` was
left reading `pending`. One writer, one spelling.

Usage:
    spec_gate_state.py status <spec.md>    print pending|approved|blocked; exit 0 approved,
                                           1 not approved, 2 unreadable
    spec_gate_state.py set <spec.md> <pending|approved|blocked>
                                           stamp it, inserting the key when it is absent
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APPROVED_EXIT = 0
NOT_APPROVED_EXIT = 1
ERROR = 2

PENDING = "pending"
APPROVED = "approved"
BLOCKED = "blocked"

STATES = (PENDING, APPROVED, BLOCKED)

#: The single stamp the one gate writes.
GATE_FIELD = "spec_gate"

#: The model stamp it replaced. Read, never written.
LEGACY_FIDELITY = "fidelity_verdict"

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)


def frontmatter(text: str) -> dict[str, str]:
    """Flat key/value view of a spec's YAML frontmatter; empty when there is none."""
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.split("#")[0].strip()
    return fields


def status(fields: dict[str, str]) -> str:
    """The spec's gate status, from the current stamp or derived from the legacy pair."""
    current = fields.get(GATE_FIELD, "").strip().lower()
    if current in STATES:
        return current
    if current:
        # A stamp nobody can read is not an approval. Under-report, as everywhere else.
        return PENDING
    fidelity = fields.get(LEGACY_FIDELITY, "").strip().upper()
    if fidelity == "NO-GO":
        return BLOCKED
    if fidelity in ("GO", "REVIEW", "PASS"):
        return APPROVED
    return PENDING


def status_of(path: Path) -> str:
    """The gate status of a spec on disk. An unreadable spec is `pending`, never approved."""
    try:
        return status(frontmatter(path.read_text(encoding="utf-8")))
    except OSError:
        return PENDING


GATE_LINE = re.compile(rf"^{GATE_FIELD}:.*$", re.MULTILINE)


def stamped(text: str, state: str) -> str:
    """The spec with `spec_gate:` set to `state`, inserting the key when it is absent."""
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("no YAML frontmatter")
    block = match.group(1)
    line = f"{GATE_FIELD}: {state}"
    block = GATE_LINE.sub(line, block) if GATE_LINE.search(block) else f"{block}\n{line}"
    return f"---\n{block}\n---" + text[match.end() :]


def set_status(path: Path, state: str) -> None:
    """Stamp a spec on disk. Raises ValueError on an unknown state or missing frontmatter."""
    if state not in STATES:
        raise ValueError(f"{state!r} is not one of {', '.join(STATES)}")
    path.write_text(stamped(path.read_text(encoding="utf-8"), state), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "set":
        try:
            set_status(Path(args[1]), args[2])
        except (OSError, ValueError) as exc:
            print(f"[spec_gate_state] cannot stamp {args[1]}: {exc}", file=sys.stderr)
            return ERROR
        return APPROVED_EXIT
    if len(args) != 2 or args[0] != "status":
        print(__doc__, file=sys.stderr)
        return ERROR
    path = Path(args[1])
    if not path.is_file():
        print(f"[spec_gate_state] no such spec: {path}", file=sys.stderr)
        return ERROR
    state = status_of(path)
    print(state)
    return APPROVED_EXIT if state == APPROVED else NOT_APPROVED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
