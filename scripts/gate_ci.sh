#!/usr/bin/env bash
# Runtime-agnostic gate floor — the MECHANICAL gates, against the working tree.
#   default (pre-commit): staged-spec artifact checks + pytest                     (fast)
#   --full (CI):          all artifact checks + cross-family + pytest + mutation   (thorough)
#
# NO MODEL RUNS HERE. Model-based gates (the Verifier's triage, the grill-me
# spec review) run in-chat; this floor only checks their COMMITTED ARTIFACTS — `review_status:
# approved` on specs, a passing `verdict.json` per phase — plus the suite, the static cross-family
# assertion, and mutation if a team turned it on. See pipeline-conventions: "Where the models run".
# The spec gate is the one model gate in this repo and it runs as an in-session hook
# (scripts/hook_spec_gate.sh), never in CI.
#
# Break-glass: GATE_BYPASS="reason" overrides a FAILING gate; logged + visible, never silent.
# bash 3.2-compatible (macOS default).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/load_env.sh"   # pipeline config from the project .env (real env always wins)
. "$SCRIPT_DIR/bypass_reason.sh"   # one owner of the break-glass reason's on-disk shape
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"       # repo root (in-repo and vendored flat layout)
COSMIC_CFG="$ROOT/cosmic-ray.toml"
cd "$ROOT"

FULL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --full) FULL=1 ;;
    --provider) shift ;;   # accepted and ignored: no model runs in this floor any more
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
fail=0
failed_gates=""

record_fail() { fail=1; failed_gates="${failed_gates} $1"; }

# 1) Spec artifact checks — the committed output of the in-chat gates. No model call.
SPECS=()
if [ "$FULL" -eq 1 ]; then
  while IFS= read -r f; do [ -n "$f" ] && SPECS+=("$f"); done \
    < <(find docs/features -type f -name spec.md 2>/dev/null)
else
  while IFS= read -r f; do [ -n "$f" ] && SPECS+=("$f"); done \
    < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E 'docs/features/.*/spec\.md$' || true)
fi
for spec in "${SPECS[@]:-}"; do
  [ -n "$spec" ] || continue
  [ -f "$spec" ] || continue
  # A spec that has not reached the implementer yet is not a CI failure — only a spec claiming to be
  # built must carry its approvals.
  grep -qE '^status:[[:space:]]*(done|in-progress)[[:space:]]*$' "$spec" 2>/dev/null || continue
  echo "• spec artifacts: $spec"
  # The ONE machine gate, read through the one module that decides what its stamp means (including
  # how a legacy fidelity_verdict reads). Never re-derived here: this used to be a second copy of
  # that rule, and a second copy is the one that drifts.
  # `status` is bound to the BYTES (issue #97): an `approved` whose body no longer hashes to what
  # the gate recorded reads `stale`, so a spec edited after its approval cannot pass here on the
  # strength of a value written over other text. The reader's own notes (an unrecorded hash, a
  # stale body) are on stderr, and they are kept - a clean token with the note discarded is the
  # over-read result this check exists to refuse.
  GATE_STATE="$(python3 "$SCRIPT_DIR/spec_gate_state.py" status "$spec")"
  if [ "$GATE_STATE" = "stale" ]; then
    echo "  ✗ spec_gate is 'stale' — the body changed after the gate approved it; the approval is of bytes this spec no longer has. Re-gate it or record a disclosed exception." >&2
    record_fail "spec-gate:$spec"
  elif [ "$GATE_STATE" != "approved" ]; then
    echo "  ✗ spec_gate is '$GATE_STATE' — the spec gate never approved this spec." >&2
    record_fail "spec-gate:$spec"
  fi
  grep -qE '^review_status:[[:space:]]*approved[[:space:]]*$' "$spec" 2>/dev/null \
    || { echo "  ✗ review_status is not 'approved' — the human sign-off never happened." >&2
         record_fail "spec-review:$spec"; }
  # Size is decided mechanically and never by a gate verdict: over the cap the spec SPLITS. A
  # rejection for size is one more thing for a spec to grow around, which is how one spec reached
  # 51k characters across four rejected rounds.
  python3 "$SCRIPT_DIR/requirement_cap.py" "$spec" >/dev/null 2>&1 \
    || { python3 "$SCRIPT_DIR/requirement_cap.py" "$spec" >/dev/null
         record_fail "requirement-cap:$spec"; }
done

# 1b) Verdict artifacts — every phase with a handover must carry a passing Verifier verdict.
if [ "$FULL" -eq 1 ]; then
  while IFS= read -r ho; do
    [ -n "$ho" ] || continue
    vd="$(dirname "$ho")/verdict.json"
    echo "• verdict: $vd"
    if [ ! -f "$vd" ]; then
      echo "  ✗ no verdict.json — the phase closed without an independent Verifier run." >&2
      record_fail "verifier:no-verdict"; continue
    fi
    v="$(jq -r '.verdict // "missing"' "$vd" 2>/dev/null || echo unparseable)"
    if [ "$v" != "pass" ]; then
      echo "  ✗ verdict is '$v'." >&2; record_fail "verifier:$v"
    else
      if [ "$(jq -r '.bypassed // false' "$vd" 2>/dev/null)" = "true" ]; then
        echo "  ⚠ verdict is 'pass' with waived findings (bypassed: true) — visible bypass, not a clean green." >&2
      fi
      # Same rule as the in-session hook: a pass must PROVE it executed. Diff-scoped through
      # verifier_evidence.py's own sweep below, not here — this loop walks every handover in the
      # tree, and a phase that closed before this rule existed has no transcript and never can.
    fi
    # The verification loop is capped at 3 attempts per phase. Enforced here as well as in the
    # in-session hook (scripts/hook_verifier.sh): a cap only a hook can apply is a cap that stops
    # existing the moment the phase is driven any other way, and this is the metric H4 is measured
    # on. At the cap and CLEAN is not a stop — the cap is on the loop, not on the phase.
    # Exit 1 is the cap; anything else is an ERROR that could not decide it. They are recorded under
    # different names, because a failed run naming the wrong cause sends the fix at the wrong thing.
    python3 "$SCRIPT_DIR/verifier_attempts.py" check "$(dirname "$ho")" >/dev/null 2>&1
    cap_rc=$?
    if [ "$cap_rc" -ne 0 ]; then
      python3 "$SCRIPT_DIR/verifier_attempts.py" check "$(dirname "$ho")" >/dev/null
      if [ "$cap_rc" -eq 1 ]; then
        record_fail "verifier:attempt-cap"
      else
        record_fail "verifier:attempt-cap-unreadable"
      fi
    fi
    # An amendment names the requirement ids a post-verification change touched. A pass standing
    # over one that is OWED re-verification — any pending amendment on a passing phase, and every
    # security-relevant amendment always — is a claim about code that has since changed.
    if ! python3 "$SCRIPT_DIR/amendments.py" due "$(dirname "$ho")" >/dev/null 2>&1; then
      python3 "$SCRIPT_DIR/amendments.py" due "$(dirname "$ho")" >/dev/null
      record_fail "amendments:owed"
    fi
  done < <(find docs/features -type f -name handover.md 2>/dev/null)
fi

# 1bd) Carried items — a handover's forward-looking claims are owed an answer by the next phase, or
#      they become nothing. Phase 8 of one measured feature predicted, verbatim, the defect phase 9
#      then shipped, past every gate, because the prediction was prose. Enforced here as well as in
#      scripts/hook_verifier.sh for the same reason the attempt cap is: a rule only an in-session
#      hook applies stops existing the moment the phase is driven another way.
#
#      DIFF-SCOPED even under --full, and deliberately unlike verifier_precheck. This obligation
#      lands on a document class every consumer repo already has on disk, so a full audit would fail
#      its CI over cards written before the rule existed - the hostage failure the scoping removes.
#      Nothing is lost: the hook holds the phase being CLOSED, which is a phase the diff touches by
#      construction. `carried_items.py check --all` is there for anyone who wants the audit.
#
#      Exit 1 is the obligation; anything else could not DECIDE it, and the two are recorded under
#      different names - a run naming the wrong cause sends the fix at the wrong thing.
echo "• carried items: claims declared, the previous phase's answered, the last card's filed"
python3 "$SCRIPT_DIR/carried_items.py" check --root "$ROOT"
carried_rc=$?
if [ "$carried_rc" -eq 1 ]; then
  record_fail "carried-items"
elif [ "$carried_rc" -ne 0 ]; then
  record_fail "carried-items:undecidable"
fi

# 1be) Execution evidence — a phase does not close on a verdict with no proof that anything ran.
#      `test_quality.reviewed` was a boolean the verifying agent wrote about itself; this reads a
#      transcript a recorder produced. Enforced here as well as in scripts/hook_verifier.sh for the
#      same reason the attempt cap is: a rule only an in-session hook applies stops existing the
#      moment the phase is driven another way.
#
#      DIFF-SCOPED even under --full, on carried_items' precedent rather than verifier_precheck's:
#      every phase that closed before this rule existed has no transcript and cannot acquire one, so
#      a full audit would fail a consumer repo's CI over a remedy that does not exist for it
#      (CLAUDE.md §3a). `verifier_evidence.py sweep --all` is the audit for anyone who wants it.
#
#      Exit 1 is the obligation (evidence genuinely absent); anything else could not DECIDE it (a
#      record nothing can parse), and the two are recorded under different names — the same split
#      scripts/hook_verifier.sh makes for this check. "Record your runs" cannot repair malformed JSON.
echo "• execution evidence: every closing phase's verdict names a transcript that ran"
python3 "$SCRIPT_DIR/verifier_evidence.py" sweep --root "$ROOT"
evidence_rc=$?
if [ "$evidence_rc" -eq 1 ]; then
  record_fail "verifier:no-execution-evidence"
elif [ "$evidence_rc" -ne 0 ]; then
  record_fail "verifier:execution-evidence-undecidable"
fi

# 1bf) Breaker — a phase that resolves to criticality: critical does not close without a Breaker record
#      (breaker.json). It was owed twice on one measured feature and ran neither time, with zero
#      trace anywhere (issue #45): a stage that emits nothing is indistinguishable from one that never
#      ran. Enforced here as well as in scripts/hook_verifier.sh, for the same reason the carried-items
#      obligation is — a rule only an in-session hook applies stops existing the moment the phase is
#      driven another way. DIFF-SCOPED even under --full, for the same reason carried-items is: this
#      obligation lands on a phase directory tree every consumer repo already has on disk, and the
#      in-session hook already holds it on the phase being CLOSED, which the diff touches by
#      construction. `breaker_gate.py check --all` is there for anyone who wants the audit.
echo "• breaker: a critical phase has a valid Breaker record"
python3 "$SCRIPT_DIR/breaker_gate.py" check --root "$ROOT"
breaker_rc=$?
if [ "$breaker_rc" -eq 1 ]; then
  record_fail "breaker"
elif [ "$breaker_rc" -ne 0 ]; then
  record_fail "breaker:undecidable"
fi

# 1bg) Defect emission — a phase does not close carrying fewer defects than its own verdicts
#      describe. `found_by` is the one field in firstmate's record that cannot be reconstructed
#      after a run, and two measured phases closed reporting 1 defect against at least 5 and 0
#      against at least 2, while their Verifiers were returning real, EXECUTED findings. The
#      emission itself is fail-open by design (measurement may never fail a phase), so without this
#      a writer that refused every entry looks exactly like a phase that found nothing.
#
#      Enforced here as well as in scripts/hook_verifier.sh for the same reason the attempt cap is:
#      a rule only an in-session hook applies stops existing the moment the phase is driven another
#      way. DIFF-SCOPED (CLAUDE.md §3a) — a phase this change did not touch is not this change's
#      responsibility, and a phase with no metrics record at all is NOT CHECKED and says so.
echo "• defect emission: the record carries every defect these phases concluded"
python3 "$SCRIPT_DIR/emission_gate.py" defects --root "$ROOT"
emission_rc=$?
if [ "$emission_rc" -eq 1 ]; then
  record_fail "defects-unrecorded"
elif [ "$emission_rc" -ne 0 ]; then
  record_fail "defects-undecidable"
fi

# 1bh) The close stamp — a phase that has LANDED does not carry a null `closed`. Issue #46 moved the
#      stamp from implementation-finish to landing and nothing emitted it there, so the premature
#      stamp was replaced by NO stamp: one phase landed with `closed`, `elapsed_minutes`,
#      `tests_before`, `tests_after` and `verification_attempts` all null, and all six were entered
#      by hand hours later. Note what watched it and reported success — the hypothesis counted
#      OVERRIDES CORRECTING a close stamp and found none, which is what a producer that stopped
#      looks like. A check that only looks for a WRONG value can never see an absent one.
#
#      `scripts/hook_phase_close.sh` is the emitter; this is what makes a missed stamp visible. Only
#      phases that already have a metrics record of this project are read — a phase this pipeline
#      never measured has no producer to have stopped — so a repository with no writer configured is
#      NOT CHECKED and says so.
echo "• close stamp: no landed phase carries a null \`closed\`"
python3 "$SCRIPT_DIR/emission_gate.py" close --root "$ROOT"
close_rc=$?
if [ "$close_rc" -eq 1 ]; then
  record_fail "close-stamp-missing"
elif [ "$close_rc" -ne 0 ]; then
  record_fail "close-stamp-undecidable"
fi

# 1ba) The Verifier's bookkeeping, done by a script. 26% of everything the Verifier raised across one
#      measured feature was this class — untraced requirement ids, stale gate stamps, a deleted
#      `## Acceptance criteria` heading — and all of it was mechanically decidable. It runs on EVERY
#      commit, which is what stops the same defect being found twice, six attempts apart, in one
#      phase — and it is diff-scoped by default for the same reason the spec loop above is: you are
#      responsible for what you change, so a repository full of pre-rule phases can upgrade instead
#      of being held hostage by CI. --full audits everything.
echo "• verifier pre-check: traceability, gate-stamp freshness, spec structure"
if [ "$FULL" -eq 1 ]; then
  python3 "$SCRIPT_DIR/verifier_precheck.py" --all --root "$ROOT" || record_fail "verifier-precheck"
else
  python3 "$SCRIPT_DIR/verifier_precheck.py" --root "$ROOT" || record_fail "verifier-precheck"
fi

# 1bcf) Fixture realism against the shapes the PROJECT declared (issue #33). It shipped as
#      instruction in three skills and a stage brief, and one phase then shipped a credential
#      refusal that could never fire: 1,009 tests green against Telegram supergroup ids an order of
#      magnitude too small, an int32 column, and a DataError before the control ran. No static rule
#      decides "a shape a real deployment produces" generically — so the project declares it in
#      fixture-shapes.toml and this check is generic. DIFF-SCOPED, like subprocess_check.py and for
#      the same measured reason. A project with no declaration is CLEAN and says so on stderr:
#      permanently green with no output is the invisible pass this exists to remove.
echo "• fixture shapes: no fixture contradicts a shape this project declared"
python3 "$SCRIPT_DIR/fixture_shapes.py"
fixtures_rc=$?
if [ "$fixtures_rc" -eq 1 ]; then
  record_fail "fixture-shapes"
elif [ "$fixtures_rc" -ne 0 ]; then
  record_fail "fixture-shapes:undecidable"
fi

# 1bcg) Interface drift — a spec's Interfaces block is a claim about code, and nothing compared the
#      two. One phase's block said the poll loop polls then sleeps while the shipped code did the
#      reverse (it had to: polling first opened a second concurrent DB session and hung the suite to
#      its watchdog); the next phase's spec over-claimed what its code did. Both were caught by an
#      implementer choosing to look. This decides the half a static rule can: a call signature the
#      block NAMES must exist in the source tree. What it cannot decide — the order of two
#      operations, i.e. that very instance — is pinned as a test in tests/test_interface_drift.py
#      rather than claimed here. DIFF-SCOPED, like fixture_shapes.py above.
echo "• interface drift: every signature a spec's Interfaces block names exists in the code"
python3 "$SCRIPT_DIR/interface_drift.py" --root "$ROOT"
drift_rc=$?
if [ "$drift_rc" -eq 1 ]; then
  record_fail "interface-drift"
elif [ "$drift_rc" -ne 0 ]; then
  record_fail "interface-drift:undecidable"
fi

# 1bd) Verdict currency — a passing verdict must not stand over a tree that has since changed
#      (issue #51). The feature-close ship gate owns both findings and fixes while it runs, so it
#      changes verified production code and touches no phase artifact; verdict.json then goes on
#      asserting a named source file is byte-identical after the fix commit changed it. The
#      mechanism for the correction already existed (scripts/amendments.py); this is the trigger
#      that makes it mandatory. NEVER a rewritten verdict — that would restate a verification nobody
#      performed, which two separate phase workers correctly refused to do.
#      --full only: it anchors on the newest verdict in a feature and asks git what came after, so
#      on a partial run it would be answering about commits the diff is not responsible for.
if [ "$FULL" -eq 1 ]; then
  echo "• verdict currency: no passing verdict stands over a tree that changed after it"
  for feature_dir in "$ROOT"/docs/features/*/; do
    [ -d "$feature_dir" ] || continue
    python3 "$SCRIPT_DIR/verdict_currency.py" check "$feature_dir"
    currency_rc=$?
    if [ "$currency_rc" -eq 1 ]; then
      record_fail "verdict-currency"
    elif [ "$currency_rc" -ne 0 ]; then
      record_fail "verdict-currency:undecidable"
    fi
  done
fi

# 1bc) Required skills exist, and every skill a stage was owed was actually observed being loaded. A
#      stage whose required skill file is missing does not get a lighter version of the rules; it
#      gets none, and until now that failed silently. The audit is the other half of pointer
#      delivery: a pointer nothing checks is the instruction-with-no-mechanism it replaced.
if [ "$FULL" -eq 1 ]; then
  echo "• required skills: every stage's required SKILL.md is present"
  python3 "$SCRIPT_DIR/required_skills.py" verify --root "$ROOT" || record_fail "required-skills"
  echo "• required skills: every required skill has an observed load"
  python3 "$SCRIPT_DIR/required_skills.py" audit --all || record_fail "required-skills:unloaded"
fi

# 1bb) The document read path — the artifacts obey their declared readers and caps, and no stage
#      instruction has quietly re-acquired a read that was removed. The second half is the one that
#      matters over time: the cost this guards against came back one caller at a time last time, so
#      the check is attached to the invariant (scripts/doc_read_path.py owns the table) rather than
#      to any single command.
#      The artifact half is diff-scoped by default and audits everything under --full, the same way
#      this script already switches between the staged diff and a full scan for specs. --sources is
#      always full: a re-acquired read is a defect wherever it was added.
echo "• read path: docs/features artifacts + canonical stage instructions"
READ_PATH_ARGS="check --sources"
[ "$FULL" -eq 1 ] && READ_PATH_ARGS="check --sources --all"
if ! python3 "$SCRIPT_DIR/doc_read_path.py" $READ_PATH_ARGS "$ROOT"; then
  record_fail "read-path"
fi

# 1bba) The frontmatter contract, in BOTH directions. `--sources` above catches a removed read
#       coming back; it cannot see a document class nobody ever decided about. Four of them sat in
#       exactly that state until issue #29 - named by stage instructions, absent from the table, one
#       of them keying the Stop-hook artifact sweep while nothing was instructed to write it - and
#       the recurring shape behind that issue is documentation making a claim nothing enforces.
#       So this asks the general question: every documented claim about an artifact's frontmatter
#       has a writer instructed to produce it.
#       CANONICAL REPOSITORY ONLY, both directions. Every source it reads lives upstream, so a repo
#       that merely installed the pipeline has no remedy for anything it could find, and a rule
#       whose remedy is unavailable is a wedge. There it runs NOT diff-scoped, for the same reason
#       `--sources` and `stage_effort.py check` are not: the table and the canonical stage
#       instructions are always open, never shipped artifacts a later rule would hold hostage.
#       The step announces which of the two it is, so a skipped check never reads as a passed one.
#       Which tree this is is ASKED of the module that decides it, never restated here: a second
#       copy of the marker rule in shell is the copy that drifts.
#       The canonical bullet names its SUBJECT, never a verdict, because it prints BEFORE the check
#       runs: worded as an outcome it announced that the table and the writer instructions agree and
#       then failed two lines later, which is a failed step reading as a pass. The vendored branch
#       below reports a STATE rather than an outcome, which is why it may say what it says.
#       The not-canonical branch is keyed on exit 1 SPECIFICALLY, which is the probe's verdict.
#       Any other non-zero is the probe failing to answer - 2 is a usage error, 127 is a missing
#       python3, and a traceback out of a module doc_read_path imports exits 1's neighbourhood too -
#       and reading those as "not the canonical repository" announced a vendored install with an
#       upstream remedy inside the pipeline's own repo. Naming a cause nobody verified is the same
#       defect as claiming an outcome nobody earned.
contract_step_announce () {
  python3 "$SCRIPT_DIR/doc_read_path.py" canonical "$ROOT" >/dev/null 2>&1
  case $? in
    0) echo "• frontmatter contract: the table, the templates and the writer instructions" ;;
    1) echo "• frontmatter contract: NOT CHECKED — not the canonical pipeline repository, remedy upstream" ;;
    *) echo "• frontmatter contract: the canonical-repository probe COULD NOT ANSWER; which tree this is was not determined, and the step below reports the outcome" ;;
  esac
}
contract_step_announce
if ! python3 "$SCRIPT_DIR/doc_read_path.py" check --contract-only "$ROOT"; then
  record_fail "frontmatter-contract"
fi

# 1bcx) Every overview.md carries the `## Contracts and Decisions` heading the spec gate's CONTEXT
#       block reads (scripts/spec_gate_context.py). Without it, `contradiction` — one of the four
#       things that block a spec — can only ever be checked against the prior phase's card, never
#       the feature's own contracts, and the spec gate used to report that absence on stderr and
#       PASS regardless (issue #57: eleven phases of clickup-agents). The heading is already
#       mandated by docs/templates/overview.template.md; this is what verifies real overviews carry
#       it.
#
#       DIFF-SCOPED even under --full, on the carried-items precedent above rather than the read
#       path's: `overview.md` is a document class every consumer repo already has on disk, written
#       long before this heading was checked, so a full CI audit would fail every one of those
#       features at once the day this ships — the hostage failure the scoping exists to remove.
#       Nothing is lost: a feature is checked the moment someone works under it, which is where the
#       remedy (fill in the heading) is actually available. `spec_gate_context.py check --all` is
#       there for anyone who wants the audit, deliberately by hand.
echo "• overview contracts heading: docs/features/*/overview.md"
if ! python3 "$SCRIPT_DIR/spec_gate_context.py" check "$ROOT"; then
  record_fail "overview-contracts-heading"
fi

# 1bcy) Allowlist growth, REPORTED and never gated (issue #97). A drift guard was once quietened
#       with NINE allowlist entries instead of a fix to its extraction layer, and that landed as
#       nine lines of routine-looking maintenance. This puts the delta where a reviewer sees it, in
#       the CI log beside the checks those allowlists quieten. Deliberately not a failure: growth is
#       often legitimate, and a blocking check would be answered with a bypass rather than with the
#       attention this exists to buy.
#
#       The other half of issue #97 - that every guard with runtime output declares AND emits what
#       a clean result does not establish - is `guard_proof.py scope`, and it is NOT called here:
#       this file ships into every consumer repo and `guard_proof.py` deliberately does not, so it
#       runs from .github/workflows/guard-proof.yml with the rest of the harness's own wiring.
echo "• allowlists: growth is a signal, not routine maintenance"
python3 "$SCRIPT_DIR/guard_scope.py" allowlists --root "$ROOT" \
  ${GUARD_SCOPE_BASE:+--base "$GUARD_SCOPE_BASE"} || true

# 1bd) Per-stage reasoning effort — declared where the harness reads it, and nowhere else claimed.
#      The runbook carried an effort table for ten phases and told the orchestrator to PASS effort at
#      spawn; the delegation tool has no such parameter, so nothing could obey it and every stage ran
#      at the session default. The table was the largest cost lever the pipeline believed it had, and
#      it was decoration. This check is what stops that returning: a stage declaring no effort, a
#      document stating a pair the definitions do not, and any instruction to hand a stage its effort
#      at spawn time are each a failure.
#      NOT diff-scoped, deliberately, and for the same reason `--sources` above is not: these are
#      canonical stage instructions, which are always open to change, never shipped artifacts a
#      later rule would hold hostage.
echo "• stage effort: every stage declares it, and no document claims one nothing applies"
python3 "$SCRIPT_DIR/stage_effort.py" check --root "$ROOT" || record_fail "stage-effort"

# 1b2) Lint, in BOTH its dimensions. `ruff check` judges rules and says nothing about formatting, so
#      drift passed the gate untouched and two consecutive measured phases reported "ruff clean"
#      about a dimension nothing could have failed on. scripts/lint_gate.py owns both; its format
#      half is diff-scoped on the applicability boundary, so a tree written before the rule is
#      counted rather than held hostage.
#
#      A missing ruff is NOT a silent pass: the gate says so and this records it as a failure the
#      same way any other unrunnable check would, because "the linter was not installed" and "the
#      code is clean" must never arrive looking alike.
echo "• lint: ruff rules (whole tree) + ruff format (what this change touched)"
python3 "$SCRIPT_DIR/lint_gate.py" || record_fail "lint"

# 1c) Cross-family assertion — on the model that actually FORMS THE JUDGEMENT.
#     Checking an agent's own `model:` would be meaningless here: every subagent in this runtime is
#     Anthropic, so no agent can differ in family from the implementer. What must be decorrelated is
#     $GATE_MODEL, the model the spec gate judges on — the pipeline's one remaining model gate now
#     that the Verifier's cross-family reading pass is gone. Re-pointed rather than deleted: the
#     invariant is unchanged, only the gate it is asked about still exists.
model_token () { grep -m1 '^model:' "$1" 2>/dev/null | sed 's/.*:[[:space:]]*//;s/[[:space:]].*//' | tr '[:upper:]' '[:lower:]'; }
# One vendor table, not two. This used to carry its own `case` over four vendors; a second copy of
# the pipeline's only independence primitive is a copy that drifts, and the one that drifted first
# would be the one nobody was reading. scripts/model_vendors.py is the table; unknown still exits
# non-zero here, so the caller's loud refusal below is unchanged.
model_family () { python3 "$SCRIPT_DIR/model_vendors.py" family "$1"; }
cross_family_check () {
  local gate="${GATE_MODEL:-}" gfam impl itok ifam
  if [ -z "$gate" ]; then
    # NOT CHECKED — said out loud, never a clean pass, never fatal. This is a STATIC AUDIT: it runs
    # under --full in CI and in a disposable worktree, where no gate call happens and no project
    # .env exists, so an unset model is its normal state there and the remedy the failure would
    # prescribe is unavailable in the environment that hits it — a wedge, not a gate. The refusal
    # that belongs to an unset model is gate_runner.py's, at CALL time (issue #48), where a model
    # genuinely decides something. And no default: resolving the audit against a model nobody chose
    # is the defect #48 removed, so "not checked" is the honest state rather than a fabricated
    # subject. Same shape as stage_effort.py's "nothing checked" for a tree with no agents/.
    echo "  cross-family: GATE_MODEL is not set — nothing checked. The spec gate's family cannot" >&2
    echo "  be established without one, so the comparison against the implementers was not" >&2
    echo "  performed. Set GATE_MODEL (project .env, or the environment) to enable it." >&2
    return 0
  fi
  gfam="$(model_family "$gate")" || { echo "  ✗ unknown GATE_MODEL family: '$gate'" >&2; return 1; }
  for impl in "$ROOT/agents/avenger-backend-architect.md" "$ROOT/agents/avenger-frontend-developer.md"; do
    [ -f "$impl" ] || continue
    itok="$(model_token "$impl")"
    ifam="$(model_family "$itok")" || { echo "  ✗ unknown implementer model family in $(basename "$impl"): '$itok'" >&2; return 1; }
    if [ "$gfam" = "$ifam" ]; then
      if [ -n "${GATE_SAME_FAMILY_WAIVER:-}" ]; then
        # Waived explicitly, through the same knob the gate CALLS honour (scripts/gate_runner.py),
        # so this audit cannot become a second wedge for a configuration the operator has already
        # disclosed. Reported every time and never silent: the point of the audit is to make the
        # configuration legible, and a waived one is exactly the configuration a reader must not
        # mistake for an independent gate.
        echo "  ⚠ SAME-FAMILY WAIVER: GATE_MODEL '$gate' (family '$gfam') is the implementer's own" >&2
        echo "    family. The spec gate is NOT independent of the implementers while this holds." >&2
        echo "    reason: $(bypass_reason_oneline "$GATE_SAME_FAMILY_WAIVER")" >&2
        return 0
      fi
      echo "  ✗ GATE_MODEL '$gate' (family '$gfam') is the implementer's own family." >&2
      echo "    Same-family verification is theater — point it at another vendor." >&2
      echo "    If there is genuinely no other family to reach, waive it explicitly with" >&2
      echo "    GATE_SAME_FAMILY_WAIVER=\"\$(cat <reason-file>)\" — never by misreporting" >&2
      echo "    AUTHOR_FAMILY, which drops the invariant silently instead of disclosing it." >&2
      return 1
    fi
  done
  echo "  the spec gate judges on '$gate' (family '$gfam'); implementers are '$ifam'."
  return 0
}
if [ "$FULL" -eq 1 ]; then
  echo "• cross-family: spec gate vs implementers"
  cross_family_check || record_fail "cross-family"
fi

# 2) Test suite. Exit 5 = "no tests collected" -> not a failure.
#    Pre-commit runs the phase suites only; --full (CI) also runs the feature-level e2e tests, which
#    are slow, need the assembled system, and have nothing useful to say about an uncommitted edit.
if [ "$FULL" -eq 1 ]; then
  echo "• tests: pytest -q (incl. e2e)"
  pytest -q; pc=$?
else
  echo "• tests: pytest -q --ignore=tests/e2e"
  pytest -q --ignore=tests/e2e; pc=$?
fi
if [ "$pc" -ne 0 ] && [ "$pc" -ne 5 ]; then record_fail "tests"; fi
[ "$pc" -eq 5 ] && echo "  (no tests collected — skipping)"

# 3) Mutation gate via cosmic-ray (CI / --full only). Fail closed: an errored run stops.
#    Same contract as scripts/hook_mutation.sh: baseline first, diff-scoped, deterministic verdict
#    at MUTATION_MIN_SCORE, and the same MUTATION_POLICY authority switch (blocking|advisory|off).
#    Keep the two in step — CI and in-session must not disagree.
# Default `advisory`, not `off`. It is deterministic, diff-scoped, needs no model below the
# threshold, and every non-discriminating test this project ever caught was caught by mutation —
# including two in one phase that neither spec gate nor a green 281-test suite surfaced. Advisory
# never blocks, so the cost of the default being wrong is a line of output. It is still not a
# dedicated reader for gamed tests — there is none; it is named as partial cover, not a replacement.
MUTATION_POLICY="${MUTATION_POLICY:-advisory}"
case "$MUTATION_POLICY" in
  enforce|advisory|off) ;;
  *) echo "  ✗ MUTATION_POLICY='$MUTATION_POLICY' is not one of enforce|advisory|off (fail closed)" >&2
     record_fail "mutation:bad-mode" ;;
esac

# In advisory mode a mutation finding is reported but never counts against the run. Every mutation
# failure path goes through this instead of record_fail, so the mode cannot be missed at one of them.
mutation_fail() {
  if [ "$MUTATION_POLICY" = "advisory" ]; then
    echo "  ⓘ mutation [advisory]: $1 — reporting only, not failing the run" >&2
    return 0
  fi
  record_fail "$1"
}

# Whether a comparison base can EXIST at all, decided explicitly and named rather than inferred
# from an empty string. On a push to the default branch there is no PR base sha AND the merge-base
# with the default branch is HEAD itself, so `unknowable` is not the same shape as "resolved to
# nothing" — it is `scripts/applicability.py`'s word for a scope git cannot state, used here for
# the same reason.
mutation_base_state() {
  mbs_head="$(git rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$1" ] || { [ -n "$mbs_head" ] && [ "$1" = "$mbs_head" ]; }; then
    echo "unknowable"
  else
    echo "known"
  fi
}

# Route the scope filter's NOTHING IN SCOPE exit. `did-not-run` is REPORTED in both states — that
# honesty is the whole of issue #97 and is not softened here — and the only question is whether it
# BLOCKS. With no base to compare against, in a CI checkout where nothing is uncommitted either, an
# empty scope has no remedy an author could apply, and CLAUDE.md §3a says a rule whose remedy is
# unavailable is a wedge rather than a gate. With a base, an empty scope is an ordinary result an
# author can act on, so it still blocks under enforce.
mutation_nothing_in_scope() {
  if [ "$1" = "unknowable" ]; then
    echo "  ⓘ mutation: NO COMPARISON BASE EXISTS — the gate did not run. This is NOT a score," >&2
    echo "    and NOT a failure: with nothing to compare against there is no remedy to apply, so" >&2
    echo "    it is counted and named rather than blocking (CLAUDE.md §3a)." >&2
    return 0
  fi
  echo "  ⓘ mutation: a base EXISTS and nothing in it is in scope — the gate did not run." >&2
  echo "    This is NOT a score. A remedy exists, so it still blocks under enforce." >&2
  mutation_fail "mutation:did-not-run"
}

if [ "$FULL" -eq 1 ] && [ "$MUTATION_POLICY" = "off" ]; then
  echo "• mutation: skipped (MUTATION_POLICY=off)"
fi
if [ "$FULL" -eq 1 ] && { [ "$MUTATION_POLICY" = "enforce" ] || [ "$MUTATION_POLICY" = "advisory" ]; }; then
  echo "• mutation: cosmic-ray (min score ${MUTATION_MIN_SCORE:-0.85}, gate ${MUTATION_POLICY})"
  # Module under test, read from cosmic-ray.toml. If it doesn't exist, there's nothing to mutate
  # (e.g. a docs/config-only repo) — skip like "no tests collected", don't fail closed.
  # Only a cleanly parsed SCALAR module-path is skippable. `module-path` also accepts a TOML list,
  # and a value can carry a trailing inline comment or a `#` inside a quoted path — treating any
  # value we didn't really parse as "missing" would silently skip the whole gate (fail OPEN). That
  # is why the decision lives in scripts/mutation_target.py (real TOML parse, never a regex) and
  # why everything ambiguous runs the gate and lets cosmic-ray decide; it fails closed on a bad path.
  # Exit 0 = skippable (prints the absent path), anything else = run the gate.
  if [ ! -f "$COSMIC_CFG" ]; then
    echo "  ✗ cosmic-ray.toml missing at repo root — mutation gate cannot run (fail closed)" >&2
    mutation_fail "mutation:no-config"
  elif MODPATH="$(python3 "$SCRIPT_DIR/mutation_target.py" --root "$ROOT" "$COSMIC_CFG")"; then
    echo "  (module-path '$MODPATH' not present — no code to mutate, skipping)"
  else
    WORK=$(mktemp -d); SESSION="$WORK/session.sqlite"; TMP="$WORK/report.txt"; SCOPED="$WORK/cosmic-ray.toml"
    cp "$COSMIC_CFG" "$SCOPED"
    # Diff-scope over the WORKING TREE plus the branch, through scripts/mutation_scope.py. The
    # `cr-filter-git` this replaces scopes with `git diff --relative -U0 <branch> .`, which reports
    # nothing about an UNTRACKED file — so a change whose contribution was new files had every
    # mutant skipped and the scorer returned GO over zero measured lines (issue #97). In CI nothing
    # is uncommitted, which is what --base is for: it widens the same scope to the branch.
    CI_BASE="${MUTATION_BASE:-$(git merge-base HEAD origin/HEAD 2>/dev/null || git merge-base HEAD main 2>/dev/null || true)}"
    [ -n "$CI_BASE" ] || echo "  (no diff base resolvable — scoping on the working tree alone)"
    MUTATION_BASE_STATE="$(mutation_base_state "$CI_BASE")"
    # A repo with code but no tests yet is not a broken suite — step 2 already treated pytest's
    # exit 5 as "skip", so failing the baseline here with "suite is not green" would contradict it
    # and misdiagnose a fresh scaffold. Skip the gate instead; there is nothing to measure.
    if [ "$pc" -eq 5 ]; then
      echo "  (no tests collected — nothing for mutation to measure, skipping)"
    # Baseline first: a mutant counts as killed whenever the test command fails, so a broken suite
    # would score a perfect 1.0. No kill means anything until the unmutated suite is green.
    elif ! cosmic-ray baseline "$SCOPED" >>"$TMP" 2>&1; then
      echo "  ✗ mutation baseline FAILED — suite is not green on unmutated code (fail closed):" >&2
      tail -5 "$TMP" >&2
      mutation_fail "mutation:baseline-failed"
    elif cosmic-ray init "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
      # Scope, then exec. Exit 3 from the scope filter is NOTHING IN SCOPE: reported as
      # `did-not-run`, never as a pass, because the absence of a measurement is not a clean gate.
      python3 "$SCRIPT_DIR/mutation_scope.py" ${CI_BASE:+--base "$CI_BASE"} --root "$ROOT" "$SESSION" >>"$TMP" 2>&1
      mfc=$?
      if [ "$mfc" -eq 3 ]; then
        tail -3 "$TMP" >&2
        mutation_nothing_in_scope "$MUTATION_BASE_STATE"
      elif [ "$mfc" -ne 0 ]; then
        echo "  ✗ mutation scope filter errored (fail closed):" >&2; tail -5 "$TMP" >&2
        mutation_fail "mutation:filter-errored"
      elif ! cosmic-ray exec "$SCOPED" "$SESSION" >>"$TMP" 2>&1; then
        echo "  ✗ cosmic-ray exec errored (fail closed):" >&2; tail -5 "$TMP" >&2
        mutation_fail "mutation:errored"
      else
        # Deterministic verdict: 0 = GO (no model call), 1 = below threshold, 2 = cannot score,
        # 3 = nothing tested, which is the absence of a measurement rather than a score.
        python3 "$SCRIPT_DIR/mutation_score.py" --min-score "${MUTATION_MIN_SCORE:-0.85}" "$SESSION"
        msc=$?
        if [ "$msc" -eq 1 ]; then
          { echo "---- mutation score (deterministic gate verdict: NO-GO) ----"
            python3 "$SCRIPT_DIR/mutation_score.py" --min-score "${MUTATION_MIN_SCORE:-0.85}" --json "$SESSION" 2>&1
            echo "---- survivors (cosmic-ray dump) ----"; cosmic-ray dump "$SESSION" 2>&1; } >>"$TMP"
          cat "$TMP" >&2   # survivors; the Verifier interprets them in chat, no model here
          mutation_fail "mutation"
        elif [ "$msc" -eq 3 ]; then
          mutation_fail "mutation:did-not-run"
        elif [ "$msc" -ne 0 ]; then
          mutation_fail "mutation:unscorable"
        fi
      fi
    else
      echo "  ✗ cosmic-ray run errored (fail closed):" >&2; tail -5 "$TMP" >&2
      mutation_fail "mutation:errored"
    fi
    rm -rf "$WORK"
  fi
fi

# Break-glass: a visible, logged override of a failing gate. Never silent.
#
# Through `bypass_log.sh`, the ONE writer of this log, exactly as the hook bypass and the Verifier's
# per-finding waiver do. This block used to format its own identical `printf`, so the record grammar
# lived in two places: behaviour agreed, but a change to one silently desynced the other and nothing
# failed when it did (issue #10). Routing every writer through one is what makes the guarantee
# structural rather than a rule each caller has to remember.
#
# **An override that could not be logged is not an override.** `bypass_log.sh` exits 2 when the
# append does not land, and that failure is this script's failure: the gates stay failed and CI stays
# red, rather than the bypass proceeding with no audit line behind it.
if [ "$fail" -ne 0 ] && [ -n "${GATE_BYPASS:-}" ]; then
  if CLAUDE_PROJECT_DIR="$ROOT" bash "$SCRIPT_DIR/bypass_log.sh" --gates "${failed_gates# }"; then
    exit 0
  fi
  echo "✗ pipeline gates failed:${failed_gates} — and the override was NOT recorded (above)." >&2
  exit 1
fi

if [ "$fail" -ne 0 ]; then
  echo "✗ pipeline gates failed:${failed_gates}" >&2
  echo "  (override intentionally with GATE_BYPASS=\"reason\" — logged + visible)" >&2
  exit 1
fi
echo "✓ pipeline gates passed"
# What that pass does NOT establish, beside the pass itself (issue #97). A later stage reads output,
# not source, and "pipeline gates passed" is exactly the line that gets read as more than it is.
python3 "$SCRIPT_DIR/guard_scope.py" emit gate_ci.sh >/dev/null || true
exit 0
