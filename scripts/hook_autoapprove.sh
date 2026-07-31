#!/usr/bin/env bash
# PreToolUse hook: skip permission prompts while an `/avenger-run --auto` run is active.
#
# A slash command cannot change a running session's permission mode, so `--auto` writes a sentinel
# (.avenger-auto, holding a unix timestamp) and this hook grants permission while it exists. Anything
# else — no sentinel, expired sentinel, unreadable sentinel, unparseable payload — prints NOTHING and
# the normal permission flow applies untouched. Silence is the safe default and every failure path
# takes it.
#
# HARD DENIALS are not configurable. `git push`, publishing commands and `rm` are denied outright for
# the duration of an auto run: the orchestrator branches and commits but never pushes, and nothing in
# the pipeline needs to delete files. No environment variable re-enables them.
#
#   AVENGER_AUTO_DENY      extra regex of commands to deny (added to, never replacing, the hard list)
#   AVENGER_AUTO_TTL_MIN   sentinel lifetime in minutes (default 240) — a crashed run expires
set -uo pipefail

PROJECT="${CLAUDE_PROJECT_DIR:-}"
[ -n "$PROJECT" ] || exit 0
SENTINEL="$PROJECT/.avenger-auto"
[ -f "$SENTINEL" ] || exit 0

PAYLOAD="$(cat)"

python3 - "$SENTINEL" "$PAYLOAD" "${AVENGER_AUTO_DENY:-}" "${AVENGER_AUTO_TTL_MIN:-240}" <<'PY'
import json
import re
import sys
import time

sentinel, raw, extra_deny, ttl_min = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

# Outward-facing or unrecoverable. Deliberately not overridable — see the header.
HARD_DENY = r"""
    git\s+push
  | gh\s+(pr|release|repo)\s+(create|merge|edit|delete)
  | (npm|yarn|pnpm)\s+publish
  | twine\s+upload
  | (^|[;&|]\s*)\s*rm\b
  | (^|[;&|]\s*)\s*sudo\b
  | \bdd\s+if=
  | \bmkfs
  | curl[^|]*\|\s*(ba)?sh
"""


def defer() -> None:
    """Print nothing: the normal permission flow decides."""
    raise SystemExit(0)


try:
    started = float(open(sentinel, encoding="utf-8").read().strip())
except (OSError, ValueError):
    defer()

try:
    if (time.time() - started) > float(ttl_min) * 60:
        defer()
except ValueError:
    defer()

try:
    payload = json.loads(raw)
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
except (ValueError, AttributeError):
    defer()

command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""


def decide(decision: str, reason: str) -> None:
    """Emit the permission decision and stop."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    raise SystemExit(0)


if command:
    if re.search(HARD_DENY, command, re.IGNORECASE | re.VERBOSE):
        decide(
            "deny",
            "avenger-run --auto never auto-approves push, publish, rm or sudo. "
            "Run it yourself outside the auto run if you meant it.",
        )
    if extra_deny.strip():
        try:
            if re.search(extra_deny, command, re.IGNORECASE):
                decide("deny", f"blocked by AVENGER_AUTO_DENY: {extra_deny}")
        except re.error:
            defer()  # a bad user regex must not silently grant

decide("allow", f"/avenger-run --auto is active; {tool or 'tool'} auto-approved")
PY

exit 0
