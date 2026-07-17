#!/usr/bin/env bash
# PostToolUse: when a phase handover.md is written, run the per-phase mutation gate (cosmic-ray).
# The Verifier runs once per phase — after every spec in the phase is green and the handover lands.
#
# The verdict is DETERMINISTIC and computed by scripts/mutation_score.py, not by a model:
#   score >= MUTATION_MIN_SCORE (default 0.85)  -> GO, and no model is called at all
#   score <  MUTATION_MIN_SCORE                 -> the survivors are sent to the gate model, which
#                                                  turns each one into the missing test case, then
#                                                  we stop and route back to the Test-Author
#   cannot score                                -> stop (fail closed)
# Mutation is diff-scoped: cr-filter-git skips mutants outside the phase's diff vs MUTATION_BASE,
# so a phase is judged on the code it actually changed, not the whole package.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */handover.md) ;;
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0

AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
MUTATION_MIN_SCORE="${MUTATION_MIN_SCORE:-0.85}"
CFG="$CLAUDE_PROJECT_DIR/cosmic-ray.toml"
if [ ! -f "$CFG" ]; then
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
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation:baseline-failed"
  exit 2
fi

if ! cosmic-ray init "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
  echo "cosmic-ray init errored (fail closed):" >&2; tail -5 "$TMP" >&2
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation:errored"
  exit 2
fi
# Diff-scope: mark every mutant outside the phase's diff as skipped. mutation_score.py excludes
# skipped mutants from the denominator (cosmic-ray's own cr-rate would count them as kills).
if [ -n "$FILTER_BASE" ] && ! cr-filter-git --config "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
  echo "cr-filter-git errored (fail closed) — refusing to mutate unscoped:" >&2; tail -5 "$TMP" >&2
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation:filter-errored"
  exit 2
fi
if ! cosmic-ray exec "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
  echo "cosmic-ray exec errored (fail closed):" >&2; tail -5 "$TMP" >&2
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation:errored"
  exit 2
fi

# Deterministic verdict. 0 = GO (no model call), 1 = survivors below threshold, 2 = cannot score.
python3 "$SD/mutation_score.py" --min-score "$MUTATION_MIN_SCORE" "$SESSION"
sc=$?
if [ "$sc" -eq 0 ]; then
  exit 0
fi
if [ "$sc" -ne 1 ]; then
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation:unscorable"
  exit 2
fi

# Below threshold: the model's only job is turning each survivor into the missing test case.
# The verdict is already decided here, so gate_runner's own exit code is advisory — we stop either
# way. (If it errors, we still stop: fail closed.)
{ echo "---- mutation score (deterministic gate verdict: NO-GO) ----"
  python3 "$SD/mutation_score.py" --min-score "$MUTATION_MIN_SCORE" --json "$SESSION" 2>&1
  echo "---- survivors (cosmic-ray dump) ----"
  cosmic-ray dump "$SESSION" 2>&1
} >>"$TMP"
python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/mutation-interpret.md" \
  --model "${GATE_MODEL:-google/gemini-2.5-pro}" --author-family "$AUTHOR_FAMILY" \
  --target "$TMP" || true
echo "mutation gate: NO-GO — route back to Test-Author (add the cases named above)." >&2
[ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation"
exit 2
