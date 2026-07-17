#!/usr/bin/env bash
# install.sh — vendor agentic-avengers into a target repo, WITH update detection.
# Goes at scripts/install.sh in the avengers repo. Claude Code updates natively via /plugin;
# this covers the opencode + git-floor + scripts surface.
#
#   scripts/install.sh <target-repo> [--check] [--prune]
#     --check   report what WOULD change; write nothing
#     --prune   also remove files upstream deleted (only if you haven't modified them)
#
# 3-way detection per file (source vs last-installed manifest vs current target):
#   NEW    not in target                         -> add
#   UPDATE target == manifest, source changed    -> overwrite
#   SAME   target == manifest == source          -> skip
#   DRIFT  target != manifest (you edited it)     -> SKIP + warn (never clobbered)
#   GONE   in manifest, no longer in source       -> report (--prune removes if unmodified)
#
# Portable to macOS /bin/bash 3.2 (no associative arrays).
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"; shift || true
CHECK=0; PRUNE=0
for a in "$@"; do case "$a" in --check) CHECK=1;; --prune) PRUNE=1;; esac; done
[ -n "$TARGET" ] && [ -d "$TARGET/.git" ] || { echo "usage: scripts/install.sh <target-repo> [--check] [--prune]"; exit 1; }

ver()     { grep -m1 '"version"' "$SRC/.claude-plugin/plugin.json" | sed 's/.*: *"//; s/".*//'; }
sha()     { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }
manisha() { awk -F'\t' -v p="$1" '$2==p{print $1}' "$MANI" 2>/dev/null; }

VERSION="$(ver)"
MANI_DIR="$TARGET/.avengers"; MANI="$MANI_DIR/manifest"
TMP="$(mktemp)"; SRCLIST="$(mktemp)"; trap 'rm -f "$TMP" "$SRCLIST"' EXIT

# The vendored surface (Claude Code plugin is handled separately via /plugin).
# scripts/hook_*.sh are the single implementation of the gates: Claude Code runs them via
# hooks/hooks.json, and .opencode/plugin/pipeline-gates.ts shells out to the SAME files rather than
# reimplementing them. They must be vendored or the opencode runtime has no gates at all.
# mutation_score.py (the mutation verdict) and spec_gate_cache.py (skip re-gating an unchanged spec)
# are called by those hooks and by gate_ci.sh.
SRC_SETS=".opencode hooks prompts scripts/gate_runner.py scripts/gate_ci.sh scripts/mutation_score.py scripts/spec_gate_cache.py scripts/bypass_log.sh scripts/codemap.py scripts/hook_fidelity.sh scripts/hook_spec_review.sh scripts/hook_verifier.sh scripts/hook_mutation.sh scripts/hook_artifact_check.sh cosmic-ray.toml .pre-commit-config.yaml .github/workflows/pipeline-gates.yml AGENTS.md"
: > "$SRCLIST"
for s in $SRC_SETS; do
  if [ -d "$SRC/$s" ]; then find "$SRC/$s" -type f ! -type l \
       -not -path '*/node_modules/*' -not -name '*.pyc' -not -path '*/__pycache__/*' \
       | sed "s#^$SRC/##" >> "$SRCLIST"
  elif [ -e "$SRC/$s" ]; then echo "$s" >> "$SRCLIST"; fi
done

new=0; upd=0; same=0; drift=0
while IFS= read -r p; do
  s_sha="$(sha "$SRC/$p")"; t="$TARGET/$p"
  if [ ! -e "$t" ]; then
    echo "  NEW    $p"; new=$((new+1))
    [ "$CHECK" -eq 0 ] && { mkdir -p "$(dirname "$t")"; cp "$SRC/$p" "$t"; }
    printf '%s\t%s\n' "$s_sha" "$p" >> "$TMP"
  else
    t_sha="$(sha "$t")"; m_sha="$(manisha "$p")"
    if [ -n "$m_sha" ] && [ "$t_sha" != "$m_sha" ]; then
      echo "  DRIFT  $p   (locally modified — skipped)"; drift=$((drift+1)); printf '%s\t%s\n' "$m_sha" "$p" >> "$TMP"
    elif [ "$s_sha" = "$t_sha" ]; then
      same=$((same+1)); printf '%s\t%s\n' "$s_sha" "$p" >> "$TMP"
    else
      echo "  UPDATE $p"; upd=$((upd+1)); [ "$CHECK" -eq 0 ] && cp "$SRC/$p" "$t"; printf '%s\t%s\n' "$s_sha" "$p" >> "$TMP"
    fi
  fi
done < "$SRCLIST"

# upstream-removed: manifest paths no longer in source
if [ -f "$MANI" ]; then
  while IFS="$(printf '\t')" read -r m_sha p; do
    if ! grep -qxF "$p" "$SRCLIST"; then
      if [ "$PRUNE" -eq 1 ] && [ -e "$TARGET/$p" ] && [ "$(sha "$TARGET/$p")" = "$m_sha" ]; then
        echo "  PRUNE  $p"; [ "$CHECK" -eq 0 ] && rm -f "$TARGET/$p"
      else
        echo "  GONE   $p   (upstream removed; keep, or re-run with --prune)"; printf '%s\t%s\n' "$m_sha" "$p" >> "$TMP"
      fi
    fi
  done < "$MANI"
fi

echo
echo "avengers $VERSION -> $TARGET : new=$new update=$upd same=$same drift=$drift"
if [ "$CHECK" -eq 1 ]; then echo "(--check: nothing written)"; exit 0; fi
mkdir -p "$MANI_DIR"; sort -u "$TMP" > "$MANI"; echo "$VERSION" > "$MANI_DIR/version"
echo "Manifest updated → $MANI_DIR/version ($VERSION). Drifted files left untouched — reconcile by hand."
