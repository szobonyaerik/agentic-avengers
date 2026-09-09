#!/usr/bin/env bash
# PreToolUse hook: a helper agent is dispatched with a declared budget, or not at all.
#
# WHAT THIS REPLACES. Issue #118, measured with the harness panel's own timers. One stage held five
# helper agents at once; two captures ninety minutes apart showed IDENTICAL timers and token counts
# on all five, while the parent's progress artifact had not moved for 64 minutes and its process
# stayed alive - so every liveness check read the run as healthy. The largest helper had spent
# 1h 8m and 341.4k tokens deciding whether two amendment ids were marked done, which is one grep.
# Three of the five were questions a shell command answers. A human eventually read the panel and
# told the parent to abandon the helpers and run the checks itself; it resumed within a minute.
#
# WHY PreToolUse. This is the one event that can refuse, and the last moment the remedy exists -
# before the sixth helper joins five frozen ones. `SubagentStart` cannot block, so a rule written
# there would be one more thing that looks enforced and is not, the same reasoning as
# hook_implementer_lock.sh and hook_plugin_release.sh.
#
# WHAT IT BINDS. Helpers, meaning every spawn that is NOT an `avenger-*` pipeline stage: the stage
# chain is the pipeline's own sequence, dispatched by the orchestrator and bounded by its phase.
# WHICH spawns those are is one pattern in `scripts/helper_budget.py`, never a second copy here.
#
# WHAT IS MECHANISM AND WHAT IS NOT. Two halves are decided here: a dispatch with no declared budget
# is refused, and a dispatch made while an existing helper is already past ITS declared budget is
# refused. ABANDONING an overrunning helper mid-wait is NOT mechanised - the harness exposes no
# timeout on a delegation and fires no event inside a parent that is blocked - so that half is
# carried by the rule in skills/pipeline-conventions. Whether a question has a one-command answer is
# a judgement no static rule makes, and this never claims to decide it.
#
# EVERYTHING THAT IS NOT A VERDICT FAILS OPEN, and says so. No python3, an unreadable payload, an
# unusable HELPER_AGENTS pattern, no activity log - each lets the spawn through with a line on
# stderr, because "could not look" and "all clear" must never arrive looking alike (CLAUDE.md 3a).
# The verdict is read as BOTH the exit code AND the checker's own REFUSE marker, so a traceback -
# which also exits 1 - is not mistaken for a finding.
#
#   HELPER_AGENTS           regex of subagent_type values this binds (anchored, case-insensitive).
#   HELPER_BUDGET_MAX_S     ceiling on a DECLARED budget (default 1800), so `Budget: 10h` is not a
#                           budget. Raise it deliberately, never to get past a refusal.
#   HELPER_BUDGET_OFF=1     disable the refusal entirely.
#   GATE_BYPASS_GATES="helper-budget" GATE_BYPASS="reason"
#                           proceed anyway, waiving THIS gate alone; audited in gate-overrides.log.
#
# opencode does not carry this: its adapter hooks `tool.execute.after`, which is after the fact.
set -uo pipefail

[ "${HELPER_BUDGET_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # HELPER_* may live in the project .env

# The payload reaches the checker as a FILE, never as an argument: a delegation prompt is arbitrary
# author text of arbitrary length, and putting it on a command line is both an argv limit and the
# quoting hazard skills/pipeline-conventions forbids for prose.
PAYLOAD_FILE="$(mktemp -t helper-budget.XXXXXX 2>/dev/null)" || exit 0
trap 'rm -f "$PAYLOAD_FILE"' EXIT
cat > "$PAYLOAD_FILE"

REPORT=$(python3 "$SD/helper_budget.py" check --payload-file "$PAYLOAD_FILE" \
  --root "${CLAUDE_PROJECT_DIR:-$PWD}" 2>&1)
RC=$?

if [ "$RC" -eq 0 ]; then
  [ -n "$REPORT" ] && printf '%s\n' "$REPORT" >&2
  # The clean result carries what it does NOT establish beside it: a later stage reads output, not
  # source (issue #97). This hook's statement is about the REFUSAL it did not issue; the checker's
  # own statement, already in $REPORT, is about the two halves it decided.
  python3 "$SD/guard_scope.py" emit hook_helper_budget.sh >/dev/null || true
  exit 0
fi

if [ "$RC" -ne 1 ] || ! printf '%s\n' "$REPORT" | grep -q '\[helper-budget\] REFUSE:'; then
  printf '%s\n' \
    "helper budget: cannot tell whether this dispatch is bounded (exit $RC) - the spawn is NOT" \
    "blocked, because a check that could not look is not a finding. Its output follows." \
    "$REPORT" >&2
  exit 0
fi

printf '%s\n' "$REPORT" >&2

if [ -n "${GATE_BYPASS:-}" ]; then
  printf '%s\n' "Proceeding under break-glass; the spawn is NOT blocked." >&2
  exec "$SD/bypass_log.sh" "helper-budget"
fi

exit 2
