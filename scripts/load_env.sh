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

  # Linked-worktree fallback (the crewmate case: firstmate/treehouse run the pipeline in a
  # `git worktree` of the project). A `.env` is gitignored, so it stays behind in the primary
  # checkout — without this, every gate in a worktree fails closed on a missing key. Root-local
  # values still win: env_file.py never overwrites what the first eval already exported.
  if [ ! -f "$__avenger_env_root/.env" ] && command -v git >/dev/null 2>&1; then
    __avenger_env_common="$(git -C "$__avenger_env_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
    case "$__avenger_env_common" in
      */.git) __avenger_env_primary="${__avenger_env_common%/.git}" ;;
      *)      __avenger_env_primary="" ;;   # bare gate repos and odd layouts: no fallback
    esac
    if [ -n "$__avenger_env_primary" ] && [ "$__avenger_env_primary" != "$__avenger_env_root" ] \
        && [ -f "$__avenger_env_primary/.env" ]; then
      eval "$(python3 "$__avenger_env_dir/env_file.py" --export --root "$__avenger_env_primary" 2>/dev/null)"
    fi
    unset __avenger_env_common __avenger_env_primary
  fi
fi

unset __avenger_env_root __avenger_env_dir
