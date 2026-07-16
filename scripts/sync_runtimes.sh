#!/usr/bin/env bash
# Regenerate the opencode adapter from the canonical agents/, skills/, commands/.
# Run from the repo root after editing any canonical source.
# (Claude Code consumes the canonical sources natively — nothing to generate for it.)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "→ opencode adapter"
python3 scripts/sync_opencode.py

echo "✓ opencode adapter regenerated from agents/ + skills/ + commands/."
echo "  Note: AGENTS.md is hand-maintained from skills/pipeline-conventions/SKILL.md —"
echo "  update it if the rules themselves change."
