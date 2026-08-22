#!/usr/bin/env bash
# PostToolUse: the MECHANICAL half of the phase gate. Runs the suite when work is DECLARED DONE, and
# on handover checks that the Verifier's committed artifact exists and passes.
#
# Why no model call here: the Verifier is an AGENT (agents/avenger-verifier.md), not a hook. It runs
# in-chat, traces coverage per `binding:`, drives adversarial execution against a real collaborator,
# records both through verifier_evidence.py, and persists
# docs/features/<f>/phases/<n>-<slug>/verdict.json. Model-based gates run in chat; mechanical
# gates run in hooks and CI, which only check the committed artifacts. (pipeline-conventions: "Where
# the models run".) The one exception in this repo is the spec gate, which is a model hook.
#
# This hook also runs the Verifier's MECHANICAL pre-check (scripts/verifier_precheck.py) and the
# amendment obligation (scripts/amendments.py due), because both are decidable without a model and
# 26% of everything the Verifier raised across one measured feature was that class.
#
# Why not on every src/ edit: the implementer runs a red -> green loop, so red IS an expected state
# throughout a build. Firing per edit stopped the agent to route a failure back to itself.
#
# Triggers (both derive the phase from the written path, so no guessing):
#   */specs/<n>.<k>-*/spec.md  with `status: done`  -> smoke-check that spec's mapping + phase suite
#   */handover.md                                   -> full phase suite + require a passing verdict.json
#
# `status: done` is not trusted on sight (issue #68): a spec's own implementer writes it, and used
# to keep working afterward — test-mapping.md, test-evidence.md and the phase's mutation gate all
# land later. On the `spec-done` trigger this hook checks the mapping is non-empty and the suite is
# green BEFORE letting a NEWLY written stamp stand; either check failing reverts it to
# `status: in-progress` (scripts/spec_done_guard.py) and then fails. A premature `done` is undone,
# not just complained about.
#
# The revert acts only on evidence scoped to the SAME thing it rewrites: a spec whose every
# requirement declares `binding: none` owes no mapping row at all (§4a), and a suite that could only
# be run repository-wide is undecidable for one spec rather than proof against it. Both still fail
# the hook and both leave the stamp exactly as written, as does GATE_BYPASS.
#
# The claim is exactly as wide as the mechanism and no wider: this is a PostToolUse hook matched on
# Write|Edit|MultiEdit (hooks/hooks.json), so no TOOL CALL can leave a false `done` stamp on disk.
# A stamp written through Bash — sed -i, a heredoc, python3 -c — never reaches this hook and is
# outside this mechanism's reach.
#
# $PHASE overrides the derived slug. Unresolvable phase -> full suite (minus e2e), never zero tests.
set -uo pipefail
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # plugin scripts dir (bypass_log)
. "$SD/load_env.sh"   # pipeline config from the project .env (real env always wins)
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$FILE" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0

case "$FILE" in
  */verdict.json)
    # MEASUREMENT ONLY — this branch never gates anything and always exits 0.
    #
    # A verdict write is the moment the Verifier CONCLUDES a defect, and it is the only moment the
    # finding is guaranteed to exist: `skills/verifier-triage` archives a superseded attempt to
    # verdict-attempt-<n>.json and a passing verdict carries no findings at all, so a close-time
    # reader opening verdict.json alone sees the last attempt and, on a phase that passed, nothing.
    # One measured phase closed reporting ONE defect against at least five it produced, four of them
    # the Verifier's own, found by executing code on attempt 1. `found_by` is the one field in the
    # record that cannot be reconstructed afterwards, so a phase that closes without it closes
    # without it forever.
    #
    # Emitted HERE rather than at a caller: a stage cannot forget an emission it does not make.
    # Idempotent by finding id, so this and the close-time emission converge on one entry.
    python3 "$SD/pipeline_metrics.py" verifier-findings "$(dirname "$FILE")" "$FILE" >/dev/null || true
    exit 0 ;;
  */handover.md)
    TRIGGER="handover" ;;
  */spec.md)
    grep -qE '^status:[[:space:]]*done[[:space:]]*$' "$FILE" 2>/dev/null || exit 0
    TRIGGER="spec-done" ;;
  *) exit 0 ;;
esac

# Phase slug and feature from the path:
# docs/features/<feature>/phases/<n>-<slug>/{handover.md,specs/<n>.<k>-*/spec.md}
derive_phase() {
  local f="$1" rest
  case "$f" in
    */phases/*) rest="${f#*/phases/}"; printf '%s' "${rest%%/*}" ;;
    *) return 1 ;;
  esac
}
derive_feature() {
  local f="$1" rest
  case "$f" in
    */docs/features/*|docs/features/*) rest="${f#*docs/features/}"; printf '%s' "${rest%%/*}" ;;
    *) return 1 ;;
  esac
}

SLUG="${PHASE:-$(derive_phase "$FILE" || true)}"
FEATURE="$(derive_feature "$FILE" || true)"

# NOT the close stamp (issue #46). A handover.md being WRITTEN is the Verifier's precondition, not
# the phase landing — this hook still has to check the suite, the verdict, amendments and carried
# items below, any of which can still route the phase back. Stamping here recorded `closed` and
# `elapsed_minutes` for phases that were not, in fact, done: an open amendment, a further Verifier
# finding, a blocked handover, nothing pushed. `commands/avenger-run.md` §5 stamps the close itself,
# directly, right after the per-phase commit actually lands — the one moment this hook cannot see.
# `record_phase_close` also refuses the write itself while the phase directory is still uncommitted,
# so a caller that got the ordering wrong fails the write rather than recording a false close.

# Layout: tests/<feature>/<n>-<slug>/... ; fall back to tests/<slug> for repos on the older layout.
TESTPATH=""
if [ -n "$FEATURE" ] && [ -n "$SLUG" ] && [ -d "tests/$FEATURE/$SLUG" ]; then
  TESTPATH="tests/$FEATURE/$SLUG"
elif [ -n "$SLUG" ] && [ -d "tests/$SLUG" ]; then
  TESTPATH="tests/$SLUG"
fi

fail() {   # $1 = bypass tag, rest = message
  local tag="$1"; shift
  printf '%s\n' "$*" >&2
  [ -n "${GATE_BYPASS:-}" ] && exec "$SD/bypass_log.sh" "$tag"
  exit 2
}

# A stamp is not a completion signal (issue #68) unless something makes it one: the moment
# `status: done` lands, revert it back to `in-progress` before this hook fails, so a premature
# stamp never survives past the check that reads it — a wedge guard watching this field sees
# nothing to trust until the checks below actually pass.
#
# Break-glass RESTORES what it overrides. `fail()` hands off to bypass_log.sh, which exits 0, so a
# revert that ran first cleared the failure and left the spec at `in-progress` anyway — an override
# an operator cannot act on, because re-stamping `done` re-fires this hook and reverts again, and
# `spec-done` is not in `applicability.RULES` so the disclosed-exception ledger is no route either.
# A bypass with no reachable end state is a trap, not an escape hatch. With GATE_BYPASS set the
# stamp is LEFT AS WRITTEN: done, overridden, and audited in gate-overrides.log.
#
# What it may bind is the applicability boundary (CLAUDE.md §3a, scripts/applicability.py): ONLY
# the TRANSITION into `done`. This trigger fires on any tool write to a spec.md that merely
# CONTAINS `status: done`, so on its own it cannot tell a stamp that just landed from one that has
# sat there since the phase closed — and rewriting the second destroys the single evidence
# `applicability.spec_shipped` reads, which flips the requirement cap from counting a shipped spec
# to blocking it with a split it cannot take. A spec already stamped `done` at committed HEAD is
# CLOSED: counted and named on stderr, never reverted, never blocked here.
STAMP_BINDS=0
STAMP_NOTE=""

revert_premature_stamp() {
  [ "$STAMP_BINDS" = "1" ] || return 0
  if [ -n "${GATE_BYPASS:-}" ]; then
    STAMP_NOTE="GATE_BYPASS is set, so the stamp is LEFT AS WRITTEN — this failure is the audited exception, and the spec stays 'done'."
    printf '%s\n' "[spec-done] $STAMP_NOTE" >&2
    return 0
  fi
  python3 "$SD/spec_done_guard.py" revert "$FILE" >&2 || true
  STAMP_NOTE="Reverted status to 'in-progress'."
}

# Exit 1 is the boundary (already `done` at HEAD, or a scope git cannot state); anything else is an
# ERROR that could not DECIDE it, and the two carry different tags and different messages — the
# same split carried_items, breaker_gate and the attempt cap below already make, and the rule
# CLAUDE.md § Gates states as "every stop names which". A check that never ran may not rewrite a
# spec, so neither undecidable branch reverts anything.
if [ "$TRIGGER" = "spec-done" ]; then
  python3 "$SD/spec_done_guard.py" stamp-is-new "$FILE"; stamp_rc=$?
  if [ "$stamp_rc" -eq 0 ]; then
    STAMP_BINDS=1
  elif [ "$stamp_rc" -ne 1 ]; then
    fail "verifier:spec-done-undecidable" \
      "verifier ($TRIGGER): whether this 'status: done' stamp is NEW could not be DECIDED (cause" \
      "above) — this is not a premature stamp, and finishing the mapping will not repair it. The" \
      "stamp is left exactly as written. Fix what it named."
  fi
fi

if [ "$STAMP_BINDS" = "1" ]; then
  python3 "$SD/spec_done_guard.py" mapping-complete "$FILE"; mapping_rc=$?
  if [ "$mapping_rc" -eq 1 ]; then
    revert_premature_stamp
    fail "verifier:spec-done-mapping" \
      "verifier ($TRIGGER): status was stamped 'done' but test-mapping.md next to it records no" \
      "test yet — no rows past the separator, or only rows whose requirement-id cell is still the" \
      "template's R<n>.<k>.<m> placeholder. (A spec whose every requirement declares" \
      "\`binding: none\` owes no row and is never asked for one.)" \
      "$STAMP_NOTE Finish recording the mapping, then stamp done again."
  elif [ "$mapping_rc" -eq 3 ]; then
    # A spec stating NO requirement states nothing about what it owes, and that is neither the
    # `binding: none` exemption nor an empty mapping — both of which read off the same empty binding
    # list. Its own stop, with its own remedy: the mapping was never judged, so recording a row
    # answers nothing, and nothing here justifies rewriting the stamp.
    fail "verifier:spec-done-no-requirements" \
      "verifier ($TRIGGER): status was stamped 'done' on a spec that declares no requirement at all" \
      "(named above) — so nothing in it says what it owes, and the mapping check could not judge it." \
      "This is NOT the \`binding: none\` exemption. Declare the spec's requirements with their" \
      "\`binding:\` tier, then stamp done again. The stamp is left exactly as written."
  elif [ "$mapping_rc" -ne 0 ]; then
    fail "verifier:spec-done-undecidable" \
      "verifier ($TRIGGER): whether this spec's mapping is recorded could not be DECIDED (cause" \
      "above) — this is not an empty mapping, and recording one will not repair it. The stamp is" \
      "left exactly as written."
  fi
fi

# Through `suite_outcome.py run`, never bare `pytest`: a suite that HANGS used to wedge this hook
# until the harness killed it, and a suite that died before printing anything came back with an exit
# code and no result to read. `suite_outcome` bounds the run in its own process group and answers
# the one question an exit code cannot — did this suite RUN, or did it merely stop. The decision
# lives there because the recorded transcript (`verifier_evidence.py`) asks it of the same suite,
# and a rule written twice is a rule that drifts. `SUITE_BUDGET_S` is where a project puts the
# watchdog inside this hook's own harness budget.
if [ -n "$TESTPATH" ]; then
  SCOPE="$TESTPATH ($TRIGGER)"
  OUT=$(python3 "$SD/suite_outcome.py" run -- pytest -q --tb=short "$TESTPATH" 2>&1); pc=$?
else
  SCOPE="full suite minus e2e ($TRIGGER; phase '${SLUG:-unresolved}' has no tests dir)"
  OUT=$(python3 "$SD/suite_outcome.py" run -- pytest -q --tb=short --ignore=tests/e2e 2>&1); pc=$?
fi

# 86 = the run did not COMPLETE: killed by its watchdog, or over before it stated what it ran.
# That is not the same as a red suite and it does not revert the `status: done` stamp — the same
# rule every undecidable branch above follows: a check that could not answer the question may not
# rewrite a spec. It still fails the hook, so nothing proceeds on a suite nobody watched finish.
if [ "$pc" -eq 86 ]; then
  fail "verifier:suite-incomplete" \
    "verifier ($SCOPE): the suite DID NOT COMPLETE — it was killed by its watchdog, or it ended" \
    "without stating how many tests it ran. A run with no result is not a green run, and this is" \
    "the shape an intermittent hang takes: clean on one run, wedged on the next, same defect in" \
    "both. The stamp (if any) is left exactly as written. Reasons and the tail of what it did" \
    "produce are below." \
    "$(printf '%s\n' "$OUT" | tail -25)"
fi

# Exit 5 = no tests collected. A phase whose tests don't exist yet is not a failure.
#
# The REVERT is spec-scoped, so the evidence it acts on must be scoped to the same thing. With no
# phase test directory resolvable the run above is the whole repository minus e2e — the permanent
# state of any project whose tests do not live under `tests/` (what SUBPROC_CHECK_PATHS exists for)
# — and one unrelated red test there says nothing about whether THIS spec is done, while
# `agents/avenger-backend-architect.md` tells the implementer outright that pre-existing failures
# are expected and are to be surfaced rather than fixed. So an unscoped suite is UNDECIDABLE for the
# revert: the hook still fails closed, and the stamp is left exactly as written. Same direction as
# both undecidable branches above — a check that could not answer the question may not rewrite a spec.
if [ "$pc" -ne 0 ] && [ "$pc" -ne 5 ]; then
  if [ "$STAMP_BINDS" = "1" ] && [ -z "$TESTPATH" ]; then
    fail "verifier:spec-done-undecidable" \
      "verifier ($SCOPE): the suite is RED, but this phase's own test directory could not be" \
      "RESOLVED, so this is repository-wide evidence about a spec-scoped question — a failure" \
      "anywhere in the tree does not say this spec is unfinished. The 'status: done' stamp is left" \
      "exactly as written. Put the phase's tests at tests/<feature>/<n>-<slug> (or set PHASE) so" \
      "the check can be scoped, and fix the failures below." \
      "$(printf '%s\n' "$OUT" | tail -20)"
  fi
  revert_premature_stamp
  fail "verifier:tests" "verifier ($SCOPE): the suite is RED — the phase is not done." \
       "$STAMP_NOTE" \
       "$(printf '%s\n' "$OUT" | tail -20)"
fi

# Fixture realism, mechanically (issue #33). One measured phase shipped a credential refusal that
# could never fire — 1,009 tests green against Telegram supergroup ids an order of magnitude too
# small for a real deployment, so the int32 column overflowed before the control ran. It was carried
# as INSTRUCTION in three skills and a stage brief, which is the promise-with-no-mechanism this
# pipeline keeps paying for. No static rule decides "a shape a real deployment produces" generically,
# so the PROJECT declares it (fixture-shapes.toml) and the check is generic.
#
# Asked HERE, on `spec-done`, because this is the first moment the fixtures exist AND the implementer
# who wrote them still owns them. Diff-scoped, so it holds what this change touched and counts the
# rest. A project that declares no shapes is CLEAN and says so — never a silent green.
#
# It does NOT revert the `done` stamp: that revert (issue #68) acts only on the two evidences it is
# scoped to, and widening it here would rewrite a stamp on evidence about a different question. The
# hook still fails, so nothing proceeds on it.
if [ "$TRIGGER" = "spec-done" ]; then
  python3 "$SD/fixture_shapes.py" ${TESTPATH:+"$TESTPATH"}; fixtures_rc=$?
  if [ "$fixtures_rc" -eq 1 ]; then
    fail "verifier:fixture-shapes" \
      "verifier ($SCOPE): a fixture contradicts a shape this project declared for an external" \
      "identifier (named above, with the reason the project gave). A value a real deployment never" \
      "produces is what let an int32 overflow hide behind 1,009 green tests. Fix the fixture, or" \
      "correct the declaration in fixture-shapes.toml if the shape itself is wrong." \
      "$STAMP_NOTE"
  elif [ "$fixtures_rc" -ne 0 ]; then
    fail "verifier:fixture-shapes-undecidable" \
      "verifier ($SCOPE): the fixture-shape check could not read its declaration or a test file" \
      "(named above) — a file it cannot read is a file it cannot clear, so this fails closed." \
      "$STAMP_NOTE"
  fi
fi

[ "$TRIGGER" = "handover" ] || exit 0

PHASE_DIR="$(dirname "$FILE")"

# The bookkeeping the Verifier used to raise by hand, once per phase, as 26% of its findings — an
# untraced requirement id, a stale gate stamp, a deleted `## Acceptance criteria` heading. All of it
# is mechanically decidable, so it is decided here, for no tokens. The Verifier keeps only what no
# script can do: coverage judged per `binding:`, and adversarial execution against secrets, resource
# lifetimes and concurrency invariants.
if ! python3 "$SD/verifier_precheck.py" "$PHASE_DIR"; then
  fail "verifier:precheck" \
    "verifier pre-check: the phase's own bookkeeping does not hold (named above)." \
    "These are mechanical, so fix them mechanically — do not spend a verification attempt on them."
fi

# Required skills a stage was owed and was never observed loading. A pointer is the cheap half of
# delivery — it saves injecting a large skill body on every spawn — and this audit is the other half:
# without it a pointer is exactly the "load skills/tdd before you start" instruction-with-no-mechanism
# that the delivery was introduced to replace. A phase does not close over an unobserved required
# load. Fail closed; the script names the stage and the skill.
#
# SCOPED TO THE PHASE IN FLIGHT, and it needs no session id to be: the evidence lives in the
# per-phase metrics record and `hook_skill_load.sh` writes nothing when no phase is in flight, so a
# pointer delivered in phase 1 cannot block phase 8 by construction. Same "you are responsible for
# what you change" rule as verifier_precheck above. `gate_ci.sh --full` sweeps every phase.
if ! python3 "$SD/required_skills.py" audit; then
  fail "verifier:skills" \
    "verifier: a stage in this phase required a skill and no load of it was ever observed." \
    "Open the named SKILL.md in that stage — reading it is what records the load — or re-run the" \
    "stage: a stage that never loaded its rules did not run under them."
fi

# Amendments owed re-verification NOW: every security-relevant one, always, plus any pending one on
# a phase whose verdict already passes. Batching is a cost optimisation and it does not apply to a
# credential already exposed.
if ! python3 "$SD/amendments.py" due "$PHASE_DIR"; then
  fail "verifier:amendments" \
    "verifier: this phase has amendments owed re-verification (named above). Re-verify the" \
    "requirement ids they name — not the whole phase — then close each with" \
    "scripts/amendments.py close <phase-dir> <A-id> --evidence <path>."
fi

# The forward-looking half of the contract card. A handover that PREDICTS a problem and is answered
# by nobody is not a record, it is a note to itself: phase 8 wrote down that caller-supplied
# identifiers would become a problem in phases 9-12, and phase 9 - the first such caller - shipped
# exactly that defect, past every gate. So the card's own `## Open items` section must state what it
# carries (a row, or an explicit `none`), and every item the PREVIOUS phase carried must have an
# answer here: built, tested, or declined with a reason.
#
# Deliberately NOT run beside the amendment obligation above, and the difference is the message. This
# is the last thing between a PASSING phase and its close, so it runs inside the `pass` branch below:
# a phase whose verdict is `fail`, or which is at the attempt cap, is already not closing, and
# reporting an undeclared items section at that moment would answer a question nobody asked while
# hiding the one that stopped the phase.
#
# Exit 1 is the obligation; anything else is an ERROR that could not DECIDE it, and the two carry
# different tags and different messages. Collapsed into one, a corrupt carried.json was reported as
# an unanswered item and prescribed `discharge` - a remedy that cannot repair malformed JSON. Same
# rule the attempt cap below already follows, and the one CLAUDE.md § Gates states as "every stop
# names which".
carried_items_gate () {
  local rc
  python3 "$SD/carried_items.py" declared "$PHASE_DIR"; rc=$?
  if [ "$rc" -eq 1 ]; then
    fail "verifier:carried-undeclared" \
      "verifier: this phase's contract card does not say what it carries forward (named above)." \
      "Write the section - a row per item with a stable id, or an explicit \`none\` row. Silence is" \
      "not none, and a prediction left in prose is owed to nobody."
  elif [ "$rc" -ne 0 ]; then
    fail "verifier:carried-undecidable" \
      "verifier: what this phase carries forward could not be DECIDED (cause above) - this is not a" \
      "missing section, and writing one will not fix it. A check that cannot be read enforces" \
      "nothing, so this fails closed. Fix what it named."
  fi
  python3 "$SD/carried_items.py" due "$PHASE_DIR"; rc=$?
  if [ "$rc" -eq 1 ]; then
    fail "verifier:carried" \
      "verifier: items the previous phase carried forward have no answer in this phase (named above)."
  elif [ "$rc" -ne 0 ]; then
    fail "verifier:carried-undecidable" \
      "verifier: whether the previous phase's items are answered could not be DECIDED (cause above)" \
      "- this is not an unanswered item, and \`discharge\` cannot repair it. Fix what it named."
  fi
  # The LAST card has no successor, so the obligation above binds nobody on it - the one place this
  # rule would reopen the hole it closes. There a forward claim must name an issue reference instead:
  # a presence check, never a judgement about whether the claim was worth carrying.
  python3 "$SD/carried_items.py" filed "$PHASE_DIR"; rc=$?
  if [ "$rc" -eq 1 ]; then
    fail "verifier:carried-unfiled" \
      "verifier: this is the LAST phase's card and it carries a forward claim no later phase can" \
      "answer (named above). Add an issue reference to that row - \`#<number>\` or an issue URL."
  elif [ "$rc" -ne 0 ]; then
    fail "verifier:carried-undecidable" \
      "verifier: whether the last card's forward claims are filed could not be DECIDED (cause" \
      "above). Fix what it named."
  fi
}

# Handover: the Verifier agent must already have run and left a passing verdict.
VERDICT="$PHASE_DIR/verdict.json"
if [ ! -f "$VERDICT" ]; then
  fail "verifier:no-verdict" \
    "verifier: no verdict.json next to $FILE." \
    "The phase cannot close on a green suite alone — a green suite the implementer wrote proves only" \
    "that the author agreed with themselves. Run @avenger-verifier first; it traces coverage per" \
    "\`binding:\`, drives adversarial execution, records both through verifier_evidence.py, and" \
    "writes verdict.json."
fi

V=$(jq -r '.verdict // "missing"' "$VERDICT" 2>/dev/null) || V="unparseable"
case "$V" in
  pass)
    if [ "$(jq -r '.bypassed // false' "$VERDICT" 2>/dev/null)" = "true" ]; then
      echo "⚠ verifier: phase passed with waived findings (verdict.json bypassed: true) — visible bypass, not a clean green." >&2
    fi
    OPEN=$(jq -r '[.findings[]? | select(.status == "open")] | length' "$VERDICT" 2>/dev/null || echo 0)
    if [ "${OPEN:-0}" -gt 0 ]; then
      fail "verifier:inconsistent" \
        "verifier: verdict.json says 'pass' but still carries $OPEN open finding(s). Fail closed."
    fi
    # A pass must PROVE it executed. This used to read `test_quality.reviewed` — a boolean the
    # verifying agent wrote about itself, which a stage that skipped its work could set just as
    # easily as one that did it. `verifier_evidence.py` reads a transcript a RECORDER produced:
    # commands, exit codes, measured wall clock, the sha256 of each run's output, and the digest of
    # the specs and tests it ran against. The verdict must name that transcript's chain, so a pass
    # cannot be paired with runs from a different attempt or a different tree.
    #
    # Exit 1 is the obligation; anything else is an ERROR that could not DECIDE it, and the two carry
    # different tags and different messages — the same split the Breaker, carried-items and
    # attempt-cap checks make, and the rule CLAUDE.md § Gates states as "every stop names which".
    python3 "$SD/verifier_evidence.py" check "$PHASE_DIR" --verdict "$VERDICT"; ev_rc=$?
    if [ "$ev_rc" -eq 1 ]; then
      fail "verifier:no-execution-evidence" \
        "verifier: verdict.json is 'pass' but nothing proves the verification ran (named above)." \
        "A green suite the implementer wrote is not evidence, and neither is a verdict that says so."
    elif [ "$ev_rc" -ne 0 ]; then
      fail "verifier:execution-evidence-undecidable" \
        "verifier: whether this phase's verification actually ran could not be DECIDED (cause" \
        "above) — this is not missing evidence, and recording a run will not repair it. A check" \
        "that cannot be read enforces nothing, so this fails closed. Fix what it named."
    fi
    # A phase that declares criticality: critical routes the Breaker (commands/avenger-run.md §4) —
    # and on one measured feature it was owed twice and ran neither time, with zero trace anywhere in
    # the feature's docs or tests (issue #45). A stage that emits nothing is indistinguishable from a
    # stage that never ran, so this checks for its RECORD (breaker.json), the same way the handover
    # check below checks for handover.md — mechanically, not by trusting the run to remember.
    #
    # Exit 1 is the obligation; anything else is an ERROR that could not DECIDE it, and the two
    # carry different tags and different messages — the same split gate_ci.sh already makes for this
    # check, and the rule CLAUDE.md § Gates states as "every stop names which". Collapsed into one,
    # an unreadable phase directory would be reported as a Breaker that never ran and prescribed a
    # Breaker run and a waiver, neither of which repairs it.
    python3 "$SD/breaker_gate.py" due "$PHASE_DIR"; breaker_rc=$?
    if [ "$breaker_rc" -eq 1 ]; then
      fail "verifier:breaker" \
        "verifier: this phase's Breaker obligation is not met (named above)." \
        "Run plan-build-verify:avenger-breaker over the critical/security paths; it persists" \
        "breaker.json with a verdict and what it actually attacked. If the run is deliberately" \
        "waived, record why: scripts/applicability.py record <phase-dir> --rule breaker" \
        "--subject <phase> --reason-file <f> --recorded-by <who>."
    elif [ "$breaker_rc" -ne 0 ]; then
      fail "verifier:breaker-undecidable" \
        "verifier: this phase's Breaker obligation could not be DECIDED (cause above) — this is not" \
        "a Breaker that never ran, and neither running it nor waiving it will repair this. A check" \
        "that cannot be read enforces nothing, so this fails closed. Fix what it named."
    fi
    carried_items_gate
    # Which stage found each defect is the one field the record cannot recover afterwards (§6d).
    # It used to be emitted by `verifier_review.sh` the moment the removed cross-family reading
    # pass produced its findings; that script is gone, and the verdict is now the only place those findings exist. So
    # it is emitted here, over the same finding shape, from the one point every closing phase passes.
    #
    # Measurement, never a gate: `|| true`, over a sink that swallows its own failures, and the exit
    # below is unaffected by whether anything was written. Its stderr is deliberately NOT discarded —
    # this is the pipeline's highest-volume defect-attribution path, and swallowing the diagnostic
    # would leave a run that dropped every verifier defect looking like one that found none.
    python3 "$SD/pipeline_metrics.py" verifier-findings "$PHASE_DIR" "$VERDICT" >/dev/null || true
    # ...and the check that makes its absence visible. The emission above is fail-open by design, so
    # on its own a producer that stopped producing is indistinguishable from a phase that found
    # nothing — which is exactly what two measured phases looked like while their Verifiers were
    # returning real, executed findings. This is a GATE and it is the one thing here that is: a
    # phase does not close carrying fewer defects than its own verdicts describe.
    #
    # Exit 1 is the obligation; anything else is an ERROR that could not DECIDE it, and the two
    # carry different tags and different messages — the same split every other check here makes.
    # "Emit the defects" cannot repair a verdict that will not parse.
    python3 "$SD/emission_gate.py" defects "$PHASE_DIR"; emit_rc=$?
    if [ "$emit_rc" -eq 1 ]; then
      fail "verifier:defects-unrecorded" \
        "verifier: this phase's record carries fewer defects than its own verdicts describe (named" \
        "above). \`found_by\` cannot be reconstructed after the run, so closing here loses them."
    elif [ "$emit_rc" -ne 0 ]; then
      fail "verifier:defects-undecidable" \
        "verifier: whether this phase's defects were recorded could not be DECIDED (cause above) —" \
        "this is not an unrecorded defect, and emitting one will not repair it. Fix what it named."
    fi
    exit 0 ;;
  fail)
    # At the attempt cap the loop STOPS here, and stopping is the whole point: 80% of re-attempts
    # measured across one feature were the Verifier routing back to itself. This used to be
    # `|| true` — a cap that printed a notice and then routed back anyway, which is a limit that
    # never fires while reading as one that does, and it would have made H4's
    # `verification_attempts` metric look bounded by a bound that did nothing.
    #
    # The route-back below is deliberately NOT reached at the cap: the three honest remedies are to
    # carry the remainder as known-open in handover.md, waive it explicitly, or escalate. Break-glass
    # still applies through fail(), because escapable and audited beats a hard wedge.
    python3 "$SD/verifier_attempts.py" check "$PHASE_DIR"; cap_rc=$?
    if [ "$cap_rc" -eq 1 ]; then
      fail "verifier:attempt-cap" \
        "verifier: the verification loop is at its cap (the series is above) — a further attempt is" \
        "refused. Carry the remaining findings as KNOWN-OPEN in handover.md, waive them explicitly" \
        "(scripts/bypass_log.sh verifier <finding-id> <who>), or escalate to a human."
    elif [ "$cap_rc" -ne 0 ]; then
      # Exit 2 is an ERROR, not the cap, and it carries its own tag so the override log can tell the
      # two apart. Deliberately no cap guidance here: carrying, waiving or escalating cannot repair
      # an unreadable record, and printing those remedies for it is how a crash came to read as a
      # verdict in the first place.
      fail "verifier:attempt-cap-unreadable" \
        "verifier: the attempt cap could not be DECIDED (cause above) — this is not the cap itself." \
        "A cap that cannot be read bounds nothing, so this fails closed. Fix what it named."
    fi
    fail "verifier" "verifier: verdict.json is 'fail' — route back per its findings:" \
      "$(jq -r '.routed[]? | "  - \(.to): \(.reason) (\(.spec_id // "-")) finding \(.finding_id)"' "$VERDICT" 2>/dev/null)" ;;
  *)
    fail "verifier:unparseable" "verifier: verdict.json has no readable verdict ('$V'). Fail closed." ;;
esac
