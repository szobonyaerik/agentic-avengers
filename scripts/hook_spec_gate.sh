#!/usr/bin/env bash
# PostToolUse: THE spec gate. One gate, fired once, on a spec.md write.
#
# It replaces scripts/hook_fidelity.sh and scripts/hook_spec_review.sh, which ran two model rubrics
# over the same document at the same moment and asked overlapping questions of it. In one measured
# phase a spec passed one and failed the other on BYTE-IDENTICAL TEXT.
#
# Four stages, in this order, and the order is the design:
#
#   1. MECHANICAL, no model — undeclared subprocess spawners in tests/ (scripts/subprocess_check.py).
#      The pipeline's only cost gate: every reading stage judges correctness, and an expensive test
#      is not incorrect. DIFF-SCOPED (scripts/applicability.py): repository-wide it refused every
#      spec write of one measured phase over 17 spawners in locked phases nobody had opened.
#   2. MECHANICAL, no model — the requirement cap (scripts/requirement_cap.py). Over the cap the
#      spec SPLITS. This runs BEFORE any model sees the document, because a rejection for size is
#      one more thing for a spec to grow around: the two gates it replaced had no size ceiling, no
#      requirement cap and no cost dimension anywhere, so the only answer a rejected spec had was
#      more text — 25k -> 51k characters across four rejected rounds, measured.
#   3. OBSERVE (model) — prompts/spec-gate-observe.md reports everything it notices, with NO verdict
#      to give. It literally cannot block: it answers with `observations`, not `verdict`. It is given
#      a `## CONTEXT (reference only)` block (scripts/spec_gate_context.py) carrying the binding
#      contracts a spec can CONTRADICT, since that is one of the four things that block and it is
#      undetectable without them.
#   4. TRIAGE (model, cheaper) + DECIDE (script) — prompts/spec-gate-triage.md classifies each
#      observation against the CLOSED blocking set, and scripts/spec_gate_triage.py turns those
#      classifications into the verdict deterministically. **No model decides whether a spec is
#      blocked.** Four things block; everything else is a note, and notes never block — they land in
#      spec-notes.md, which the implementer reads once.
#
# Everything from the two hooks it replaces that was load-bearing is kept verbatim in behaviour:
# fail-closed on any non-verdict, the killed-hook trap, the body-hash skip, the diff-scoped re-gate
# bundle, break-glass honoured on a replayed rejection, and the runner identity guard.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (gate_runner, prompts, …)
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
. "$SD/gate_runner_guard.sh"

# A hook the harness kills leaves no verdict, no report and no cause — and the run reads that absence
# as the gate having objected. Say so instead. This is the failure mode that made a 120s-vs-300s
# timeout inversion look like a model that could not handle large specs.
trap 'echo "spec-gate: HOOK KILLED by the harness (signal) — this is NOT a gate verdict. The gate did not answer." >&2; exit 2' TERM INT

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$FILE" in
  */spec.md) ;;
  *) exit 0 ;;
esac
# Claude Code sends an absolute path; the opencode adapter can hand us a project-relative one.
cd "$CLAUDE_PROJECT_DIR" || exit 0

# --- 0. Measurement (never a gate) --------------------------------------------------------------
# A spec write is the earliest moment a phase is observable, so it is where the phase record opens.
# Deliberately BEFORE the mechanical checks below: an over-cap spec is this gate's cheapest possible
# rejection, and it is the one case that would otherwise go unmeasured entirely.
#
# Every call here fails open by construction (scripts/metrics_sink.py) and is `|| true` besides: a
# phase must never stop because a number went unrecorded. AVENGER_METRICS_SPEC_PATH is exported for
# BOTH gate calls below, because this gate's target is very often the diff bundle in a temp file,
# from which no spec and no phase can be derived.
export AVENGER_METRICS_SPEC_PATH="$FILE"
python3 "$SD/pipeline_metrics.py" phase-open "$FILE" >/dev/null 2>&1 || true

# --- 1. Mechanical: undeclared subprocess spawners (no model, always) ----------------------------
# No path argument: the checker resolves its own root from $SUBPROC_CHECK_PATHS, falling back to
# `tests`, and scopes enforcement to what this change touched — an argument here would ask it to
# enforce that path whole, which is the repository-wide behaviour that held a phase hostage to 17
# spawners in locked tests. Its output is echoed even on success — the "no such test root" note and
# the scope line are exactly the cases that must not be silent.
SUBPROC=$(python3 "$SD/subprocess_check.py" 2>&1); subproc_rc=$?
[ -n "$SUBPROC" ] && echo "$SUBPROC" >&2
if [ "$subproc_rc" -ne 0 ]; then
  if [ -n "${GATE_BYPASS:-}" ]; then
    # Log and FALL THROUGH to the rest of the gate. `exec` would end the hook at exit 0 and the
    # model half would never run. GATE_BYPASS is one unscoped variable: setting it here also waives
    # a later block in the same run. That is audited, not silent — each override writes its own
    # record to gate-overrides.log. This is the one caller that does NOT hand off with `exec`, so
    # it is also the one that has to carry the writer's refusal itself: an unwritable log here would
    # otherwise fall through into the rest of the gate as an override nobody recorded.
    "$SD/bypass_log.sh" "spec-gate-subprocess" || exit 2
  elif [ "$subproc_rc" -eq 2 ]; then
    echo "spec-gate: the subprocess check could not read a file under the tests root (named above)" >&2
    echo "  — a file it cannot read is a file it cannot clear, so this fails closed." >&2
    echo "  Fix the syntax or encoding error, then write the spec again." >&2
    exit 2
  else
    echo "spec-gate: undeclared subprocess spawners in tests/ — mark each one" >&2
    echo "  @pytest.mark.subprocess(\"<why a real process is required>\") or drive it in-process." >&2
    echo "  route_back: the implementer that owns those tests." >&2
    exit 2
  fi
fi

# --- 2. Mechanical: the requirement cap, a SPLIT trigger (no model, always) ----------------------
# Deliberately before the model call, and deliberately not a gate verdict. The gate rubrics are told
# never to reject a spec for being large; size is decided here, and the remedy is a split.
python3 "$SD/requirement_cap.py" "$FILE"; cap_rc=$?
if [ "$cap_rc" -ne 0 ]; then
  if [ -n "${GATE_BYPASS:-}" ]; then
    exec "$SD/bypass_log.sh" "spec-gate-requirement-cap"
  elif [ "$cap_rc" -eq 2 ]; then
    # Exit 1 is OVER the cap; exit 2 is "the count could not be decided" — an unreadable file, an
    # unparseable ceiling, or a requirement layout this check cannot see. Collapsing the two sent the
    # writer to SPLIT a document nobody could count, which is the same distinction the subprocess
    # branch above already draws: a file it cannot read is a file it cannot clear.
    echo "spec-gate: the requirement cap could not count this spec (cause named above)." >&2
    echo "  This is NOT a split trigger and no model has judged it — the count itself failed, so" >&2
    echo "  this fails closed. Fix what it named, then write the spec again." >&2
    exit 2
  else
    echo "  route_back: avenger-spec-writer, to SPLIT this spec — not to shorten it." >&2
    exit 2
  fi
fi

# --- 3. Preconditions for the model half --------------------------------------------------------
# The hook must be able to outlive the calls it wraps (it makes TWO), and the runner must be the
# shipped one rather than whatever sits at that path. Both fail closed.
if [ -f "$SD/../hooks/hooks.json" ]; then
  python3 "$SD/gate_timeouts.py" verify "$SD/../hooks/hooks.json" || exit 2
fi
require_gate_runner "$SD/gate_runner.py" || exit 2

# Skip when the spec's BODY is unchanged since this gate judged it. A frontmatter-only edit (the
# implementer stamping `status: done`, a stamp being written) must not re-run a paid gate — and must
# not re-roll a fresh nondeterministic verdict over an already-approved spec. Exit 1 = unchanged,
# with the stored verdict on stdout: an unchanged body that was BLOCKED replays its block rather
# than sliding through.
CACHED=$(python3 "$SD/spec_gate_cache.py" check "$FILE" gate); cached=$?
if [ "$cached" -eq 1 ]; then
  case "$CACHED" in
    GO|REVIEW|APPROVED) exit 0 ;;
    *)
      # A replayed block stops the turn exactly like a fresh one, so it honours break-glass exactly
      # like a fresh one. Without this an override was a ONE-SHOT. `exec` is safe here: no tempfile
      # exists yet, and below this point the EXIT trap owns them.
      [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "spec-gate"
      echo "spec-gate: $CACHED (unchanged since it was judged) — route back to avenger-spec-writer" >&2
      python3 "$SD/spec_gate_cache.py" report "$FILE" gate >&2 ||
        echo "  (the report for that verdict is no longer in the gate cache — edit the spec to re-gate)" >&2
      exit 2 ;;
  esac
fi

# The round is NOT measured here. It used to be — the body reached this line, so it was counted
# before either paid call — and that made a gate that never answered indistinguishable from one that
# did: a provider refusing for billing still counted as a round. A round is one COMPLETED gate
# evaluation (scripts/pipeline_metrics.py, `record_spec_round`, which owns the definition and
# refuses a call that names no verdict), so it is recorded in the two branches below that HAVE one.
# The gate calls between here and there still report the round in flight: `_spec_round` compares the
# body on disk against the one last counted, so a call belongs to `closed + 1` until its verdict
# lands.

# Diff-scoped re-gate: only for a spec that was approved AND has reached the implementer. A spec
# still in draft has no settled text to protect, so it is always gated whole.
TARGET="$FILE"
BUNDLE=""
if [ "$(python3 "$SD/spec_gate_state.py" status "$FILE" 2>/dev/null)" = "approved" ] &&
   grep -qE '^status:[[:space:]]*(done|in-progress)[[:space:]]*$' "$FILE" 2>/dev/null &&
   PREV=$(python3 "$SD/spec_gate_cache.py" previous "$FILE" gate 2>/dev/null); then
  BUNDLE="$(mktemp "${TMPDIR:-/tmp}/spec-gate-bundle.XXXXXX")"
  PREV_FILE="$(mktemp "${TMPDIR:-/tmp}/spec-gate-prev.XXXXXX")"
  NOW_FILE="$(mktemp "${TMPDIR:-/tmp}/spec-gate-now.XXXXXX")"
  # Body against body, never body against whole file: frontmatter carries the gate's own stamps, so
  # diffing it in would report `gate_gated_hash` as a change and hand the reader noise to judge.
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
    rm -f "$BUNDLE"; BUNDLE=""   # unreadable body -> gate the whole spec, the safe direction
  fi
  rm -f "$PREV_FILE" "$NOW_FILE"
fi

OBS="$(mktemp "${TMPDIR:-/tmp}/spec-gate-observations.XXXXXX")"
CLS="$(mktemp "${TMPDIR:-/tmp}/spec-gate-classifications.XXXXXX")"
TRIAGE_IN="$(mktemp "${TMPDIR:-/tmp}/spec-gate-triage-in.XXXXXX")"
DECISION="$(mktemp "${TMPDIR:-/tmp}/spec-gate-decision.XXXXXX")"
GERR="$(mktemp "${TMPDIR:-/tmp}/spec-gate-stderr.XXXXXX")"
REPORT="$(mktemp "${TMPDIR:-/tmp}/spec-gate-report.XXXXXX")"
CONTEXT="$(mktemp "${TMPDIR:-/tmp}/spec-gate-context.XXXXXX")"
OBSERVE_IN="$(mktemp "${TMPDIR:-/tmp}/spec-gate-observe-in.XXXXXX")"
REPORT_CTX="$(mktemp "${TMPDIR:-/tmp}/spec-gate-report-ctx.XXXXXX")"
cleanup () {
  rm -f "$OBS" "$CLS" "$TRIAGE_IN" "$DECISION" "$GERR" "$REPORT" "$CONTEXT" "$OBSERVE_IN" \
        "$REPORT_CTX" ${BUNDLE:+"$BUNDLE"}
}
trap cleanup EXIT

# The CONTEXT the closed blocking set depends on. `contradiction` is defined as a statement that
# cannot hold beside another in this spec OR one that breaks a binding contract the overview or the
# prior phase's card declares — and that second half was unobservable, because nothing assembled
# those two documents. A closed set with an undetectable member is three items and a claim.
#
# Exactly the extents scripts/doc_read_path.py declares for this reader and no more: the overview's
# `## Contracts and Decisions` section, and the IMMEDIATELY prior phase's contract card. Never the
# whole overview, never every prior phase, never handover-archive.md.
#
# Reference only, and NEVER a gate: absent context is normal (phase 1 has no prior card), so it is
# omitted and named on stderr rather than failing anything. It composes with the re-gate bundle
# rather than replacing it — `## CONTEXT` sits ahead of the markers the observe prompt already reads.
#
# Exit 3 is a THIRD state, not a failure: contradiction can only ever be checked against the prior
# phase's card, never this feature's own contracts. That used to be one line among many on stderr —
# easy to miss for eleven phases running (issue #57). It still never fails this gate, but it is no
# longer discarded with `|| :`: it is echoed loudly here AND folded into the persisted report below,
# so it shows up wherever this gate's verdict is read, not only in a log nobody was watching.
#
# THREE shapes exit 3 — a missing or unreadable overview.md, a missing heading, and a heading holding
# only boilerplate — and each has its own remedy, so the cause is not re-authored on that path (the
# builder-failure branch below is a different fact and does author its own). The builder
# already names the one that fired, on stderr, in its own words; this lifts that line verbatim out of
# $GERR (which later gate calls overwrite, so it is captured now) and carries it into both the echo
# and the persisted banner. A stamped report naming a cause that did not fire prescribes a fix
# already applied, which is a worse record than no banner at all.
python3 "$SD/spec_gate_context.py" "$FILE" > "$CONTEXT" 2>"$GERR"; ctx_rc=$?
cat "$GERR" >&2
CONTEXT_DEGRADED=0
CONTEXT_CAUSE=""
if [ "$ctx_rc" -eq 3 ]; then
  CONTEXT_DEGRADED=1
  CONTEXT_CAUSE="$(sed -n 's/^[[:space:]]*spec-gate context: \(DEGRADED:.*\)$/\1/p' "$GERR")"
  if [ -z "$CONTEXT_CAUSE" ]; then
    CONTEXT_CAUSE="DEGRADED: the context builder reported a degraded state without naming which \
shape — see its stderr above."
  fi
  printf 'spec-gate: CONTEXT %s\n' "$CONTEXT_CAUSE" >&2
  echo "  See docs/templates/overview.template.md, or run" >&2
  echo "  'python3 $SD/spec_gate_context.py check --all' to find every overview like this one." >&2
elif [ "$ctx_rc" -ne 0 ]; then
  # A builder that could not run at all carries the same consequence as one that ran and reported a
  # degraded context: `contradiction` is checked against less than the closed set says it is. So it
  # is recorded the same way — banner folded into the persisted report — rather than printed to a
  # stderr nothing that reads the verdict later will ever see. Its cause line says which of the two
  # it was, because the remedies do not overlap: a degraded overview is fixed in the overview, and
  # this is a defect in the builder or its inputs.
  CONTEXT_DEGRADED=1
  CONTEXT_CAUSE="UNAVAILABLE: the context builder exited $ctx_rc without producing a block — this \
is a builder failure, not a degraded overview. See the hook's stderr for its cause."
  echo "spec-gate: the context block could not be built (exit $ctx_rc, cause named above) —" >&2
  echo "  treating it as absent. This never fails the gate on its own." >&2
fi
if [ -s "$CONTEXT" ]; then
  {
    cat "$CONTEXT"
    echo
    if [ -n "$BUNDLE" ]; then
      cat "$BUNDLE"
    else
      # The marker is required, not decorative: the observe prompt reads the WHOLE input as the spec
      # when no marker is present, which would hand it the context to review as if it were the spec.
      echo "## SPEC UNDER REVIEW"
      cat "$FILE"
    fi
  } > "$OBSERVE_IN"
  TARGET="$OBSERVE_IN"
fi
# `exec` replaces this shell, so the EXIT trap would never run — clean up first, by hand.
bypass_and_exit () { cleanup; trap - EXIT; exec "$SD/bypass_log.sh" "spec-gate"; }

# One place that runs a pass in the background and waits, so a kill is reported at once AND the gate
# child goes down with the hook. bash defers a trap until the running foreground command returns, so
# a foreground call would report the kill only after the call it was killed for had finished.
#
# The kill trap also RECORDS the kill, per pass, under that pass's own stage name. The runner being
# killed cannot record its own death, and telling a killed hook apart from a model that answered
# NO-GO is what a 120s hook around a 300s call once cost a day of reading as a model size ceiling.
# This gate makes two calls, so "which one was killed" is exactly the fact worth keeping.
#
#   run_pass <rubric> <model> <target> <json-key> <emit-json> <metrics-stage>
#
# --author-family keeps its `:-anthropic` fallback ON PURPOSE. Emptying it is not a way to waive the
# cross-family assertion: an empty author family makes `assert_cross_family` return without
# comparing anything, which drops the invariant SILENTLY and stamps a same-family verdict that reads
# like an independent one. The waiver below is the explicit route, and it never touches who the
# author is.

# The marker is read OUT OF THE RUNNER, never spelled again here: two copies of the one string that
# makes a waived verdict legible would drift, and the copy that drifted first would be the one
# nobody was reading. An unreadable marker with a waiver asked for is fatal — the waiver would then
# be honoured by the runner and recorded by nobody, which is the silent same-family verdict this
# whole path exists to refuse. With no waiver asked for there is nothing to detect and nothing to
# fail.
SAME_FAMILY_MARKER="$(sed -n 's/^SAME_FAMILY_MARKER = "\(.*\)"$/\1/p' "$SD/gate_runner.py" 2>/dev/null)"
if [ -z "$SAME_FAMILY_MARKER" ] && [ -n "${GATE_SAME_FAMILY_WAIVER:-}" ]; then
  echo "spec-gate: GATE_SAME_FAMILY_WAIVER is set, but this hook could not read the gate runner's" >&2
  echo "  same-family marker, so a waived verdict could not be recorded as waived. Fails closed:" >&2
  echo "  an undisclosed same-family verdict is worse than no verdict." >&2
  exit 2
fi

# Passed as an ARRAY, never as a `${VAR:+--flag "$VAR"}` expansion: that idiom word-splits, and this
# value is a prose reason with spaces in it — the flag would take its first word and the rest would
# reach argparse as stray arguments, failing the call rather than waiving anything.
SAME_FAMILY_ARGS=()
[ -n "${GATE_SAME_FAMILY_WAIVER:-}" ] &&
  SAME_FAMILY_ARGS=(--same-family-waiver "$GATE_SAME_FAMILY_WAIVER")

SAME_FAMILY_WAIVER_LINE=""
note_same_family_waiver () {
  local line
  [ -n "$SAME_FAMILY_MARKER" ] || return 0
  line="$(grep -m1 "^$SAME_FAMILY_MARKER:" "$GERR" 2>/dev/null)" || return 0
  [ -n "$line" ] || return 0
  [ -n "$SAME_FAMILY_WAIVER_LINE" ] && return 0   # one audit record per hook run, not per pass
  SAME_FAMILY_WAIVER_LINE="$line"
  # Audited or not recorded (CLAUDE.md 3a). bypass_log.sh exits 2 when it could not append, and an
  # override nobody logged is not an override — so the gate stops rather than judging on the
  # author's own family with no durable trace of the waiver anywhere.
  if ! GATE_BYPASS="${GATE_SAME_FAMILY_WAIVER:-}" "$SD/bypass_log.sh" "spec-gate" "cross-family"; then
    echo "spec-gate: the same-family waiver could not be audited, so it does not hold." >&2
    echo "  Fix the log destination named above, then write the spec again." >&2
    exit 2
  fi
}

# What produced the verdict. Phase 13's first spec gate ran on `anthropic/claude-3-haiku` over the
# OpenRouter transport, and that fact survived ONLY because the worker typed it into a status line —
# so ruling afterwards on whether that gate stood meant trusting prose. The runner announces the
# model, its family and the transport for every REACHED verdict; this collects one entry per pass
# and `spec_gate_cache.py stamp` writes them onto the verdict itself, on approvals and blocks alike.
#
# The marker is read OUT OF THE RUNNER for the same reason the same-family one is: two copies of the
# string drift, and the drifted copy is the one nobody is reading. A marker this cannot read, or a
# pass that announced none, leaves the attribution EMPTY — which the stamp records as `unrecorded`,
# a named state rather than an absent key. That is deliberately not fail-closed: no attribution is
# the honest record of a runner that said nothing, whereas an undisclosed same-family verdict is a
# false claim of independence.
ATTRIBUTION_MARKER="$(sed -n 's/^ATTRIBUTION_MARKER = "\(.*\)"$/\1/p' "$SD/gate_runner.py" 2>/dev/null)"
GATE_ATTRIBUTION=""
note_attribution () {   # $1 = the pass's metrics stage, e.g. spec-gate-observe
  local line model provider label
  [ -n "$ATTRIBUTION_MARKER" ] || return 0
  line="$(grep -m1 "^$ATTRIBUTION_MARKER:" "$GERR" 2>/dev/null)" || return 0
  [ -n "$line" ] || return 0
  model="$(printf '%s' "$line" | sed -n 's/.*[[:space:]]model=\([^[:space:]]*\).*/\1/p')"
  provider="$(printf '%s' "$line" | sed -n 's/.*[[:space:]]provider=\([^[:space:]]*\).*/\1/p')"
  [ -n "$model" ] || return 0
  label="${1#spec-gate-}"
  GATE_ATTRIBUTION="${GATE_ATTRIBUTION:+$GATE_ATTRIBUTION; }${label}=${model}@${provider:-unknown}"
}

run_pass () {
  python3 "$SD/gate_runner.py" \
    --rubric "$1" --model "$2" --author-family "${AUTHOR_FAMILY:-anthropic}" \
    ${SAME_FAMILY_ARGS[@]+"${SAME_FAMILY_ARGS[@]}"} \
    ${GATE_PROVIDER:+--provider "$GATE_PROVIDER"} \
    --json-key "$4" --emit-json "$5" --target "$3" >/dev/null 2>"$GERR" &
  local pid=$!
  trap 'kill -TERM "'"$pid"'" 2>/dev/null; python3 "$SD/pipeline_metrics.py" gate-killed --stage "'"$6"'" --spec-path "$FILE" >/dev/null 2>&1 || true; cat "$GERR" >&2; echo "spec-gate: HOOK KILLED by the harness (signal) while the gate was still running — this is NOT a gate verdict. The gate did not answer; the call was terminated." >&2; exit 2' TERM INT
  wait "$pid"; local rc=$?
  trap 'echo "spec-gate: HOOK KILLED by the harness (signal) — this is NOT a gate verdict." >&2; exit 2' TERM INT
  cat "$GERR" >&2
  # Read from the runner's own marker rather than from the variable being set: the waiver only
  # APPLIES when the gate model really does share the author's family, and a waiver left in the
  # environment while the gate is cross-family must never stamp a verdict as same-family.
  note_same_family_waiver
  note_attribution "$6"
  return "$rc"
}

# --- 4. OBSERVE — report everything, no verdict --------------------------------------------------
# The gate's model is resolved ONCE, here, and both passes are handed the result. Written twice it
# is two defaults for one decision, and a drift between them is two gate models where the operator
# chose one.
GATE_OBSERVE_MODEL="${GATE_MODEL:-google/gemini-3.1-pro-preview}"

if ! run_pass "$SD/../prompts/spec-gate-observe.md" \
              "$GATE_OBSERVE_MODEL" "$TARGET" observations "$OBS" \
              spec-gate-observe; then
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit
  echo "spec-gate: the observe pass did not answer (fail closed) — the cause is named above." >&2
  exit 2
fi

# --- 5. TRIAGE — classify against the closed set, then DECIDE in a script ------------------------
{
  echo "## OBSERVATIONS TO CLASSIFY"
  cat "$OBS"
  echo
  echo "## SPEC THEY WERE MADE AGAINST (reference only)"
  cat "$FILE"
} > "$TRIAGE_IN"

# GATE_TRIAGE_MODEL used to fall back to a bare `deepseek/deepseek-chat`, which resolves to
# OpenRouter regardless of GATE_PROVIDER — a third, unpinned provider the operator never
# configured. An unset triage model now defaults to GATE_MODEL, the observe pass's own model:
# the operator has already chosen it and already proven it reachable. This can never regress to
# reaching an unconfigured provider silently, and it costs an existing project nothing — a
# project that never set GATE_TRIAGE_MODEL keeps running, just on GATE_MODEL for both passes
# instead of on a model nobody chose (issue #48, evidence in #36).
if ! run_pass "$SD/../prompts/spec-gate-triage.md" \
              "${GATE_TRIAGE_MODEL:-$GATE_OBSERVE_MODEL}" "$TRIAGE_IN" \
              classifications "$CLS" spec-gate-triage; then
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit
  echo "spec-gate: the triage pass did not answer (fail closed) — the cause is named above." >&2
  exit 2
fi

python3 "$SD/spec_gate_triage.py" decide "$OBS" "$CLS" > "$DECISION" 2>"$GERR"; decided=$?
cat "$GERR" >&2
if [ "$decided" -gt 1 ]; then
  # An unknown category, an unclassified observation, a reply in a shape nobody can read. Never a
  # judgement call: guessing "blocking" reinstates the ratchet and guessing "note" deletes findings.
  [ -n "${GATE_BYPASS:-}" ] && bypass_and_exit
  echo "spec-gate: triage produced no usable classification (fail closed)." >&2
  exit 2
fi

# The known-open list. Notes are written whatever the verdict — an approved spec's notes are the
# whole point of a filter that does not block on them.
python3 "$SD/spec_notes.py" write "$FILE" "$DECISION" >&2 || true

# The report kept with the verdict, so a replayed block can say what it found. Rendered by the
# module that owns the decision shape, not inline here: the first version was a `python3 -c` whose
# shell quoting made it emit nothing, so a block arrived with an empty report — which is exactly the
# unexplained rejection this pass exists to remove.
python3 "$SD/spec_gate_triage.py" report "$DECISION" > "$REPORT" 2>/dev/null

# Fold the warnings that must outlive this hook's stderr INTO the persisted report. A verdict this
# gate stamps is read again later — on a replayed block, in a triage, by a human — and none of those
# reads see the hook's own stderr. This is what makes such a state "unmistakable" rather than merely
# printed once: neither of these is a gate failure, and neither gets to look like a clean pass.
#
# Two banners, each carrying the words of whatever produced it rather than a re-authored summary:
#   CONTEXT ... — the context builder's own cause line, so the durable record names the shape that
#                 actually fired rather than one of the three.
#   SAME-FAMILY WAIVER ... — the gate runner's own marker line, so a verdict produced without
#                 decorrelation can never be read afterwards as an independent judgement. This is
#                 the property that makes the waiver safe to have at all.
#
# The fold itself must not fail the way it is fixing. A full or read-only TMPDIR makes the write or
# the move fail, and a silent one leaves the persisted report indistinguishable from a clean pass —
# the exact state this block exists to remove. So it goes through a temp file the EXIT trap already
# owns, and a failure says so on stderr rather than being swallowed by `&&`.
BANNER=""
[ "$CONTEXT_DEGRADED" -eq 1 ] && BANNER="CONTEXT $CONTEXT_CAUSE"
if [ -n "$SAME_FAMILY_WAIVER_LINE" ]; then
  BANNER="${BANNER:+$BANNER
}SAME-FAMILY WAIVER: ${SAME_FAMILY_WAIVER_LINE#"$SAME_FAMILY_MARKER": }"
fi
if [ -n "$BANNER" ]; then
  if {
    printf '%s\n' "$BANNER"
    echo
    cat "$REPORT"
  } > "$REPORT_CTX" && mv "$REPORT_CTX" "$REPORT"; then
    :
  else
    echo "spec-gate: could not fold this run's banner into the persisted report" >&2
    echo "  (writing $REPORT_CTX or moving it over $REPORT failed — check TMPDIR). The stamped" >&2
    echo "  report will NOT carry it, so the hook's stderr above is the only record of it:" >&2
    printf '%s\n' "$BANNER" >&2
  fi
fi

if [ "$decided" -eq 0 ]; then
  # Approved. Stamp the machine gate through its ONE writer. The hook used to have two spellings —
  # a sed for the key-present case and an inline heredoc for the key-absent one — and only the
  # second could insert a missing key, so a BLOCKED spec whose frontmatter lacked `spec_gate:` was
  # left reading `pending`. In unattended mode the automated gate is the whole wall, so it also
  # carries the human sign-off. These are script edits: they do not re-trigger PostToolUse hooks.
  python3 "$SD/spec_gate_state.py" set "$FILE" approved
  if [ "${SPEC_REVIEW_MODE:-}" = "auto" ] && grep -q '^review_status:[[:space:]]*pending' "$FILE"; then
    sed -i.bak 's/^review_status:[[:space:]]*pending/review_status: approved/' "$FILE" && rm -f "$FILE.bak"
  fi
  python3 "$SD/spec_gate_cache.py" stamp "$FILE" gate APPROVED "$REPORT" "$GATE_ATTRIBUTION" \
    >/dev/null 2>&1
  # A completed evaluation, so it is a round. Measured here — its size and its requirement count —
  # because a spec that grew 25k -> 51k across rounds it was rewritten to satisfy a gate is the
  # ratchet, and nothing recorded it while it was happening. Idempotent by CONTENT, so a re-gate
  # over an unchanged body cannot double-count it.
  python3 "$SD/pipeline_metrics.py" spec-round "$FILE" --verdict approved >/dev/null 2>&1 || true
  echo "spec-gate: APPROVED ($FILE)" >&2
  exit 0
fi

# Blocked. The hash is recorded WITH the verdict on a block too: a stamp only on passes is a
# rejection with no record of which text was rejected and no reasoning to answer.
python3 "$SD/spec_gate_state.py" set "$FILE" blocked
python3 "$SD/spec_gate_cache.py" stamp "$FILE" gate BLOCKED "$REPORT" "$GATE_ATTRIBUTION" \
  >/dev/null 2>&1
# A block is a completed evaluation, so it is a round — and the ratchet this measures is built out
# of blocks. Recorded before the break-glass hand-off below, which ends this hook with `exec`.
python3 "$SD/pipeline_metrics.py" spec-round "$FILE" --verdict blocked >/dev/null 2>&1 || true
[ -n "${GATE_BYPASS:-}" ] && bypass_and_exit
echo "spec-gate: BLOCKED — route back to avenger-spec-writer" >&2
echo "--- blocking findings (the closed set: missing requirement, contradiction, untestable criterion, unhandled critical edge case) ---" >&2
cat "$REPORT" >&2
echo "--- end blocking findings ---" >&2
exit 2
