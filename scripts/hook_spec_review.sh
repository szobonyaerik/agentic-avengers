#!/usr/bin/env bash
# PostToolUse: the spec-review gate, in two halves.
#
#   1. The MECHANICAL half runs in BOTH modes and calls no model: an AST walk over the project's
#      tests/ for undeclared subprocess spawners (scripts/subprocess_check.py; `SUBPROC_CHECK_PATHS`
#      points it elsewhere when the tests are not at `tests/`). It fires here rather than only in
#      `--auto` because HITL is the default, and a check that runs in the mode nobody uses is a
#      check that does not run. Per pipeline-conventions, mechanical gates belong in hooks.
#   2. The MODEL half is auto-only (SPEC_REVIEW_MODE=auto): on a spec.md write, run the cross-family
#      checklist gate; GO/REVIEW -> stamp review_status: approved in place; NO-GO -> stop (fail
#      closed, surfaces route-back). HITL runs that half interactively via /spec-review.
#
# A spec already approved AND implemented is re-gated on its CHANGES ONLY. Re-judging settled text
# is how one spec drew three verdicts from one model on one rubric, the third a NO-GO over lines the
# first two passed. When the previously judged body is unavailable the bundle is not built and the
# whole spec is gated — the safe direction.
set -uo pipefail

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, bypass_log)
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */spec.md) ;;
  *) exit 0 ;;
esac
cd "$CLAUDE_PROJECT_DIR" || exit 0

# --- 1. Mechanical half: no model, both modes ---------------------------------------------------
# Cost is invisible to every reading stage — spec review, fidelity, cross-family review and
# verification all read for correctness, and a subprocess is not incorrect. This is the one stage
# that can see it, and it sees it while the spec is still cheap to change.
# No path argument: the checker resolves its own root from $SUBPROC_CHECK_PATHS, falling back to
# `tests`, so a project whose tests live elsewhere has one place to say so. Its output is echoed
# even on success, because the "no such test root" note is exactly the case that must not be silent.
SUBPROC=$(python3 "$SD/subprocess_check.py" 2>&1); subproc_rc=$?
[ -n "$SUBPROC" ] && echo "$SUBPROC" >&2
if [ "$subproc_rc" -ne 0 ]; then
  # Break-glass here logs and FALLS THROUGH to the model half. `exec` would replace the shell and
  # end the hook at exit 0, so the cross-family review never ran at all. GATE_BYPASS is a single
  # unscoped variable: setting it for this half also waives a later cross-family NO-GO in the same
  # hook run. That is audited, not silent — each override writes its own record to
  # gate-overrides.log. Scoping break-glass per gate is deliberately out of scope here.
  if [ -n "${GATE_BYPASS:-}" ]; then
    "$SD/bypass_log.sh" "spec-review-subprocess"
  elif [ "$subproc_rc" -eq 2 ]; then
    # Fail closed, but say the true reason: a file the checker could not read has no spawner to
    # declare, and telling its author to add a marker sends them looking for one that isn't there.
    echo "spec-review: the subprocess check could not read a file under the tests root (named" >&2
    echo "  above) — a file it cannot read is a file it cannot clear, so this fails closed." >&2
    echo "  Fix the syntax or encoding error, then write the spec again." >&2
    exit 2
  else
    echo "spec-review: undeclared subprocess spawners in tests/ — mark each one" >&2
    echo "  @pytest.mark.subprocess(\"<why a real process is required>\") or drive it in-process." >&2
    echo "  route_back: the implementer that owns those tests." >&2
    exit 2
  fi
fi

[ "${SPEC_REVIEW_MODE:-}" = "auto" ] || exit 0   # --- HITL (default): the model half is /spec-review

# --- 2. Model half: auto only -------------------------------------------------------------------
# Skip when the spec's BODY is unchanged since this gate last judged it — a frontmatter-only edit
# (`status: done`, a verdict stamp) must not re-run a paid gate. Own key, so fidelity's stamp can't
# make this one skip. Exit 1 = unchanged; anything else falls through and gates.
python3 "$SD/spec_gate_cache.py" check "$FILE" review; cached=$?
[ "$cached" -eq 1 ] && exit 0

# Diff-scoped re-gate: only for a spec that was approved AND has reached the implementer. A spec
# still in draft has no settled text to protect, so it is always gated whole.
TARGET="$FILE"
BUNDLE=""
if grep -qE '^review_status:[[:space:]]*approved[[:space:]]*$' "$FILE" 2>/dev/null &&
   grep -qE '^status:[[:space:]]*(done|in-progress)[[:space:]]*$' "$FILE" 2>/dev/null &&
   PREV=$(python3 "$SD/spec_gate_cache.py" previous "$FILE" review 2>/dev/null); then
  BUNDLE="$(mktemp "${TMPDIR:-/tmp}/spec-review-bundle.XXXXXX")"
  PREV_FILE="$(mktemp "${TMPDIR:-/tmp}/spec-review-prev.XXXXXX")"
  NOW_FILE="$(mktemp "${TMPDIR:-/tmp}/spec-review-now.XXXXXX")"
  # Body against body, never body against whole file: frontmatter carries the gates' own stamps, so
  # diffing it in would report `review_gated_hash` as a change to the spec and hand the reviewer
  # noise to gate on — the precise failure this bundle exists to prevent.
  printf '%s\n' "$PREV" > "$PREV_FILE"
  if python3 "$SD/spec_gate_cache.py" body "$FILE" > "$NOW_FILE" 2>/dev/null; then
    {
      echo "## PREVIOUSLY APPROVED (reference only)"
      cat "$PREV_FILE"
      echo
      echo "## SPEC UNDER REVIEW"
      cat "$FILE"
      echo
      echo "## CHANGES SINCE APPROVAL"
      diff -u --label "approved" --label "current" "$PREV_FILE" "$NOW_FILE" || true
    } > "$BUNDLE"
    TARGET="$BUNDLE"
  else
    rm -f "$BUNDLE"
    BUNDLE=""   # unreadable body -> gate the whole spec, the safe direction
  fi
  rm -f "$PREV_FILE" "$NOW_FILE"
fi

VERDICT=$(python3 "$SD/gate_runner.py" \
  --rubric "$SD/../prompts/spec-review-rubric.md" \
  --model "${GATE_MODEL:-google/gemini-3.1-pro-preview}" --author-family "${AUTHOR_FAMILY:-anthropic}" \
  ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
  --print-verdict --target "$TARGET" 2> >(cat >&2))
rc=$?
[ -n "$BUNDLE" ] && rm -f "$BUNDLE"

# Fail closed: any error (missing key, same-family, no verdict) stops — do not approve.
if [ "$rc" -ne 0 ]; then
  [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "spec-review-auto"
  echo "spec-review (auto): gate errored (fail closed) — review_status left pending" >&2
  exit 2
fi

case "$VERDICT" in
  GO|REVIEW)
    # In-place stamp (shell edit — does not re-trigger PostToolUse hooks).
    if grep -q '^review_status:[[:space:]]*pending' "$FILE"; then
      sed -i.bak 's/^review_status:[[:space:]]*pending/review_status: approved/' "$FILE" && rm -f "$FILE.bak"
      echo "spec-review (auto): $VERDICT -> review_status: approved  ($FILE)" >&2
    fi
    # Record the body this verdict was reached against, so frontmatter-only edits don't re-gate and
    # the next re-gate can diff against exactly what was approved here.
    python3 "$SD/spec_gate_cache.py" stamp "$FILE" review >/dev/null 2>&1
    exit 0
    ;;
  *)  # NO-GO or anything unexpected
    [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "spec-review-auto"
    echo "spec-review (auto): NO-GO — review_status stays pending; route back to avenger-spec-writer" >&2
    exit 2
    ;;
esac
