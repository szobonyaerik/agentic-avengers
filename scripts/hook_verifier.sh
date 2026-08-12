#!/usr/bin/env bash
# PostToolUse: the MECHANICAL half of the phase gate. Runs the suite when work is DECLARED DONE, and
# on handover checks that the Verifier's committed artifact exists and passes.
#
# Why no model call here: the Verifier is an AGENT (agents/avenger-verifier.md), not a hook. It runs
# in-chat on a cross-family model, reads the phase's tests for the anti-patterns in skills/tdd, and
# persists docs/features/<f>/phases/<n>-<slug>/verdict.json. Model-based gates run in chat; mechanical
# gates run in hooks and CI, which only check the committed artifacts. (pipeline-conventions: "Where
# the models run".) The one exception in this repo is the spec gate, which is a model hook.
#
# This hook also runs the Verifier's MECHANICAL pre-check (scripts/verifier_precheck.py) and the
# amendment obligation (scripts/amendments.py due), because both are decidable without a model and
# 26% of everything the Verifier raised across one measured feature was that class.
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

# Measurement, never a gate. A handover being written is the phase landing, so this is where its
# close, its wall clock and the suite it landed with are stamped — and it is stamped BEFORE the
# suite runs below, because a phase this hook stops still spent every minute and every test it
# spent, and those numbers are unrecoverable once the run is over. `set` overwrites, so a handover
# rewritten after a route-back converges on the last one rather than accumulating.
if [ "$TRIGGER" = "handover" ]; then
  python3 "$SD/pipeline_metrics.py" phase-close "$FILE" >/dev/null 2>&1 || true
fi

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

PHASE_DIR="$(dirname "$FILE")"

# The bookkeeping the Verifier used to raise by hand, once per phase, as 26% of its findings — an
# untraced requirement id, a stale gate stamp, a deleted `## Acceptance criteria` heading. All of it
# is mechanically decidable, so it is decided here, for no tokens. The Verifier keeps only what no
# script can do: coverage judged per `binding:`, reading a green suite for gamed tests, and
# adversarial execution against secrets, resource lifetimes and concurrency invariants.
if ! python3 "$SD/verifier_precheck.py" "$PHASE_DIR"; then
  fail "verifier:precheck" \
    "verifier pre-check: the phase's own bookkeeping does not hold (named above)." \
    "These are mechanical, so fix them mechanically — do not spend a verification attempt on them."
fi

# Required skills a stage was owed and was never observed loading. A pointer is the cheap half of
# delivery — it saves injecting a large skill body on every spawn — and this audit is the other half:
# without it a pointer is exactly the "load skills/tdd before you start" instruction-with-no-mechanism
# that the delivery was introduced to replace. A phase does not close over an unobserved required
# load. Fail closed; the script names the stage and the skill.
#
# SCOPED TO THE PHASE IN FLIGHT, and it needs no session id to be: the evidence lives in the
# per-phase metrics record and `hook_skill_load.sh` writes nothing when no phase is in flight, so a
# pointer delivered in phase 1 cannot block phase 8 by construction. Same "you are responsible for
# what you change" rule as verifier_precheck above. `gate_ci.sh --full` sweeps every phase.
if ! python3 "$SD/required_skills.py" audit; then
  fail "verifier:skills" \
    "verifier: a stage in this phase required a skill and no load of it was ever observed." \
    "Open the named SKILL.md in that stage — reading it is what records the load — or re-run the" \
    "stage: a stage that never loaded its rules did not run under them."
fi

# Amendments owed re-verification NOW: every security-relevant one, always, plus any pending one on
# a phase whose verdict already passes. Batching is a cost optimisation and it does not apply to a
# credential already exposed.
if ! python3 "$SD/amendments.py" due "$PHASE_DIR"; then
  fail "verifier:amendments" \
    "verifier: this phase has amendments owed re-verification (named above). Re-verify the" \
    "requirement ids they name — not the whole phase — then close each with" \
    "scripts/amendments.py close <phase-dir> <A-id> --evidence <path>."
fi

# Handover: the Verifier agent must already have run and left a passing verdict.
VERDICT="$PHASE_DIR/verdict.json"
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
    # At the attempt cap the loop STOPS here, and stopping is the whole point: 80% of re-attempts
    # measured across one feature were the Verifier routing back to itself. This used to be
    # `|| true` — a cap that printed a notice and then routed back anyway, which is a limit that
    # never fires while reading as one that does, and it would have made H4's
    # `verification_attempts` metric look bounded by a bound that did nothing.
    #
    # The route-back below is deliberately NOT reached at the cap: the three honest remedies are to
    # carry the remainder as known-open in handover.md, waive it explicitly, or escalate. Break-glass
    # still applies through fail(), because escapable and audited beats a hard wedge.
    python3 "$SD/verifier_attempts.py" check "$PHASE_DIR"; cap_rc=$?
    if [ "$cap_rc" -eq 1 ]; then
      fail "verifier:attempt-cap" \
        "verifier: the verification loop is at its cap (the series is above) — a further attempt is" \
        "refused. Carry the remaining findings as KNOWN-OPEN in handover.md, waive them explicitly" \
        "(scripts/bypass_log.sh verifier <finding-id> <who>), or escalate to a human."
    elif [ "$cap_rc" -ne 0 ]; then
      # Exit 2 is an ERROR, not the cap, and it carries its own tag so the override log can tell the
      # two apart. Deliberately no cap guidance here: carrying, waiving or escalating cannot repair
      # an unreadable record, and printing those remedies for it is how a crash came to read as a
      # verdict in the first place.
      fail "verifier:attempt-cap-unreadable" \
        "verifier: the attempt cap could not be DECIDED (cause above) — this is not the cap itself." \
        "A cap that cannot be read bounds nothing, so this fails closed. Fix what it named."
    fi
    fail "verifier" "verifier: verdict.json is 'fail' — route back per its findings:" \
      "$(jq -r '.routed[]? | "  - \(.to): \(.reason) (\(.spec_id // "-")) finding \(.finding_id)"' "$VERDICT" 2>/dev/null)" ;;
  *)
    fail "verifier:unparseable" "verifier: verdict.json has no readable verdict ('$V'). Fail closed." ;;
esac
