#!/usr/bin/env bash
# SubagentStart + SubagentStop hook: append one JSON line per agent lifecycle event to the
# project-root activity log, `.agent-activity.jsonl` (gitignored).
#
# Pure observability, NOT a gate: it injects no context, blocks nothing, and emits nothing on
# stdout. Its one consumer is the tavern monitor (tavern/server.py), which tails the log to show
# which subagents are alive right now — a start event with no matching stop is "in the bar".
# The existing hooks read only `agent_type` and deliberately drop the rest of the payload; this
# hook is the one place the ids needed to correlate an agent to its transcript get persisted.
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

python3 - "$LOG" "$MAX_LINES" "$PAYLOAD" <<'PY'
import json
import sys
import time

log_path, max_lines_raw, raw = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    payload = json.loads(raw.lstrip("﻿"))
    if not isinstance(payload, dict):
        raise ValueError
except ValueError:
    sys.exit(0)  # fail closed: an unreadable payload logs nothing

event = str(payload.get("hook_event_name") or "").strip()
if event not in ("SubagentStart", "SubagentStop"):
    sys.exit(0)  # only lifecycle events belong in the activity log

record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event}
# Persist whichever correlation ids this harness version provides — absent keys are simply omitted,
# so the record shape degrades instead of the hook guessing.
for key in ("agent_type", "agent_id", "session_id", "transcript_path", "agent_transcript_path", "cwd"):
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        record[key] = value.strip()

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
