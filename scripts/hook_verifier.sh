#!/usr/bin/env bash
# PostToolUse: when src/ changes, run the ACTIVE PHASE's tests; on failure, triage and stop.
#
# Scoped on purpose. This fires on every code edit, and its output is piped into a gate model —
# running the whole suite with full tracebacks made every edit pay for every test in the repo. It
# runs the active phase's dir with --tb=short instead, and skips tests/e2e/ (those are feature-level
# and run at feature close, not per edit).
#
# Phase resolution: $PHASE wins; otherwise the most recently modified docs/features/*/phases/*/.
# If neither resolves, fall back to the FULL suite — a hook that silently tests nothing is worse
# than a slow one.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# CODE_PATH_GLOB (project .env): comma-separated glob list for non-src/ layouts, e.g.
# "core/*,migrations/*,agents/*". Defaults to the historical src/-only glob.
match_code_path() {
  local f="$1" pat
  IFS=',' read -ra pats <<<"${CODE_PATH_GLOB:-*/src/*,src/*}"
  for pat in "${pats[@]}"; do
    case "$f" in
      $pat) return 0 ;;
    esac
  done
  return 1
}
match_code_path "$FILE" || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0

# Resolve the active phase slug (e.g. "1-webhook") -> tests/<slug>/ if that dir exists.
resolve_phase_tests() {
  local slug dir
  if [ -n "${PHASE:-}" ]; then
    [ -d "tests/$PHASE" ] && { printf 'tests/%s' "$PHASE"; return 0; }
    return 1
  fi
  # Most recently touched phase dir. `ls -td` orders by mtime; the pipeline writes into the phase
  # it is working on, so this tracks the active phase without extra state.
  for dir in $(ls -td docs/features/*/phases/*/ 2>/dev/null); do
    slug=$(basename "$dir")
    [ -d "tests/$slug" ] && { printf 'tests/%s' "$slug"; return 0; }
  done
  return 1
}

if TESTPATH=$(resolve_phase_tests); then
  OUT=$(pytest -q --tb=short "$TESTPATH" 2>&1); pc=$?
  SCOPE="$TESTPATH"
else
  OUT=$(pytest -q --tb=short --ignore=tests/e2e 2>&1); pc=$?
  SCOPE="full suite (no active phase resolved)"
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
  --target "$TMP"
rc=$?
rm -f "$TMP"
if [ "$rc" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  exec "$SD/bypass_log.sh" "verifier"
fi
exit $rc
