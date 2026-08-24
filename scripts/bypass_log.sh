#!/usr/bin/env bash
# The writer of gate-overrides.log for the hook and Verifier-waiver paths. Records a visible, logged
# override — a whole-gate break-glass
#
# from a hook, or a per-finding waiver acknowledged by the Verifier — then exits 0 so the session
# continues. Never silent, and **exit 0 means the record was written**: an override that could not be
# appended exits 2 — the blocking code every caller here already fails closed on — so the one thing
# that makes an override sanctioned is the one thing that decides whether it proceeds.
#
#   usage: bypass_log.sh <gate-name> [finding-id] [waived-by]
#          bypass_log.sh --gates "<gate> <gate> …"      (gate_ci.sh's multi-gate CI bypass)
#
# SCOPING (issue #59). `$GATE_BYPASS` is one switch, so a break-glass aimed at one gate also waived
# every other gate reached in the same run — a spec-gate bypass meant for the subprocess check also
# waived a cross-family NO-GO. Setting `$GATE_BYPASS_GATES` to a space- or comma-separated list of
# gate names limits the override to exactly those; a gate outside it is REFUSED (exit 2, the same
# blocking code an unlogged override uses, which every caller already fails closed on) and nothing
# is written. Matching is EXACT: `spec-gate` does not cover `spec-gate-requirement-cap`, because
# prefix matching would reinstate the same leak one level down. A `--gates` list needs EVERY name
# allowed, so a scoped override cannot waive a gate it did not name. Unset, the semantics are
# unchanged — scoping is opt-in, so no existing caller breaks.
#
# Requires $GATE_BYPASS (the reason) and $CLAUDE_PROJECT_DIR.
#
# Record grammar — one line per override, tab-separated, REASON ALWAYS LAST because it is the only
# free-text field:
#   <when>\t<who>\tgate:<gate>[\tfinding:<id>]\treason: <text>
# `--gates` writes the same grammar with a `gates:<list>` scope, for `gate_ci.sh`'s multi-gate CI
# bypass. That path used to format its own identical `printf`, so the record grammar lived in two
# places and a change to one desynced the other with nothing failing (issue #10). One writer is now
# true because exactly one `printf` appends to this log, not because a comment says so.
# Nothing else may append to that log by hand: the reason (and a waiver's id/author, which come from
# verdict.json where a JSON string carries newlines freely) is prose, and a raw newline or tab would
# split one override into two records — the second with no timestamp, no author and no scope. That is
# why routing every writer through here is structural rather than a rule anyone has to remember.
set -uo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bypass_reason.sh"
MULTI=0
if [ "${1:-}" = "--gates" ]; then MULTI=1; shift; fi
GATE="$(bypass_reason_oneline "${1:-unknown}")"
FINDING="$(bypass_reason_oneline "${2:-}")"
WAIVED_BY="$(bypass_reason_oneline "${3:-}")"
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LOG="$ROOT/gate-overrides.log"
who="${WAIVED_BY:-$(git -C "$ROOT" config user.email 2>/dev/null || whoami)}"
when="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
reason="$(bypass_reason_oneline "${GATE_BYPASS:-}")"
# The scope check happens BEFORE the append: a refused override must leave no record at all, since a
# line in this log is the assertion that a gate WAS overridden.
if [ -n "${GATE_BYPASS_GATES:-}" ]; then
  allowed=" $(printf '%s' "$GATE_BYPASS_GATES" | tr ',' ' ' | tr -s ' ') "
  for name in $GATE; do
    case "$allowed" in
      *" $name "*) ;;
      *)
        echo "✗ NOT BYPASSED: gate '$name' is not in GATE_BYPASS_GATES." >&2
        echo "  GATE_BYPASS_GATES=\"${GATE_BYPASS_GATES}\" scopes this override to those gates" >&2
        echo "  and to no others, so '$name' still fails and nothing was logged. Add it to the" >&2
        echo "  list (names match exactly, so a prefix does not cover it), or unset" >&2
        echo "  GATE_BYPASS_GATES to override every gate this run reaches." >&2
        exit 2
        ;;
    esac
  done
fi
if [ "$MULTI" -eq 1 ]; then scope="gates:$GATE"; else scope="gate:$GATE"; fi
[ -n "$FINDING" ] && scope="$scope$(printf '\t')finding:$FINDING"
# The append is the whole guarantee, so its failure is this script's failure. An unwritable log (a
# read-only mount, a missing $CLAUDE_PROJECT_DIR, a full disk) used to reach `exit 0` and read to
# every caller as an audited override — the silent bypass the log exists to refuse.
#
# It exits **2**, this repository's blocking code, and not merely non-zero. Every break-glass caller
# hands off with `exec "$SD/bypass_log.sh" …` (hook_spec_gate.sh, hook_verifier.sh's fail(),
# hook_mutation.sh), so this exit code IS the hook's: 1 is not blocking to the harness, and an
# unlogged override that lets the write through is the same silent pass with an extra step.
#
# This message is the whole disclosure — the log line that would have carried it is exactly what
# could not be written.
if ! printf '%s\t%s\t%s\treason: %s\n' "$when" "$who" "$scope" "$reason" >> "$LOG"; then
  echo "✗ NOT LOGGED: could not append to $LOG." >&2
  echo "  The gate was NOT bypassed and the waiver was NOT recorded: an override that is not" >&2
  echo "  audited is not an override. Fix the log destination, then override again." >&2
  exit 2
fi
if [ -n "$FINDING" ]; then
  echo "⚠ WAIVED finding '$FINDING' (gate '$GATE') — reason: ${GATE_BYPASS:-}" >&2
elif [ "$MULTI" -eq 1 ]; then
  echo "⚠ BYPASSED failing gate(s): $GATE — reason: ${GATE_BYPASS:-}" >&2
else
  echo "⚠ BYPASSED gate '$GATE' — reason: ${GATE_BYPASS:-}" >&2
fi
echo "  logged to $LOG. Record this in the phase handover.md." >&2
exit 0
