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
#   off            - skipped entirely; no mutation tool runs anywhere.
#   advisory (DEFAULT) - runs everything and reports the score and the missing cases, never blocks.
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
# Default `advisory`, matching gate_ci.sh — the two must not disagree about what runs. Advisory
# reports the score and its survivors and never blocks; it is an extra deterministic signal, not the
# independence mechanism. `off` stays available and runs no mutation tool anywhere.
MUTATION_POLICY="${MUTATION_POLICY:-advisory}"

case "$MUTATION_POLICY" in
  enforce|advisory) ;;
  off)
    echo "mutation: skipped (MUTATION_POLICY=off). Independence rests on the Verifier's test-quality review." >&2
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

# A hook the harness kills leaves no verdict, no report and no cause — and the run reads that absence
# as the gate having objected. Say so instead, exactly as scripts/hook_spec_gate.sh does. This
# matters more now that the policy defaults to `advisory`: every internal failure path already exits
# 0 under advisory, and a kill must not be the one that blocks a phase the gate never judged.
#
# SAY IT FIRST, then clean up. The message is split from the exit precisely so nothing can run before
# it: a harness that follows SIGTERM with a SIGKILL on a short grace would otherwise kill this hook
# mid-cleanup and the line would never be emitted, which is indistinguishable from the objection it
# exists to deny.
announce_kill() {
  if [ "$MUTATION_POLICY" = "advisory" ]; then
    echo "mutation [advisory]: HOOK KILLED by the harness (signal) — this is NOT a gate verdict." >&2
    echo "  The gate did not answer; advisory never blocks." >&2
    return 0
  fi
  echo "mutation: HOOK KILLED by the harness (signal) — this is NOT a gate verdict. The gate did not answer." >&2
}
exit_killed() {
  cleanup; trap - EXIT
  [ "$MUTATION_POLICY" = "advisory" ] && exit 0
  exit 2
}
on_kill() { announce_kill; exit_killed; }
trap on_kill TERM INT

# bash defers a trap until the running FOREGROUND command returns, so a long child must run in the
# background and be waited on — otherwise the kill is reported only after the call it was killed for
# has finished, which is the same defect the spec gate's run_pass exists to avoid. Every cosmic-ray
# invocation here can outlive the hook's budget, so all of them go through this.
#
# The child is launched into its OWN SESSION and killed by PROCESS GROUP. `cosmic-ray exec` is a CLI
# that spawns workers which run the test suite, and signalling the direct child alone leaves those
# workers running — the exact leak scripts/proc_group.py was written for, where a reported 300s
# timeout was measured against 569s, 3818s and 4276s of real activity. That module is NOT reused here
# because it owns the whole call lifecycle (it captures output, applies its own timeout and returns a
# ChildResult), while this hook must stream into $TMP and die on a signal delivered from outside by a
# bash trap. So only its primitive is applied — same session-per-child, same SIGTERM, grace, SIGKILL
# order, same TERM_GRACE_S — and this comment is the statement that a second, narrower use of the
# pipeline's own primitive lives here.
TERM_GRACE_S=5

signal_child_group() {
  local pgid="$1"
  [ -n "$pgid" ] || return 0
  kill -TERM "-$pgid" 2>/dev/null || kill -TERM "$pgid" 2>/dev/null
}

reap_child_group() {
  local pgid="$1"
  [ -n "$pgid" ] || return 0
  local waited=0
  while [ "$waited" -lt $((TERM_GRACE_S * 10)) ] && kill -0 "$pgid" 2>/dev/null; do
    sleep 0.1; waited=$((waited + 1))
  done
  # Unconditional, as in proc_group.py: a group whose leader exited still has members, so "the child
  # is gone" is never evidence that the work stopped.
  kill -KILL "-$pgid" 2>/dev/null || true
}

run_child() {
  # os.setsid() then exec: the backgrounded pid IS the new session and group leader, so `kill -<pid>`
  # reaches every worker it spawns. `setsid(1)` is not on macOS, which is why this is done in python.
  python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@" >>"$TMP" 2>&1 &
  local pid=$!
  # Signal, SAY IT, then wait out the grace and escalate. The grace poll is up to TERM_GRACE_S of
  # `sleep`, so announcing after it puts a five-second window between the signal and the one line
  # whose absence reads as a gate objection — and a harness KILL inside that window loses it.
  trap 'signal_child_group "'"$pid"'"; announce_kill; reap_child_group "'"$pid"'"; exit_killed' TERM INT
  wait "$pid"; local rc=$?
  trap on_kill TERM INT
  return "$rc"
}

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
if ! run_child cosmic-ray baseline "$SCOPED"; then
  echo "mutation: baseline FAILED — the suite does not pass on unmutated code, so every mutant" >&2
  echo "would score as killed. Refusing to score (fail closed). Fix the suite first:" >&2
  tail -5 "$TMP" >&2
  stop_or_report "mutation:baseline-failed" "baseline failed, so no mutant result is meaningful"
fi

if ! run_child cosmic-ray init "$SCOPED" "$SESSION"; then
  echo "cosmic-ray init errored (fail closed):" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:errored" "cosmic-ray init errored"
fi
# Diff-scope: mark every mutant outside the phase's diff as skipped. mutation_score.py excludes
# skipped mutants from the denominator (cosmic-ray's own cr-rate would count them as kills).
if [ -n "$FILTER_BASE" ] && ! run_child cr-filter-git --config "$SCOPED" "$SESSION"; then
  echo "cr-filter-git errored (fail closed) — refusing to mutate unscoped:" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:filter-errored" "cr-filter-git errored, scope unknown"
fi
if ! run_child cosmic-ray exec "$SCOPED" "$SESSION"; then
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
