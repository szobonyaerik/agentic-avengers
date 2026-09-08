#!/usr/bin/env python3
"""Is an implementer still WORKING? Answered from the event it finishes on, never from a stamp.

## The defect

Issue #68, measured in clickup-agents phase 11. An implementer stamps its spec `status: done` and
then keeps working - `test-mapping.md`, `test-evidence.md` and the phase's mutation gate all land
after the stamp. A phase worker had armed a condition-wait on that stamp as a wedge guard, and **it
fired at 24 minutes while the agent was still running**. Had the next spec's implementer been
dispatched on it, two implementers would have been running in one worktree against one database -
forbidden outright, and phase 9 measured the cost: a git stash from one swallowed the other's
uncommitted work, and the shared database produced foreign-key violations plus a spurious lint
failure. The tell was two workers reporting suite totals one apart. Nothing else flagged it.

`scripts/spec_done_guard.py` made the stamp self-correcting. It did not make it a **completion
signal**, and the issue's own fix direction says so: telling people not to wait on the stamp is a
sentence claiming behaviour nothing enforces. So the signal is produced from the one event that
means the implementer FINISHED - `SubagentStop` - which `scripts/hook_activity.sh` has recorded to
`.agent-activity.jsonl` since it was written, and which nothing read for this.

**Nothing here can be moved by writing a document.** A start with no matching stop is an implementer
still in the worktree, whatever any spec's frontmatter says.

## What bounds it - the process, first

`SubagentStop` is not the only way an implementer ends, and for a long time it was the only ending
this read (issue #98). A stage killed through the `TaskStop` tool fires no `SubagentStop`, so its
start stayed unmatched and its lock was held for the full four-hour ceiling: measured at 1410
activity rows, 33 starts, 1334 stops, exactly one unmatched - the killed agent. Recovery cost half
a working day for a routine correct action. So an open start is now checked against two OUTCOMES
before the ceiling is even consulted:

* **`TaskStop`** - `hook_activity.sh` records the harness's own `PostToolUse` for that tool, and
  records it only when the tool's response says the stop succeeded. A start whose id was stopped
  that way is not live.
* **The harness process** - every start row carries `harness_pid`, the pid of the `claude` process
  the subagent runs inside. `proc_group.process_exists` asks the kernel; a pid that is gone is an
  implementer that cannot be running, whatever the log says. A row with no pid (written before
  this rule) is not judged by it, so nothing recorded earlier changes meaning.

A crashed subagent inside a harness that is still alive can still leave a start with no stop, and
reading that as live forever is a permanent wedge with no remedy - the shape §3a exists to refuse.
`IMPLEMENTER_MAX_AGE_S` (default 4 hours) still bounds that last case, and an aged-out start is
named rather than silently dropped.

**A missing log is not "nothing is running".** The activity hook may be off (`ACTIVITY_OFF=1`), or
this may not be a pipeline run at all. That is `LivenessUnknown`, and the caller says so rather than
reading it as all-clear.

`--stage` is answered HERE too, rather than by the caller: which stages are bound and which are
live are one pattern, and a caller that pre-filtered with its own copy of it would be a second
statement of the same fact - the drift this repository keeps paying for. A stage that is not bound
is simply "none live", so the hook needs no pattern of its own.

Usage:
    implementer_liveness.py live [--root .] [--stage NAME]
        exit 0 = none live (or this stage is not bound), 1 = LIVE (named), 2 = cannot tell
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402
from proc_group import process_exists  # noqa: E402

NONE_LIVE = 0
LIVE = 1
UNKNOWN = 2

#: The marker a caller greps, so a traceback (which also exits 1) is never read as a verdict - the
#: same discipline `plugin_release.py`'s `STALE:` marker and this pipeline's other hooks run on.
LIVE_MARKER = "[implementer-liveness] LIVE:"

LOG_NAME = ".agent-activity.jsonl"
LOG_ENV = "ACTIVITY_LOG"

#: The stages that WRITE to the worktree, and therefore the ones that may not overlap. Unanchored
#: and case-insensitive, so a plugin-scoped name (`plan-build-verify:avenger-backend-architect`)
#: matches. The Verifier, the Breaker and the bug-hunter deliberately are not here: they run beside
#: an implementer by design, and this rule is about two writers in one working copy.
AGENTS_ENV = "IMPLEMENTER_AGENTS"
DEFAULT_AGENTS = r"avenger-backend-architect|avenger-frontend-developer"

#: Seconds after which a start with no stop is presumed dead rather than live. Generous - an
#: implementer can legitimately run for hours - and bounded, because the alternative is a lock that
#: a crashed agent holds forever.
MAX_AGE_ENV = "IMPLEMENTER_MAX_AGE_S"
DEFAULT_MAX_AGE_S = 4 * 60 * 60

START = "SubagentStart"
STOP = "SubagentStop"
#: The harness reported that it stopped this agent (`hook_activity.sh` writes it from the TaskStop
#: tool's own success response). Keyed by id alone: the tool names a task id, not an agent type.
KILLED = "TaskStop"
PID_KEY = "harness_pid"


class LivenessUnknown(Exception):
    """The question could not be answered. Never the same as `nothing is running`."""


def log_path(root: Path) -> Path:
    declared = (os.environ.get(LOG_ENV) or "").strip()
    return Path(declared) if declared else Path(root) / LOG_NAME


def agents_pattern() -> re.Pattern[str]:
    raw = (os.environ.get(AGENTS_ENV) or "").strip() or DEFAULT_AGENTS
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error as exc:
        raise LivenessUnknown(
            f"{AGENTS_ENV}={raw!r} is not a usable regular expression ({exc}), so which stages this "
            f"binds cannot be decided"
        ) from exc


def max_age_s() -> int:
    raw = (os.environ.get(MAX_AGE_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_S
    try:
        value = int(raw)
    except ValueError as exc:
        raise LivenessUnknown(
            f"{MAX_AGE_ENV}={raw!r} is not an integer number of seconds"
        ) from exc
    if value <= 0:
        raise LivenessUnknown(
            f"{MAX_AGE_ENV}={raw!r} must be a positive number of seconds"
        )
    return value


def _when(entry: dict) -> datetime | None:
    raw = str(entry.get("ts") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _key(entry: dict) -> tuple[str, str]:
    """What identifies one agent's run. `agent_id` when the harness supplied it, else its type.

    `hook_activity.sh` omits keys the harness did not provide rather than guessing, so a run with no
    ids has to degrade to type-matching instead of holding the lock forever.
    """
    return (str(entry.get("agent_type") or ""), str(entry.get("agent_id") or ""))


def live(root: Path, *, now: datetime | None = None) -> list[dict]:
    """Every implementer that started, has not stopped, and is not older than the ceiling.

    Raises `LivenessUnknown` when there is no log to read: the activity hook may be off, or this may
    not be a pipeline run, and a lock that reads "I cannot see" as "all clear" means nothing.
    """
    path = log_path(root)
    pattern = agents_pattern()
    ceiling = max_age_s()
    moment = now or datetime.now(timezone.utc)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LivenessUnknown(f"{path} could not be read ({exc})") from exc

    open_runs: dict[tuple[str, str], dict] = {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # one malformed line is not a reason to stop reading the rest
        if not isinstance(entry, dict):
            continue
        agent = str(entry.get("agent_type") or "")
        if not pattern.search(agent):
            continue
        event = str(entry.get("event") or "")
        if event == START:
            open_runs[_key(entry)] = entry
        elif event == STOP:
            key = _key(entry)
            if key in open_runs:
                open_runs.pop(key)
            elif not key[1]:
                # A stop with no id clears the newest still-open run of its type - the only reading
                # available when the harness supplies no ids at all.
                same = [k for k in open_runs if k[0] == key[0]]
                if same:
                    open_runs.pop(same[-1])

    killed = _killed_ids(text)
    still: list[dict] = []
    for (_, agent_id), entry in open_runs.items():
        if agent_id and agent_id in killed:
            continue  # the harness reported this stop itself; a TaskStop fires no SubagentStop
        if not _harness_alive(entry):
            continue  # the process the agent ran inside is gone, so the agent is too
        when = _when(entry)
        if when is None:
            continue  # an entry that cannot be placed in time cannot be shown to be current
        if (moment - when).total_seconds() > ceiling:
            continue
        still.append(entry)
    return still


def _killed_ids(text: str) -> set[str]:
    """Every agent id the harness reported as stopped through the TaskStop tool.

    A `TaskStop` row carries no `agent_type` - the tool names a task id - so it is matched on id
    across every open run rather than through the type-keyed loop above.
    """
    ids: set[str] = set()
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and str(entry.get("event") or "") == KILLED:
            agent_id = str(entry.get("agent_id") or "").strip()
            if agent_id:
                ids.add(agent_id)
    return ids


def _harness_alive(entry: dict) -> bool:
    """Whether the harness process a start row names still exists. Asked of the kernel.

    A row with no usable pid is UNOBSERVABLE, and unobservable is read as alive: this check may
    release a lock only on a fact it actually saw, and a row written before pids were recorded is
    judged by the ceiling alone, exactly as it was before.
    """
    raw = entry.get(PID_KEY)
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return True
    return process_exists(pid)


def describe(entry: dict) -> str:
    return (
        f"{entry.get('agent_type', '?')} (id {entry.get('agent_id') or 'unknown'}, "
        f"started {entry.get('ts', 'at an unknown time')})"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)
    live_cmd = sub.add_parser("live", help="who is still in the worktree")
    live_cmd.add_argument("--root", default=".")
    live_cmd.add_argument(
        "--stage",
        default=None,
        help="the stage about to be spawned; an unbound one is never blocked",
    )
    args = ap.parse_args(argv)

    try:
        if args.stage is not None and not agents_pattern().search(args.stage):
            return NONE_LIVE
        running = live(Path(args.root).resolve())
    except LivenessUnknown as exc:
        print(f"[implementer-liveness] cannot tell: {exc}", file=sys.stderr)
        return UNKNOWN
    if not running:
        print("[implementer-liveness] no implementer is running.", file=sys.stderr)
        return NONE_LIVE
    for entry in running:
        print(f"{LIVE_MARKER} {describe(entry)}")
    return LIVE


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
