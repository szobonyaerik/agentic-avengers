#!/usr/bin/env bash
# PreToolUse hook: refuse to spawn a pipeline stage from a plugin copy that is known-stale against
# the merged repository.
#
# `scripts/plugin_release.py check` already answers "is the copy executing right now the same code
# as the merged repository" and exits 1 when it is not. Nothing in the shipped payload ever ran it:
# the STOP lived in `commands/avenger-run.md` §1 as a sentence telling the orchestrator to halt, and
# an orchestrator that does not read that line, or reads it and continues, runs every phase against
# code a merged fix already replaced with no signal anywhere. That is issue #69's class exactly - a
# document asserting behaviour nothing enforces - and it is the class that made ten merged pipeline
# fixes inert while every run looked healthy. The check was mechanical; the halt was prose.
#
# So the halt is here, on the one event that can actually refuse: PreToolUse, at the moment a stage
# is spawned and immediately before stale code would execute a phase. `SubagentStart` is where every
# other stage-scoped hook in this repo lives and is the wrong event for this one - it CANNOT block
# (Claude Code documents exit 2 there as "shows stderr to user only", the subagent proceeds), so a
# halt written there would be one more thing that looks enforced and is not.
#
# WHAT IT BINDS. Only a confirmed STALE, and only for this pipeline's own stages:
#
#   * AN HONEST "UNKNOWN" IS NOT A HALT. A machine with no source repository resolvable cannot
#     answer the question at all - agentic-avengers ships standalone and most repositories using it
#     have no way to answer it - so `unknown` proceeds exactly as it does today. This is the same
#     applicability boundary (CLAUDE.md §3a) every other check here draws around a scope it cannot
#     resolve: what it cannot state, it does not block.
#   * EVERYTHING THAT IS NOT A VERDICT FAILS OPEN. No jq, no python3, an unreadable payload, a
#     checker that crashed, a bad `PLUGIN_RELEASE_STAGES` regex - each lets the spawn through. The
#     halt exists to stop a run on a KNOWN defect, never to stop one on its own inability to look.
#     A non-zero exit that is not the STALE verdict is still said out loud on stderr rather than
#     passing invisibly, because "could not tell" and "clean" must not look identical (§3a).
#   * ONLY THIS PIPELINE'S STAGES. `Explore`, `general-purpose` and every foreign subagent spawn
#     through the same tool and are none of this hook's business.
#
# The verdict is read as BOTH the exit code AND the checker's own `STALE:` marker. `main()` exits 1
# for a stale copy, and a Python traceback exits 1 too - a crash arriving as a halt would prescribe
# "cut a release" for a defect a release cannot fix, which is the same wrong-remedy failure the spec
# gate's exit-3 split exists to prevent. Nothing here re-decides the verdict: `plugin_release.py` is
# falsification-tested and untouched, and this hook only refuses to let its answer be ignored.
#
# Break-glass is the ordinary one: GATE_BYPASS="reason" proceeds on stale code, logged visibly to
# gate-overrides.log through the single writer every other gate uses.
#
#   PLUGIN_RELEASE_STAGES   regex of subagent_type values this binds (unanchored, case-insensitive).
#                           Default `avenger-`, which matches plugin-scoped names such as
#                           "plan-build-verify:avenger-verifier" because it is unanchored.
#   GATE_BYPASS="reason"    proceed anyway; audited in gate-overrides.log.
#
# opencode does not carry this: its adapter (.opencode/plugin/pipeline-gates.ts) hooks
# `tool.execute.after`, which is after the fact and has no pre-spawn event to refuse at. There, the
# preflight report in commands/avenger-run.md is the only signal, and it says so.
set -uo pipefail

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # PLUGIN_RELEASE_STAGES / AVENGER_SOURCE_REPO may live in the project .env

PAYLOAD="$(cat)"

# Decided on `subagent_type`, never on the tool's NAME: the spawn tool is `Task` in some harness
# versions and `Agent` in others, and a hook keyed to the wrong name would silently never fire - 
# exactly the "looks enforced, is not" failure this hook exists to remove. hooks.json still matches
# both names so the hook is only invoked on spawns, but the decision below does not depend on it.
STAGE=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)
[ -n "$STAGE" ] || exit 0

STAGES_RE="${PLUGIN_RELEASE_STAGES:-avenger-}"
printf '%s' "$STAGE" | grep -qiE "$STAGES_RE" 2>/dev/null || exit 0

REPORT=$(python3 "$SD/plugin_release.py" check 2>&1); RC=$?

# Exit 0 is fresh OR unknown - both proceed. The checker's own two-line report is relayed here
# rather than dropped: it is captured with `2>&1` above, so without this the documented "unknown is
# reported, never enforced" contract would be a claim nothing performed, and a standalone install
# would see the same silence for "cannot tell" as for "clean" (§3a).
if [ "$RC" -eq 0 ]; then
  printf '%s\n' "$REPORT" >&2
  exit 0
fi

if ! printf '%s\n' "$REPORT" | grep -q '\[plugin-release\] STALE:'; then
  printf '%s\n' \
    "plugin release: the staleness check could not reach a verdict (exit $RC) - the spawn is NOT" \
    "blocked, because a check that could not look is not a finding. Its output follows." \
    "$REPORT" >&2
  exit 0
fi

# The finding is stated before the outcome is decided, and the outcome is stated by the branch that
# takes it. Printing the whole refusal above this point told a bypassing operator that '$STAGE' "is
# refused" one line before the spawn proceeded, which is the opposite of what happened.
printf '%s\n' \
  "plugin release: STALE - this run is executing a plugin copy that is NOT the merged repository," \
  "so merged fixes are not in effect for '$STAGE'." \
  "" \
  "$REPORT" >&2

if [ -n "${GATE_BYPASS:-}" ]; then
  printf '%s\n' \
    "Proceeding on stale code under break-glass; the spawn is NOT blocked." >&2
  exec "$SD/bypass_log.sh" "plugin-release"
fi

printf '%s\n' \
  "The stage is refused before it can build against them." \
  "" \
  "Cut a release from your checkout of this plugin's own repository:" \
  "" \
  "    python3 scripts/plugin_release.py cut" \
  "" \
  "then restart Claude Code so the harness re-reads the refreshed cache, and start the run again." \
  "" \
  "If that cut is refused because the version already holds different content, the payload has" \
  "changed since it was released under that number: bump \"version\" in .claude-plugin/plugin.json" \
  "first, then cut again." \
  "" \
  "To proceed on stale code anyway, re-run with GATE_BYPASS=\"<reason>\" - visible and logged to" \
  "gate-overrides.log." >&2

exit 2
