#!/usr/bin/env bash
# PostToolUse on Bash: stamp the close of every phase the commit that just ran actually landed.
#
# MEASUREMENT, NEVER A GATE. This always exits 0 — the commit has already happened by the time this
# runs, so a non-zero exit here would stop a turn over a record nobody can write. Everything below
# fails open, and `emission_gate.py close` is what makes a stamp that never landed visible later.
#
# WHY A HOOK AT ALL. Issue #46 moved `closed` from implementation completion to LANDING, correctly:
# a stamp taken when the implementer finished understates the phase by verification, route-backs and
# close — its most expensive stages — and the too-early number is indistinguishable from a good one.
# Nothing then emitted it at landing. `commands/avenger-run.md` §5 asked the orchestrator to run
# `phase-close` itself right after the commit, and said outright that "no hook can see this commit
# land, so no hook can stamp it". That was the whole defect: an instruction an agent has to remember
# is not a mechanism, and for two measured phases running it was not remembered. Phase 12 landed at
# 2026-08-21T11:15:08Z carrying `closed`, `elapsed_minutes`, `tests_before`, `tests_after` and
# `verification_attempts` all still null, and a person entered all six by hand hours later.
#
# A `PostToolUse` hook on `Bash` runs AFTER the tool call, so it does see the commit land. It is the
# same correction `hook_plugin_release.sh` made in the other direction: put the rule where something
# executes it.
#
# §5's instruction stays, and the two do not conflict: `record_phase_close` now CONVERGES on a phase
# already closed, writing nothing. That matters more than tidiness — opencode's adapter routes only
# `write`/`edit` tool events, so **opencode does not carry this hook** and its orchestrator's own
# `phase-close` is the only emitter there. Said out loud rather than implied, the way every other
# runtime gap in this pipeline is.
#
# SCOPE. The phases the commit touched, read from the commit itself (`git show --name-only`), not
# from a phase the caller names: the commit is the landing, so what it contains is what landed.
# `record_phase_close` still refuses the write while anything under the phase directory is
# uncommitted, so a `git commit` that left the phase dirty records nothing rather than a false close.
#
# BUDGET. `hooks.json` gives this 120s, which is `gate_timeouts.HOOK_HEADROOM_S` and is what one
# `phase-close` can actually spend: `AVENGER_METRICS_TIMEOUT` (10s) for the writer plus
# `COLLECT_TIMEOUT_S` (60s) for the `pytest --collect-only` that sizes the suite. It is NOT covered
# by `gate_timeouts.py verify`, which only walks hooks that reach the gate runner — stated here
# rather than left to be assumed, since this hook spends measurement budget like the ones that are.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)

INPUT=$(cat)
# No `jq`, an unreadable payload, a tool call carrying no command: nothing is stamped and nothing is
# claimed. The close is then owed to the orchestrator's own `phase-close`, and `emission_gate.py
# close` fails the next commit if neither happened — a missed stamp is visible, never silent.
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$CMD" ] || exit 0
case "$CMD" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --verify HEAD >/dev/null 2>&1 || exit 0

# A commit whose `git commit` failed leaves HEAD where it was, so this reads an earlier commit and
# every phase in it is already closed — which converges to nothing rather than to a wrong stamp.
git show --name-only --pretty=format: HEAD 2>/dev/null \
  | sed -n 's|^\(docs/features/[^/]*/phases/[^/]*\)/.*|\1|p' \
  | sort -u \
  | while IFS= read -r dir; do
      [ -n "$dir" ] && [ -d "$dir" ] || continue
      # stderr is deliberately kept: a run whose every stamp was refused must not look like a run
      # with no phase to stamp.
      python3 "$SD/pipeline_metrics.py" phase-close "$dir" >/dev/null || true
    done

exit 0
