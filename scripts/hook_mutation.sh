#!/usr/bin/env bash
# PostToolUse: when a phase implementation-report.md is written, run mutation testing.
set -uo pipefail
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */implementation-report.md) ;;
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0
TMP=$(mktemp)
{ mutmut run; echo "---- results ----"; mutmut results; } >"$TMP" 2>&1 || true
# Always let the model read the mutmut output and decide GO (no survivors) vs NO-GO.
# This is more robust than grepping mutmut's version-specific output in bash.
python3 "$CLAUDE_PROJECT_DIR/scripts/gate_runner.py" \
  --rubric "$CLAUDE_PROJECT_DIR/prompts/mutation-interpret.md" \
  --model google/gemini-2.5-pro \
  --target "$TMP"
rc=$?
rm -f "$TMP"
exit $rc