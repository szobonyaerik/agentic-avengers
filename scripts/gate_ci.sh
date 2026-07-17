#!/usr/bin/env bash
# Runtime-agnostic gate floor. Runs the pipeline gates against the working tree.
#   default (pre-commit): fidelity on STAGED specs + pytest              (fast)
#   --full (CI):          fidelity on ALL specs + pytest + cosmic-ray    (thorough)
# Provider: --provider <p> or $GATE_PROVIDER (default opencode; CI sets openrouter).
# Author family: $AUTHOR_FAMILY (default anthropic) — gates fail closed if same-family.
# Break-glass: GATE_BYPASS="reason" overrides a FAILING gate; logged + visible, never silent.
# bash 3.2-compatible (macOS default).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"       # repo root (in-repo and vendored flat layout)
GATE_RUNNER="$SCRIPT_DIR/gate_runner.py"
FIDELITY_RUBRIC="$SCRIPT_DIR/../prompts/fidelity-rubric.md"
MUTATION_RUBRIC="$SCRIPT_DIR/../prompts/mutation-interpret.md"
COSMIC_CFG="$ROOT/cosmic-ray.toml"
OVERRIDE_LOG="$ROOT/gate-overrides.log"
AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
FID_MODEL="${GATE_MODEL:-deepseek/deepseek-chat}"     # GATE_MODEL overrides all gates
MUT_MODEL="${GATE_MODEL:-google/gemini-2.5-pro}"
cd "$ROOT"

FULL=0
PROVIDER="${GATE_PROVIDER:-opencode}"
while [ $# -gt 0 ]; do
  case "$1" in
    --full) FULL=1 ;;
    --provider) PROVIDER="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
PARGS=(--provider "$PROVIDER" --author-family "$AUTHOR_FAMILY")
fail=0
failed_gates=""

record_fail() { fail=1; failed_gates="${failed_gates} $1"; }

# 1) Fidelity gate — changed specs (pre-commit) or all specs (--full)
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
  echo "• fidelity gate: $spec"
  python3 "$GATE_RUNNER" --rubric "$FIDELITY_RUBRIC" \
    --model "$FID_MODEL" "${PARGS[@]}" --target "$spec" || record_fail "fidelity:$spec"
done

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
#    at MUTATION_MIN_SCORE. Keep the two in step — CI and in-session must not disagree.
if [ "$FULL" -eq 1 ]; then
  echo "• mutation: cosmic-ray (min score ${MUTATION_MIN_SCORE:-0.85})"
  # Module under test, read from cosmic-ray.toml. If it doesn't exist, there's nothing to mutate
  # (e.g. a docs/config-only repo) — skip like "no tests collected", don't fail closed.
  # Only a SCALAR module-path is skippable this way. `module-path` also accepts a TOML list, which
  # this scalar parser cannot read — and treating an unparsed value as "missing" would silently skip
  # the whole gate (fail OPEN). Anything that isn't a plain existing-or-missing scalar runs the gate
  # and lets cosmic-ray decide; it fails closed on a bad path.
  MODPATH=""
  [ -f "$COSMIC_CFG" ] && MODPATH="$(grep -m1 '^[[:space:]]*module-path' "$COSMIC_CFG" | sed 's/.*=[[:space:]]*//; s/^["'\'']//; s/["'\'']$//')"
  case "$MODPATH" in
    \[*) MODPATH="" ;;   # list form -> not skippable, fall through to the gate
  esac
  if [ ! -f "$COSMIC_CFG" ]; then
    echo "  ✗ cosmic-ray.toml missing at repo root — mutation gate cannot run (fail closed)" >&2
    record_fail "mutation:no-config"
  elif [ -n "$MODPATH" ] && [ ! -e "$ROOT/$MODPATH" ]; then
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
      record_fail "mutation:baseline-failed"
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
        python3 "$GATE_RUNNER" --rubric "$MUTATION_RUBRIC" \
          --model "$MUT_MODEL" "${PARGS[@]}" --target "$TMP" || true
        record_fail "mutation"
      elif [ "$msc" -ne 0 ]; then
        record_fail "mutation:unscorable"
      fi
    else
      echo "  ✗ cosmic-ray run errored (fail closed):" >&2; tail -5 "$TMP" >&2
      record_fail "mutation:errored"
    fi
    rm -rf "$WORK"
  fi
fi

# Break-glass: a visible, logged override of a failing gate. Never silent.
if [ "$fail" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  who="$(git config user.email 2>/dev/null || whoami)"
  when="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s\t%s\tgates:%s\treason: %s\n' "$when" "$who" "${failed_gates# }" "$GATE_BYPASS" >> "$OVERRIDE_LOG"
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
