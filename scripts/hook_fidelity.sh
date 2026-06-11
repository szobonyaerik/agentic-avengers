#!/usr/bin/env bash
# PostToolUse: when a spec.md is written/edited, run the fidelity gate.
set -uo pipefail
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */spec.md)
    exec python3 "$CLAUDE_PROJECT_DIR/scripts/gate_runner.py" \
      --rubric "$CLAUDE_PROJECT_DIR/prompts/fidelity-rubric.md" \
      --model deepseek/deepseek-chat \
      --target "$FILE"
    ;;
esac
exit 0