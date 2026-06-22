#!/usr/bin/env bash
# Runtime-agnostic gate floor. Runs the pipeline gates against the working tree.
#   default (pre-commit): fidelity on STAGED specs + pytest        (fast)
#   --full (CI):          fidelity on ALL specs + pytest + mutation (thorough)
# Provider: --provider <p> or $GATE_PROVIDER (default opencode; CI sets openrouter).
# bash 3.2-compatible (macOS default).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GATE_RUNNER="$SCRIPT_DIR/gate_runner.py"
FIDELITY_RUBRIC="$SCRIPT_DIR/../prompts/fidelity-rubric.md"
MUTATION_RUBRIC="$SCRIPT_DIR/../prompts/mutation-interpret.md"
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
PARGS=(--provider "$PROVIDER")
fail=0

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
    --model deepseek/deepseek-chat "${PARGS[@]}" --target "$spec" || fail=1
done

# 2) Test suite (always). Exit 5 = "no tests collected" -> not a failure.
echo "• tests: pytest -q"
pytest -q; pc=$?
if [ "$pc" -ne 0 ] && [ "$pc" -ne 5 ]; then fail=1; fi
[ "$pc" -eq 5 ] && echo "  (no tests collected — skipping)"

# 3) Mutation gate (CI / --full only)
if [ "$FULL" -eq 1 ]; then
  echo "• mutation: mutmut"
  TMP=$(mktemp)
  { mutmut run; echo "---- results ----"; mutmut results; } >"$TMP" 2>&1 || true
  python3 "$GATE_RUNNER" --rubric "$MUTATION_RUBRIC" \
    --model google/gemini-2.5-pro "${PARGS[@]}" --target "$TMP" || fail=1
  rm -f "$TMP"
fi

if [ "$fail" -ne 0 ]; then
  echo "✗ pipeline gates failed" >&2
  exit 1
fi
echo "✓ pipeline gates passed"
exit 0
