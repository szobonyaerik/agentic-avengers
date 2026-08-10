#!/usr/bin/env bash
# PostToolUse: when a spec.md is written/edited, run the fidelity gate and stamp fidelity_verdict.
# Plugin assets (gate_runner, prompts, bypass_log) resolve from THIS script's dir ($SD) — under the
# Claude Code plugin they live in the plugin cache, not the target project. Project paths
# ($CLAUDE_PROJECT_DIR) are used only for the spec being judged and the override log.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
. "$SD/gate_runner_guard.sh"

# A hook the harness kills leaves no verdict, no report and no cause — and the run reads that
# absence as the gate having objected. Say so instead. This is the failure mode that made a
# 120s-vs-300s timeout inversion look like a model that could not handle large specs.
trap 'echo "fidelity: HOOK KILLED by the harness (signal) — this is NOT a gate verdict. The gate did not answer." >&2; exit 2' TERM INT

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */spec.md) ;;
  *) exit 0 ;;
esac
# Resolve $FILE relative to the project like the sibling hooks do. Claude Code sends an absolute
# path, but the opencode adapter can hand us a project-relative one — without this, the gate would
# fail to open the spec and fail CLOSED, blocking the turn over a path bug.
cd "$CLAUDE_PROJECT_DIR" || exit 0

# The gate must be able to outlive the call it wraps. Checked against the shipped hooks.json when
# there is one; the opencode adapter has no hooks.json and is covered by the static assertion in
# tests/test_gate_timeouts.py instead.
if [ -f "$SD/../hooks/hooks.json" ]; then
  python3 "$SD/gate_timeouts.py" verify "$SD/../hooks/hooks.json" || exit 2
fi

# Refuse a runner that cannot identify itself: a scaffold that prints GO having checked nothing is
# indistinguishable from a real pass at every point downstream of here.
require_gate_runner "$SD/gate_runner.py" || exit 2

# Skip when the spec's BODY is unchanged since it was last gated. Frontmatter-only edits (the
# implementer stamping `status: done`, a verdict being written) must not re-run a paid gate — and
# must not re-roll a fresh nondeterministic verdict over an already-approved spec. Exit 1 =
# unchanged, and the stored verdict is on stdout: an unchanged body that was REJECTED replays its
# rejection rather than sliding through, which is what "skip" used to mean for every verdict.
CACHED=$(python3 "$SD/spec_gate_cache.py" check "$FILE" fidelity); cached=$?
if [ "$cached" -eq 1 ]; then
  case "$CACHED" in
    GO|REVIEW) exit 0 ;;
    *)
      echo "fidelity: $CACHED (unchanged since it was judged) — route back to avenger-spec-writer" >&2
      python3 "$SD/spec_gate_cache.py" report "$FILE" fidelity >&2 ||
        echo "  (the report for that verdict is no longer in the gate cache — edit the spec to re-gate)" >&2
      exit 2 ;;
  esac
fi

# --emit-json carries the gate's own `report` back in a form that cannot be lost: the previous code
# read the report off stderr through a process substitution, which the hook could outrun, so a
# rejection routinely arrived with no reasoning attached at all.
VJSON="$(mktemp "${TMPDIR:-/tmp}/fidelity-verdict.XXXXXX")"
GERR="$(mktemp "${TMPDIR:-/tmp}/fidelity-stderr.XXXXXX")"
REPORT="$(mktemp "${TMPDIR:-/tmp}/fidelity-report.XXXXXX")"
VOUT="$(mktemp "${TMPDIR:-/tmp}/fidelity-token.XXXXXX")"
cleanup () { rm -f "$VJSON" "$GERR" "$REPORT" "$VOUT"; }
trap cleanup EXIT
# `exec` replaces this shell, so the EXIT trap would never run — clean up first, by hand.
bypass_and_exit () { cleanup; trap - EXIT; exec "$SD/bypass_log.sh" "fidelity"; }

# Run the gate in the BACKGROUND and `wait` for it. bash defers a trap until the running foreground
# command returns, so a hook killed mid-call would report nothing until the call it was killed for
# had finished anyway — and the call would outlive the hook regardless. `wait` is interruptible, so
# the signal is reported at once AND the gate child is taken down with us.
python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/fidelity-rubric.md" \
  --model "${GATE_MODEL:-deepseek/deepseek-chat}" \
  --author-family "${AUTHOR_FAMILY:-anthropic}" \
  ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
  --emit-json "$VJSON" \
  --print-verdict --target "$FILE" >"$VOUT" 2>"$GERR" &
gate_pid=$!
trap 'kill -TERM "$gate_pid" 2>/dev/null; cat "$GERR" >&2; echo "fidelity: HOOK KILLED by the harness (signal) while the gate was still running — this is NOT a gate verdict. The gate did not answer; the call was terminated." >&2; exit 2' TERM INT
wait "$gate_pid"; rc=$?
trap 'echo "fidelity: HOOK KILLED by the harness (signal) — this is NOT a gate verdict." >&2; exit 2' TERM INT
VERDICT="$(cat "$VOUT")"
cat "$GERR" >&2

# Fail closed: missing key, unreachable model, same-family, timeout, or no verdict -> stop. The
# runner's stderr above names the cause; do not restate it here as a generic "errored".
if [ "$rc" -ne 0 ]; then
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit
  echo "fidelity gate did not reach a verdict (fail closed) — the cause is named above." >&2
  exit 2
fi

# Stamp the verdict into the spec frontmatter (shell edit — does not re-trigger PostToolUse hooks).
if grep -q '^fidelity_verdict:' "$FILE"; then
  sed -i.bak "s/^fidelity_verdict:.*/fidelity_verdict: $VERDICT/" "$FILE" && rm -f "$FILE.bak"
fi

# The judged body's hash is recorded WITH its verdict, on every verdict including NO-GO. Recording
# only passes is what left a rejection with no trace of which text was rejected.
python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("report") or "")
except Exception:
    pass' "$VJSON" > "$REPORT" 2>/dev/null
python3 "$SD/spec_gate_cache.py" stamp "$FILE" fidelity "$VERDICT" "$REPORT" >/dev/null 2>&1

case "$VERDICT" in
  GO|REVIEW)
    echo "fidelity: $VERDICT ($FILE)" >&2; exit 0 ;;
  *)  # NO-GO
    [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit
    echo "fidelity: NO-GO — route back to avenger-spec-writer" >&2
    echo "--- fidelity gate report ---" >&2
    cat "$REPORT" >&2
    echo "--- end fidelity gate report ---" >&2
    exit 2 ;;
esac
