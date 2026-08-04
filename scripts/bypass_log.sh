#!/usr/bin/env bash
# The ONE writer of gate-overrides.log. Records a visible, logged override — a whole-gate break-glass
# from a hook, or a per-finding waiver acknowledged by the Verifier — then exits 0 so the session
# continues. Never silent.
#
#   usage: bypass_log.sh <gate-name> [finding-id] [waived-by]
#
# Requires $GATE_BYPASS (the reason) and $CLAUDE_PROJECT_DIR.
#
# Record grammar — one line per override, tab-separated, REASON ALWAYS LAST because it is the only
# free-text field:
#   <when>\t<who>\tgate:<gate>[\tfinding:<id>]\treason: <text>
# `scripts/gate_ci.sh` writes the same grammar with a `gates:<list>` scope for a multi-gate CI bypass.
# Nothing else may append to that log by hand: the reason (and a waiver's id/author, which come from
# verdict.json where a JSON string carries newlines freely) is prose, and a raw newline or tab would
# split one override into two records — the second with no timestamp, no author and no scope. That is
# why routing every writer through here is structural rather than a rule anyone has to remember.
set -uo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bypass_reason.sh"
GATE="$(bypass_reason_oneline "${1:-unknown}")"
FINDING="$(bypass_reason_oneline "${2:-}")"
WAIVED_BY="$(bypass_reason_oneline "${3:-}")"
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LOG="$ROOT/gate-overrides.log"
who="${WAIVED_BY:-$(git -C "$ROOT" config user.email 2>/dev/null || whoami)}"
when="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
reason="$(bypass_reason_oneline "${GATE_BYPASS:-}")"
scope="gate:$GATE"
[ -n "$FINDING" ] && scope="$scope$(printf '\t')finding:$FINDING"
printf '%s\t%s\t%s\treason: %s\n' "$when" "$who" "$scope" "$reason" >> "$LOG"
if [ -n "$FINDING" ]; then
  echo "⚠ WAIVED finding '$FINDING' (gate '$GATE') — reason: ${GATE_BYPASS:-}" >&2
else
  echo "⚠ BYPASSED gate '$GATE' — reason: ${GATE_BYPASS:-}" >&2
fi
echo "  logged to $LOG. Record this in the phase handover.md." >&2
exit 0
