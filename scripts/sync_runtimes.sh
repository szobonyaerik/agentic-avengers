#!/usr/bin/env bash
# Regenerate every runtime adapter from the canonical agents/, skills/, commands/.
# Run from the repo root after editing any canonical source.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "→ opencode adapter"
python3 scripts/sync_opencode.py

echo "→ copilot adapter"
python3 scripts/sync_copilot.py

echo "✓ adapters regenerated from agents/ + skills/ + commands/."
echo "  Note: AGENTS.md and .github/copilot-instructions.md are hand-maintained from"
echo "  skills/pipeline-conventions/SKILL.md — update them if the rules themselves change."
