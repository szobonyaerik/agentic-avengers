#!/usr/bin/env bash
# PostToolUse: when a phase handover.md is written, run the per-phase mutation gate (cosmic-ray).
# The Verifier runs once per phase — after every spec in the phase is green and the handover lands.
#
# The verdict is DETERMINISTIC and computed by scripts/mutation_score.py, not by a model:
#   score >= MUTATION_MIN_SCORE (default 0.85)  -> GO, and no model is called at all
#   score <  MUTATION_MIN_SCORE                 -> the survivors are sent to the gate model, which
#                                                  turns each one into the missing test case, then
#                                                  we stop and route back to the implementer
#   cannot score                                -> stop (fail closed)
# Mutation is diff-scoped: cr-filter-git skips mutants outside the phase's diff vs MUTATION_BASE,
# so a phase is judged on the code it actually changed, not the whole package.
#
# MUTATION_POLICY selects how much authority the verdict has (pipeline-conventions: "Gates"):
#   off (DEFAULT)  - skipped entirely; no mutation tool runs anywhere. Most teams leave it here.
#   advisory       - runs everything and reports the score and the missing cases, but never blocks.
#   enforce        - below threshold or unscorable STOPS the phase.
# Mutation is an EXTRA signal, not the pipeline's independence mechanism — independence is the
# Verifier's test-quality review (agents/avenger-verifier.md). Only `enforce` fails closed.
# The Verifier invokes this when the policy is on; it is not a standalone quality bar.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */handover.md) ;;
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0

AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
MUTATION_MIN_SCORE="${MUTATION_MIN_SCORE:-0.85}"
MUTATION_POLICY="${MUTATION_POLICY:-off}"

case "$MUTATION_POLICY" in
  enforce|advisory) ;;
  off)
    echo "mutation: skipped (MUTATION_POLICY=off, the default). Independence rests on the Verifier's test-quality review." >&2
    exit 0 ;;
  *)
    echo "mutation: MUTATION_POLICY='$MUTATION_POLICY' is not one of enforce|advisory|off (fail closed)" >&2
    exit 2 ;;
esac

CFG="$CLAUDE_PROJECT_DIR/cosmic-ray.toml"
if [ ! -f "$CFG" ]; then
  if [ "$MUTATION_POLICY" = "advisory" ]; then
    echo "mutation [advisory]: cosmic-ray.toml missing at repo root — gate could not run, not blocking" >&2
    exit 0
  fi
  echo "cosmic-ray.toml missing at repo root — mutation gate cannot run (fail closed)" >&2
  exit 2
fi

# Diff base for scoping. MUTATION_BASE wins; otherwise the merge-base with the default branch.
# No base resolvable (shallow clone, no remote) -> mutate everything rather than silently
# scoping to nothing. A gate that measures less than it claims is worse than a slow one.
resolve_base() {
  if [ -n "${MUTATION_BASE:-}" ]; then printf '%s' "$MUTATION_BASE"; return; fi
  local head_ref default_branch merge_base
  head_ref=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null)
  default_branch="${head_ref#refs/remotes/origin/}"
  [ -n "$default_branch" ] || default_branch=$(git rev-parse --verify -q main >/dev/null 2>&1 && echo main || echo master)
  merge_base=$(git merge-base HEAD "$default_branch" 2>/dev/null) \
    || merge_base=$(git merge-base HEAD "origin/$default_branch" 2>/dev/null) \
    || return 1
  printf '%s' "$merge_base"
}

WORK=$(mktemp -d); SESSION="$WORK/session.sqlite"; TMP="$WORK/report.txt"; SCOPED="$WORK/cosmic-ray.toml"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# Break-glass hand-off. `exec` replaces this process, so the EXIT trap would never fire — clean up
# first, or every bypassed run leaks its work dir.
bypass_and_exit() { cleanup; trap - EXIT; exec "$SD/bypass_log.sh" "$1"; }

# Every stopping condition funnels through here so `advisory` cannot be forgotten at one of them.
# $1 = bypass tag, $2 = what went wrong (already printed in detail by the caller).
stop_or_report() {
  if [ "$MUTATION_POLICY" = "advisory" ]; then
    printf 'mutation [advisory]: %s — reporting only, not blocking (MUTATION_POLICY=advisory)\n' "$2" >&2
    exit 0
  fi
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "$1"
  exit 2
}

# Scoped config = the project's config plus a git-filter section naming the diff base.
cp "$CFG" "$SCOPED"
if BASE=$(resolve_base) && [ -n "$BASE" ]; then
  printf '\n[cosmic-ray.filters.git-filter]\nbranch = "%s"\n' "$BASE" >>"$SCOPED"
  FILTER_BASE="$BASE"
else
  echo "mutation: no diff base resolvable — mutating the full module-path (unscoped)" >&2
  FILTER_BASE=""
fi

# Baseline FIRST. A mutant counts as "killed" whenever the test command fails — so if the suite is
# already broken (a collection error, say), every mutant is killed and the score is a perfect 1.0.
# A broken suite would otherwise score better than a real one. Verified: with an import error in the
# suite, all 7 fixture mutants reported 'killed'. No kill means anything until the baseline is green.
if ! cosmic-ray baseline "$SCOPED" >>"$TMP" 2>&1; then
  echo "mutation: baseline FAILED — the suite does not pass on unmutated code, so every mutant" >&2
  echo "would score as killed. Refusing to score (fail closed). Fix the suite first:" >&2
  tail -5 "$TMP" >&2
  stop_or_report "mutation:baseline-failed" "baseline failed, so no mutant result is meaningful"
fi

if ! cosmic-ray init "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
  echo "cosmic-ray init errored (fail closed):" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:errored" "cosmic-ray init errored"
fi
# Diff-scope: mark every mutant outside the phase's diff as skipped. mutation_score.py excludes
# skipped mutants from the denominator (cosmic-ray's own cr-rate would count them as kills).
if [ -n "$FILTER_BASE" ] && ! cr-filter-git --config "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
  echo "cr-filter-git errored (fail closed) — refusing to mutate unscoped:" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:filter-errored" "cr-filter-git errored, scope unknown"
fi
if ! cosmic-ray exec "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
  echo "cosmic-ray exec errored (fail closed):" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:errored" "cosmic-ray exec errored"
fi

# Deterministic verdict. 0 = GO (no model call), 1 = survivors below threshold, 2 = cannot score.
python3 "$SD/mutation_score.py" --min-score "$MUTATION_MIN_SCORE" "$SESSION"
sc=$?

# Measurement, never a gate. Recorded on GO as well as NO-GO: survivors above the threshold are
# still behaviours no test catches, and `found_by` only tells you which stage earns its cost if
# every stage's catches are in it. Fails open — this runs between the verdict and acting on it, and
# cannot change either.
python3 "$SD/mutation_score.py" --min-score "$MUTATION_MIN_SCORE" --json "$SESSION" >"$WORK/score.json" 2>/dev/null || true
python3 "$SD/pipeline_metrics.py" mutation-survivors "$FILE" "$WORK/score.json" >/dev/null 2>&1 || true

if [ "$sc" -eq 0 ]; then
  exit 0
fi
if [ "$sc" -ne 1 ]; then
  stop_or_report "mutation:unscorable" "session could not be scored honestly"
fi

# Below threshold: print the score and the survivor list. NO model call — turning a survivor into the
# missing test case is the Verifier's job, using skills/mutation-interpret, in chat. This hook stays
# mechanical (pipeline-conventions: "Where the models run").
{ echo "---- mutation score (deterministic verdict: below ${MUTATION_MIN_SCORE}) ----"
  python3 "$SD/mutation_score.py" --min-score "$MUTATION_MIN_SCORE" --json "$SESSION" 2>&1
  echo "---- survivors (cosmic-ray dump) ----"
  cosmic-ray dump "$SESSION" 2>&1
} >>"$TMP"
cat "$TMP" >&2
echo "" >&2
echo "Each survivor is a behavior no test catches. The Verifier records them in verdict.json and" >&2
echo "routes the missing case to the implementer (skills/mutation-interpret)." >&2

if [ "$MUTATION_POLICY" = "advisory" ]; then
  echo "mutation [advisory]: not blocking (MUTATION_POLICY=advisory). Record the score in handover.md." >&2
  exit 0
fi
echo "mutation gate: NO-GO (MUTATION_POLICY=enforce) — route back to the implementer." >&2
[ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation"
exit 2
