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

# 2) Test suite (always). Exit 5 = "no tests collected" -> not a failure.
echo "• tests: pytest -q"
pytest -q; pc=$?
if [ "$pc" -ne 0 ] && [ "$pc" -ne 5 ]; then record_fail "tests"; fi
[ "$pc" -eq 5 ] && echo "  (no tests collected — skipping)"

# 3) Mutation gate via cosmic-ray (CI / --full only). Fail closed: an errored run stops.
if [ "$FULL" -eq 1 ]; then
  echo "• mutation: cosmic-ray"
  # Module under test, read from cosmic-ray.toml. If it doesn't exist, there's nothing to mutate
  # (e.g. a docs/config-only repo) — skip like "no tests collected", don't fail closed.
  MODPATH=""
  [ -f "$COSMIC_CFG" ] && MODPATH="$(grep -m1 '^[[:space:]]*module-path' "$COSMIC_CFG" | sed 's/.*=[[:space:]]*//; s/^["'\'']//; s/["'\'']$//')"
  if [ ! -f "$COSMIC_CFG" ]; then
    echo "  ✗ cosmic-ray.toml missing at repo root — mutation gate cannot run (fail closed)" >&2
    record_fail "mutation:no-config"
  elif [ -n "$MODPATH" ] && [ ! -e "$ROOT/$MODPATH" ]; then
    echo "  (module-path '$MODPATH' not present — no code to mutate, skipping)"
  else
    SESSION="$ROOT/session.sqlite"; TMP=$(mktemp)
    rm -f "$SESSION"
    if cosmic-ray init "$COSMIC_CFG" "$SESSION" >>"$TMP" 2>&1 \
       && cosmic-ray exec "$COSMIC_CFG" "$SESSION" >>"$TMP" 2>&1; then
      { echo "---- survivors (cosmic-ray dump) ----"; cosmic-ray dump "$SESSION" 2>>"$TMP"; \
        echo "---- survival rate (cr-rate) ----"; cr-rate "$SESSION" 2>>"$TMP"; } >>"$TMP" 2>&1
      python3 "$GATE_RUNNER" --rubric "$MUTATION_RUBRIC" \
        --model "$MUT_MODEL" "${PARGS[@]}" --target "$TMP" || record_fail "mutation"
    else
      echo "  ✗ cosmic-ray run errored (fail closed):" >&2; tail -5 "$TMP" >&2
      record_fail "mutation:errored"
    fi
    rm -f "$TMP" "$SESSION"
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
