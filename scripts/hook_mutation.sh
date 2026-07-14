#!/usr/bin/env bash
# PostToolUse: when a phase handover.md is written, run the per-phase mutation gate (cosmic-ray).
# The Verifier runs once per phase — after every spec in the phase is green and the handover lands.
set -uo pipefail
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */handover.md) ;;
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0

AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
CFG="$CLAUDE_PROJECT_DIR/cosmic-ray.toml"
if [ ! -f "$CFG" ]; then
  echo "cosmic-ray.toml missing at repo root — mutation gate cannot run (fail closed)" >&2
  exit 2
fi

SESSION="$CLAUDE_PROJECT_DIR/session.sqlite"; TMP=$(mktemp); rm -f "$SESSION"
if cosmic-ray init "$CFG" "$SESSION" >>"$TMP" 2>&1 \
   && cosmic-ray exec "$CFG" "$SESSION" >>"$TMP" 2>&1; then
  { echo "---- survivors (cosmic-ray dump) ----"; cosmic-ray dump "$SESSION" 2>>"$TMP"; \
    echo "---- survival rate (cr-rate) ----"; cr-rate "$SESSION" 2>>"$TMP"; } >>"$TMP" 2>&1
else
  echo "cosmic-ray run errored (fail closed):" >&2; tail -5 "$TMP" >&2
  rm -f "$TMP" "$SESSION"
  [ -n "${GATE_BYPASS:-}" ] && exec "$CLAUDE_PROJECT_DIR/scripts/bypass_log.sh" "mutation:errored"
  exit 2
fi

# Feed the survivor report to the model (one call per phase); it decides GO vs NO-GO.
python3 "$CLAUDE_PROJECT_DIR/scripts/gate_runner.py" \
  --rubric "$CLAUDE_PROJECT_DIR/prompts/mutation-interpret.md" \
  --model google/gemini-2.5-pro --author-family "$AUTHOR_FAMILY" \
  --target "$TMP"
rc=$?
rm -f "$TMP" "$SESSION"
if [ "$rc" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  exec "$CLAUDE_PROJECT_DIR/scripts/bypass_log.sh" "mutation"
fi
exit $rc
