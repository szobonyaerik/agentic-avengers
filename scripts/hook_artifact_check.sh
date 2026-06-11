#!/usr/bin/env bash
# Stop hook: any phase that finished implementing must have its full artifact set.
# (Keyed to implementation-report.md so it never fires on a phase still being built.)
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0
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
exit 0