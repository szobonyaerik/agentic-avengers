#!/usr/bin/env bash
# SubagentStart hook: hand the spec writer the gate's rubric BEFORE it writes, not by rejection.
#
# Phase 9 of one measured feature ran fourteen gate rounds on its first spec and one, three and one
# on the next three. The writer learned what the collapsed gate blocks by being rejected fourteen
# times, and nothing carried that learning forward - so every phase paid the fourteen again.
#
# The criteria were already mechanical (scripts/spec_gate_triage.py's closed set,
# scripts/requirement_cap.py's count, the two gate prompts). This delivers them.
#
# It is a HOOK for the same reason ponytail and lessons are: SessionStart context never reaches
# subagents, and "read the rubric before you start" is an instruction with no mechanism - the exact
# shape pipeline-conventions §4e exists to remove. See scripts/hook_ponytail.sh, scripts/hook_lessons.sh.
#
# It injects the rubric WHOLE rather than a pointer, deliberately: it is ~6 KB, it is delivered once
# per spec-writer spawn, and a pointer whose target is never opened reproduces the defect. That is
# the same size test scripts/hook_skills.sh applies (SKILL_INJECT_MAX_BYTES, default 8192).
#
#   SPEC_RUBRIC_AGENTS   regex of agent_type values to inject into (unanchored, case-insensitive).
#                        Default: the spec writer alone. Plugin-scoped names such as
#                        "plan-build-verify:avenger-spec-writer" match because it is unanchored.
#   SPEC_RUBRIC_OFF=1    disable injection everywhere.
#
# opencode has no SubagentStart event, so its spec writer gets the pointer from the agent prompt
# line instead - agents/avenger-spec-writer.md names the script by path for exactly that case.
set -uo pipefail

[ "${SPEC_RUBRIC_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # SPEC_RUBRIC_* and SPEC_REQUIREMENT_MAX may live in the project .env

AGENTS_RE="${SPEC_RUBRIC_AGENTS:-avenger-spec-writer}"

# The hook payload arrives on stdin and the python below is fed on stdin by the heredoc, so read it
# here and pass it as an argument - same shape as scripts/hook_lessons.sh.
PAYLOAD="$(cat)"

python3 - "$SD" "$AGENTS_RE" "$PAYLOAD" <<'PY'
import json
import re
import sys

scripts_dir, agents_re, raw = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    payload = json.loads(raw.lstrip("﻿"))
    agent_type = str(payload.get("agent_type") or "").strip()
except (ValueError, AttributeError):
    sys.exit(0)  # fail closed: unreadable payload injects nothing

try:
    pattern = re.compile(agents_re, re.IGNORECASE)
except re.error:
    sys.exit(0)  # fail closed: a bad regex must not inject a spec rubric into every subagent

if not agent_type or not pattern.search(agent_type):
    sys.exit(0)

sys.path.insert(0, scripts_dir)
try:
    import spec_rubric
except ImportError as exc:  # a vendored install missing the module
    context = (
        f"THE SPEC GATE'S RUBRIC COULD NOT BE DELIVERED: {exc}. You are writing without the "
        f"criteria the gate judges by. Render them yourself with "
        f"`python3 \"${{CLAUDE_PLUGIN_ROOT:-.}}/scripts/spec_rubric.py\"` before you write, and say "
        f"so if you cannot."
    )
else:
    try:
        context = spec_rubric.render()
    except Exception as exc:  # noqa: BLE001 - any failure here is reported, never swallowed
        # Deliberately NOT silent. This hook's whole job is that the writer knows the standard, so a
        # writer running without it must know THAT, rather than being quietly primed against
        # nothing. spec_rubric.render() refuses to emit a partial rubric for the same reason.
        cause = getattr(exc, "cause", type(exc).__name__)
        context = (
            f"THE SPEC GATE'S RUBRIC COULD NOT BE RENDERED (cause={cause}): {exc}\n\n"
            f"You are writing without the criteria the gate judges by. Fix what is named above, or "
            f"say plainly in your handoff that this spec was written unprimed."
        )

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
