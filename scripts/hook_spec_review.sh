#!/usr/bin/env bash
# PostToolUse: hands-off AUTOMATED spec-review. No-op unless SPEC_REVIEW_MODE=auto.
# On a spec.md write, run the cross-family checklist gate; GO/REVIEW -> stamp review_status: approved
# in place; NO-GO -> stop (fail closed, surfaces route-back). HITL mode is the default and skips this.
set -uo pipefail

[ "${SPEC_REVIEW_MODE:-}" = "auto" ] || exit 0   # HITL (default) -> nothing to do

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */spec.md) ;;
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0

# Skip when the spec's BODY is unchanged since this gate last judged it — a frontmatter-only edit
# (`status: done`, a verdict stamp) must not re-run a paid gate. Own key, so fidelity's stamp can't
# make this one skip. Exit 1 = unchanged; anything else falls through and gates.
python3 "$SD/spec_gate_cache.py" check "$FILE" review; cached=$?
[ "$cached" -eq 1 ] && exit 0

VERDICT=$(python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/spec-review-rubric.md" \
  --model "${GATE_MODEL:-google/gemini-2.5-pro}" --author-family "${AUTHOR_FAMILY:-anthropic}" \
  ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
  --print-verdict --target "$FILE" 2> >(cat >&2))
rc=$?

# Fail closed: any error (missing key, same-family, no verdict) stops — do not approve.
if [ "$rc" -ne 0 ]; then
  [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "spec-review-auto"
  echo "spec-review (auto): gate errored (fail closed) — review_status left pending" >&2
  exit 2
fi

case "$VERDICT" in
  GO|REVIEW)
    # In-place stamp (shell edit — does not re-trigger PostToolUse hooks).
    if grep -q '^review_status:[[:space:]]*pending' "$FILE"; then
      sed -i.bak 's/^review_status:[[:space:]]*pending/review_status: approved/' "$FILE" && rm -f "$FILE.bak"
      echo "spec-review (auto): $VERDICT -> review_status: approved  ($FILE)" >&2
    fi
    # Record the body this verdict was reached against, so frontmatter-only edits don't re-gate.
    python3 "$SD/spec_gate_cache.py" stamp "$FILE" review >/dev/null 2>&1
    exit 0
    ;;
  *)  # NO-GO or anything unexpected
    [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "spec-review-auto"
    echo "spec-review (auto): NO-GO — review_status stays pending; route back to avenger-spec-writer" >&2
    exit 2
    ;;
esac
