#!/usr/bin/env bash
# Vendor the pipeline into a target repo for a runtime that can't use the Claude Code plugin.
# Usage: scripts/vendor_runtime.sh <target-dir> <claude|opencode|copilot|all>
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_IN="${1:-}"; RUNTIME="${2:-}"
[ -n "$TARGET_IN" ] && [ -n "$RUNTIME" ] || {
  echo "usage: scripts/vendor_runtime.sh <target-dir> <claude|opencode|copilot|all>" >&2; exit 2; }
mkdir -p "$TARGET_IN"; TARGET="$(cd "$TARGET_IN" && pwd)"

need() { [ -e "$SRC/$1" ] || { echo "missing $1 — run python3 scripts/sync_$2.py first" >&2; exit 1; }; }
vendor_skills() { rm -rf "$TARGET/agentic_development/skills"; cp -rL "$SRC/skills" "$TARGET/agentic_development/skills"; }

# --- git floor (every runtime) ---
mkdir -p "$TARGET/agentic_development/scripts" "$TARGET/agentic_development/prompts" "$TARGET/docs/features" "$TARGET/.github/workflows"
cp "$SRC/scripts/gate_runner.py" "$SRC/scripts/gate_ci.sh" "$TARGET/agentic_development/scripts/"
cp -L "$SRC"/prompts/*.md "$TARGET/agentic_development/prompts/"
[ -f "$SRC/.pre-commit-config.yaml" ] && cp "$SRC/.pre-commit-config.yaml" "$TARGET/"
[ -f "$SRC/.github/workflows/pipeline-gates.yml" ] && cp "$SRC/.github/workflows/pipeline-gates.yml" "$TARGET/.github/workflows/"
echo "+ git floor (gate_runner, gate_ci, prompts, pre-commit, CI)"

case "$RUNTIME" in opencode|all)
  need ".opencode/agents" opencode
  vendor_skills
  mkdir -p "$TARGET/.opencode"
  cp -rL "$SRC/.opencode/agents" "$TARGET/.opencode/agents"
  cp "$SRC/AGENTS.md" "$TARGET/AGENTS.md"
  [ -f "$SRC/opencode.json" ] && cp "$SRC/opencode.json" "$TARGET/opencode.json"
  ln -sfn ../agentic_development/skills "$TARGET/.opencode/skills"
  echo "+ opencode adapter (.opencode/agents, AGENTS.md, skills)"
;; esac

case "$RUNTIME" in copilot|all)
  need ".github/agents" copilot
  vendor_skills
  mkdir -p "$TARGET/.github"
  cp -rL "$SRC/.github/agents" "$TARGET/.github/agents"
  [ -d "$SRC/.github/prompts" ] && cp -rL "$SRC/.github/prompts" "$TARGET/.github/prompts"
  [ -f "$SRC/.github/copilot-instructions.md" ] && cp "$SRC/.github/copilot-instructions.md" "$TARGET/.github/copilot-instructions.md"
  ln -sfn ../agentic_development/skills "$TARGET/.github/skills"
  echo "+ copilot adapter (.github/agents, prompts, copilot-instructions, skills)"
;; esac

case "$RUNTIME" in claude)
  echo "claude: install the plugin instead (/plugin install plan-build-verify@erik-tools);"
  echo "        only the git floor + docs/features were vendored."
;; esac

echo "done -> $TARGET"
echo "next: cd '$TARGET' && pip install pre-commit && pre-commit install ; add OPENROUTER_API_KEY (CI secret)."
