#!/usr/bin/env bash
# The Verifier's test-quality review, run on a CROSS-FAMILY model.
#
# Why this script exists at all: in this runtime every subagent is an Anthropic model, so the Verifier
# *agent* can never be a different family than the implementer it checks — opus-vs-sonnet is not
# decorrelation, they share lineage and blind spots. The pipeline's independence claim
# (pipeline-conventions: "Fresh model ≠ author") is only true if the model that actually FORMS THE
# JUDGEMENT is cross-family. So the agent stays the orchestrator — it computes the bounded review set,
# runs the suite, merges and persists verdict.json — but the reading of the tests is delegated here,
# to gate_runner.py on a model from another vendor. The agent does not get to overrule the result.
#
# Usage:
#   scripts/verifier_review.sh <phase-dir> <review-set-file>...
#     <phase-dir>        docs/features/<feature>/phases/<n>-<slug>
#     <review-set-file>  the files YOU selected per skills/verifier-triage's scope algorithm:
#                        tests mapped to this phase ∪ test files it changed, plus their directly
#                        referenced helpers. Do not pass the whole suite; the scope is the point.
#
# Writes <phase-dir>/.verifier-review.json (the parsed verdict incl. findings, each with a
# deterministic id) for the agent to merge into verdict.json. Exit 0 = GO, 2 = NO-GO or fail-closed.
#
# Env: VERIFIER_GATE_MODEL (default google/gemini-2.5-pro) · AUTHOR_FAMILY (default anthropic)
#      GATE_PROVIDER · TEST_CMD (default: pytest -q --tb=short on the phase's tests dir)
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$SD/.." && pwd)}"
cd "$ROOT" || exit 2

PHASE_DIR="${1:-}"; shift || true
if [ -z "$PHASE_DIR" ] || [ ! -d "$PHASE_DIR" ]; then
  echo "usage: scripts/verifier_review.sh <phase-dir> <review-set-file>..." >&2
  exit 2
fi
if [ "$#" -eq 0 ]; then
  echo "verifier-review: no review-set files given (fail closed)." >&2
  echo "Compute the review set first — skills/verifier-triage, 'Build the review set'. A review of" >&2
  echo "zero tests is not a clean review; it is no review." >&2
  exit 2
fi

MODEL="${VERIFIER_GATE_MODEL:-google/gemini-2.5-pro}"
AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
LIMIT="${VERIFIER_SRC_LIMIT:-120000}"
OUT="$PHASE_DIR/.verifier-review.json"
BUNDLE="$(mktemp)"; trap 'rm -f "$BUNDLE"' EXIT

# --- assemble the bundle -----------------------------------------------------------------------
{
  printf '=== PHASE ===\n%s\n\n' "$PHASE_DIR"

  printf '=== SPEC REQUIREMENTS & ACCEPTANCE CRITERIA ===\n'
  found_spec=0
  for spec in "$PHASE_DIR"/specs/*/spec.md; do
    [ -f "$spec" ] || continue
    found_spec=1
    printf -- '--- %s ---\n' "$spec"; cat "$spec"; printf '\n'
  done
  [ "$found_spec" -eq 1 ] || printf '(no spec.md found under %s/specs — report this)\n' "$PHASE_DIR"

  printf '\n=== TEST MAPPINGS ===\n'
  found_map=0
  for m in "$PHASE_DIR"/specs/*/test-mapping.md; do
    [ -f "$m" ] || continue
    found_map=1
    printf -- '--- %s ---\n' "$m"; cat "$m"; printf '\n'
  done
  [ "$found_map" -eq 1 ] || printf '(no test-mapping.md found — every requirement is untraced; report it)\n'

  printf '\n=== TEST RUN ===\n'
  if [ -n "${TEST_RESULT_FILE:-}" ] && [ -f "$TEST_RESULT_FILE" ]; then
    cat "$TEST_RESULT_FILE"
  else
    eval "${TEST_CMD:-pytest -q --tb=short --ignore=tests/e2e}" 2>&1 | tail -60
  fi

  printf '\n=== REVIEW SET (bounded — see skills/verifier-triage) ===\n'
  SRC=""
  for f in "$@"; do
    if [ -f "$f" ]; then
      SRC="${SRC}$(printf -- '--- %s ---\n' "$f"; cat "$f"; printf '\n')"
    else
      SRC="${SRC}$(printf -- '--- %s --- (MISSING — report as a finding)\n' "$f")"
    fi
  done
  if [ "${#SRC}" -gt "$LIMIT" ]; then
    printf '%s\n' "${SRC:0:$LIMIT}"
    printf '\n[TRUNCATED at %s chars — you have NOT seen the whole review set. Judge only what is above and say the review was partial.]\n' "$LIMIT"
  else
    printf '%s\n' "$SRC"
  fi
} >"$BUNDLE"

# --- judge, cross-family -------------------------------------------------------------------------
# gate_runner asserts family(MODEL) != AUTHOR_FAMILY and exits 2 if they match, so a misconfigured
# model can't quietly turn this back into same-family self-review.
python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/verifier-review.md" \
  --model "$MODEL" \
  --author-family "$AUTHOR_FAMILY" \
  ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
  --emit-json "$OUT" \
  --target "$BUNDLE"
rc=$?

if [ ! -f "$OUT" ]; then
  echo "verifier-review: no verdict written (model unreachable, non-JSON reply, or same-family) — fail closed." >&2
  exit 2
fi

# --- deterministic finding ids -------------------------------------------------------------------
# sha1(kind|spec_id|normalized_target)[:12], per skills/verifier-triage. Computed here, not by the
# model: ids must be stable across runs or an engineer's break_glass waiver won't survive a re-run.
python3 - "$OUT" "$MODEL" <<'PY'
import hashlib, json, sys
path, model = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    v = json.load(fh)
for f in v.get("findings") or []:
    key = "|".join([str(f.get("kind", "")), str(f.get("spec_id", "")),
                    str(f.get("target", "")).strip()])
    f["id"] = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    f.setdefault("status", "open")
    f.setdefault("break_glass", False)
    f.setdefault("waiver_reason", None)
    f.setdefault("waived_by", None)
    f.setdefault("waived_at", None)
v["reviewed_by"] = model
with open(path, "w", encoding="utf-8") as fh:
    json.dump(v, fh, indent=2)
n = len(v.get("findings") or [])
print(f"verifier-review: {v.get('verdict', '?')} — {n} finding(s) -> {path}", file=sys.stderr)
PY

exit $rc
