#!/usr/bin/env bash
# Source this from a hook to pull pipeline config out of the project's .env.
#
#   . "$SD/load_env.sh"
#
# The file is PARSED by env_file.py and re-emitted as shlex-quoted `export` statements, never
# sourced — a `.env` is a place people paste secrets, not shell to execute. Variables already set in
# the environment are left alone, so a CI secret always beats the file.
#
# Silent and non-fatal by design: no .env, no python3, a malformed file — the caller proceeds with
# whatever environment it already had, and the gate's own fail-closed check reports a missing key.

__avenger_env_root="${CLAUDE_PROJECT_DIR:-$PWD}"
__avenger_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  eval "$(python3 "$__avenger_env_dir/env_file.py" --export --root "$__avenger_env_root" 2>/dev/null)"
fi

unset __avenger_env_root __avenger_env_dir
