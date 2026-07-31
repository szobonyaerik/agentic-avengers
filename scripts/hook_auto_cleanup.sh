#!/usr/bin/env bash
# Stop hook: retire the `/avenger-run --auto` sentinel when the turn ends.
#
# An --auto run is one long agentic turn, so Stop is the end of the run. Removing the sentinel here
# means the permission bypass cannot outlive it: the TTL in hook_autoapprove.sh is only the backstop
# for a session that dies without reaching Stop.
# Falls back to the working directory when CLAUDE_PROJECT_DIR is unset: disarming must never fail
# open. A no-op here would leave the permission bypass armed until the TTL expires.
set -uo pipefail
rm -f "${CLAUDE_PROJECT_DIR:-$PWD}/.avenger-auto" 2>/dev/null
exit 0
