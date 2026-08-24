#!/usr/bin/env bash
# Stop hook: any phase that finished implementing must have its full artifact set, and every
# artifact must obey the read path (skills/pipeline-conventions § The document read path).
#
# The key is `scripts/phase_artifacts.py` — a phase whose every spec is stamped `status: done` — so
# the sweep never fires on a phase still being built. It used to be keyed to the PRESENCE of
# `implementation-report.md`, a file nothing in the pipeline was instructed to write: no template,
# no agent, no skill, no command, no script. A sweep keyed to an artifact nobody produces is a sweep
# that mostly does not run, and it is why that class was removed rather than declared (issue #29).
#
# It asks for ONE artifact: `test-mapping.md` beside each spec, owed at that spec's own
# `status: done` stamp. It deliberately does NOT ask for `handover.md` — `hook_verifier.sh` owns
# that write and refuses it on a passing verdict plus six further checks, so any condition this
# sweep could ask is weaker than the one that permits the write, and asking at all produces phases
# told to create a document nothing will let them write.
# Diff-scoped like the check below it (no --all):
# phases you have not touched are counted on stderr rather than blocking the session.
#
# ONLY AN OBLIGATION BLOCKS. Both checks below split their exit codes: 1 means an artifact is
# genuinely owed, 2 means the check could not reach a verdict at all. Flattening the two onto one
# blocking code told an agent "create them before stopping" for a crash, which is a remedy that
# cannot repair one, and this hook honours no GATE_BYPASS, so a defect in a checker wedged a session
# that owed nothing. A check that could not look is not a finding: it lets the Stop through and says
# so, naming itself, which is the convention hook_plugin_release.sh and hook_implementer_lock.sh
# already follow.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# $1 = the check's name, "$@" = the command. Blocks on 1, steps aside loudly on anything else.
run_check () {
  local name="$1"; shift
  "$@"
  local rc=$?
  [ "$rc" -eq 0 ] && return 0
  [ "$rc" -eq 1 ] && exit 2
  printf '%s\n' \
    "⚠ artifact check: $name could not reach a verdict (exit $rc). The Stop is NOT blocked," \
    "  because a check that could not answer is not an artifact anybody forgot to write. Its own" \
    "  output above names the cause; this is a defect in the check or in what it reads." >&2
  return 0
}

run_check "phase_artifacts.py check" python3 "$SD/phase_artifacts.py" check .

# The handover byte cap and the `readers:` declaration. Mechanical, no model — the template asked
# for a 5-line summary for as long as it existed and got 37 KB averages, so the cap is checked
# rather than requested. Diff-scoped (no --all): you answer for the artifacts you changed, and
# history you have not touched is counted on stderr rather than blocking the session.
run_check "doc_read_path.py check" python3 "$SD/doc_read_path.py" check .
exit 0
