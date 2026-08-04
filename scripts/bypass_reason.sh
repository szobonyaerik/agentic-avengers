#!/usr/bin/env bash
# One owner of the break-glass reason's on-disk shape.
#
# `gate-overrides.log` is the accountability record for the only sanctioned way to override a gate,
# and it is a one-record-per-line tab-separated log. The reason is author-written prose that arrives
# from a file (`GATE_BYPASS="$(cat <file>)" …`, see skills/pipeline-conventions), so it can carry
# newlines and tabs — either of which would split one override into two records, the second with no
# timestamp, no author and no gate. A record that its own reason text can split or forge is not an
# audit trail.
#
# Collapse, never truncate: the reason is meant to be read later, so every word survives; only the
# record separators are neutralised. Sourced by every writer of that log — bash 3.2 compatible.

# Echo the reason with newlines/tabs/carriage returns collapsed to single spaces.
bypass_reason_oneline() {
  local reason="${1-}"
  reason="${reason//[$'\n\r\t']/ }"
  printf '%s' "$reason"
}
