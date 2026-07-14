#!/usr/bin/env bash
# Break-glass logger for in-session hooks. Records a visible, logged override of a failing gate,
# then exits 0 so the session continues. Never silent.
#   usage: bypass_log.sh <gate-name>
# Requires $GATE_BYPASS (the reason) and $CLAUDE_PROJECT_DIR.
set -uo pipefail
GATE="${1:-unknown}"
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LOG="$ROOT/gate-overrides.log"
who="$(git -C "$ROOT" config user.email 2>/dev/null || whoami)"
when="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s\t%s\tgate:%s\treason: %s\n' "$when" "$who" "$GATE" "${GATE_BYPASS:-}" >> "$LOG"
echo "⚠ BYPASSED gate '$GATE' — reason: ${GATE_BYPASS:-}" >&2
echo "  logged to $LOG. Record this in the phase handover.md." >&2
exit 0
