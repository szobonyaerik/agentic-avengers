#!/usr/bin/env bash
# SubagentStop hook: ask a finishing stage's skill contract at the one moment it can still answer.
#
# `required_skills.py audit` has always blocked on a required skill with no observed load, and it ran
# in exactly two places, both at the end: the handover hook and CI. Measured on clickup-agents phase
# 10, that meant `avenger-spec-writer` learned it had never loaded `skills/spec-review-checklist` at
# the contract card — every spec of the phase already written, gated, reviewed and implemented. The
# most expensive moment the pipeline has, and the one at which the prescribed remedy ("open the file
# in that stage") no longer exists, because the stage is gone. A genuine requirement therefore
# arrived as a blocker on the artifact that did not cause it.
#
# This asks the same question of the stage that is finishing, scoped to it, while it is still alive:
#
#   * SAME JUDGEMENT. The gap is computed by the same `audit_gaps`, from the same per-phase record,
#     with the same wording and the same exit code. Nothing here decides what counts as a gap; only
#     WHEN it is asked moves, and therefore what it costs to answer.
#   * IT IS NOT A REPLACEMENT. `hook_verifier.sh` and `gate_ci.sh --full` still audit at close.
#     opencode has no `SubagentStop`, and the main thread — which writes specs and runs verifier
#     triage — reaches none either, so removing the backstop would trade a covered path for an
#     uncovered one.
#   * IT NEVER BLOCKS TWICE. `stop_hook_active` means this hook already spoke and the stage came
#     back; blocking again on an unchanged record is a loop, and a loop costs more than the defect.
#     The second pass still says it out loud, on stderr, and lets the stage finish.
#   * A BLOCKED STOP IS NOT A STOP. This is the first hook in this repo that can refuse a
#     `SubagentStop`, and `hook_activity.sh` has already logged that stop by the time it does — so
#     `.agent-activity.jsonl` says the stage ended while it is in fact still working, and
#     `pipeline_metrics.observing_stage` then reads no live subagent and attributes the remedial
#     `Read` of the named SKILL.md to `main-thread`. That writes `main-thread:<skill>` and leaves
#     the stage's own row `loaded: false`: the prescribed remedy could not clear the gap it was
#     prescribed for. Refusing the stop therefore re-opens the lifecycle through the log's one
#     writer, so the record stays true to who is running. The real stop that follows is logged
#     normally and balances it.
#   * IT FAILS OPEN, unlike its gate siblings, and deliberately: it is the EARLY copy of a check that
#     still blocks at close. An unreadable payload, an agent this pipeline does not own, no phase in
#     flight, no metrics writer — each lets the stage finish, and the close-time audit is what makes
#     that safe. A gate that fails closed here would stop stages on no evidence at all.
#
#   SKILL_AUDIT_OFF=1   disable this early audit (the close-time one is unaffected).
#   SKILLS_OFF=1        delivery is off, so nothing was owed — honoured by the audit itself.
set -uo pipefail

[ "${SKILL_AUDIT_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # SKILL_* and AVENGER_METRICS_* may live in the project .env

PAYLOAD="$(cat)"

# The two fields this hook reads. A payload that does not parse yields neither, and an empty
# agent_type exits below rather than being guessed at — attributing a stop to the wrong stage would
# stop a stage for a contract that is not its own.
AGENT=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // empty' 2>/dev/null)
REENTRY=$(printf '%s' "$PAYLOAD" | jq -r 'if .stop_hook_active then "1" else "" end' 2>/dev/null)

[ -z "$AGENT" ] && exit 0

# An agent this pipeline does not own has an empty contract by construction, so the audit would be a
# no-op — but resolving that costs a metrics read, and this hook runs on every subagent stop in the
# session, most of which are Explore, general-purpose and other foreign agents.
CONTRACT=$(python3 "$SD/required_skills.py" for "$AGENT" 2>/dev/null)
[ -z "$CONTRACT" ] && exit 0

REPORT=$(python3 "$SD/required_skills.py" audit --stage "$AGENT" 2>&1)
STATUS=$?

# Exit 1 is the audit's one verdict: a required skill with no observed load. Anything else — a clean
# audit, no phase, no writer, a crash — lets the stage finish, and the close-time audit still holds.
if [ "$STATUS" -ne 1 ]; then
  exit 0
fi

printf '%s\n' "$REPORT" >&2

if [ -n "$REENTRY" ]; then
  printf '%s\n' \
    "skill audit: still unanswered, and this hook has already stopped this agent once — not" \
    "stopping it again. The phase's close-time audit (scripts/required_skills.py audit) will" \
    "refuse the handover until the named skill is loaded in this stage." >&2
  exit 0
fi

# The stage is going back to work, so it is live again. Replayed through `hook_activity.sh` rather
# than written here: that hook is the log's only writer, and it owns ACTIVITY_OFF, ACTIVITY_LOG and
# rotation. Best-effort by construction — a lost re-open costs an attribution, never the block.
printf '%s' "$PAYLOAD" \
  | jq -c '.hook_event_name = "SubagentStart"' 2>/dev/null \
  | bash "$SD/hook_activity.sh" >/dev/null 2>&1 || true

printf '%s\n' \
  "" \
  "You were pointed at a REQUIRED skill and no load of it was ever observed. Reading the named" \
  "SKILL.md is what records the load — the record is written by observation, so there is nothing" \
  "to report and nothing to claim. Open it now and finish your work under it." \
  "" \
  "This is asked here, at your stop, because it is the last moment it is cheap: unanswered, the" \
  "same gap refuses the phase's contract card, after every spec of the phase has been written," \
  "gated and implemented, with no way left to load a skill into a stage that has already ended." >&2
exit 2
