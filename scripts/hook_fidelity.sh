#!/usr/bin/env bash
# PostToolUse: when a spec.md is written/edited, run the fidelity gate and stamp fidelity_verdict.
# Plugin assets (gate_runner, prompts, bypass_log) resolve from THIS script's dir ($SD) — under the
# Claude Code plugin they live in the plugin cache, not the target project. Project paths
# ($CLAUDE_PROJECT_DIR) are used only for the spec being judged and the override log.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */spec.md) ;;
  *) exit 0 ;;
esac
# Resolve $FILE relative to the project like the sibling hooks do. Claude Code sends an absolute
# path, but the opencode adapter can hand us a project-relative one — without this, the gate would
# fail to open the spec and fail CLOSED, blocking the turn over a path bug.
cd "$CLAUDE_PROJECT_DIR" || exit 0

# Skip when the spec's BODY is unchanged since it was last gated. Frontmatter-only edits (the
# implementer stamping `status: done`, a verdict being written) must not re-run a paid gate — and
# must not re-roll a fresh nondeterministic verdict over an already-approved spec. Exit 1 = unchanged;
# anything else (incl. errors) falls through and gates.
python3 "$SD/spec_gate_cache.py" check "$FILE" fidelity; cached=$?
[ "$cached" -eq 1 ] && exit 0

VERDICT=$(python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/fidelity-rubric.md" \
  --model "${GATE_MODEL:-deepseek/deepseek-chat}" \
  --author-family "${AUTHOR_FAMILY:-anthropic}" \
  ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
  --print-verdict --target "$FILE" 2> >(cat >&2))
rc=$?

# Fail closed: missing key, unreachable model, same-family, or no verdict -> stop.
if [ "$rc" -ne 0 ]; then
  [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "fidelity"
  echo "fidelity gate errored (fail closed) — see above" >&2
  exit 2
fi

# Stamp the verdict into the spec frontmatter (shell edit — does not re-trigger PostToolUse hooks).
if grep -q '^fidelity_verdict:' "$FILE"; then
  sed -i.bak "s/^fidelity_verdict:.*/fidelity_verdict: $VERDICT/" "$FILE" && rm -f "$FILE.bak"
fi

case "$VERDICT" in
  GO|REVIEW)
    # Record the body this verdict was reached against, so frontmatter-only edits don't re-gate.
    python3 "$SD/spec_gate_cache.py" stamp "$FILE" fidelity >/dev/null 2>&1
    echo "fidelity: $VERDICT ($FILE)" >&2; exit 0 ;;
  *)  # NO-GO
    [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "fidelity"
    echo "fidelity: NO-GO — route back to avenger-spec-writer" >&2
    exit 2 ;;
esac
