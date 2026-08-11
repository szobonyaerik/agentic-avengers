#!/usr/bin/env python3
"""Which skills each stage REQUIRES - and the evidence that it actually got them.

The pipeline delegates its core behaviour to thirteen skills, and until now it delegated by *asking*:
every implementer prompt says "Load `skills/tdd` before you start". That is an instruction, not a
mechanism. Nothing checked, nothing recorded, and a stage that skipped a required skill fell back
silently to whatever the model already believed - which is the failure mode the whole pipeline exists
to remove from everything else. `skills/ponytail` is the only one genuinely injected, and
`docs/lessons/` shipped with a complete written procedure and **zero invocations** for the same
reason: a directive in a skill reaches only the agents that load that skill.

## Delivery: POINTER PLUS EVIDENCED LOAD, not blanket injection

Injecting every required skill's whole body is one way to **guarantee** the load, and it was the
first shape of this. It is also expensive in exactly the way the read path taught: every row of the
table below requires `pipeline-conventions`, the largest file in `skills/`, and inlining it on every
`avenger-*` spawn is the same order of cost the read-path work had just removed from `handover.md`.

The `skill_loads` record this module already builds is a cheaper way to **detect** a failure to load,
and a required skill with no recorded load is a loud blocker that stops the phase anyway.
**Detection at roughly a million tokens saved per feature beats prevention at roughly a million
spent, when both end the same way.** So delivery is decided by size:

- **At or under `SKILL_INJECT_MAX_BYTES` (default 8192): injected in full.** For these the injection
  IS the load, and it is recorded `loaded: true` at injection. Small enough that injecting costs less
  than the risk.
- **Over it: a POINTER, and a pointer is not a suggestion.** The stage is handed the skill's path,
  size and one-line description, told the load is REQUIRED, and told the command that records it. A
  pointer with no matching load recorded is a **blocking** audit failure at handover and in CI - a
  bare pointer with nothing checking it would be the instruction-not-mechanism failure this exists to
  fix, one layer up.

The saving is a **PREDICTION, not an achievement**: declared as **H9** in the metrics ledger before
this landed - roughly 1M tokens saved per 8-phase feature with zero unrecorded required loads -
and settled in phase 9, not here.

`.avenger-skill-loads.jsonl` (gitignored scratch) is the evidence, one JSON record per line:

    {"at", "event": "delivery"|"load", "agent_type", "agent_id", "session_id", "skill",
     "required": true, "delivery": "inject"|"pointer", "loaded": bool, "bytes", "path"}

`agent_id` is a SPAWN id and identifies who owes a load; `session_id` is a RUN id and scopes which
run an audit covers. They are separate fields and neither substitutes for the other.

**A required skill that is missing or unreadable is a LOUD BLOCKER**, not a silent fallback. The hook
says so in the injected context and records `loaded: false`. The one thing it must never do is let
the stage proceed believing it has rules it does not have.

The table is keyed by an unanchored, case-insensitive regex over `agent_type`, so plugin-scoped names
like `plan-build-verify:avenger-verifier` match. Order matters only for readability; the first
matching entry wins, so the more specific patterns come first.

Reach is scoped deliberately, and differently per skill for the same reason `hook_ponytail.sh`
excludes the Verifier and `hook_lessons.sh` does not: a skill that fights a stage's job must not
reach it.

Usage:
    required_skills.py for <agent-type>          print the skill dirs that stage requires
    required_skills.py table                     print the whole table, with each skill's delivery
    required_skills.py verify [--root .]         every required skill exists and is readable
                                                 (exit 1 when one does not)
    required_skills.py record <agent-type> <skill> [--agent-id ID] [--session-id ID] [--log PATH]
                                                 evidence that a POINTER skill was actually loaded
    required_skills.py audit --session <id>      exit 1 when a spawn in THAT RUN was handed a
                                                 pointer for a required skill and never recorded
                                                 loading it
    required_skills.py audit --all               the same over every delivery ever recorded
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

OK = 0
MISSING = 1
ERROR = 2

#: At or under this, a required skill is injected whole; over it, it is a pointer the stage must load
#: and record. 8192 bytes is the line at which injecting costs less than the risk of a missed load.
DEFAULT_INJECT_MAX_BYTES = 8192

INJECT = "inject"
POINTER = "pointer"

#: (agent_type pattern, required skill directory names). Every pipeline agent gets
#: `pipeline-conventions` - it is the shared rulebook, and an agent that has not read it is an agent
#: guessing at phases, gates and the ID scheme.
REQUIRED: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Implementers write both the tests and the code, so the TDD procedure is not optional for them.
    (
        r"avenger-(backend-architect|frontend-developer)",
        ("pipeline-conventions", "tdd", "self-improvement"),
    ),
    # The Verifier's triage procedure and verdict schema. `tdd` too, because the anti-patterns it
    # reads a green suite for are defined there and nowhere else.
    (r"avenger-verifier", ("pipeline-conventions", "verifier-triage", "tdd", "self-improvement")),
    (r"avenger-spec-writer", ("pipeline-conventions", "spec-review-checklist", "self-improvement")),
    (r"avenger-(breaker|bug-hunter)", ("pipeline-conventions", "self-improvement")),
    (r"avenger-handover", ("pipeline-conventions", "phase-handover", "self-improvement")),
    (
        r"avenger-(task-analyst|solution-architect|implementation-planner)",
        ("pipeline-conventions", "codemap", "self-improvement"),
    ),
    # Anything else in the family: the rulebook and the lessons procedure, nothing more.
    (r"avenger-", ("pipeline-conventions", "self-improvement")),
)


def required_for(agent_type: str) -> tuple[str, ...]:
    """The skills that stage must have. Empty for an agent this pipeline does not own."""
    name = (agent_type or "").strip()
    if not name:
        return ()
    for pattern, skills in REQUIRED:
        try:
            if re.search(pattern, name, re.IGNORECASE):
                return skills
        except re.error:  # a broken pattern must not inject into every subagent
            return ()
    return ()


def skill_path(root: Path, skill: str) -> Path:
    return Path(root) / "skills" / skill / "SKILL.md"


def inject_max_bytes() -> int:
    """The injection ceiling in force. A value that does not parse is a hard error, never a guess.

    Same rule as the requirement cap and `GATE_CALL_TIMEOUT`: a budget believed rather than checked
    is the defect. Guessing the default here would silently change every stage's delivery mode.
    """
    raw = os.environ.get("SKILL_INJECT_MAX_BYTES", "").strip()
    if not raw:
        return DEFAULT_INJECT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"SKILL_INJECT_MAX_BYTES={raw!r} is not a whole number of bytes. It decides whether a "
            f"required skill is injected or pointed at, so it is not guessed."
        ) from None
    if value < 0:
        raise ValueError(f"SKILL_INJECT_MAX_BYTES={raw!r} cannot be negative.")
    return value


def delivery_for(size: int, limit: int | None = None) -> str:
    """`inject` for a body at or under the ceiling, `pointer` above it."""
    return INJECT if size <= (inject_max_bytes() if limit is None else limit) else POINTER


def load_record(
    agent_type: str,
    skill: str,
    *,
    event: str,
    delivery: str,
    loaded: bool,
    size: int = 0,
    path: str = "",
    agent_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """One evidence line. The shape is shared by the hook and by `record`, so audit reads one shape.

    `agent_id` is a SPAWN id and `session_id` is a RUN id, kept apart on purpose - the same
    separation `scripts/hook_activity.sh` keeps, for the same reason. A session id substituted for a
    spawn id would make every delivery in one run share a key, which is precisely the looseness
    `audit_gaps` refuses. The spawn key identifies WHO owes the load; the session key scopes WHICH
    RUN is being audited.
    """
    return {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "agent_type": agent_type,
        "agent_id": agent_id,
        "session_id": session_id,
        "skill": skill,
        "required": True,
        "delivery": delivery,
        "loaded": loaded,
        "bytes": size,
        "path": path,
    }


def default_log() -> Path:
    """Where the evidence goes. The hook and `record` must agree, so the rule lives here."""
    configured = os.environ.get("SKILL_LOAD_LOG", "").strip()
    if configured:
        return Path(configured)
    root = os.environ.get("CLAUDE_PROJECT_DIR", "").strip() or "."
    return Path(root) / ".avenger-skill-loads.jsonl"


def append_record(log: Path, record: dict) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_records(log: Path) -> list[dict]:
    """Every evidence line. A line nobody can parse is an ERROR, never a line quietly skipped."""
    records: list[dict] = []
    for number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{log}:{number} is not readable JSON ({exc})") from None
        if not isinstance(parsed, dict):
            raise ValueError(f"{log}:{number} is not a record object")
        records.append(parsed)
    return records


def is_delivery(record: dict) -> bool:
    """A required-skill delivery. Records predating the shape carry no `event` and are deliveries."""
    return record.get("event", "delivery") == "delivery" and bool(record.get("required"))


def record_command(record: dict, log: Path | None = None) -> str:
    """The exact `record` invocation that clears THIS delivery's gap.

    Every match key the audit uses has to appear here, or the printed remedy cannot satisfy the gate
    that printed it. A load recorded without the delivery's `--session-id` keys as a different run,
    so the gap never clears however many times the operator follows the instruction - and the only
    ways out are `GATE_BYPASS` or hand-editing a gitignored log, which is how a break-glass stops
    meaning what it says.
    """
    parts = ["required_skills.py record", str(record.get("agent_type") or "<agent-type>"),
             str(record.get("skill") or "<skill>")]
    if record.get("agent_id"):
        parts.append(f"--agent-id {record['agent_id']}")
    if record.get("session_id"):
        parts.append(f"--session-id {record['session_id']}")
    if log is not None:
        parts.append(f"--log {log}")
    return " ".join(parts)


def scope_to_run(records: list[dict], session: str) -> tuple[list[dict], str, str | None]:
    """(records to audit, how the scope resolved, a loud note when it could not be applied).

    **An empty scope is not a clean scope.** Filtering to `session_id == session` and auditing the
    empty result reported "every delivered skill has evidence of a load — 0 record(s)" over a log
    holding unrecorded pointers: a check reporting coverage it never had, which is the one failure
    this whole mechanism exists not to reproduce, and it is the instrument that settles H9.

    The asymmetry that caused it is real and stays: `hook_skills.sh` reads `session_id` from a
    `SubagentStart` payload that may not carry one, while `hook_verifier.sh` reads it from a
    `PostToolUse` payload that does. So the scope is resolved by what the DELIVERIES actually carry:

      * some delivery carries this session   -> scope applies; other runs are counted, not enforced.
      * every delivery carries SOME session, none this one -> the scope applied and this run
        delivered nothing. Clean, with the out-of-scope count named, which is the phase-1-must-not-
        block-phase-8 case the scoping was ruled for.
      * a delivery carries NO session at all  -> the scope cannot be applied. Audit the WHOLE log by
        `agent_type` attribution and say so loudly. Degrading honestly is the same rule
        `hook_activity.sh` follows for this field and `doc_read_path.py` follows when git cannot say
        what changed; silently narrowing to nothing is not a fallback, it is a false clean.
    """
    delivered = [r for r in records if is_delivery(r)]
    if not delivered:
        return records, f"this run only (session {session}) — no deliveries recorded at all", None

    mine = [d for d in delivered if d.get("session_id") == session]
    if mine:
        skipped = len(delivered) - len(mine)
        return (
            [r for r in records if r.get("session_id") == session],
            f"this run only (session {session}); {skipped} delivery/ies from other runs counted, "
            f"not enforced",
            None,
        )

    unattributed = [d for d in delivered if not d.get("session_id")]
    if not unattributed:
        return (
            [],
            f"this run only (session {session}) — it delivered nothing; {len(delivered)} "
            f"delivery/ies from other runs counted, not enforced",
            None,
        )
    return (
        records,
        f"WHOLE LOG by agent_type (session {session} could not be applied)",
        f"[required_skills] {len(unattributed)} of {len(delivered)} deliveries carry no session id, "
        f"so --session {session} matched none of them. The scope could not be applied, so the WHOLE "
        f"log is audited by agent_type instead — an empty scope is not a clean scope.",
    )


def audit_gaps(records: list[dict], log: Path | None = None) -> tuple[list[str], str]:
    """(one line per required skill with no evidence of a load, the key the match was made on).

    Two things are a gap, and they are the same gap wearing different clothes:

      * a **pointer** handed to a spawn with no matching `load` record - the stage was told to load a
        required skill and there is no evidence it did;
      * an **injection** recorded `loaded: false` - the file was missing or unreadable, so the stage
        ran with no rules rather than lighter ones.

    Matching is as precise as the record allows, and **no looser**:

      * a delivery carrying an `agent_id` is cleared only by a load recorded against **that same
        spawn id**;
      * a delivery with no spawn id is cleared only by a load recorded against **the same
        `agent_type`**. It is not enough that somebody, somewhere, loaded that skill: the Verifier
        loading `pipeline-conventions` says nothing about whether the implementer did.
      * every key also carries the delivery's `session_id`, so a load in one run cannot clear a
        pointer left unrecorded in another. Without it `--all` reproduced the same looseness one
        level up: across runs instead of across stages.
      * a load record carrying neither id nor type cannot be attributed at all, so it clears any
        delivery of that skill in its own run. Nothing this pipeline writes produces one - `record`
        requires an agent type - so it exists only for a hand-written line, and it is the single
        deliberately loose case rather than an accidental one.

    That distinction is the whole check. Keying the id-less case on the skill alone made ONE recorded
    load clear EVERY stage's pointer for it, while the return value still said `agent_type`: an audit
    reporting coverage it did not have, which is the exact defect class the pointer design exists not
    to reproduce - and this audit is what settles H9, so a hypothesis would have been settled by an
    instrument that never ran. Which key was used is RETURNED and printed for the same reason.

    Records written before this shape existed carry no `event` or `delivery`; they are read as
    injected deliveries, so an old log cannot fail an audit it predates.
    """
    loaded_by_id: set[tuple[str | None, str, str]] = set()
    loaded_by_type: set[tuple[str | None, str, str]] = set()
    loaded_unattributed: set[tuple[str | None, str]] = set()
    for record in records:
        if record.get("event") != "load":
            continue
        skill, run = record.get("skill"), record.get("session_id")
        agent_id, agent_type = record.get("agent_id"), record.get("agent_type")
        if agent_id:
            loaded_by_id.add((run, agent_id, skill))
        if agent_type:
            loaded_by_type.add((run, agent_type, skill))
        if not agent_id and not agent_type:
            loaded_unattributed.add((run, skill))

    gaps: list[str] = []
    keys: set[str] = set()
    for record in records:
        if not is_delivery(record):
            continue
        skill, agent = record.get("skill"), record.get("agent_type", "?")
        agent_id, run = record.get("agent_id"), record.get("session_id")
        if record.get("delivery", INJECT) == POINTER:
            if agent_id:
                keys.add("agent_id")
                cleared = (run, agent_id, skill) in loaded_by_id
            else:
                keys.add("agent_type")
                cleared = (run, record.get("agent_type"), skill) in loaded_by_type
            if not cleared and (run, skill) not in loaded_unattributed:
                gaps.append(
                    f"{agent} was handed a POINTER to skills/{skill} and never recorded loading it. "
                    f"A required skill with no evidence of a load is a stage running on whatever the "
                    f"model already believed.\n"
                    f"      Looking for a load keyed "
                    f"({'agent_id ' + agent_id if agent_id else 'agent_type ' + str(agent)}, "
                    f"session {run or 'none'}). Clear it with:\n"
                    f"      python3 {record_command(record, log)}"
                )
        elif not record.get("loaded"):
            gaps.append(
                f"{agent} required skills/{skill} and it was missing or unreadable at spawn "
                f"(loaded: false). An absent required skill is not a lighter version of the rules."
            )
    if not keys:
        key_used = "n/a"
    elif len(keys) > 1:
        key_used = "agent_id where the delivery carried one, agent_type otherwise"
    else:
        key_used = next(iter(keys))
    return gaps, key_used


def missing(root: Path) -> list[tuple[str, str]]:
    """(agent pattern, skill) for every required skill that is absent or unreadable."""
    out: list[tuple[str, str]] = []
    for pattern, skills in REQUIRED:
        for skill in skills:
            path = skill_path(root, skill)
            try:
                if not path.read_text(encoding="utf-8").strip():
                    out.append((pattern, skill))
            except OSError:
                out.append((pattern, skill))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_for = sub.add_parser("for")
    p_for.add_argument("agent_type")
    p_table = sub.add_parser("table")
    p_table.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    p_record = sub.add_parser("record")
    p_record.add_argument("agent_type")
    p_record.add_argument("skill")
    p_record.add_argument("--agent-id", default=None)
    p_record.add_argument("--session-id", default=None)
    p_record.add_argument("--log", default=None, type=Path)
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--session", default=None, help="audit this run's deliveries only")
    p_audit.add_argument("--all", action="store_true", help="every delivery ever recorded")
    p_audit.add_argument("--log", default=None, type=Path)
    args = parser.parse_args(argv)

    if args.action == "for":
        print("\n".join(required_for(args.agent_type)))
        return OK
    if args.action == "table":
        try:
            limit = inject_max_bytes()
        except ValueError as exc:
            print(f"[required_skills] {exc}", file=sys.stderr)
            return ERROR
        for pattern, skills in REQUIRED:
            annotated = []
            for skill in skills:
                path = skill_path(args.root, skill)
                size = path.stat().st_size if path.is_file() else 0
                annotated.append(f"{skill}:{delivery_for(size, limit)}")
            print(f"{pattern}\t{','.join(annotated)}")
        return OK
    if args.action == "record":
        # A pointer is only as good as the evidence that it was followed, so this is the other half
        # of the pointer delivery — not an optional courtesy.
        log = args.log or default_log()
        try:
            append_record(
                log,
                load_record(
                    args.agent_type, args.skill, event="load", delivery=POINTER,
                    loaded=True, agent_id=args.agent_id, session_id=args.session_id,
                ),
            )
        except OSError as exc:
            print(f"[required_skills] cannot record the load to {log}: {exc}", file=sys.stderr)
            return ERROR
        print(f"recorded: {args.agent_type} loaded skills/{args.skill}", file=sys.stderr)
        return OK
    if args.action == "audit":
        if os.environ.get("SKILLS_OFF", "").strip() == "1":
            # Delivery is off, so nothing was ever handed to a stage to load. Auditing the residue of
            # earlier runs would block a phase for a mechanism the operator switched off.
            print("  (skill delivery is off, SKILLS_OFF=1 — nothing to audit)", file=sys.stderr)
            return OK
        log = args.log or default_log()
        if not log.is_file():
            # Nothing was ever delivered here, so there is nothing to have missed. Said out loud
            # rather than passing invisibly, the same rule as an absent subprocess-check root.
            print(f"  (no skill-load evidence at {log} — no spawns recorded)", file=sys.stderr)
            return OK
        try:
            records = read_records(log)
        except (OSError, ValueError) as exc:
            print(f"[required_skills] {exc}", file=sys.stderr)
            return ERROR

        # SCOPE: you are responsible for what you change. The log is one append-only file per
        # repository, covering every feature and every run, so auditing all of it at a phase handover
        # lets a pointer nobody recorded in phase 1 block phase 8 — and a different feature besides.
        # The same rule as `verifier_precheck.py` and `doc_read_path.py`, and the same edge: when the
        # scope cannot be established, NOTHING is enforced and the check says so, rather than falling
        # back to enforcing everything.
        if args.all:
            scoped, mode, note = records, "--all: every delivery ever recorded", None
        elif args.session:
            scoped, mode, note = scope_to_run(records, args.session)
        else:
            print(
                "[required_skills] no --session given, so which run's deliveries to audit is "
                "unknowable and none is enforced. Pass --session <id>, or --all for a full sweep.",
                file=sys.stderr,
            )
            return OK
        if note:
            print(note, file=sys.stderr)

        gaps, key_used = audit_gaps(scoped, args.log or default_log())
        if not gaps:
            print(
                f"  required skills: every delivered skill has evidence of a load — {mode}, "
                f"{len(scoped)} record(s), matched on {key_used}",
                file=sys.stderr,
            )
            return OK
        print(
            f"required_skills: a required skill was delivered and never loaded — {mode}, matched on "
            f"{key_used}:",
            file=sys.stderr,
        )
        for gap in gaps:
            print(f"  ✗ {gap}", file=sys.stderr)
        return MISSING

    gaps = missing(args.root)
    if not gaps:
        return OK
    print(
        "required_skills: a stage requires a skill that is missing or empty. A required skill that "
        "is not there is a stage running on whatever the model already believed:",
        file=sys.stderr,
    )
    for pattern, skill in gaps:
        print(f"  ✗ {pattern} requires skills/{skill}/SKILL.md", file=sys.stderr)
    return MISSING


if __name__ == "__main__":
    raise SystemExit(main())
