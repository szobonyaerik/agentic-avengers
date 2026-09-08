#!/usr/bin/env bash
# SubagentStop hook: what a helper agent actually spent, recorded the moment it returns.
#
# Issue #118's third half. The cost of a runaway helper landed on the account and on nothing the
# pipeline records, so an hour of it left no trace in the retrospective and `compare` could not be
# asked about it at all. The only visible symptom was the parent going quiet, which every other
# explanation also produces.
#
# WHAT IT RECORDS. One `gate_calls` row per returning helper: elapsed time (in `latency_ms`, the
# field that already means how long a thing took), the budget its dispatch declared, and its token
# count WHERE THE HARNESS REPORTS ONE - which today it does not, so that is written as `unrecorded`
# rather than as zero. A helper that never returns fires no `SubagentStop` and leaves no row here;
# its dispatch row, written by `hook_helper_budget.sh` at `PreToolUse`, is what says it existed, and
# a dispatch with no spend row IS the helper that never came back.
#
# Measurement, never a gate: it blocks nothing, injects nothing, writes nothing to stdout, and every
# failure is swallowed. It exits 0 always (CLAUDE.md 6d).
#
#   HELPER_SPEND_OFF=1   disable the observation entirely.
set -uo pipefail

[ "${HELPER_SPEND_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"

PAYLOAD="$(cat)"

python3 - "$SD" "$PAYLOAD" <<'PY' >/dev/null 2>&1 || true
import json
import os
import sys
from pathlib import Path

scripts_dir, raw = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts_dir)

import helper_budget
import pipeline_metrics as metrics

try:
    payload = json.loads(raw.lstrip("﻿"))
    if not isinstance(payload, dict):
        raise ValueError
except ValueError:
    sys.exit(0)  # an unreadable payload records nothing, exactly as the sibling hooks treat it

if str(payload.get("hook_event_name") or "").strip() != "SubagentStop":
    sys.exit(0)

agent_type = str(payload.get("agent_type") or "").strip()
if not agent_type or not helper_budget.is_helper(agent_type):
    sys.exit(0)  # a pipeline stage is not a helper; its cost is the phase's own

phase = metrics.current_phase()
if phase is None:
    sys.exit(0)  # no phase in flight - a helper's spend belongs to a phase or to no record at all

try:
    spend = helper_budget.spend_for(
        Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()),
        agent_type=agent_type,
        agent_id=str(payload.get("agent_id") or "").strip(),
    )
except helper_budget.BudgetUnknown:
    sys.exit(0)

# Tokens WHERE THE HARNESS REPORTS THEM. It does not today, in either spelling this looks for, so
# the row says `unrecorded`. A default of zero would read as a free helper rather than an unmeasured
# one, which is the absence-named-as-absence rule this record runs on everywhere else.
usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
tokens = payload.get("total_tokens", usage.get("total_tokens"))
try:
    tokens = int(tokens)
except (TypeError, ValueError):
    tokens = None

metrics.record_helper_spend(
    phase,
    agent_type=agent_type,
    token=spend["token"],
    elapsed_s=spend["elapsed_s"],
    budget_s=spend["budget_s"],
    tokens=tokens,
)
PY

exit 0
