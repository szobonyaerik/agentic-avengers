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
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SD/phase_artifacts.py" check . || exit 2

# The handover byte cap and the `readers:` declaration. Mechanical, no model — the template asked
# for a 5-line summary for as long as it existed and got 37 KB averages, so the cap is checked
# rather than requested. Diff-scoped (no --all): you answer for the artifacts you changed, and
# history you have not touched is counted on stderr rather than blocking the session.
python3 "$SD/doc_read_path.py" check . || exit 2
exit 0
