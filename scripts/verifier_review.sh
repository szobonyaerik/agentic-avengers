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
# On any path that does not produce a fresh trustworthy verdict the file is removed, so the caller
# never merges an earlier run's pass (INVARIANT below).
#
# Env: VERIFIER_GATE_MODEL (default google/gemini-3.1-pro-preview) · AUTHOR_FAMILY (default anthropic)
#      GATE_PROVIDER · TEST_CMD (default: pytest -q --tb=short on the phase's tests dir)
#      VERIFIER_SRC_LIMIT (default 400000) — chars of review-set source; an over-limit set is refused
#      before the model is called, never truncated (see the comment on LIMIT below)
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
. "$SD/gate_runner_guard.sh"
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$SD/.." && pwd)}"
cd "$ROOT" || exit 2

PHASE_DIR="${1:-}"; shift || true
if [ -z "$PHASE_DIR" ] || [ ! -d "$PHASE_DIR" ]; then
  echo "usage: scripts/verifier_review.sh <phase-dir> <review-set-file>..." >&2
  exit 2
fi

# INVARIANT: no verdict artifact survives a run that does not produce a fresh one. The Verifier agent
# merges .verifier-review.json into verdict.json, so a previous run's verdict left on disk (an
# argument refusal, an over-limit set, a provider outage, a non-JSON reply) is read as THIS run's
# pass, for code it never saw — the same fail-open this script exists to close. The invariant is held
# structurally rather than by each branch remembering: this is the first statement after $OUT becomes
# computable, so no exit path above it can leave an artifact behind (the two exits that do precede it
# — a failed cd and an invalid phase dir — have no $OUT to leave). The removal is verified, not
# assumed, because `set -uo pipefail` carries no -e and a failed rm would otherwise pass silently.
OUT="$PHASE_DIR/.verifier-review.json"
rm -f "$OUT"
if [ -e "$OUT" ]; then
  echo "verifier-review: cannot remove stale $OUT — fail closed." >&2
  echo "  A verdict from a previous run would be merged into verdict.json as this run's pass." >&2
  exit 2
fi

if [ "$#" -eq 0 ]; then
  echo "verifier-review: no review-set files given (fail closed)." >&2
  echo "Compute the review set first — skills/verifier-triage, 'Build the review set'. A review of" >&2
  echo "zero tests is not a clean review; it is no review." >&2
  exit 2
fi

# The judgement is only worth what the runner is. Refuse one that cannot identify itself as the
# shipped gate runner — a scaffold that prints GO is indistinguishable from a real pass here.
require_gate_runner "$SD/gate_runner.py" || exit 2

MODEL="${VERIFIER_GATE_MODEL:-google/gemini-3.1-pro-preview}"
AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
# Chars of review-set source the gate model is given. 120000 (~30k tokens) was far below what any
# current gate model can read, and it was the value that made truncation the normal case rather than
# the exception — one real phase's bounded review set measured 158k. 400000 (~100k tokens) clears
# that with headroom. Raise it for a large-context model; it is a fail-closed cap, not a budget, so
# too LOW only costs a loud stop, while too high risks the model quietly degrading on the tail.
# Scope: it bounds the REVIEW-SET SOURCE only. The bundle also carries every spec.md, every
# test-mapping.md and up to 60 lines of test output, all unbounded and uncounted — so a set under
# the cap is not a guarantee that the whole prompt fits the model's context.
LIMIT="${VERIFIER_SRC_LIMIT:-400000}"
BUNDLE="$(mktemp)"; trap 'rm -f "$BUNDLE"' EXIT

# --- review set: assemble, then refuse to proceed if it does not fit ---------------------------
# This runs BEFORE the model call on purpose. The old code truncated the bundle here and appended a
# note ASKING the model to report the review as partial — an instruction, not an assertion — then
# returned the model's verdict as the exit code, so a truncated review could pass a phase silently.
# Review sets only grow, so each later phase was likelier to hit it than the last. A partial review
# is an unreviewed phase; failing here costs nothing and spends no tokens.
# The separating newline is appended OUTSIDE the command substitution on purpose: `$(...)` strips
# every trailing newline, which glued each file's `--- <path> ---` header onto the previous file's
# last line. The model attributes its findings against those headers, and the substance check below
# refuses a verdict that names none of those paths — a glued header corrupts exactly that.
SRC=""
for f in "$@"; do
  if [ -f "$f" ]; then
    SRC="${SRC}$(printf -- '--- %s ---\n' "$f"; cat "$f")"$'\n'
  else
    SRC="${SRC}$(printf -- '--- %s --- (MISSING — report as a finding)' "$f")"$'\n'
  fi
done
if [ "${#SRC}" -gt "$LIMIT" ]; then
  echo "verifier-review: review set is ${#SRC} chars, over VERIFIER_SRC_LIMIT ($LIMIT) — fail closed." >&2
  echo "  A truncated review is an unreviewed phase. Do ONE of:" >&2
  echo "    • raise the cap:  VERIFIER_SRC_LIMIT=$(( ${#SRC} + 20000 )) scripts/verifier_review.sh ..." >&2
  echo "      (only up to what \$VERIFIER_GATE_MODEL can actually read — check its context window)" >&2
  echo "    • or split the review set and run this once per chunk, merging the findings." >&2
  echo "  Do not drop files to get under the cap: the bounded set is the review." >&2
  exit 2
fi

# --- scope: which specs this run reviews, and which carry forward -------------------------------
# The bundle used to re-send every spec and every mapping on every attempt; one measured ~832k
# tokens. A spec byte-identical to what the last COMPLETED review saw carries its verdict and its
# findings forward instead (scripts/verifier_bundle_scope.py). No state, or VERIFIER_SCOPE=full, or
# nothing unchanged -> the full bundle, exactly as before. Carried findings are merged back in after
# the review and an open one still forces NO-GO, so this shrinks the prompt, never the bar.
# `--tsv` so no inline parser stands between the plan and the bundle: an earlier version unpacked
# the JSON with a `python3 -c` whose quoting was wrong, and the carried list came back EMPTY without
# failing anything — the notice just vanished and the bundle looked fine.
PLAN="$(python3 "$SD/verifier_bundle_scope.py" plan "$PHASE_DIR" --tsv)"; plan_rc=$?
if [ "$plan_rc" -ne 0 ]; then
  echo "verifier-review: could not compute the bundle scope (exit $plan_rc) — fail closed." >&2
  echo "  Scoping the bundle is not optional: an unscoped guess is either a review of the wrong" >&2
  echo "  specs or a silent full re-send. Fix the error above, or set VERIFIER_SCOPE=full." >&2
  exit 2
fi
SCOPED_SPECS="$(printf '%s\n' "$PLAN" | awk -F'\t' '$1=="REVIEW"{print $2}')"
CARRIED_SPECS="$(printf '%s\n' "$PLAN" | awk -F'\t' '$1=="CARRY"{printf "%s\t%s\t%s\n", $2, $3, $4}')"
if [ -z "$SCOPED_SPECS" ] && [ -z "$CARRIED_SPECS" ] && [ -d "$PHASE_DIR/specs" ]; then
  echo "verifier-review: the phase has a specs/ directory but the scope came back empty — fail closed." >&2
  exit 2
fi

# --- assemble the bundle -----------------------------------------------------------------------
{
  printf '=== PHASE ===\n%s\n\n' "$PHASE_DIR"

  if [ -n "$CARRIED_SPECS" ]; then
    printf '=== SPECS CARRIED FORWARD (already reviewed, unchanged since — out of scope here) ===\n'
    printf 'These specs are byte-identical to what the previous completed review of this phase saw.\n'
    printf 'Their verdicts and findings are carried forward automatically and merged into the result\n'
    printf 'of this run, so they are deliberately not included below. Treat what follows as the\n'
    printf 'COMPLETE set you were asked to review; nothing was withheld from it or shortened.\n'
    printf '%s\n' "$CARRIED_SPECS" | while IFS="$(printf '\t')" read -r s v n; do
      printf -- '  %s: previous verdict %s, %s finding(s) carried\n' "$s" "$v" "$n"
    done
    printf '\n'
  fi

  printf '=== SPEC REQUIREMENTS & ACCEPTANCE CRITERIA ===\n'
  found_spec=0
  for spec_dir in $SCOPED_SPECS; do
    spec="$spec_dir/spec.md"
    [ -f "$spec" ] || continue
    found_spec=1
    printf -- '--- %s ---\n' "$spec"; cat "$spec"; printf '\n'
  done
  if [ "$found_spec" -ne 1 ] && [ -z "$CARRIED_SPECS" ]; then
    printf '(no spec.md found under %s/specs — report this)\n' "$PHASE_DIR"
  fi

  printf '\n=== TEST MAPPINGS ===\n'
  found_map=0
  for spec_dir in $SCOPED_SPECS; do
    m="$spec_dir/test-mapping.md"
    [ -f "$m" ] || continue
    found_map=1
    printf -- '--- %s ---\n' "$m"; cat "$m"; printf '\n'
  done
  if [ "$found_map" -ne 1 ] && [ -z "$CARRIED_SPECS" ]; then
    printf '(no test-mapping.md found — every requirement is untraced; report it)\n'
  fi

  printf '\n=== TEST RUN ===\n'
  if [ -n "${TEST_RESULT_FILE:-}" ] && [ -f "$TEST_RESULT_FILE" ]; then
    cat "$TEST_RESULT_FILE"
  else
    eval "${TEST_CMD:-pytest -q --tb=short --ignore=tests/e2e}" 2>&1 | tail -60
  fi

  printf '\n=== REVIEW SET (bounded — see skills/verifier-triage) ===\n'
  printf '%s\n' "$SRC"
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

# --- substance: did the review actually happen? --------------------------------------------------
# A verdict that names none of the files it was handed, or that reports itself as partial, is not a
# review. Deterministic and unit-tested (tests/test_verifier_review_check.py); never the model's call.
if ! python3 "$SD/verifier_review_check.py" "$OUT" "$@"; then
  rm -f "$OUT"   # the invariant above, restated for the one artifact this run did write
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

# --- carry forward, and record what this run establishes ------------------------------------------
# Merges the findings of every carried spec back into $OUT, names the carried specs in the verdict
# artifact and on stderr, and records the fingerprints the next run scopes against. An open carried
# finding holds the phase even when this run's narrower review said GO — the scope shrinks the
# prompt, not the bar.
python3 "$SD/verifier_bundle_scope.py" finalize "$PHASE_DIR" "$OUT"
scope_rc=$?
[ "$scope_rc" -ne 0 ] && rc="$scope_rc"

exit $rc
