#!/usr/bin/env bash
# PostToolUse: the MECHANICAL half of the phase gate. Runs the suite when work is DECLARED DONE, and
# on handover checks that the Verifier's committed artifact exists and passes.
#
# Why no model call here: the Verifier is an AGENT (agents/avenger-verifier.md), not a hook. It runs
# in-chat on a cross-family model, reads the phase's tests for the anti-patterns in skills/tdd, and
# persists docs/features/<f>/phases/<n>-<slug>/verdict.json. Model-based gates run in chat; mechanical
# gates run in hooks and CI, which only check the committed artifacts. (pipeline-conventions: "Where
# the models run".) The one exception in this repo is the Fidelity Gate, which is a model hook.
#
# Why not on every src/ edit: the implementer runs a red -> green loop, so red IS an expected state
# throughout a build. Firing per edit stopped the agent to route a failure back to itself.
#
# Triggers (both derive the phase from the written path, so no guessing):
#   */specs/<n>.<k>-*/spec.md  with `status: done`  -> smoke-check that spec's phase suite
#   */handover.md                                   -> full phase suite + require a passing verdict.json
#
# $PHASE overrides the derived slug. Unresolvable phase -> full suite (minus e2e), never zero tests.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (bypass_log)
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$FILE" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0

case "$FILE" in
  */handover.md)
    TRIGGER="handover" ;;
  */spec.md)
    grep -qE '^status:[[:space:]]*done[[:space:]]*$' "$FILE" 2>/dev/null || exit 0
    TRIGGER="spec-done" ;;
  *) exit 0 ;;
esac

# Phase slug and feature from the path:
# docs/features/<feature>/phases/<n>-<slug>/{handover.md,specs/<n>.<k>-*/spec.md}
derive_phase() {
  local f="$1" rest
  case "$f" in
    */phases/*) rest="${f#*/phases/}"; printf '%s' "${rest%%/*}" ;;
    *) return 1 ;;
  esac
}
derive_feature() {
  local f="$1" rest
  case "$f" in
    */docs/features/*|docs/features/*) rest="${f#*docs/features/}"; printf '%s' "${rest%%/*}" ;;
    *) return 1 ;;
  esac
}

SLUG="${PHASE:-$(derive_phase "$FILE" || true)}"
FEATURE="$(derive_feature "$FILE" || true)"

# Layout: tests/<feature>/<n>-<slug>/... ; fall back to tests/<slug> for repos on the older layout.
TESTPATH=""
if [ -n "$FEATURE" ] && [ -n "$SLUG" ] && [ -d "tests/$FEATURE/$SLUG" ]; then
  TESTPATH="tests/$FEATURE/$SLUG"
elif [ -n "$SLUG" ] && [ -d "tests/$SLUG" ]; then
  TESTPATH="tests/$SLUG"
fi

if [ -n "$TESTPATH" ]; then
  SCOPE="$TESTPATH ($TRIGGER)"
  OUT=$(pytest -q --tb=short "$TESTPATH" 2>&1); pc=$?
else
  SCOPE="full suite minus e2e ($TRIGGER; phase '${SLUG:-unresolved}' has no tests dir)"
  OUT=$(pytest -q --tb=short --ignore=tests/e2e 2>&1); pc=$?
fi

fail() {   # $1 = bypass tag, rest = message
  local tag="$1"; shift
  printf '%s\n' "$*" >&2
  [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "$tag"
  exit 2
}

# Exit 5 = no tests collected. A phase whose tests don't exist yet is not a failure.
if [ "$pc" -ne 0 ] && [ "$pc" -ne 5 ]; then
  fail "verifier:tests" "verifier ($SCOPE): the suite is RED — the phase is not done." \
       "$(printf '%s\n' "$OUT" | tail -20)"
fi

[ "$TRIGGER" = "handover" ] || exit 0

# Handover: the Verifier agent must already have run and left a passing verdict.
VERDICT="$(dirname "$FILE")/verdict.json"
if [ ! -f "$VERDICT" ]; then
  fail "verifier:no-verdict" \
    "verifier: no verdict.json next to $FILE." \
    "The phase cannot close on a green suite alone — a green suite the implementer wrote proves only" \
    "that the author agreed with themselves. Run @avenger-verifier (a different model family) first;" \
    "it traces coverage, reads the phase's tests for gamed patterns, and writes verdict.json."
fi

V=$(jq -r '.verdict // "missing"' "$VERDICT" 2>/dev/null) || V="unparseable"
case "$V" in
  pass)
    if [ "$(jq -r '.bypassed // false' "$VERDICT" 2>/dev/null)" = "true" ]; then
      echo "⚠ verifier: phase passed with waived findings (verdict.json bypassed: true) — visible bypass, not a clean green." >&2
    fi
    OPEN=$(jq -r '[.findings[]? | select(.status == "open")] | length' "$VERDICT" 2>/dev/null || echo 0)
    if [ "${OPEN:-0}" -gt 0 ]; then
      fail "verifier:inconsistent" \
        "verifier: verdict.json says 'pass' but still carries $OPEN open finding(s). Fail closed."
    fi
    # A pass must prove the cross-family test-quality review actually happened. Without this an agent
    # could write 'pass' having only run the suite — which is exactly the self-review this gate exists
    # to prevent (pipeline-conventions: "Fresh model ≠ author").
    REVIEWED=$(jq -r '.test_quality.reviewed // false' "$VERDICT" 2>/dev/null)
    NFILES=$(jq -r '[.test_quality.scope.test_files[]?] | length' "$VERDICT" 2>/dev/null || echo 0)
    if [ "$REVIEWED" != "true" ] || [ "${NFILES:-0}" -eq 0 ]; then
      fail "verifier:unreviewed" \
        "verifier: verdict.json is 'pass' but records no completed test-quality review" \
        "(test_quality.reviewed=$REVIEWED, ${NFILES:-0} file(s) in scope)." \
        "A green suite the implementer wrote is not evidence. Run scripts/verifier_review.sh over the" \
        "bounded review set on a cross-family model, then record its scope and findings in verdict.json."
    fi
    exit 0 ;;
  fail)
    fail "verifier" "verifier: verdict.json is 'fail' — route back per its findings:" \
      "$(jq -r '.routed[]? | "  - \(.to): \(.reason) (\(.spec_id // "-")) finding \(.finding_id)"' "$VERDICT" 2>/dev/null)" ;;
  *)
    fail "verifier:unparseable" "verifier: verdict.json has no readable verdict ('$V'). Fail closed." ;;
esac
