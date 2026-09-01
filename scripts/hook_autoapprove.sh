#!/usr/bin/env bash
# PreToolUse hook: skip permission prompts while an `/avenger-run --auto` run is active.
#
# A slash command cannot change a running session's permission mode, so `--auto` writes a sentinel
# (.avenger-auto, holding a unix timestamp) and this hook grants permission while it exists. Anything
# else — no sentinel, expired sentinel, unreadable sentinel, unparseable payload — prints NOTHING and
# the normal permission flow applies untouched. Silence is the safe default and every failure path
# takes it.
#
# HARD DENIALS are not configurable. `git push`, publishing commands, GitHub PR/issue/release/gist
# creation and `rm` are denied outright for the duration of an auto run: the orchestrator branches
# and commits but never pushes, and nothing in the pipeline needs to delete files. Issue creation is
# included because the pipeline-retrospective files improvement issues upstream, and its
# confirmation gate is a human selecting them in a lavish artifact — which cannot happen on an
# unattended run. No environment variable re-enables any of them.
#
#   AVENGER_AUTO_DENY      extra regex of commands to deny (added to, never replacing, the hard list)
#   AVENGER_AUTO_TTL_MIN   sentinel lifetime in minutes (default 240) — a crashed run expires
set -uo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/load_env.sh"   # AVENGER_AUTO_* may live in the project .env

PROJECT="${CLAUDE_PROJECT_DIR:-}"
[ -n "$PROJECT" ] || exit 0
SENTINEL="$PROJECT/.avenger-auto"
[ -f "$SENTINEL" ] || exit 0

PAYLOAD="$(cat)"

python3 - "$SENTINEL" "$PAYLOAD" "${AVENGER_AUTO_DENY:-}" "${AVENGER_AUTO_TTL_MIN:-240}" \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" <<'PY'
import json
import re
import sys
import time
from pathlib import Path

sentinel, raw, extra_deny, ttl_min = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
# The scripts directory, because this body runs as `python3 -` and has no __file__ of its own.
SCRIPT_DIR = sys.argv[5]

# Outward-facing or unrecoverable. Deliberately not overridable — see the header.
HARD_DENY = r"""
    git\s+push
  | gh(-axi)?\s+(pr|release|repo|issue|gist)\s+(create|merge|edit|delete|close)
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


def scope_statement() -> str:
    """What a clean (allow) result here does NOT establish, carried on the output a reader sees.

    It rides the DENY reason rather than the allow: an allow is emitted per tool call, thousands of
    times in one run, and a scope statement there would bury every other line the pipeline prints.
    The deny is where somebody is being told a verdict, and the limitation this guard has - the
    regex matches the whole command STRING, so it cannot tell an intent from a mention - is exactly
    what that reader needs. Never raises: a decision may not be moved by its own documentation.
    """
    try:
        sys.path.insert(0, str(Path(SCRIPT_DIR)))
        import guard_scope

        found, why = guard_scope.resolve("hook_autoapprove.sh")
        rendered = found.render() if found else guard_scope.notice("hook_autoapprove.sh", why)
        return f" {rendered}"
    except Exception:
        return ""


def decide(decision: str, reason: str) -> None:
    """Emit the permission decision and stop."""
    if decision == "deny":
        reason = f"{reason}{scope_statement()}"
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
            "avenger-run --auto never auto-approves push, publish, GitHub "
            "PR/issue/release/gist creation, rm or sudo. "
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
