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
# Its two halves are keyed at different moments: each spec owes `test-mapping.md` at its own
# `status: done` stamp, and the phase owes `handover.md` only once a PASSING verdict.json exists,
# because `hook_verifier.sh` refuses the handover write before that and the verification stage is a
# window a resumable run legitimately stops inside. "Passing" is read the strict way that hook reads
# it — `status: open` counted literally, break-glass waiver included — so the gate that asks for the
# handover and the gate that allows it cannot disagree about one verdict.
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
