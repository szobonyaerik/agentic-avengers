#!/usr/bin/env bash
# PostToolUse: when src/ changes, run the suite; on failure, triage and stop.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
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
python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/verifier-triage.md" \
  --model "${GATE_MODEL:-google/gemini-2.5-pro}" \
  --author-family "${AUTHOR_FAMILY:-anthropic}" \
  --target "$TMP"
rc=$?
rm -f "$TMP"
if [ "$rc" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  exec "$SD/bypass_log.sh" "verifier"
fi
exit $rc