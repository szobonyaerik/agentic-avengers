#!/usr/bin/env bash
# Stop hook: any phase that finished implementing must have its full artifact set, and every
# artifact must obey the read path (skills/pipeline-conventions § The document read path).
# (Keyed to implementation-report.md so it never fires on a phase still being built.)
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
missing=""
while IFS= read -r report; do
  dir=$(dirname "$report")
  for f in test-mapping.md handover.md; do
    [ -f "$dir/$f" ] || missing="${missing}"$'\n'"- $dir/$f"
  done
done < <(find docs/features -type f -name implementation-report.md 2>/dev/null)
if [ -n "$missing" ]; then
  printf 'Phase artifacts missing (create them before stopping):%s\n' "$missing" >&2
  exit 2
fi

# The handover byte cap and the `readers:` declaration. Mechanical, no model — the template asked
# for a 5-line summary for as long as it existed and got 37 KB averages, so the cap is checked
# rather than requested. Diff-scoped (no --all): you answer for the artifacts you changed, and
# history you have not touched is counted on stderr rather than blocking the session.
python3 "$SD/doc_read_path.py" check . || exit 2
exit 0
