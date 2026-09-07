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
# Mutation is diff-scoped over the WORKING TREE (scripts/mutation_scope.py): mutants outside the
# lines this change is responsible for - modified, staged AND untracked - are skipped, so a phase is
# judged on the code it actually changed. Nothing in scope is `did-not-run`, never a pass.
#
# MUTATION_POLICY selects how much authority the verdict has (pipeline-conventions: "Gates"):
#   off            - skipped entirely; no mutation tool runs anywhere.
#   advisory (DEFAULT) - runs everything and reports the score and the missing cases, never blocks.
#   enforce        - below threshold or unscorable STOPS the phase.
# Mutation is an EXTRA signal. With the cross-family reading pass gone it is the only systematic
# signal about non-discriminating tests — partial cover, not a dedicated reader. Only `enforce`
# fails closed.
# The Verifier invokes this when the policy is on; it is not a standalone quality bar.
#
# TREE INTEGRITY (issue #95). `cosmic-ray exec` mutates source IN PLACE and reverts in a `finally`;
# the kill path below SIGTERMs then SIGKILLs its process group, and neither runs a `finally`, so the
# mutant applied at that moment stayed on disk and was committed as authored code - four times in one
# measured phase, three under `verdict: pass`, in live trading code. So every `exec` here is bracketed
# by scripts/mutation_exec_guard.py: a snapshot of every file the session can write before, an
# explicit restore-and-compare after - on the KILL PATH too, which is where the damage happens - and a
# tree that differed FAILS THE HOOK whatever the policy, because a corrupted tree is never advisory.
# And exec is not started at all when the baseline's measured wall clock times the pending mutants
# would not fit the budget the hook has left (MUTATION_HOOK_BUDGET_S, default the hooks.json
# timeout): a run that would be killed mid-mutant is refused up front, where that can be known.
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
HOOK_T0=$SECONDS

AUTHOR_FAMILY="${AUTHOR_FAMILY:-anthropic}"
MUTATION_MIN_SCORE="${MUTATION_MIN_SCORE:-0.85}"
# Default `advisory`, matching gate_ci.sh — the two must not disagree about what runs. Advisory
# reports the score and its survivors and never blocks; it is an extra deterministic signal, not the
# independence mechanism. `off` stays available and runs no mutation tool anywhere.
MUTATION_POLICY="${MUTATION_POLICY:-advisory}"

# The budget exec must fit inside: the hook's own hooks.json timeout unless the operator says
# otherwise. Read from the shipped hooks.json rather than restated here, so the two cannot drift.
# EXEC_HEADROOM_S is what runs AFTER exec - the restore, the score, the metrics - plus the kill
# grace; the estimate is checked against what is left once both are set aside.
hook_budget_default() {
  local cfg="$SD/../hooks/hooks.json"
  [ -f "$cfg" ] || return 0
  jq -r '[.. | objects | select(has("command")) | select(.command | test("hook_mutation.sh")) | .timeout] | first // empty' "$cfg" 2>/dev/null
}
MUTATION_HOOK_BUDGET_S="${MUTATION_HOOK_BUDGET_S:-$(hook_budget_default)}"
: "${MUTATION_HOOK_BUDGET_S:=600}"
EXEC_HEADROOM_S=30

case "$MUTATION_POLICY" in
  enforce|advisory) ;;
  off)
    echo "mutation: skipped (MUTATION_POLICY=off). No systematic signal about non-discriminating tests remains." >&2
    exit 0 ;;
  *)
    echo "mutation: MUTATION_POLICY='$MUTATION_POLICY' is not one of enforce|advisory|off (fail closed)" >&2
    exit 2 ;;
esac

# A gate that could not run must leave a record saying so. `advisory` never blocks — that is a
# deliberate decision, not the defect — but for two whole phases the gate never once ran (no config
# at the repo root, the tool not installed) and nothing DURABLE distinguished that from a gate that
# ran and found nothing. A hypothesis then settled on the premise that MUTATION_POLICY=advisory was
# its changed variable, while the gate under test had never executed. Stderr is not where a later
# reader looks. Fails open like every other metrics call here: `|| true`, and the CLI exits 0.
record_unavailable() {
  python3 "$SD/pipeline_metrics.py" mutation-unavailable "$FILE" "$1" >/dev/null 2>&1 || true
}

# The budget is validated the moment it can be, and never reaches bash arithmetic unvalidated: a
# non-numeric value makes `$(( ))` fail, leaves REMAINING_S unset, and the next expansion under
# `set -u` exits 1 - the whole gate and its tree guard skipped, no record written, and exit 1 is not
# the blocking code either, so `enforce` does not stop. A configuration error stops, named, exactly
# as an unrecognised MUTATION_POLICY does. It is asked AFTER the policy case so `off` still runs no
# mutation tool anywhere. Leading zeros are normalised out: `$((0600))` is octal.
budget_config_error() {
  echo "mutation: MUTATION_HOOK_BUDGET_S='$MUTATION_HOOK_BUDGET_S' is not a positive integer number of seconds (fail closed)" >&2
  record_unavailable "MUTATION_HOOK_BUDGET_S='$MUTATION_HOOK_BUDGET_S' is not a positive integer number of seconds - the gate did not run"
  exit 2
}
case "$MUTATION_HOOK_BUDGET_S" in
  ''|*[!0-9]*) budget_config_error ;;
  *) [ "$((10#$MUTATION_HOOK_BUDGET_S))" -gt 0 ] || budget_config_error ;;
esac
MUTATION_HOOK_BUDGET_S=$((10#$MUTATION_HOOK_BUDGET_S))

CFG="$CLAUDE_PROJECT_DIR/cosmic-ray.toml"
if [ ! -f "$CFG" ]; then
  record_unavailable "cosmic-ray.toml missing at repo root"
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

# The pre-exec snapshot, and the one restore that answers it. SNAP is armed only once the snapshot
# has COMPLETED - never before it, because `snapshot` mkdirs its files directory as its first action,
# so an armed-early SNAP names a directory holding no manifest for the whole time it is being
# written, and a signal arriving in that window ran a restore against nothing and reported a tree
# that could not be put back while exec had provably never started. It is cleared BEFORE the restore
# runs so a second signal arriving mid-restore cannot start a second one. The restore runs in the
# FOREGROUND on purpose: bash defers a trap until the running command returns, so a kill during it
# completes the restore first. A tree that differed is recorded in TREE_CORRUPTED and every exit path
# asks it before asking the policy. Only rc 1 means the tree differed AND every file is back; rc 2
# means at least one is NOT, and any other non-zero rc - a restore killed by a signal, a crash -
# means the restore did not complete and the tree state is UNKNOWN. The last two never claim the tree
# was put back, and neither does a snapshot with no manifest to compare against.
SNAP=""
TREE_CORRUPTED=0
TREE_NOTE="the working tree was altered by cosmic-ray exec; this run's result is void"
restore_tree() {
  [ -n "$SNAP" ] && [ -d "$SNAP" ] || return 0
  local snap="$SNAP"; SNAP=""
  if [ ! -f "$snap/manifest.json" ]; then
    TREE_CORRUPTED=1
    TREE_NOTE="the pre-exec snapshot carries no manifest, so what cosmic-ray exec left behind is UNKNOWN; this run's result is void"
    { echo "mutation: TREE INTEGRITY UNKNOWN - the snapshot at $snap holds no manifest, so there is nothing to compare the tree against."
      echo "mutation: no claim can be made that the tree was put back. Inspect the working tree by hand before committing anything."
    } >&2
    return 0
  fi
  python3 "$SD/mutation_exec_guard.py" restore --root "$CLAUDE_PROJECT_DIR" "$snap" >"$WORK/restore.txt" 2>&1
  local rc=$?
  # A CLEAN restore says on stderr what a clean result does not establish (guard_scope, CLAUDE.md
  # §11). Held in a file and cat'd only in the failure branch, that statement reached the reader on
  # exactly the runs it exists for - the clean ones, which are the ones that get over-read.
  if [ "$rc" -eq 0 ]; then cat "$WORK/restore.txt" >&2; return 0; fi
  TREE_CORRUPTED=1
  if [ "$rc" -eq 1 ]; then
    TREE_NOTE="the working tree was altered by cosmic-ray exec and restored from the pre-exec snapshot; this run's result is void"
  elif [ "$rc" -eq 2 ]; then
    TREE_NOTE="the working tree was altered by cosmic-ray exec and NOT every file could be put back; this run's result is void and the tree needs inspecting by hand"
  else
    TREE_NOTE="the tree check after cosmic-ray exec did not complete (exit $rc), so whether a mutant is still applied is UNKNOWN; this run's result is void"
  fi
  { if [ "$rc" -eq 1 ] || [ "$rc" -eq 2 ]; then
      echo "mutation: TREE INTEGRITY FAILED - cosmic-ray exec left the working tree different from the state it found it in."
    else
      echo "mutation: TREE INTEGRITY UNKNOWN - the restore did not complete (exit $rc), so what cosmic-ray exec left behind could not be established."
    fi
    cat "$WORK/restore.txt"
    if [ "$rc" -eq 1 ]; then
      echo "mutation: every in-scope file has been put back from the pre-exec snapshot. Nothing from this run is a verdict."
    elif [ "$rc" -eq 2 ]; then
      echo "mutation: and NOT every file could be put back. Inspect the working tree by hand before committing anything."
    else
      echo "mutation: no claim can be made that the tree was put back. Inspect the working tree by hand before committing anything."
    fi
  } >&2
}
# Never advisory. A mutant left in the tree is not a weak test signal, it is authored code that
# nobody authored; the policy switch selects how much authority the SCORE has and says nothing about
# this. GATE_BYPASS is honoured through the same audited path as every other blocking check, and the
# restore has already run by the time it is asked.
fail_tree() {
  record_unavailable "$TREE_NOTE"
  echo "mutation: a corrupted tree is never advisory - failing the hook (MUTATION_POLICY=$MUTATION_POLICY)." >&2
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "mutation:tree-corrupted"
  cleanup; trap - EXIT
  exit 2
}

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
  # The kill path is where the mutant is left behind, so the tree is asked BEFORE the policy is.
  restore_tree
  [ "$TREE_CORRUPTED" -eq 1 ] && fail_tree
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
  # The tree is restored AFTER the reap (inside exit_killed): a worker still alive could re-apply
  # the next mutant over a restore that ran before it was gone.
  trap 'signal_child_group "'"$pid"'"; announce_kill; reap_child_group "'"$pid"'"; exit_killed' TERM INT
  wait "$pid"; local rc=$?
  trap on_kill TERM INT
  return "$rc"
}

# Every stopping condition funnels through here so `advisory` cannot be forgotten at one of them.
# $1 = bypass tag, $2 = what went wrong (already printed in detail by the caller).
stop_or_report() {
  record_unavailable "$2"
  if [ "$MUTATION_POLICY" = "advisory" ]; then
    printf 'mutation [advisory]: %s — reporting only, not blocking (MUTATION_POLICY=advisory)\n' "$2" >&2
    exit 0
  fi
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit "$1"
  exit 2
}

# Scoped config = the project's config, unchanged. The `[cosmic-ray.filters.git-filter]` section
# this used to append is gone with the filter that read it: scoping is now
# scripts/mutation_scope.py, over the working tree. The base is still resolved, because it WIDENS
# the working-tree scope to the branch for a run with nothing uncommitted (CI).
cp "$CFG" "$SCOPED"
if BASE=$(resolve_base) && [ -n "$BASE" ]; then
  FILTER_BASE="$BASE"
else
  echo "mutation: no diff base resolvable — scoping on the working tree alone" >&2
  FILTER_BASE=""
fi

# Baseline FIRST. A mutant counts as "killed" whenever the test command fails — so if the suite is
# already broken (a collection error, say), every mutant is killed and the score is a perfect 1.0.
# A broken suite would otherwise score better than a real one. Verified: with an import error in the
# suite, all 7 fixture mutants reported 'killed'. No kill means anything until the baseline is green.
BASELINE_T0=$SECONDS
if ! run_child cosmic-ray baseline "$SCOPED"; then
  echo "mutation: baseline FAILED — the suite does not pass on unmutated code, so every mutant" >&2
  echo "would score as killed. Refusing to score (fail closed). Fix the suite first:" >&2
  tail -5 "$TMP" >&2
  stop_or_report "mutation:baseline-failed" "baseline failed, so no mutant result is meaningful"
fi
BASELINE_S=$((SECONDS - BASELINE_T0)); [ "$BASELINE_S" -ge 1 ] || BASELINE_S=1

if ! run_child cosmic-ray init "$SCOPED" "$SESSION"; then
  echo "cosmic-ray init errored (fail closed):" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:errored" "cosmic-ray init errored"
fi
# Diff-scope over the WORKING TREE, not `git diff` against a branch. `cr-filter-git` scopes with
# `git diff --relative -U0 <branch> .`, which reports nothing about an UNTRACKED file - and this
# pipeline verifies uncommitted work, so a phase whose contribution was NEW FILES had every mutant
# marked skipped and the scorer returned GO over zero measured lines (issue #97).
# scripts/mutation_scope.py asks the same question `applicability.changed_paths` asks everywhere
# else here - modified, staged, untracked - at line granularity. mutation_score.py excludes skipped
# mutants from the denominator (cosmic-ray's own cr-rate would count them as kills).
#
# Exit 3 is NOTHING IN SCOPE. It is routed through `stop_or_report` rather than treated as a pass,
# so advisory records `did-not-run` durably and enforce stops. That distinction IS the fix: the
# absence of a measurement must never arrive looking like a clean gate.
run_child python3 "$SD/mutation_scope.py" ${FILTER_BASE:+--base "$FILTER_BASE"} --root "$CLAUDE_PROJECT_DIR" "$SESSION"
fc=$?
if [ "$fc" -eq 3 ]; then
  tail -5 "$TMP" >&2
  stop_or_report "mutation:did-not-run" "no mutant fell inside this phase's changed lines - the gate did not run, and this is NOT a score"
elif [ "$fc" -ne 0 ]; then
  echo "the working-tree scope filter errored (fail closed) - refusing to mutate unscoped:" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:filter-errored" "the working-tree scope filter errored, scope unknown"
fi

# Refuse to START exec when it cannot finish (issue #95). The estimate is the one available up front
# on every run: the baseline's measured wall clock - one run of the test command - times the mutants
# still pending after scoping, against what the budget has left once the headroom and the kill grace
# are set aside. A lower bound, deliberately: a hung mutant runs to cosmic-ray's own timeout, which is
# why the restore below runs on the kill path as well rather than trusting this.
REMAINING_S=$((MUTATION_HOOK_BUDGET_S - (SECONDS - HOOK_T0) - TERM_GRACE_S - EXEC_HEADROOM_S))
python3 "$SD/mutation_exec_guard.py" budget --session "$SESSION" --baseline-s "$BASELINE_S" --budget-s "$REMAINING_S" >>"$TMP" 2>&1
bc=$?
if [ "$bc" -eq 1 ]; then
  tail -3 "$TMP" >&2
  stop_or_report "mutation:over-budget" "exec would not finish inside the hook's budget (${BASELINE_S}s baseline, ${REMAINING_S}s left of ${MUTATION_HOOK_BUDGET_S}s) - refused to start rather than be killed with a mutant applied"
elif [ "$bc" -ne 0 ]; then
  echo "the exec budget could not be estimated (fail closed):" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:budget-errored" "the exec budget could not be estimated, so a kill mid-mutant could not be ruled out"
fi

# Snapshot every file the session can write, from the session itself. No snapshot, no exec: a run
# that cannot be checked afterwards is a run that can leave a mutant behind unnoticed, which is the
# defect. Foreground, like the restore: short, and a kill during it completes it first.
#
# SNAP is armed AFTER the command returns 0, and that ordering is the whole of it: while the snapshot
# is being written its directory exists and holds no manifest, so a SNAP armed ahead of it points at
# a snapshot that cannot answer anything. A kill in that window is a kill BEFORE exec - nothing was
# altered, and the restore has nothing to say - which is what an unarmed SNAP reports.
if ! python3 "$SD/mutation_exec_guard.py" snapshot --root "$CLAUDE_PROJECT_DIR" --session "$SESSION" "$WORK/tree" >>"$TMP" 2>&1; then
  echo "the in-scope files could not be snapshotted before exec (fail closed) - refusing to start it:" >&2; tail -5 "$TMP" >&2
  stop_or_report "mutation:integrity-unavailable" "the in-scope files could not be snapshotted, so exec could not be guarded - refused to start"
fi
SNAP="$WORK/tree"

run_child cosmic-ray exec "$SCOPED" "$SESSION"
ec=$?
# The tree is asked FIRST, on success and failure alike, and its answer outranks both.
restore_tree
[ "$TREE_CORRUPTED" -eq 1 ] && fail_tree
if [ "$ec" -ne 0 ]; then
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
  # A passing score is not a quality bar, and this says so beside the pass (issue #97).
  python3 "$SD/guard_scope.py" emit hook_mutation.sh >/dev/null || true
  exit 0
fi
if [ "$sc" -eq 3 ]; then
  stop_or_report "mutation:did-not-run" "nothing was tested - the gate did not run, and this is NOT a score"
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
