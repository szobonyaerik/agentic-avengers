#!/usr/bin/env bash
# Runtime-agnostic gate floor — the MECHANICAL gates, against the working tree.
#   default (pre-commit): staged-spec artifact checks + pytest                     (fast)
#   --full (CI):          all artifact checks + cross-family + pytest + mutation   (thorough)
#
# NO MODEL RUNS HERE. Model-based gates (the Verifier's triage and test-quality review, the grill-me
# spec review) run in-chat; this floor only checks their COMMITTED ARTIFACTS — `review_status:
# approved` on specs, a passing `verdict.json` per phase — plus the suite, the static cross-family
# assertion, and mutation if a team turned it on. See pipeline-conventions: "Where the models run".
# The Fidelity Gate is the one model gate in this repo and it runs as an in-session hook
# (scripts/hook_fidelity.sh), never in CI.
#
# Break-glass: GATE_BYPASS="reason" overrides a FAILING gate; logged + visible, never silent.
# bash 3.2-compatible (macOS default).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/load_env.sh"   # pipeline config from the project .env (real env always wins)
. "$SCRIPT_DIR/bypass_reason.sh"   # one owner of the break-glass reason's on-disk shape
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"       # repo root (in-repo and vendored flat layout)
COSMIC_CFG="$ROOT/cosmic-ray.toml"
OVERRIDE_LOG="$ROOT/gate-overrides.log"
cd "$ROOT"

FULL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) FULL=1 ;;
    --provider) shift ;;   # accepted and ignored: no model runs in this floor any more
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
fail=0
failed_gates=""

record_fail() { fail=1; failed_gates="${failed_gates} $1"; }

# 1) Spec artifact checks — the committed output of the in-chat gates. No model call.
SPECS=()
if [ "$FULL" -eq 1 ]; then
  while IFS= read -r f; do [ -n "$f" ] && SPECS+=("$f"); done \
    < <(find docs/features -type f -name spec.md 2>/dev/null)
else
  while IFS= read -r f; do [ -n "$f" ] && SPECS+=("$f"); done \
    < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E 'docs/features/.*/spec\.md$' || true)
fi
for spec in "${SPECS[@]:-}"; do
  [ -n "$spec" ] || continue
  [ -f "$spec" ] || continue
  # A spec that has not reached the implementer yet is not a CI failure — only a spec claiming to be
  # built must carry its approvals.
  grep -qE '^status:[[:space:]]*(done|in-progress)[[:space:]]*$' "$spec" 2>/dev/null || continue
  echo "• spec artifacts: $spec"
  grep -qE '^review_status:[[:space:]]*approved[[:space:]]*$' "$spec" 2>/dev/null \
    || { echo "  ✗ review_status is not 'approved' — the human spec review never passed." >&2
         record_fail "spec-review:$spec"; }
  if grep -qE '^fidelity_verdict:[[:space:]]*NO-GO' "$spec" 2>/dev/null; then
    echo "  ✗ fidelity_verdict is NO-GO — the spec was routed back and never re-gated." >&2
    record_fail "fidelity:$spec"
  fi
done

# 1b) Verdict artifacts — every phase with a handover must carry a passing Verifier verdict.
if [ "$FULL" -eq 1 ]; then
  while IFS= read -r ho; do
    [ -n "$ho" ] || continue
    vd="$(dirname "$ho")/verdict.json"
    echo "• verdict: $vd"
    if [ ! -f "$vd" ]; then
      echo "  ✗ no verdict.json — the phase closed without an independent Verifier run." >&2
      record_fail "verifier:no-verdict"; continue
    fi
    v="$(jq -r '.verdict // "missing"' "$vd" 2>/dev/null || echo unparseable)"
    if [ "$v" != "pass" ]; then
      echo "  ✗ verdict is '$v'." >&2; record_fail "verifier:$v"
    else
      if [ "$(jq -r '.bypassed // false' "$vd" 2>/dev/null)" = "true" ]; then
        echo "  ⚠ verdict is 'pass' with waived findings (bypassed: true) — visible bypass, not a clean green." >&2
      fi
      # Same rule as the in-session hook: a pass must evidence the cross-family test-quality review.
      rv="$(jq -r '.test_quality.reviewed // false' "$vd" 2>/dev/null)"
      nf="$(jq -r '[.test_quality.scope.test_files[]?] | length' "$vd" 2>/dev/null || echo 0)"
      if [ "$rv" != "true" ] || [ "${nf:-0}" -eq 0 ]; then
        echo "  ✗ verdict is 'pass' but records no completed test-quality review (reviewed=$rv, ${nf:-0} files)." >&2
        record_fail "verifier:unreviewed"
      fi
    fi
  done < <(find docs/features -type f -name handover.md 2>/dev/null)
fi

# 1bb) The document read path — the artifacts obey their declared readers and caps, and no stage
#      instruction has quietly re-acquired a read that was removed. The second half is the one that
#      matters over time: the cost this guards against came back one caller at a time last time, so
#      the check is attached to the invariant (scripts/doc_read_path.py owns the table) rather than
#      to any single command.
echo "• read path: docs/features artifacts + canonical stage instructions"
if ! python3 "$SCRIPT_DIR/doc_read_path.py" check --sources "$ROOT"; then
  record_fail "read-path"
fi

# 1c) Cross-family assertion — on the model that actually FORMS THE JUDGEMENT.
#     Checking the Verifier agent's own `model:` would be meaningless here: every subagent in this
#     runtime is Anthropic, so the agent can never differ in family from the implementer. What must be
#     decorrelated is $VERIFIER_GATE_MODEL, the model scripts/verifier_review.sh sends the tests to.
model_token () { grep -m1 '^model:' "$1" 2>/dev/null | sed 's/.*:[[:space:]]*//;s/[[:space:]].*//' | tr '[:upper:]' '[:lower:]'; }
# One vendor table, not two. This used to carry its own `case` over four vendors; a second copy of
# the pipeline's only independence primitive is a copy that drifts, and the one that drifted first
# would be the one nobody was reading. scripts/model_vendors.py is the table; unknown still exits
# non-zero here, so the caller's loud refusal below is unchanged.
model_family () { python3 "$SCRIPT_DIR/model_vendors.py" family "$1"; }
cross_family_check () {
  local gate="${VERIFIER_GATE_MODEL:-google/gemini-3.1-pro-preview}" gfam impl itok ifam
  gfam="$(model_family "$gate")" || { echo "  ✗ unknown VERIFIER_GATE_MODEL family: '$gate'" >&2; return 1; }
  for impl in "$ROOT/agents/avenger-backend-architect.md" "$ROOT/agents/avenger-frontend-developer.md"; do
    [ -f "$impl" ] || continue
    itok="$(model_token "$impl")"
    ifam="$(model_family "$itok")" || { echo "  ✗ unknown implementer model family in $(basename "$impl"): '$itok'" >&2; return 1; }
    if [ "$gfam" = "$ifam" ]; then
      echo "  ✗ VERIFIER_GATE_MODEL '$gate' (family '$gfam') is the implementer's own family." >&2
      echo "    Same-family verification is theater — point it at another vendor." >&2
      return 1
    fi
  done
  echo "  verifier judgement runs on '$gate' (family '$gfam'); implementers are '$ifam'."
  return 0
}
if [ "$FULL" -eq 1 ]; then
  echo "• cross-family: verifier vs implementers"
  cross_family_check || record_fail "cross-family"
fi

# 2) Test suite. Exit 5 = "no tests collected" -> not a failure.
#    Pre-commit runs the phase suites only; --full (CI) also runs the feature-level e2e tests, which
#    are slow, need the assembled system, and have nothing useful to say about an uncommitted edit.
if [ "$FULL" -eq 1 ]; then
  echo "• tests: pytest -q (incl. e2e)"
  pytest -q; pc=$?
else
  echo "• tests: pytest -q --ignore=tests/e2e"
  pytest -q --ignore=tests/e2e; pc=$?
fi
if [ "$pc" -ne 0 ] && [ "$pc" -ne 5 ]; then record_fail "tests"; fi
[ "$pc" -eq 5 ] && echo "  (no tests collected — skipping)"

# 3) Mutation gate via cosmic-ray (CI / --full only). Fail closed: an errored run stops.
#    Same contract as scripts/hook_mutation.sh: baseline first, diff-scoped, deterministic verdict
#    at MUTATION_MIN_SCORE, and the same MUTATION_POLICY authority switch (blocking|advisory|off).
#    Keep the two in step — CI and in-session must not disagree.
MUTATION_POLICY="${MUTATION_POLICY:-off}"
case "$MUTATION_POLICY" in
  enforce|advisory|off) ;;
  *) echo "  ✗ MUTATION_POLICY='$MUTATION_POLICY' is not one of enforce|advisory|off (fail closed)" >&2
     record_fail "mutation:bad-mode" ;;
esac

# In advisory mode a mutation finding is reported but never counts against the run. Every mutation
# failure path goes through this instead of record_fail, so the mode cannot be missed at one of them.
mutation_fail() {
  if [ "$MUTATION_POLICY" = "advisory" ]; then
    echo "  ⓘ mutation [advisory]: $1 — reporting only, not failing the run" >&2
    return 0
  fi
  record_fail "$1"
}

if [ "$FULL" -eq 1 ] && [ "$MUTATION_POLICY" = "off" ]; then
  echo "• mutation: skipped (MUTATION_POLICY=off)"
fi
if [ "$FULL" -eq 1 ] && { [ "$MUTATION_POLICY" = "enforce" ] || [ "$MUTATION_POLICY" = "advisory" ]; }; then
  echo "• mutation: cosmic-ray (min score ${MUTATION_MIN_SCORE:-0.85}, gate ${MUTATION_POLICY})"
  # Module under test, read from cosmic-ray.toml. If it doesn't exist, there's nothing to mutate
  # (e.g. a docs/config-only repo) — skip like "no tests collected", don't fail closed.
  # Only a cleanly parsed SCALAR module-path is skippable. `module-path` also accepts a TOML list,
  # and a value can carry a trailing inline comment or a `#` inside a quoted path — treating any
  # value we didn't really parse as "missing" would silently skip the whole gate (fail OPEN). That
  # is why the decision lives in scripts/mutation_target.py (real TOML parse, never a regex) and
  # why everything ambiguous runs the gate and lets cosmic-ray decide; it fails closed on a bad path.
  # Exit 0 = skippable (prints the absent path), anything else = run the gate.
  if [ ! -f "$COSMIC_CFG" ]; then
    echo "  ✗ cosmic-ray.toml missing at repo root — mutation gate cannot run (fail closed)" >&2
    mutation_fail "mutation:no-config"
  elif MODPATH="$(python3 "$SCRIPT_DIR/mutation_target.py" --root "$ROOT" "$COSMIC_CFG")"; then
    echo "  (module-path '$MODPATH' not present — no code to mutate, skipping)"
  else
    WORK=$(mktemp -d); SESSION="$WORK/session.sqlite"; TMP="$WORK/report.txt"; SCOPED="$WORK/cosmic-ray.toml"
    cp "$COSMIC_CFG" "$SCOPED"
    # Diff-scope to the branch's changes when a base is resolvable; otherwise mutate everything
    # rather than silently scoping to nothing.
    CI_BASE="${MUTATION_BASE:-$(git merge-base HEAD origin/HEAD 2>/dev/null || git merge-base HEAD main 2>/dev/null || true)}"
    if [ -n "$CI_BASE" ]; then
      printf '\n[cosmic-ray.filters.git-filter]\nbranch = "%s"\n' "$CI_BASE" >>"$SCOPED"
    else
      echo "  (no diff base resolvable — mutating the full module-path)"
    fi
    # A repo with code but no tests yet is not a broken suite — step 2 already treated pytest's
    # exit 5 as "skip", so failing the baseline here with "suite is not green" would contradict it
    # and misdiagnose a fresh scaffold. Skip the gate instead; there is nothing to measure.
    if [ "$pc" -eq 5 ]; then
      echo "  (no tests collected — nothing for mutation to measure, skipping)"
    # Baseline first: a mutant counts as killed whenever the test command fails, so a broken suite
    # would score a perfect 1.0. No kill means anything until the unmutated suite is green.
    elif ! cosmic-ray baseline "$SCOPED" >>"$TMP" 2>&1; then
      echo "  ✗ mutation baseline FAILED — suite is not green on unmutated code (fail closed):" >&2
      tail -5 "$TMP" >&2
      mutation_fail "mutation:baseline-failed"
    elif cosmic-ray init "$SCOPED" "$SESSION" >>"$TMP" 2>&1 \
       && { [ -z "$CI_BASE" ] || cr-filter-git --config "$SCOPED" "$SESSION" >>"$TMP" 2>&1; } \
       && cosmic-ray exec "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
      # Deterministic verdict: 0 = GO (no model call), 1 = below threshold, 2 = cannot score.
      python3 "$SCRIPT_DIR/mutation_score.py" --min-score "${MUTATION_MIN_SCORE:-0.85}" "$SESSION"
      msc=$?
      if [ "$msc" -eq 1 ]; then
        { echo "---- mutation score (deterministic gate verdict: NO-GO) ----"
          python3 "$SCRIPT_DIR/mutation_score.py" --min-score "${MUTATION_MIN_SCORE:-0.85}" --json "$SESSION" 2>&1
          echo "---- survivors (cosmic-ray dump) ----"; cosmic-ray dump "$SESSION" 2>&1; } >>"$TMP"
        cat "$TMP" >&2   # survivors; the Verifier interprets them in chat, no model here
        mutation_fail "mutation"
      elif [ "$msc" -ne 0 ]; then
        mutation_fail "mutation:unscorable"
      fi
    else
      echo "  ✗ cosmic-ray run errored (fail closed):" >&2; tail -5 "$TMP" >&2
      mutation_fail "mutation:errored"
    fi
    rm -rf "$WORK"
  fi
fi

# Break-glass: a visible, logged override of a failing gate. Never silent.
if [ "$fail" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  who="$(git config user.email 2>/dev/null || whoami)"
  when="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  reason="$(bypass_reason_oneline "$GATE_BYPASS")"
  printf '%s\t%s\tgates:%s\treason: %s\n' "$when" "$who" "${failed_gates# }" "$reason" >> "$OVERRIDE_LOG"
  echo "⚠ BYPASSED failing gate(s):${failed_gates} — reason: $GATE_BYPASS" >&2
  echo "  logged to $OVERRIDE_LOG. Record this in the phase handover.md." >&2
  exit 0
fi

if [ "$fail" -ne 0 ]; then
  echo "✗ pipeline gates failed:${failed_gates}" >&2
  echo "  (override intentionally with GATE_BYPASS=\"reason\" — logged + visible)" >&2
  exit 1
fi
echo "✓ pipeline gates passed"
exit 0
