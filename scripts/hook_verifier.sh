#!/usr/bin/env bash
# PostToolUse: run the phase's suite when work is DECLARED DONE — never mid-implementation.
#
# Why not on every src/ edit (what this used to do): the Test-Author locks the suite RED before the
# implementer starts, so red IS the expected state for the whole build. Firing per edit meant every
# partial save ran pytest, paid for a gate model call to triage a failure that was simply "not
# finished yet", and stopped the agent with "route back to Implementer" — telling it to route back to
# itself. That was the real context burn. The implementer runs `pytest tests/<phase>/` itself as its
# inner loop (free, no model); this gate exists for the moment it claims to be finished.
#
# Triggers (both derive the phase from the written path, so no guessing):
#   */specs/<n>.<k>-*/spec.md  with `status: done`  -> smoke-check that spec's phase suite
#   */handover.md                                   -> the Verifier proper (pipeline-conventions §2:
#                                                      once per phase, after every spec is green)
# $PHASE overrides the derived slug. Unresolvable phase -> full suite (minus e2e), never zero tests.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$FILE" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0

# Decide whether this write means "done". Anything else is work in progress -> stay out of the way.
case "$FILE" in
  */handover.md)
    TRIGGER="handover" ;;
  */spec.md)
    # Only when the implementer has marked it done; a spec still being written is the gates' business,
    # not ours.
    grep -qE '^status:[[:space:]]*done[[:space:]]*$' "$FILE" 2>/dev/null || exit 0
    TRIGGER="spec-done" ;;
  *) exit 0 ;;
esac

# Phase slug from the path: docs/features/<f>/phases/<n>-<slug>/{handover.md,specs/<n>.<k>-*/spec.md}
derive_phase() {
  local f="$1" rest
  case "$f" in
    */phases/*) rest="${f#*/phases/}"; printf '%s' "${rest%%/*}" ;;
    *) return 1 ;;
  esac
}

SLUG="${PHASE:-$(derive_phase "$FILE" || true)}"
if [ -n "$SLUG" ] && [ -d "tests/$SLUG" ]; then
  TESTPATH="tests/$SLUG"
  SCOPE="$TESTPATH ($TRIGGER)"
else
  # No phase tests to point at — fall back to everything rather than silently testing nothing.
  TESTPATH=""
  SCOPE="full suite minus e2e ($TRIGGER; phase '${SLUG:-unresolved}' has no tests/ dir)"
fi

if [ -n "$TESTPATH" ]; then
  OUT=$(pytest -q --tb=short "$TESTPATH" 2>&1); pc=$?
else
  OUT=$(pytest -q --tb=short --ignore=tests/e2e 2>&1); pc=$?
fi

# Exit 5 = no tests collected. A phase whose tests don't exist yet is not a failure.
if [ "$pc" -eq 0 ] || [ "$pc" -eq 5 ]; then
  exit 0
fi

TMP=$(mktemp)
{ printf 'scope: %s\n\n' "$SCOPE"; printf '%s\n' "$OUT"; } >"$TMP"
python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/verifier-triage.md" \
  --model "${GATE_MODEL:-google/gemini-2.5-pro}" \
  --author-family "${AUTHOR_FAMILY:-anthropic}" \
  ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
  --target "$TMP"
rc=$?
rm -f "$TMP"
if [ "$rc" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  exec "$SD/bypass_log.sh" "verifier"
fi
exit $rc
