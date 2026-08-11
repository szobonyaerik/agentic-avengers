#!/usr/bin/env bash
# SubagentStart hook: INJECT the skills a stage requires, and record that it got them.
#
# The pipeline delegates its core behaviour to thirteen skills and, until now, delegated by asking:
# "Load `skills/tdd` before you start" is an instruction, not a mechanism. Nothing checked and
# nothing recorded, so a stage that skipped a required skill fell back silently. `skills/ponytail`
# was the only one genuinely injected — and `docs/lessons/` shipped with a complete written procedure
# and zero invocations, for exactly this reason.
#
# Which skills each stage requires is scripts/required_skills.py, the one table. This hook only
# delivers it. Every injection appends a line to .avenger-skill-loads.jsonl (gitignored scratch),
# which is the evidence behind "100% of required loads evidenced".
#
# A required skill that is missing or unreadable is a LOUD BLOCKER in the injected context and is
# recorded `loaded: false`. Everything else fails CLOSED and injects nothing: an unreadable payload,
# an agent this pipeline does not own, a broken table.
#
#   SKILLS_OFF=1        disable injection entirely.
#   SKILL_LOAD_LOG      where the evidence goes (default $CLAUDE_PROJECT_DIR/.avenger-skill-loads.jsonl)
set -uo pipefail

[ "${SKILLS_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SD/.." && pwd)}"
LOG="${SKILL_LOAD_LOG:-${CLAUDE_PROJECT_DIR:-$ROOT}/.avenger-skill-loads.jsonl}"

PAYLOAD="$(cat)"

python3 - "$ROOT" "$LOG" "$PAYLOAD" <<'PY'
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

root, log_path, raw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(root / "scripts"))

try:
    from required_skills import required_for, skill_path
except ImportError:
    sys.exit(0)  # fail closed: no table, no injection

try:
    agent_type = str(json.loads(raw.lstrip("﻿")).get("agent_type") or "").strip()
except (ValueError, AttributeError):
    sys.exit(0)  # fail closed: unreadable payload injects nothing

skills = required_for(agent_type)
if not skills:
    sys.exit(0)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
sections, missing, records = [], [], []

for skill in skills:
    path = skill_path(root, skill)
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        body = ""
    body = re.sub(r"\A---.*?\n---\s*", "", body, flags=re.DOTALL).strip()
    loaded = bool(body)
    records.append(
        {"at": now, "agent_type": agent_type, "skill": skill, "required": True,
         "loaded": loaded, "bytes": len(body), "path": str(path)}
    )
    if loaded:
        sections.append(f"### skills/{skill}\n\n{body}")
    else:
        missing.append(skill)

# The evidence, best-effort: losing a log line must never stop a stage starting.
try:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
except OSError:
    pass

header = [
    "REQUIRED SKILLS FOR THIS STAGE — injected, not requested.",
    "",
    "These are loaded for you. You do not need to open them, and you may not decline them. They are",
    "the pipeline's rules for the work you are about to do; where they conflict with your own",
    "judgement about process, they win.",
    "",
    f"Loaded: {', '.join(s for s in skills if s not in missing) or '(none)'}",
]
if missing:
    header += [
        "",
        "!! BLOCKER — a REQUIRED skill could not be loaded: " + ", ".join(missing),
        "!! Do not proceed by guessing at what it said. This is a pipeline installation fault:",
        "!! stop, report the missing skill file(s) by name, and let a human fix the install.",
        "!! A required skill that is absent is not a lighter version of the rules; it is no rules.",
    ]

json.dump(
    {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": "\n".join(header) + "\n\n" + "\n\n".join(sections),
        }
    },
    sys.stdout,
)
PY

exit 0
