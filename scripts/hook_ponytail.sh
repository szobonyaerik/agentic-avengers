#!/usr/bin/env bash
# SubagentStart hook: inject skills/ponytail into implementer subagents only.
#
# SessionStart context never reaches subagents, and the implementers ARE subagents — so a
# SessionStart hook would inject the minimalism ruleset everywhere EXCEPT where code gets written.
# SubagentStart is the only event that reaches them. See hooks/hooks.json.
#
# Scoping is deliberate and fails CLOSED: the ruleset must never reach avenger-verifier,
# avenger-breaker or avenger-bug-hunter, whose job is to demand MORE tests and more counterexamples.
# An unreadable payload, an unknown agent_type or a missing skill file injects nothing.
#
#   PONYTAIL_AGENTS   regex of agent_type values to inject into (unanchored, case-insensitive).
#                     Default: the two implementers. Plugin-scoped names such as
#                     "plan-build-verify:avenger-backend-architect" match because it is unanchored.
#   PONYTAIL_OFF=1    disable injection everywhere.
set -uo pipefail

[ "${PONYTAIL_OFF:-0}" = "1" ] && exit 0

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SKILL="$ROOT/skills/ponytail/SKILL.md"
[ -f "$SKILL" ] || exit 0

AGENTS_RE="${PONYTAIL_AGENTS:-avenger-backend-architect|avenger-frontend-developer}"

# The hook payload arrives on stdin, but the python script below is itself fed on stdin via the
# heredoc — so read the payload here and hand it over as an argument instead.
PAYLOAD="$(cat)"

python3 - "$SKILL" "$AGENTS_RE" "$PAYLOAD" <<'PY'
import json
import re
import sys

skill_path, agents_re, raw = sys.argv[1], sys.argv[2], sys.argv[3]

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
    with open(skill_path, encoding="utf-8") as fh:
        body = fh.read()
except OSError:
    sys.exit(0)

body = re.sub(r"\A---.*?\n---\s*", "", body, flags=re.DOTALL)

json.dump(
    {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": "PONYTAIL ACTIVE for this implementation.\n\n" + body,
        }
    },
    sys.stdout,
)
PY

exit 0
