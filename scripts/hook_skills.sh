#!/usr/bin/env bash
# SubagentStart hook: DELIVER the skills a stage requires, and record that it got them.
#
# The pipeline delegates its core behaviour to thirteen skills and, until now, delegated by asking:
# "Load `skills/tdd` before you start" is an instruction, not a mechanism. Nothing checked and
# nothing recorded, so a stage that skipped a required skill fell back silently. `skills/ponytail`
# was the only one genuinely injected — and `docs/lessons/` shipped with a complete written procedure
# and zero invocations, for exactly this reason.
#
# Delivery is POINTER PLUS EVIDENCED LOAD, decided by size (scripts/required_skills.py owns both the
# table and the ceiling):
#
#   <= SKILL_INJECT_MAX_BYTES (8192)  the whole body is injected. The injection IS the load.
#   >  SKILL_INJECT_MAX_BYTES         a POINTER: path, size, description, and the command that
#                                     records the load. Injecting every body was the same order of
#                                     cost the read-path work had just removed. Injection GUARANTEES
#                                     a load; the evidence record DETECTS a missing one, and a
#                                     required skill with no recorded load blocks the phase anyway —
#                                     so detection at ~1M tokens saved per feature beats prevention
#                                     at ~1M spent. That saving is a PREDICTION (H9), not a result.
#
# A pointer is not a suggestion: `required_skills.py audit` runs at handover (scripts/hook_verifier.sh)
# and in CI (gate_ci.sh --full), and a pointer with no matching `record` fails it.
#
# A required skill that is missing or unreadable is a LOUD BLOCKER in the injected context and is
# recorded `loaded: false`. Everything else fails CLOSED and injects nothing: an unreadable payload,
# an agent this pipeline does not own, a broken table, an unparseable ceiling.
#
#   SKILLS_OFF=1             disable delivery entirely.
#   SKILL_INJECT_MAX_BYTES   the inject-vs-pointer ceiling (default 8192).
#   SKILL_LOAD_LOG           where the evidence goes (default $CLAUDE_PROJECT_DIR/.avenger-skill-loads.jsonl)
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
from pathlib import Path

root, log_path, raw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(root / "scripts"))

try:
    from required_skills import (
        INJECT, POINTER, append_record, delivery_for, inject_max_bytes, load_record,
        required_for, skill_path,
    )
except ImportError:
    sys.exit(0)  # fail closed: no table, no delivery

try:
    payload = json.loads(raw.lstrip("﻿"))
    agent_type = str(payload.get("agent_type") or "").strip()
except (ValueError, AttributeError):
    sys.exit(0)  # fail closed: unreadable payload injects nothing

# The audit matches as precisely as the payload allows, so take the spawn's own id when it carries
# one under any of the names a runtime might use, and fall back to the agent type when it does not.
agent_id = None
if isinstance(payload, dict):
    for key in ("agent_id", "subagent_id", "session_id", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            agent_id = value
            break

skills = required_for(agent_type)
if not skills:
    sys.exit(0)

try:
    limit = inject_max_bytes()
except ValueError:
    sys.exit(0)  # fail closed: a ceiling nobody can read decides nothing

DESCRIPTION = re.compile(r"^description:[ \t]*(.+)$", re.MULTILINE)

sections, pointers, missing, records = [], [], [], []
injected, pointed = [], []

for skill in skills:
    path = skill_path(root, skill)
    try:
        raw_body = path.read_text(encoding="utf-8")
    except OSError:
        raw_body = ""
    described = DESCRIPTION.search(raw_body.split("\n---", 1)[0] if raw_body.startswith("---") else "")
    body = re.sub(r"\A---.*?\n---\s*", "", raw_body, flags=re.DOTALL).strip()
    if not body:
        missing.append(skill)
        records.append(load_record(agent_type, skill, event="delivery", delivery=INJECT,
                                   loaded=False, path=str(path), agent_id=agent_id))
        continue

    mode = delivery_for(len(body), limit)
    if mode == INJECT:
        # For an injected skill the injection IS the load, so it is recorded loaded here.
        sections.append(f"### skills/{skill}\n\n{body}")
        injected.append(skill)
        records.append(load_record(agent_type, skill, event="delivery", delivery=INJECT,
                                   loaded=True, size=len(body), path=str(path), agent_id=agent_id))
        continue

    record_cmd = (
        f"python3 {root / 'scripts' / 'required_skills.py'} record {agent_type} {skill} "
        f"--log {log_path}" + (f" --agent-id {agent_id}" if agent_id else "")
    )
    pointers.append("\n".join([
        f"### skills/{skill} — REQUIRED, {len(body)} bytes, load it yourself",
        f"    {described.group(1).strip() if described else '(no description in its frontmatter)'}",
        f"    Path: {path}",
        "    READ THIS FILE BEFORE YOU START. It is required, not suggested: it is too large to",
        "    inject on every spawn, which is the only reason you are being pointed at it. Then",
        "    record the load, which is what makes the requirement a mechanism rather than a request:",
        f"      {record_cmd}",
    ]))
    pointed.append(skill)
    records.append(load_record(agent_type, skill, event="delivery", delivery=POINTER,
                               loaded=False, size=len(body), path=str(path), agent_id=agent_id))

# The evidence, best-effort: losing a log line must never stop a stage starting.
try:
    for record in records:
        append_record(log_path, record)
except OSError:
    pass

header = [
    "REQUIRED SKILLS FOR THIS STAGE — delivered, not requested.",
    "",
    "These are the pipeline's rules for the work you are about to do; where they conflict with your",
    "own judgement about process, they win. You may not decline them.",
    "",
    f"Injected in full (nothing to open): {', '.join(injected) or '(none)'}",
    f"Pointed at, and REQUIRED to load: {', '.join(pointed) or '(none)'}",
    "",
    "A required skill you were pointed at and did not record loading BLOCKS THE PHASE: the audit",
    "runs at handover and in CI (`required_skills.py audit`). Load it, then record it.",
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
            "additionalContext": "\n\n".join(
                part for part in ["\n".join(header), "\n\n".join(pointers), "\n\n".join(sections)]
                if part
            ),
        }
    },
    sys.stdout,
)
PY

exit 0
