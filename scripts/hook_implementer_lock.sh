#!/usr/bin/env bash
# PreToolUse hook: refuse to spawn an implementer while another implementer is still in the worktree.
#
# WHAT THIS REPLACES. Issue #68, measured in clickup-agents phase 11: an implementer stamps its spec
# `status: done` and then KEEPS WORKING - test-mapping.md, test-evidence.md and the phase's mutation
# gate all land after the stamp. A phase worker had armed a condition-wait on that stamp as a wedge
# guard and it fired at 24 minutes while the agent was still running. Had the next spec's implementer
# been dispatched on that signal, two implementers would have been running in one worktree against
# one database - forbidden outright, and phase 9 measured why: a git stash from one swallowed the
# other's uncommitted work, and the shared database produced foreign-key violations plus a spurious
# lint failure. The tell was two workers reporting suite totals one apart.
#
# `scripts/spec_done_guard.py` made the stamp self-correcting. It did not produce a COMPLETION
# SIGNAL. The issue's own fix direction says the remedy may not be "tell people not to wait on the
# stamp", because that is a sentence claiming behaviour nothing enforces. So the signal comes from
# the implementer FINISHING - `SubagentStop`, recorded by `scripts/hook_activity.sh` - and it is
# read by `scripts/implementer_liveness.py`, which nothing can move by writing a document.
#
# WHY PreToolUse. This is the one event that can actually refuse, and the one moment the remedy
# still exists: immediately before the second implementer would start writing. `SubagentStart`
# cannot block (exit 2 there shows stderr and the subagent proceeds), so a rule written there would
# be one more thing that looks enforced and is not - the same reasoning as hook_plugin_release.sh.
#
# WHAT IT BINDS. Only the two stages that WRITE to the working copy. The Verifier, the Breaker and
# the bug-hunter run beside an implementer by design and are not bound; `Explore`, `general-purpose`
# and every foreign subagent are none of this hook's business.
#
# EVERYTHING THAT IS NOT A VERDICT FAILS OPEN, and says so. No jq, no python3, an unreadable payload,
# no activity log (the hook may be off, or this may not be a pipeline run), an unusable
# IMPLEMENTER_AGENTS pattern - each lets the spawn through with a line on stderr, because "could not
# look" and "all clear" must never arrive looking alike (CLAUDE.md 3a). The verdict is read as BOTH
# the exit code AND the checker's own LIVE marker, so a traceback - which also exits 1 - is not
# mistaken for a finding.
#
#   IMPLEMENTER_AGENTS      regex of subagent_type values this binds (unanchored, case-insensitive).
#   IMPLEMENTER_MAX_AGE_S   seconds after which a start with no stop is presumed dead (default 4h),
#                           so a crashed agent cannot hold the lock forever.
#   IMPLEMENTER_LOCK_OFF=1  disable the refusal entirely.
#   GATE_BYPASS="reason"    proceed anyway; audited in gate-overrides.log.
#
# opencode does not carry this: its adapter hooks `tool.execute.after`, which is after the fact and
# has no pre-spawn event to refuse at.
set -uo pipefail

[ "${IMPLEMENTER_LOCK_OFF:-0}" = "1" ] && exit 0

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # IMPLEMENTER_* may live in the project .env

PAYLOAD="$(cat)"

# Decided on `subagent_type`, never on the tool's NAME: the spawn tool is `Task` in some harness
# versions and `Agent` in others, and a hook keyed to the wrong name would silently never fire.
STAGE=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)
[ -n "$STAGE" ] || exit 0

# WHICH stages are bound is decided by the module, not here. The same `IMPLEMENTER_AGENTS` pattern
# answers "is this stage an implementer" and "is a live entry an implementer", and a second copy of
# it in this shell would be one more fact stated twice - the drift this repository keeps paying for.
# An unbound stage comes back as "none live" and costs one python start.
REPORT=$(python3 "$SD/implementer_liveness.py" live --stage "$STAGE" \
  --root "${CLAUDE_PROJECT_DIR:-$PWD}" 2>&1)
RC=$?

if [ "$RC" -eq 0 ]; then
  exit 0
fi

if [ "$RC" -ne 1 ] || ! printf '%s\n' "$REPORT" | grep -q '\[implementer-liveness\] LIVE:'; then
  printf '%s\n' \
    "implementer lock: cannot tell whether another implementer is running (exit $RC) - the spawn is" \
    "NOT blocked, because a check that could not look is not a finding. Its output follows." \
    "$REPORT" >&2
  exit 0
fi

printf '%s\n' \
  "implementer lock: another implementer is STILL RUNNING in this working copy, so '$STAGE' would" \
  "be the second writer in it." \
  "" \
  "$REPORT" >&2

if [ -n "${GATE_BYPASS:-}" ]; then
  printf '%s\n' "Proceeding under break-glass; the spawn is NOT blocked." >&2
  exec "$SD/bypass_log.sh" "implementer-lock"
fi

printf '%s\n' \
  "" \
  "Two implementers in one worktree against one database is forbidden outright: a git stash from" \
  "one swallowed the other's uncommitted work, and the shared database produced foreign-key" \
  "violations plus a spurious lint failure. The only tell was two workers reporting suite totals" \
  "one apart." \
  "" \
  "Wait for the running implementer to FINISH. A spec's \`status: done\` stamp is NOT that signal -" \
  "its own implementer writes it and then keeps working (issue #68). The signal is the agent's own" \
  "stop event, which is what this check reads." \
  "" \
  "If that agent is already gone and its stop was never recorded, it ages out of the lock after" \
  "\$IMPLEMENTER_MAX_AGE_S (default 4 hours), or re-run with GATE_BYPASS=\"<reason>\" - visible and" \
  "logged to gate-overrides.log." >&2

exit 2
