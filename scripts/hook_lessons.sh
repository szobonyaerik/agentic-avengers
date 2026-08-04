#!/usr/bin/env bash
# SubagentStart hook: point pipeline subagents at the project's lessons log (docs/lessons/).
#
# Same reason ponytail is a hook and not a passive skill: SessionStart context never reaches
# subagents, and a `SKILL.md` nobody loads self-activates ~never — which is exactly how the lessons
# log stayed dormant with zero invocations. See hooks/hooks.json and scripts/hook_ponytail.sh.
#
# Unlike ponytail this reaches ALL canonical avenger agents, including the verifier, breaker and
# bug-hunter: a "write less code" persona conflicts with their job, prior lessons never do.
#
# It injects a POINTER, never content — it fires on every spawn, so the log itself (and the prose
# files) stay on disk and get read selectively per skills/self-improvement.
#
#   LESSONS_AGENTS   regex of agent_type values to inject into (unanchored, case-insensitive).
#                    Default: every avenger agent. Plugin-scoped names such as
#                    "plan-build-verify:avenger-verifier" match because it is unanchored.
#   LESSONS_OFF=1    disable injection everywhere.
set -uo pipefail

[ "${LESSONS_OFF:-0}" = "1" ] && exit 0

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/load_env.sh"   # LESSONS_* may live in the project .env
PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
INDEX="$PROJECT/docs/lessons/lessons.json"

AGENTS_RE="${LESSONS_AGENTS:-avenger-}"

# The hook payload arrives on stdin, but the python script below is itself fed on stdin via the
# heredoc — so read the payload here and hand it over as an argument instead.
PAYLOAD="$(cat)"

python3 - "$INDEX" "$AGENTS_RE" "$PAYLOAD" <<'PY'
import json
import re
import sys

index_path, agents_re, raw = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    payload = json.loads(raw.lstrip("﻿"))
    agent_type = str(payload.get("agent_type") or "").strip()
except (ValueError, AttributeError):
    sys.exit(0)  # fail closed: unreadable payload injects nothing

try:
    pattern = re.compile(agents_re, re.IGNORECASE)
except re.error:
    sys.exit(0)  # fail closed: a bad regex must not inject into every subagent

if not agent_type or not pattern.search(agent_type):
    sys.exit(0)

try:
    with open(index_path, encoding="utf-8") as fh:
        entries = json.load(fh)
except (OSError, ValueError):
    sys.exit(0)  # fail closed: no log, or an unparseable one, is not this hook's problem to report

# An empty or non-array index means a project that has never written a lesson: no behaviour change.
if not isinstance(entries, list) or not entries:
    sys.exit(0)

context = f"""PRIOR LESSONS: `docs/lessons/lessons.json` holds {len(entries)} entr\
{"y" if len(entries) == 1 else "ies"} from earlier work on this project.

Before you start, read that INDEX ONLY — it is small by design. Filter to entries whose `tags`/`scope`
match your role and this task, then open only the handful of `docs/lessons/<id>-<slug>.md` prose files
that matter. Do not read the whole folder.

When something learning-worthy happens — a user correction, a self-caught mistake, a confirmed-good
approach — append or refine a lesson per `skills/self-improvement`. Never override an existing one."""

json.dump(
    {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": context,
        }
    },
    sys.stdout,
)
PY

exit 0
