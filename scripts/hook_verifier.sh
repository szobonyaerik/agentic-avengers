#!/usr/bin/env bash
# PostToolUse: when src/ changes, run the suite; on failure, triage and stop.
set -uo pipefail
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */src/*|src/*) ;;     # adjust this glob to your code layout (e.g. app/*, packages/*)
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0
if OUT=$(pytest -q 2>&1); then
  exit 0                # all green -> no objection
fi
TMP=$(mktemp)
printf '%s\n' "$OUT" >"$TMP"
python3 "$CLAUDE_PROJECT_DIR/scripts/gate_runner.py" \
  --rubric "$CLAUDE_PROJECT_DIR/prompts/verifier-triage.md" \
  --model google/gemini-2.5-pro \
  --target "$TMP"
rc=$?
rm -f "$TMP"
exit $rc