#!/usr/bin/env bash
# SubagentStart + SubagentStop + PostToolUse(TaskStop) hook: append one JSON line per agent lifecycle
# event to the project-root activity log, `.agent-activity.jsonl` (gitignored).
#
# Pure observability, NOT a gate: it injects no context, blocks nothing, and emits nothing on
# stdout. Two consumers: the tavern monitor (tavern/server.py), which tails the log to show which
# subagents are alive right now, and `scripts/implementer_liveness.py`, which reads it to refuse a
# second implementer in one worktree. The existing hooks read only `agent_type` and deliberately
# drop the rest of the payload; this hook is the one place the ids needed to correlate an agent to
# its transcript get persisted.
#
# TWO THINGS HERE ARE OBSERVED OUTCOMES, NOT INFERENCES (issue #98). A killed implementer held its
# lock for four hours because `TaskStop` does not fire `SubagentStop`, so a start with no stop read
# as "still running" for as long as the age ceiling allowed. Measured: 1410 rows, 33 starts, 1334
# stops, exactly one unmatched - the killed agent. So:
#
#   * A `SubagentStart` row carries `harness_pid`: the pid of the harness process this hook is a
#     child of (the nearest `claude` ancestor, else the parent). A subagent lives inside that
#     process, so a pid that no longer exists is an implementer that cannot be running, whatever the
#     log's stop rows say. That is a fact `implementer_liveness.py` checks against the kernel.
#   * A `PostToolUse` event for the `TaskStop` tool is recorded as event `TaskStop` with the stopped
#     agent's id - **only when the tool's own response says the stop succeeded.** The attempt is not
#     the outcome: a `TaskStop` that named no live task stopped nothing, and recording it would
#     release a lock on an implementer that is still writing.
#
#   ACTIVITY_OFF=1        disable logging everywhere.
#   ACTIVITY_LOG          override the log path (default: <project>/.agent-activity.jsonl).
#   ACTIVITY_MAX_LINES    rotation cap; the log is trimmed to the newest half when it exceeds
#                         this many lines (default 4000). 0 disables rotation.
set -uo pipefail

[ "${ACTIVITY_OFF:-0}" = "1" ] && exit 0

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/load_env.sh"   # ACTIVITY_* may live in the project .env
PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
LOG="${ACTIVITY_LOG:-$PROJECT/.agent-activity.jsonl}"
MAX_LINES="${ACTIVITY_MAX_LINES:-4000}"

# The hook payload arrives on stdin; hand it to python as an argument (same pattern as
# hook_lessons.sh — the heredoc owns the script's stdin).
PAYLOAD="$(cat)"

# The harness process: walk up from this hook's parent until a process named `claude` is found.
# Bounded, and every failure falls back to the immediate parent, so a harness under another name
# still records SOME pid rather than none - `implementer_liveness.py` treats an absent pid as
# "unobservable", never as dead.
harness_pid() {
  local pid="$PPID" comm hops=0
  while [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null && [ "$hops" -lt 8 ]; do
    comm="$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ')"
    case "${comm##*/}" in
      claude|claude-code|node) printf '%s' "$pid"; return 0 ;;
    esac
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    hops=$((hops + 1))
  done
  printf '%s' "$PPID"
}
HARNESS_PID="${ACTIVITY_HARNESS_PID:-$(harness_pid)}"

python3 - "$LOG" "$MAX_LINES" "$PAYLOAD" "$HARNESS_PID" <<'PY'
import json
import sys
import time

log_path, max_lines_raw, raw, harness_pid = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

try:
    payload = json.loads(raw.lstrip("﻿"))
    if not isinstance(payload, dict):
        raise ValueError
except ValueError:
    sys.exit(0)  # fail closed: an unreadable payload logs nothing

event = str(payload.get("hook_event_name") or "").strip()
record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

if event in ("SubagentStart", "SubagentStop"):
    record["event"] = event
    # Persist whichever correlation ids this harness version provides — absent keys are simply
    # omitted, so the record shape degrades instead of the hook guessing.
    for key in ("agent_type", "agent_id", "session_id", "transcript_path", "agent_transcript_path", "cwd"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            record[key] = value.strip()
    if event == "SubagentStart" and harness_pid.isdigit():
        record["harness_pid"] = int(harness_pid)
elif event == "PostToolUse" and str(payload.get("tool_name") or "").strip() == "TaskStop":
    tool_input = payload.get("tool_input") or {}
    task_id = tool_input.get("task_id") if isinstance(tool_input, dict) else None
    response = payload.get("tool_response")
    # The OUTCOME is what the tool reported back, never that the tool was called. The harness answers
    # a real stop with a message beginning "Successfully stopped"; anything else - an unknown task,
    # an error object, no response at all - stopped nothing and is not recorded as a stop.
    message = ""
    if isinstance(response, dict):
        message = str(response.get("message") or response.get("content") or "")
    elif isinstance(response, str):
        message = response
    if not (isinstance(task_id, str) and task_id.strip()):
        sys.exit(0)
    if not message.lstrip().startswith("Successfully stopped"):
        sys.exit(0)
    record["event"] = "TaskStop"
    record["agent_id"] = task_id.strip()
    session = payload.get("session_id")
    if isinstance(session, str) and session.strip():
        record["session_id"] = session.strip()
else:
    sys.exit(0)  # only lifecycle events belong in the activity log

try:
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
except OSError:
    sys.exit(0)  # fail closed: an unwritable log is not this hook's problem to report

try:
    max_lines = int(max_lines_raw)
except ValueError:
    max_lines = 4000
if max_lines > 0:
    try:
        with open(log_path, encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) > max_lines:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-max_lines // 2:])
    except OSError:
        pass
PY

exit 0
