#!/usr/bin/env bash
# SubagentStart hook: DELIVER the skills a stage requires, and record what it actually got.
#
# The pipeline delegates its core behaviour to thirteen skills and, until now, delegated by asking:
# "Load `skills/tdd` before you start" is an instruction, not a mechanism. Nothing checked and
# nothing recorded, so a stage that skipped a required skill fell back silently. `skills/ponytail`
# was the only one genuinely injected — and `docs/lessons/` shipped with a complete written procedure
# and zero invocations, for exactly this reason.
#
# WHICH skills is DERIVED from the stage's own `agents/<stage>.md` (scripts/skill_contract.py, via
# scripts/required_skills.py). There is no table here and none there: a second statement of a fact
# the agent definitions already carry is what every promise-versus-enforcement gap turned out to be.
# Minus what another SubagentStart hook already owns (`DELIVERED_ELSEWHERE`) — delivering `ponytail`
# here too would cost twice and would put the minimalism persona back after `PONYTAIL_OFF=1`.
#
# Delivery is POINTER PLUS EVIDENCED LOAD, decided by size:
#
#   <= SKILL_INJECT_MAX_BYTES (8192)  the whole body is injected. The injection IS the load, so it is
#                                     RECORDED as an observed load here, with the evidence naming
#                                     injection — an injected skill is never read, so without this it
#                                     would seed required-and-unobserved and never flip, and the
#                                     audit would report a false gap on precisely the skills whose
#                                     load is guaranteed.
#   >  SKILL_INJECT_MAX_BYTES         a POINTER: path, size and description. Injecting every body was
#                                     the same order of cost the read-path work had just removed.
#                                     Injection GUARANTEES a load; the observation hook DETECTS a
#                                     missing one, and a required skill with no observed load blocks
#                                     the phase anyway — so detection at ~1M tokens saved per feature
#                                     beats prevention at ~1M spent. That saving is a PREDICTION
#                                     (H9), not a result.
#
# The pointer does not ask the stage to report anything. Reading the file IS the record
# (scripts/hook_skill_load.sh observes the Read/Skill call), and a pointer that told the agent to run
# a command afterwards would be the instruction-with-no-mechanism this whole mechanism replaces, one
# layer up. A pointer is still not a suggestion: `required_skills.py audit` runs at handover
# (scripts/hook_verifier.sh) and in CI (gate_ci.sh --full), and an unobserved required load fails it.
#
# A required skill that is missing or unreadable is a LOUD BLOCKER in the injected context and is
# recorded `loaded: false`. Everything else fails CLOSED and injects nothing: an unreadable payload,
# an agent this pipeline does not own, an unparseable ceiling.
#
#   SKILLS_OFF=1             disable delivery entirely.
#   SKILL_INJECT_MAX_BYTES   the inject-vs-pointer ceiling (default 8192).
set -uo pipefail

[ "${SKILLS_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$SD/.." && pwd)}"

PAYLOAD="$(cat)"

python3 - "$ROOT" "$PAYLOAD" <<'PY'
import json
import re
import sys
from pathlib import Path

root, raw = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / "scripts"))

try:
    from required_skills import (
        INJECT, delivery_for, inject_max_bytes, skill_path, to_deliver,
    )
except ImportError:
    sys.exit(0)  # fail closed: no contract, no delivery

try:
    payload = json.loads(raw.lstrip("﻿"))
    agent_type = str(payload.get("agent_type") or "").strip()
except (ValueError, AttributeError):
    sys.exit(0)  # fail closed: unreadable payload injects nothing

skills = to_deliver(agent_type, root)
if not skills:
    sys.exit(0)

try:
    limit = inject_max_bytes()
except ValueError:
    sys.exit(0)  # fail closed: a ceiling nobody can read decides nothing

# Measurement, never a gate, and never able to stop a stage starting: the whole emission is optional
# and every failure inside it is swallowed. A runtime vendored without the metrics modules delivers
# skills exactly as before and records nothing.
try:
    import pipeline_metrics as metrics

    phase = metrics.current_phase()
except Exception:  # noqa: BLE001
    metrics, phase = None, None


def observed(skill: str, evidence: str, loaded: bool) -> None:
    if metrics is None or phase is None:
        return
    try:
        metrics.record_skill_load(
            phase, stage=agent_type, skill=skill, evidence=evidence, loaded=loaded
        )
    except Exception:  # noqa: BLE001 — a number nobody could write never stops a spawn
        pass


DESCRIPTION = re.compile(r"^description:[ \t]*(.+)$", re.MULTILINE)

sections, pointers, missing = [], [], []
injected, pointed = [], []

for skill in skills:
    path = skill_path(root, skill)
    try:
        raw_body = path.read_text(encoding="utf-8")
    except OSError:
        raw_body = ""
    described = DESCRIPTION.search(
        raw_body.split("\n---", 1)[0] if raw_body.startswith("---") else ""
    )
    body = re.sub(r"\A---.*?\n---\s*", "", raw_body, flags=re.DOTALL).strip()
    if not body:
        missing.append(skill)
        observed(skill, "", loaded=False)
        continue

    if delivery_for(len(body), limit) == INJECT:
        # For an injected skill the injection IS the load, so it is recorded observed here. The
        # evidence names injection, because "how do we know" is the only thing this record answers.
        sections.append(f"### skills/{skill}\n\n{body}")
        injected.append(skill)
        observed(skill, f"SubagentStart hook_skills.sh: injected {len(body)} bytes", loaded=True)
        continue

    # No command to run: reading the file is what gets recorded. A pointer that asked the agent to
    # report its own load would prove nothing, which is the failure this mechanism exists to fix.
    pointers.append("\n".join([
        f"### skills/{skill} — REQUIRED, {len(body)} bytes, load it yourself",
        f"    {described.group(1).strip() if described else '(no description in its frontmatter)'}",
        f"    Path: {path}",
        "    READ THIS FILE BEFORE YOU START (the Read tool, or the Skill tool). It is required, not",
        "    suggested: it is too large to inject on every spawn, which is the only reason you are",
        "    being pointed at it rather than handed it. Opening it is what records the load.",
    ]))
    pointed.append(skill)

header = [
    "REQUIRED SKILLS FOR THIS STAGE — delivered, not requested.",
    "",
    "These are the pipeline's rules for the work you are about to do; where they conflict with your",
    "own judgement about process, they win. You may not decline them.",
    "",
    f"Injected in full (nothing to open): {', '.join(injected) or '(none)'}",
    f"Pointed at, and REQUIRED to load: {', '.join(pointed) or '(none)'}",
    "",
    "A required skill you were pointed at and did not load BLOCKS THE PHASE: the audit runs at",
    "handover and in CI (`required_skills.py audit`) and reads what was actually observed, not what",
    "anyone reported. Open it and the record takes care of itself.",
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
