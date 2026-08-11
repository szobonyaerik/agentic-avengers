#!/usr/bin/env bash
# Which skills a stage ACTUALLY loaded — recorded on evidence, never on an instruction.
#
# The pipeline delegates core behaviour to skills/, but an agent definition only *tells* an agent to
# load one. Nothing observed whether it did, so a stage could run on its own judgement with nobody
# aware. This hook is the observation, on two events:
#
#   SubagentStart          the stage's contract is seeded from its own definition
#                          (scripts/skill_contract.py) as required-and-not-yet-observed. A seeded
#                          entry that is never flipped is the finding: it says "required, no load
#                          observed", which is exactly what went unrecorded before.
#   PostToolUse Read|Skill positive evidence that a skill's content entered context — a read of
#                          `skills/<name>/SKILL.md`, or the Skill tool returning one. The entry is
#                          keyed `<stage>:<skill>`, so this UPSERTS over the seed rather than
#                          appending, and the requirement and its answer are one row.
#
# Measurement, never a gate: it blocks nothing, injects nothing, writes nothing to stdout, and every
# failure is swallowed. Fail-closed on the payload in the sibling sense — an unreadable payload, an
# unknown skill or an unresolvable phase records nothing rather than guessing.
#
#   SKILL_LOAD_OFF=1   disable the observation entirely.
set -uo pipefail

[ "${SKILL_LOAD_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # SKILL_LOAD_* may live in the project .env

PAYLOAD="$(cat)"
EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // empty' 2>/dev/null)
SKILL_ARG=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.skill // empty' 2>/dev/null)
FILE=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Leave the common case immediately: this hook sees every Read in the session, and all but a
# handful of them have nothing to do with a skill.
if [ "$EVENT" != "SubagentStart" ] && [ -z "$SKILL_ARG" ]; then
  case "$FILE" in */SKILL.md) ;; *) exit 0 ;; esac
fi

python3 - "$SD" "$PAYLOAD" <<'PY' >/dev/null 2>&1 || true
import json
import re
import sys
from pathlib import Path

scripts_dir, raw = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts_dir)

import pipeline_metrics as metrics
import skill_contract

try:
    payload = json.loads(raw.lstrip("﻿"))
    if not isinstance(payload, dict):
        raise ValueError
except ValueError:
    sys.exit(0)  # an unreadable payload records nothing, exactly as the sibling hooks treat it

phase = metrics.current_phase()
if phase is None:
    sys.exit(0)  # no phase in flight — a skill load belongs to a phase or to no record at all

known = skill_contract.available_skills()
event = str(payload.get("hook_event_name") or "").strip()
agent_type = str(payload.get("agent_type") or "").strip()

if event == "SubagentStart":
    # Seed the contract, so a skill this stage owes and never loads is a row rather than a silence.
    for name in sorted(skill_contract.required_skills(agent_type)):
        metrics.record_skill_load(phase, stage=agent_type, skill=name, evidence="", loaded=False)
    sys.exit(0)

skill = re.sub(r"^.*:", "", str(payload.get("tool_input", {}).get("skill") or "").strip())
path = str(payload.get("tool_input", {}).get("file_path") or "").strip()
if not skill and path.endswith("SKILL.md"):
    skill = Path(path).parent.name
if skill not in known:
    sys.exit(0)

evidence = (
    f"{event} {payload.get('tool_name') or '?'}: "
    + (f"read {path}" if path else f"skill={skill}")
)
metrics.record_skill_load(phase, stage=agent_type, skill=skill, evidence=evidence)
PY

exit 0
