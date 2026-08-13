# Agentic Avengers Pipeline

The canonical rules live in `skills/pipeline-conventions/SKILL.md`. This file mirrors the essentials
for Claude Code sessions. Runtimes: **Claude Code + opencode**.

## Pipeline conventions

### 1. Artifact Documentation
Every stage writes a markdown artifact with YAML frontmatter:
- Feature-level → `docs/features/<feature>/` (`task-analysis.md`, `overview.md`, `plan.md`, `fidelity-report.md`, `scoped/review-<slice>.md`, `e2e-mapping.md`, `pipeline-observations.md`)
- Phase-level → `docs/features/<feature>/phases/<n>-<slug>/` (`verdict.json`, `verdict-attempt-<n>.json`, `handover.md`, `handover-archive.md`)
- Spec-level → `docs/features/<feature>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/` (`spec.md`, `test-mapping.md`, `test-evidence.md`)
- Tests → `tests/<feature>/<n>-<slug>/<n>.<k>-<subslug>/`; feature e2e → `tests/e2e/<feature>/`
```yaml
---
feature: <feature-name>
phase: <n>-<slug>          # omit for feature-level
stage: <stage-name>
model: <model-used>
verdict: <pass|fail|pending>   # gates only
created: <ISO-8601-timestamp>
readers: <who reads this, and when>   # the classes READ_PATH governs; `none (archive of <x>)` counts
links: <related-artifacts>
---
```

### 1a. The document read path — what decides documentation cost
**Cost is not size. It is `size x how often it is read x how long it stays resident`.**
`task-analysis.md` is 31 KB and cost **~465k tokens**, because two stages opened it on every one of
30 specs to read **one frontmatter field**. `handover.md` held 272 KB and cost **485k-1,475k**,
because every spec write and every spec review re-read *every prior phase's* handover — phase 8
alone paid ~527k before writing a line. `spec.md` was **990 KB, the largest artifact on disk**, and
cost comparatively little, because each is read mostly once, by its own implementer. **Nothing was
deleted; the read directives changed.**

`scripts/doc_read_path.py` is the one table (`… table` prints it) and the check that enforces it:
- `handover.md` is a **contract card, capped at 6144 bytes** — binding contracts, decisions,
  artifact links, next phase. The rest goes to `handover-archive.md`, which **no stage reads**. The
  ≤5-line summary was always in the template and writers produced 37 KB averages, so it is now
  checked, and `skills/phase-handover` tells the writer the length the task warrants outright.
- `work_kind` rides in **the spec's own frontmatter**; `task-analysis.md` is read once, by the
  Solution Architect. **Never send a reader to another document for a single field.**
- `overview.md` gains a stable `## Contracts and Decisions` header. **The spec gate and the human
  spec-review read the header; the spec writer still reads the whole file.**
- `test-mapping.md` is **the table**; mutation evidence, route-back history, build order and
  deviations move to `test-evidence.md`, read **on route-back only**.
- `verdict.json` archives a superseded attempt to `verdict-attempt-<n>.json` instead of nesting it,
  and caps `report` at 1500 chars. The schema is frozen — a bespoke top-level key is a finding.
- **A locked phase leaves the read path.** Later phases read its contract card, not its specs.
- **Every document the read-path table governs declares `readers:`. A document no stage reads does
  not get written.** Four classes are deliberately outside that table today — `fidelity-report.md`,
  `scoped/review-*.md`, `implementation-report.md`, `test-execution-report.md` — and are not claimed
  to carry the line; whether they belong on the read path is issue #29. This is
  the rule that stops the recurrence, and `doc_read_path.py check --sources` is its teeth: it scans
  `agents/`, `skills/`, `commands/`, `prompts/` and fails when a stage instruction re-acquires a
  removed read. **That check is one-directional** — it catches a removed read coming back, never a
  stage instructed to read something the table does not declare, so the table is not self-verifying
  and an undeclared reader leaves it incomplete rather than wrong (which is how the human
  spec-review's two reads went undeclared). **Change the directive at the table, never one caller at
  a time.** Every entry also
  names the template or stage instruction that makes its writer emit the line: declaring a reader is
  not the same as instructing anyone to write it down, and three artifact classes shipped with the
  first and not the second.
- **The artifact half of `check` is diff-scoped** — it enforces what the current diff touches and
  *counts* the rest on stderr without blocking, the same "you are responsible for what you change"
  rule as the verifier bundle, the spec re-gate cache and the mutation gate. That is what lets a
  repository full of pre-rule artifacts upgrade. `check --all` audits everything (CI's `--full`);
  when git cannot say what changed, nothing is enforced and the check says so out loud rather than
  falling back to enforcing everything.

`docs/lessons/` is untouched and stays at full price — under 2% of the bill, 16 of 18 entries cited
elsewhere, one drove test design across three phases. It is not where economising belongs.

### 2. Multi-spec phases + ID scheme
A phase is an independently verifiable slice holding one or more numbered specs `<n>.<k>`; requirement
ids are `R<n>.<k>.<m>`. **The Verifier runs once per phase**, after every spec in it is green.

### 3. The quality wall (per spec): ONE machine gate, then one human
There used to be **two** model gates here — an automated Fidelity Gate and the automated half of
spec-review — asking overlapping questions of the same document at the same moment. In phase 8 a spec
passed one and **failed the other on byte-identical text**. Both told their reviewer to judge
*"without charity … assume gaps until the spec proves otherwise"* and both ended *"when unsure between
REVIEW and NO-GO, choose NO-GO"*, over seven dimensions all asking "is everything covered?" with **no
size ceiling, requirement cap or cost dimension anywhere**. The only response available to a rejected
spec is more text: spec 8.0 grew 25k → 51k characters and 8.2 grew 40k → 57k across four rejected
rounds, until the gate flagged them for excess surface.

They are now **one gate** (`scripts/hook_spec_gate.sh`), built as **report-everything, then triage**,
with the verdict taken out of the model's hands entirely:

1. **Observe** (`prompts/spec-gate-observe.md`) — reports every observation, with **no verdict
   pressure**. It answers with `observations`, never `verdict`; it is told it is not a gate and it
   cannot block. A reviewer told to be conservative follows that literally, so the conservatism is
   removed from the pass that reads. It is handed a `## CONTEXT (reference only)` block
   (`scripts/spec_gate_context.py`) carrying **exactly** what the read path grants it — the
   overview's `## Contracts and Decisions` section and the *immediately prior* phase's contract card
   (bounded by the read path's own `HANDOVER_MAX_BYTES`, since that cap is enforced diff-scoped and
   an oversized pre-rule handover is counted rather than blocked; a truncated card says so),
   never the whole overview and never `handover-archive.md`. Without it half of `contradiction` is
   undetectable, and a closed set of four with an unobservable member is three items and a claim;
   absent context is normal, named on stderr, and never fails the gate.
2. **Triage** (`prompts/spec-gate-triage.md`, a cheaper model) — classifies each observation against
   the closed set. Its tie-break is the reverse of the old one: **when unsure, it is a note.**
3. **Decide** (`scripts/spec_gate_triage.py`) — derives the verdict deterministically. **No model
   decides whether a spec is blocked.**

**The blocking set is CLOSED — exactly four things block:** a **missing requirement**, an internal
**contradiction**, an **untestable criterion**, an **unhandled critical edge case**. Everything else
is a **note**; **notes never block** and land in the spec's known-open list (`spec-notes.md`, read
once by the implementer). It is closed *mechanically*: a category the table does not know is a hard
failure naming what was invented, never a judgement call — guessing "blocking" reinstates the ratchet
and guessing "note" deletes a finding. A fifth category is a deliberate edit to
`spec_gate_triage.BLOCKING`.

**Requirements are capped at 12 per spec, counted before the gate runs, as a SPLIT TRIGGER.**
`scripts/requirement_cap.py` runs ahead of any paid call, so an over-cap spec costs nothing; over the
cap it **splits** into siblings `<n>.<k>` under the same phase, each independently gated. **The gate
never rejects a spec for being large** — a rejection for size is one more thing to grow around. **The
cap binds a spec that can still be split** (§3a): a spec stamped `status: done` has shipped, its ids
are already pointed at by test-mapping rows and verdict findings, and the remedy does not exist — two
shipped specs declaring 30 and 29 requirements made every verdict unreachable, forever. There it is
counted and named. Growth happens while a spec is being written, which is exactly where it still binds.

**The writer is primed from that same rubric before it writes, from ONE source.** Phase 9 ran
**fourteen** gate rounds on its first spec and one, three and one on the next three, while total spec
writes barely moved against phase 8 (16 -> 19): collapsing the gates relocated the work rather than
reducing it. The writer learned what the collapsed gate blocks by being rejected fourteen times, and
nothing carried that learning forward, so every phase paid the fourteen again. `scripts/spec_rubric.py`
renders the brief; `scripts/hook_spec_rubric.sh` delivers it at `SubagentStart`, because "read the
rubric first" is an instruction with no mechanism. **Nothing in the brief is authored** - every line
is data read out of the module that decides with it (`spec_gate_triage.BLOCKING`, the requirement
cap) or a gate-prompt section lifted **verbatim** - and `agents/avenger-spec-writer.md` no longer
restates any of it. Two copies drift, and a drifted copy is **worse than none**, because the writer
is then held to a standard nobody applies; so it also **fails closed**, rendering nothing rather than
half a rubric when a prompt or a section is missing. `SPEC_RUBRIC_OFF=1` disables it; opencode has no
`SubagentStart` event, so its writer renders the brief from the pointer in the agent prompt.

The stamp is `spec_gate: pending | approved | blocked`, read in exactly one place
(`scripts/spec_gate_state.py`, which also derives the legacy `fidelity_verdict` so existing specs
still read correctly). `review_status` survives and means only what it now is: a **human** sign-off
from `/spec-review` grill-me, written by no model except under `SPEC_REVIEW_MODE=auto`, where the
machine gate is the whole wall because nobody is there. A spec reaches the implementer only when
`spec_gate: approved` AND `review_status: approved`. One machine gate plus one human is not two
rubrics; two rubrics was the defect. The hook makes **two** provider calls, and `gate_timeouts.py`
sizes its budget for two — a budget sized for one is the 120s-hook-around-a-300s-call defect exactly.

The spec gate also carries the pipeline's **only cost gate**, in two parts. Mechanically,
`scripts/subprocess_check.py` walks `tests/` for spawners lacking `@pytest.mark.subprocess("<why>")`
— it runs on every spec write in **both** modes via `hook_spec_gate.sh`, no model, and it is the only
stage that can see cost at all, since the observe pass, cross-family review and verification all read
for *correctness* and an expensive test is not incorrect. Deliberately not a wall-clock budget: seven
runs of one unchanged suite spanned 66.43s to 137.76s, so a runtime gate would fail green suites at
random. A project whose tests are not at `tests/` points the check at them with
**`SUBPROC_CHECK_PATHS`**; an absent root scans nothing, which is CLEAN but always said on stderr
rather than passing invisibly. **It is diff-scoped** (§3a): repository-wide it refused EVERY spec
write of one measured phase over 17 undeclared spawners in locked phase-1 and phase-7 tests the phase
had never opened. `--all` audits the tree and is deliberately not wired into CI, where it would
reinstate the same hostage one layer out.

**A spec already approved and implemented is re-gated on its changes only.** Unchanged text was
passed by this gate before and is not a finding — one spec drew REVIEW, REVIEW, then a NO-GO naming
requirements the same model had approved twice, unchanged. `scripts/spec_gate_cache.py` keeps the
body the gate last **approved** — a rejection records its hash, its verdict and its report but never
replaces that reference, since rejected text is not approved text; the hook hands the reviewer a
`## CHANGES SINCE APPROVAL` diff, and with no kept body gates the whole spec. A full re-gate is still owed when the diff changes the requirement
set, Scope, Interfaces / contracts, `work_kind`, or any `binding:`, and when the Verifier routed the
phase back with a **coverage gap** — there the question is what the spec failed to require, and
unchanged text is exactly where to look; a first gate is always full.

### 3a. The applicability boundary — what a mechanical rule may bind
**A mechanical rule binds what is still OPEN. What is CLOSED it may count and name, never block.**
`scripts/applicability.py` is the one module that decides which, and every check on the boundary
prints the same sentence when it counted instead of blocking.

Every check here was added after the tree it runs on, so without a boundary each asks *"does the whole
repository satisfy a rule we added later?"* instead of *"does what this change is responsible for
satisfy it?"*. In one measured phase that produced **three** blocks that look unrelated and are one
defect: the stage resolver parked forever on a phase closed under a disclosed captain-ordered cap, so
`/avenger-run --auto` could not start a phase **at all**; the requirement cap fired on two locked
implemented specs whose only remedy is a SPLIT a shipped spec cannot take; and the cost gate refused
every spec write over 17 undeclared spawners in locked phases nobody had opened.

Closed has exactly **three evidences**, and no call site invents a fourth: **untouched** (the diff
does not touch it — `changed_paths`, the mechanism `doc_read_path.py`, `verifier_precheck.py`,
`subprocess_check.py`, the verifier bundle, the spec re-gate cache and the mutation gate all now share;
when git cannot say what changed the scope is unknowable, so nothing is enforced and it is said out
loud) · **shipped** (the artifact's own stamps say the pipeline is past it — a `status: done` spec
cannot split, and **a rule whose remedy is unavailable is not a gate, it is a wedge**) · **excepted**
(a disclosed exception on the phase's ledger, `exceptions.json` beside `verdict.json`).

The **rule set is CLOSED** — `spec-gate`, `spec-review`, `verdict`, `requirement-cap`, each one read
by a named call site — and a rule outside it is a hard failure naming what was invented, never a silent
no-op: a ledger entry nothing reads is an exception that does not exist. An exception is **narrow**
(one rule, one subject, one phase), **audited or not recorded** (through `bypass_log.sh` into
`gate-overrides.log`, and a failure to log records nothing), and **never silent** — a resolver that
applies one names it on stderr. An unreadable ledger grants nothing and says so.

**A phase closed with a recorded exception is CLOSED, not incomplete**, which is what stops a
captain-ordered close wedging every later phase. The two remedies that do not need this — stamping a
human sign-off nobody gave, or claiming a machine verdict nobody obtained — are the "looks fine" class
PR 28 removed. `pipeline_state.py --from-phase <n>` is the blunt companion: it records nothing, judges
nothing, names every phase it stepped over, and — because it judges nothing — answers nothing
feature-wide over them: with any phase skipped the stage is `unknown`, never `done` or `e2e-author`.

### 4. The implementer writes the tests, test-first — and they lock at the Verifier
The **implementer** writes both the tests and the code, in a red → green loop (`skills/tdd`, vendored
from mattpocock/skills): one seam, one failing test, minimal code, repeat. **Vertical slices, never
the whole suite up front.** Red is the expected state *during* a build, not a failure.

**Locked-after-verify.** The implementer owns the phase's tests *until* `avenger-verifier` passes it;
from that point they are **locked** and weakening one requires re-verification. Locked forbids
*weakening*, not *adding* — a Breaker counterexample or a surviving mutant routes back to the
implementer to add a case.

Because the author of the code also authored its judge, **the tests get read** — on a green suite as
much as a red one — for tautological, implementation-coupled and missing-negative patterns, over a
**bounded review set** (tests mapped to the phase ∪ test files it changed, plus their directly
referenced helpers; expand only on evidence). `avenger-verifier` picks that set and persists
`verdict.json`, but the **judgement itself runs on another vendor's model** via
`scripts/verifier_review.sh` → `gate_runner.py` on `$VERIFIER_GATE_MODEL` — every subagent here is
Anthropic, so the agent cannot be its own cross-family check. It routes `wrong/gamed test` and
`coverage gap` back alongside `code issue`. That review is the pipeline's independence; it is not
optional, and it fails closed.

**Its bundle is scoped to the specs the diff touches.** It used to re-send every `spec.md` and every
`test-mapping.md` on every attempt (~832k tokens measured; one phase needed four chunks to fit at
all) — the diff-only rule above covers spec *re-gates*, not this bundle.
`scripts/verifier_bundle_scope.py` sends only changed specs, names the rest as carried forward, and
merges their findings back, so an **open carried finding still forces NO-GO**. **A spec holding an
open finding is never carried** — a `gamed test` finding is fixed in a TEST file, so no spec text
changes, and a spec never re-bundled is a finding never regenerated, which wedged the phase at NO-GO
forever; it goes back to the reader and clears or reappears on its own. No state, nothing changed, or
`VERIFIER_SCOPE=full` sends the whole phase — cheaper is never the safe direction here.

Three test modes by `work_kind`, all inside `skills/tdd`: **greenfield** (red → green per vertical
slice) · **migration** (parity-first — the *existing suite is the contract*, run it rather than
re-authoring it; characterize only genuine gaps at critical seams) · **refactor** (baseline-first
parity, no port; an intentional behavior change is greenfield work with its own requirement). Plus
**e2e-author**, not selected by `work_kind` — the implementer runs it once per feature, after the
final phase is green.

### 4c. The Verifier, narrowed — and its loop capped
A scout measured **all 46** Verifier findings across 8 phases of one feature. **Keep it, narrow it.**
Only **3 of 46** were user-visible defects no other stage could have found — but **two of those were
plaintext-credential leaks**, found by planting an adversarial value and executing it against a real
Postgres, and that is what buys the stage. So it keeps exactly three jobs: **coverage judged per
`binding:`**, **reading a green suite for gamed tests**, and **adversarial execution on secrets,
resource lifetimes and concurrency invariants**.

**Its bookkeeping is now a script.** **12 of 46 (26%)** were about the pipeline's own gate stamps,
traceability rows and spec headings — 45% on the worst phase, where **attempts 2 and 5 produced
nothing else**, ~70 minutes and ~410k tokens for four stamp-freshness observations. All of it was
mechanically decidable. `scripts/verifier_precheck.py` decides it for zero tokens: every requirement
id appears in some `test-mapping.md` row for its phase (`binding: none` exempt by construction), the
gate stamp is fresh for every spec, and every spec still has its `## Acceptance criteria` heading.
That defect recurred **twice, six attempts apart, in one phase**, because nothing checked it
continuously — so it runs on **every commit**, and, like every other check here, **diff-scoped**: the
phases that commit touches from `gate_ci.sh`, the whole phase at handover from `hook_verifier.sh`,
and everything under `gate_ci.sh --full`. A full audit on every commit would hard-fail a consumer
repo's CI over locked phases nobody touched, which is the hostage failure the scoping removes; when
git cannot say what changed, nothing is enforced and the check says so out loud.

**Verification is capped at 3 attempts per phase, and route-backs are bundled.** **16 of 20
re-attempts were the Verifier routing back to itself**, and one phase's new-finding series was
6, 2, 8, 4, 2, 1, 0, 6 — a gate disclosing a subset of what it could already see, one full
re-verification at a time. `scripts/verifier_attempts.py` stops the loop and prints the series so a
trickle is visible in the number. At the cap the remainder is **carried as known-open in
`handover.md`, waived explicitly, or escalated**; a fourth attempt is not one of the three. The trade
is named: some findings are carried rather than fixed. It is enforced in `hook_verifier.sh` and in
`gate_ci.sh --full` — enforcement that only an in-session hook can apply stops existing the moment
the phase is driven any other way — and `GATE_BYPASS` is honoured through the same audited `fail()`
path as every other blocking check.

The cap is on the **loop**, not the phase, and "resolved" is read the way the verdict schema defines
it: a `pass` whose findings are all **`fixed` or waived** clears, because *waive the remainder* is one
of the three remedies the cap's own message prescribes and the Verifier records a waiver by leaving
the finding in place with `break_glass`. Read as "the findings array is empty", the check could not be
satisfied by its own prescribed remedy, and CI stayed red with nothing left that could clear it. The
rule is not restated: `verifier_attempts.py` imports `open_findings` from `verifier_bundle_scope`,
which already owns it. What still stops is a verdict of **`fail`** at or past the cap — which is what
refuses a further attempt. **Its exit 1 means the cap and nothing else**: an uncaught exception exits
1 too, so an unreadable `attempt` field once arrived at the hook *as* a cap and prescribed three
remedies that could not repair a malformed file. Every unexpected failure exits 2 with its own cause,
per §6's rule that a stop names which.

The **Breaker stays separate** and is never folded into verification: it found phase 8's credential
leaks by *constructing inputs*, which is a different instrument from reading a test set.

### 4d. Amendments — change a verified phase without re-verifying all of it
The pipeline had no concept of a correction, so any change to a verified phase re-opened the whole
phase. One measured phase ran **eight** verification attempts; rounds 3 through 8 were that shape.

An **amendment** (`scripts/amendments.py`, ledger at `amendments.json` beside `verdict.json`) names
the requirement ids a post-verification change touched. **Only those re-verify**, carrying their own
evidence, and the verdict reads *verified at attempt N, plus amendments A1..An* — `amendments` is the
one extension to the frozen verdict schema, made at the schema, and it holds **ids only**.

- **Batched at phase close.** Ordinary amendments accumulate and re-verify together, once. Six
  route-backs become one bundled pass.
- **Security is NEVER batched.** `--security` is owed re-verification immediately: phase 8's
  credential leak must not wait for a batch, and the cost argument behind batching does not apply to
  a secret already in a log. A pending amendment on a phase whose verdict *already passes* is owed
  now too — that verdict is a claim about code that has since changed.

Both are enforced, not asked for: `hook_verifier.sh` and `gate_ci.sh --full` run `amendments.py due`.
An amendment with no requirement ids is refused — the naming **is** the re-verify scope — and a
corrupt ledger is an error, never an empty one.

### 4e. Skills are delivered, not requested — pointer plus evidenced load
The pipeline delegates core behaviour to 13 skills and used to delegate by *asking*: "Load
`skills/tdd` before you start" is an instruction with no mechanism. Nothing checked, nothing
recorded, and a stage that skipped one fell back silently. `docs/lessons/` shipped with a complete
written procedure and **zero invocations** for the same reason.

**What each stage requires is DERIVED from its own `agents/<stage>.md`** — every agent declares its
skills in one `Required skills` line and `scripts/skill_contract.py` reads them out of it. There is
no table: a hand-maintained list here was a second statement of a fact the definitions already carry,
and a second statement of a fact is exactly the promise-versus-enforcement gap this item exists to
close. Adding `skills/<name>` to that line is enough to make it required, and **only that line
counts** — a skill an agent names in *prose* is not a requirement. The Verifier's definition names
`skills/mutation-interpret` to say it applies only when the mutation gate is on, and both
implementers name `skills/ponytail` to say the hook injects it; read as requirements, that last one
made `PONYTAIL_OFF=1` a permanent phase wedge. An agent with no such line has an **empty** contract,
which `required_skills.py verify` reports rather than guessing at.

**The load is OBSERVED, never self-reported.** `scripts/hook_skill_load.sh` seeds each stage's
contract at `SubagentStart` and flips an entry on a real `Read`/`Skill` of `skills/<name>/SKILL.md`,
into the per-phase metrics record's `skill_loads[]` — there is deliberately no second evidence file.
A path that needed the agent to *run a command* to prove it had loaded a skill would be the
instruction-with-no-mechanism this item exists to fix, one layer up, so no such command exists.

**Delivery is decided by size** (`SKILL_INJECT_MAX_BYTES`, default **8192**), by
`scripts/hook_skills.sh` on `SubagentStart`. Injecting every body is one way to *guarantee* a load,
and it costs the same order as the reads the read-path work had just removed — every stage requires
`pipeline-conventions`, the largest file in `skills/`, on every `avenger-*` spawn. Observation is a
cheaper way to *detect* a missed load, and a required skill with no observed load blocks the phase
anyway: **detection beats prevention when both end the same way.** At or under the ceiling a skill is
**injected whole**, and the injection is **recorded as the load** — an injected skill is never read,
so without that record the audit would report a false gap on precisely the skills whose load is
guaranteed. Over it the stage gets a **pointer** — path, size, description — and *opening the file*
is what records it. `skills/ponytail` is delivered by `hook_ponytail.sh` alone, which records its own
load; delivering it twice would cost twice and would put the persona back after `PONYTAIL_OFF=1`.

**`skills/ponytail` is evidenced but never REQUIRED**, and that is a decision rather than an
oversight: it appears on no declared line, so requiring it would make the required set depend on an
environment variable, and a required set an env var can change is not a contract — a documented off
switch that can fail an audit is not an off switch. Its absence is still not silent. A stage
`hook_ponytail.sh` would have reached, with no injection recorded, surfaces as a **NOTE**
(`required_skills.py`): visible, never a gap, never in the exit code in any mode, and nothing branches
on it. `PONYTAIL_OFF=1` produces **no note** — an expected absence is not a surprise, and a note that
fires whenever the switch is used is noise that trains people to ignore notes.

**A pointer is not a suggestion**: `required_skills.py audit` runs at handover (`hook_verifier.sh`)
and in CI (`gate_ci.sh --full`) and **fails the phase** on a required skill with no observed load.
**It is also asked at the stage boundary**, where the remedy still exists: asked only at close, a
genuine requirement lands on the artifact that did not cause it — one measured phase learned at its
contract card that `avenger-spec-writer` had never loaded `skills/spec-review-checklist`, every spec
already written, gated and implemented, and "open the file in that stage" is unavailable to a stage
that has ended. `scripts/hook_skill_audit.sh` runs `audit --stage <stage>` at **`SubagentStop`**:
same `audit_gaps`, same wording, same exit code, narrower scope, and it returns the stage to work
rather than refusing a card. It **never blocks twice** for one stop and **fails open** — it is the
early copy of a check that still blocks at close, which is why the close-time audit stays (opencode
and the main thread reach no `SubagentStop`). `SKILL_AUDIT_OFF=1` disables the early one alone.
Every entry is keyed `<stage>:<skill>`, so the Verifier reading the rulebook is no evidence about the
implementer. It needs **no session id to be scoped**: the evidence is per-phase by construction, so a
pointer delivered in phase 1 cannot block phase 8 — `--all` sweeps every phase under `--full`. The
saving is a **prediction (H9), not a result** — roughly 1M tokens per 8-phase feature with zero
unrecorded loads, settled in phase 9.

**A required skill that is missing or unreadable is a loud BLOCKER** in the injected context, recorded
`loaded: false`: an absent required skill is not a lighter version of the rules, it is no rules.
`SKILLS_OFF=1` kills it; everything else fails closed and delivers nothing.

### 4f. Carried items - a handover's forward-looking claims are discharged, not merely written
Phase 8's handover recorded, verbatim, that caller-supplied identifiers would become a problem in
phases 9 to 12. Phase 9 was the first such caller and **shipped exactly that defect** - a
user-controlled path segment interpolated unencoded, so a name containing `?` or `#` retargets the
write - caught by the review gate only after verification had passed. The prediction was correct,
specific, actionable, and **became nothing**: no spec line, no test, no check. The same phase
produced the mirror defect, a card asserting a protection its own phase had deleted, which then
propagated into the next phase's instructions as binding. One direction over-claimed, the other
under-delivered, and **both passed every check**, because a forward-looking claim was prose.

**The card already had the slot.** `## Open items` has been a table with stable ids since the
contract card was introduced, for a measured reason: of 8 items carried as prose across 53.6 KB,
exactly one was ever picked up later - the id carried them, not the story. So nothing new sits beside
it. That section is **widened to hold forward-looking claims (`FWD-<n>`) alongside findings carried at
the attempt cap (`OBS-<n>`), and made binding** by `scripts/carried_items.py`, run from
`hook_verifier.sh` and from `gate_ci.sh` - both paths, for the same reason as the attempt cap.

- **A phase states what it carries**: a row per item, or an explicit `none` row. **Silence is not
  `none`** - silence is the state phase 8's prediction was written in.
- **The next phase answers every row and does not close until it has**: `built` into a spec
  requirement, `tested`, or `declined` with a stated reason. **`declined` is a real answer**; an item
  belonging further out is declined and **re-carried on that phase's own card**, which is how a claim
  about phases 9-12 survives without being owed to all four at once. The **spec writer** discharges,
  being the first stage that can turn a claim into a requirement.
- **The last card's forward claims name an ISSUE**, since no phase follows it to answer them: a card
  whose `next:` is `e2e` or `ship` does not close while a `forward-claim` row carries no `#<number>`
  or issue URL. A presence check and nothing more - whether a claim was worth carrying is not a
  gate's question.
- Ids are **scoped by the card that declared them**, so the ids already in use keep working. Which
  card is in force is `spec_gate_context.prior_phase`'s decision, imported rather than re-derived:
  this ledger and the spec gate's CONTEXT block must not disagree about which phase came before.
- The ledger (`carried.json`, beside `verdict.json` in the phase that **acted**) refuses an id the
  prior card never declared, and a corrupt ledger is an error rather than an empty one. **A pre-rule
  card with no section owes nothing**, and the CI sweep is **diff-scoped even under `--full`**
  (deliberately unlike `verifier_precheck`), because this obligation lands on a document class every
  consumer repo already has on disk - a full audit would fail its CI over cards written before the
  rule existed. Nothing is lost: the hook holds the phase being *closed*, which the diff touches by
  construction; `check --all` is the audit. The check runs inside the *passing* branch of the
  handover gate: a phase at the attempt cap is already not closing, and reporting an undeclared
  section there would hide what actually stopped it.

**What it does not catch, said rather than implied:** nothing can tell that a claim never written
down was worth writing, and nothing detects a card over-claiming. The counterweight to that is the
rule the card already carries - a binding contract names the file that *enforces* it, and a discharge
names the artifact that answers it. Both are checkable by the next reader; a sentence is not.

### 4a. Tiered binding decides what gets a test; tests are integration-level by default
Every requirement declares a **`binding:`** — `e2e` (an end user can observe it → carried by a
**journey** shared with the other `e2e` requirements on its path, **never its own test**) ·
`integration` (observable *only* under concurrency, fault injection or schema migration, **and the
spec says in one sentence why an e2e cannot see it** → its own test) · `none` (structural or
build-time → **no test**, enforced by CI, a type checker, or nothing). This replaced "every
requirement gets paired pass/fail criteria", which made suite size a mechanical function of id count
with no counterweight anywhere: 288 requirement ids became 458 tests at 4.87 lines of test per line
of source. The trade is accepted and named — a red journey names a journey, not a line.

Whatever does get a test drives it through a **seam** — the public entry point a caller uses (HTTP
handler, service method, CLI) — with real collaborators. Mock only what crosses a trust/cost boundary
(third-party API, LLM, vault). `test-mapping.md` carries a `level` column: `integration` (default) ·
`e2e` · `narrow`; **`narrow` requires a written justification** and is allowed only when a requirement
has no reachable seam. `binding` says *whether and where*; `level` says *how*. A journey's row is
`level: e2e` and lists **several** ids; every requirement not marked `none` appears in some row.
Migration is exempt: parity outranks the default. Rationale: tests bound to internals are the ones
rewritten on every refactor.

**The Verifier judges coverage per `binding:`, not per id** — a `binding: e2e` requirement is covered
by the journey that lists it, and `binding: none` is never a gap. Reading the old one-test-per-id
rule at that stage would route back a coverage gap on every deliberately unbound requirement and hand
the suite back the multiplier this tier removed. Rule in `skills/verifier-triage`; the cross-family
reader gets it from `prompts/verifier-review.md`.

### 4b. Feature-level e2e
**1-3 tests (5 max)**, written once after the last phase is green, in `tests/e2e/<feature>/`, tracing
to the goal in `overview.md` rather than a spec id (the one exception to "no spec id → no test").
Excluded from the mutation gate and the phase verifier hook. **Unchanged by §4a**: the journeys that
carry `binding: e2e` requirements are *phase*-level and live with the phase's other tests; this
feature-level ceiling still applies to `tests/e2e/<feature>/` alone.

### 5. Phase Ordering
Phases are built in dependency/risk order, one at a time, fully through build-and-verify.

### 6. Gates
Gates run on a fresh **cross-family** model (family ≠ author) and **fail closed** — a gate that can't
reach a verdict (missing key, provider down, non-JSON, same-family) stops, **and it names which**:
every stop carries a `cause=` from `scripts/gate_errors.py` and reproduces the provider's own words
verbatim. Four properties keep that honest, each one a defect that used to fail toward "looks fine":
a hook's `hooks.json` budget must exceed `GATE_CALL_TIMEOUT` plus headroom
(`scripts/gate_timeouts.py`, asserted at runtime and in the suite — a 120s hook around a 300s call
was killed before it could answer and read for a day as a model size ceiling), and that headroom is
checked against what **measurement** can spend inside the hook too — metrics processes on the hook's
path × `AVENGER_METRICS_TIMEOUT`, derived from what the scripts spawn, since a blocked writer costs
the full per-call bound once in every process; a timeout kills the
child's whole **process group** and reports measured wall clock (`scripts/proc_group.py` — killing
the direct child alone left workers billing for over an hour); a rejection emits its report and
records the judged hash **with its verdict**, so an unchanged rejected body replays the rejection;
and the runner must identify itself (`scripts/gate_runner_guard.sh`, `GATE_RUNNER_SHA256`) rather
than being trusted by path. `scripts/model_vendors.py` is the one vendor table and an unknown vendor
is a loud refusal — `glm-5.1` and `glm-5.2` used to read as different families. **Break-glass**
(`GATE_BYPASS="reason"`) is logged to `gate-overrides.log`, shown visibly, and recorded in
`handover.md` — never silent.

**The feature-close ship gate (`no-mistakes`) is the one sanctioned same-family exception.** It runs
once per feature — after the last phase is verified and the e2e suite is written — and covers what no
avenger stage does: lint, docs, push, PR, CI. Its pipeline agent is pinned to Anthropic Opus
(`.no-mistakes.yaml`, plus `agent_args_override` in `~/.no-mistakes/config.yaml`), a **deliberate
divergence** from the cross-family rule: it runs in the daemon's own disposable worktree with no
shared context with the stage that wrote the code, so it decorrelates *context* while accepting
shared *family* blind spots. It is not a break-glass bypass, and every **per-phase** gate (the spec
gate, the verifier) stays cross-family. While a run is active it owns both findings and fixes, so
the route-back-to-implementer rule is suspended for its duration.

It is wired as **`/avenger-run` §4a**, before the retrospective triage so that what it catches feeds
the retrospective — a defect the ship gate finds that no avenger stage covers is the most useful
observation the pipeline gets about itself. **It runs under `--auto` too**, and the only thing
`--auto` changes is `ask-user`: the gate drives its own `auto-fix`/`no-op` findings, but an
`ask-user` finding **halts the run** with the finding recorded verbatim, the same way `--auto`
already halts on a blocked spec — no-mistakes marks a finding `ask-user` because it challenges
the user's deliberate intent or changes product behaviour, so an unattended run must not answer it.
`--ship-yes` (valid only with `--auto`) passes `--yes` to no-mistakes and resolves those too: standing,
per-run consent, deliberately not the default. So an `--auto` run **can** push and open a PR — the
orchestrator itself still never does, in either mode. It stops at `checks-passed` and never merges.

**Two lavish review surfaces**, both interactive-only and both skipped under `--auto` (a foreground
`lavish-axi poll` would hang an unattended run): the **plan-approval stop** (**`/avenger-run` §3** —
plan.md rendered with a mermaid phase graph, annotations fed back to the planner until approved) and
the **retrospective triage** (**`/avenger-run` §4b**). Both are **preflight checks** in
`/avenger-run` §1 — `no-mistakes` in both modes as **three** independent states (binary + a runnable
pipeline agent via `no-mistakes doctor`; the repo **initialised**, which `no-mistakes axi` reports and
which a config file existing implies nothing about; and a `.no-mistakes.yaml` with no `REPLACE_ME`
left in a value), `lavish-axi` on interactive runs only, since `--auto` skips the two surfaces that
use it. They fail the run at the start rather than at the plan stop or at feature close, and neither
has a silent fallback.

**Prose belongs in a file and the command reads it — never on a command line.** Under `--auto`,
`hook_autoapprove.sh` matches its hard-deny regex against the **whole** Bash command string, so free
text that merely *names* `git push` or `gh pr create` denies the command carrying it — the regex
matches content, not intent, and narrowing it would spare real pushes. Any author-written free text is
written to a gitignored file with the `Write` tool and read inline as `"$(cat <file>)"`; never
`cat`/`echo`/a heredoc, which put it straight back. **This is an invariant, not a list of flags** —
`--intent`/`--instructions` on `no-mistakes axi`, `--note`/`--evidence` on `pipeline_observations.py
append`, and a `GATE_BYPASS` reason are non-exhaustive examples, and an argument's absence from them
is not a waiver. `GATE_BYPASS` is no exception for being a shell assignment prefix
(`GATE_BYPASS="$(cat <file>)" git commit …` works; `export` does not survive between Bash calls), and
a multi-line reason file is safe because every writer of `gate-overrides.log` normalises it,
`scripts/bypass_log.sh`, which normalises the reason through `scripts/bypass_reason.sh` — that log is
one tab-separated record per line, and a record its own reason text could split is not an audit
trail. Nothing appends to it by hand: the hook bypass, `gate_ci.sh`'s CI bypass and the Verifier's
per-finding waiver (`bypass_log.sh verifier <finding-id> <waived_by>`) all route through that writer,
which is what makes the guarantee structural rather than a rule each caller has to remember. The
test is whether an author could have phrased the value differently — a template with only ids, paths
and keywords substituted is not prose and stays inline. Canonical statement in
`skills/pipeline-conventions`.

**Mutation = cosmic-ray**, once per phase, **diff-scoped** (`cr-filter-git` skips mutants outside the
phase's changed lines). The verdict is **deterministic** — `scripts/mutation_score.py`, not a model:
score `>= MUTATION_MIN_SCORE` (default **0.85**) → GO with no model call; below → survivors go to the
gate model to be named as missing cases, and the phase routes back to the implementer. The threshold is
**not 100%** on purpose — chasing zero survivors is what multiplies narrow tests. Runs
`cosmic-ray baseline` first: a mutant counts as killed whenever the test command fails, so a broken
suite would otherwise score a perfect 1.0.

**Mutation is `advisory` by DEFAULT**: `MUTATION_POLICY` = `advisory` (default: runs and reports the
score + survivors, **never blocks**) · `enforce` (fails closed) · `off` (no mutation tool runs
anywhere). It was off; it is on because it is deterministic, diff-scoped, needs no model below the
threshold, and every non-discriminating test this project has caught was caught by it. Advisory never
blocks, so the cost of that default being wrong is a line of output. It is still an *extra* signal,
**not** the independence mechanism — that is the Verifier's test-quality review. The score itself is deterministic (`scripts/mutation_score.py`, diff-scoped via
`cr-filter-git`); the Verifier interprets survivors in chat using `skills/mutation-interpret`.

### 6a. Gates fire on "done", not on every edit
The implementer runs a red → green loop, so red is an expected state throughout a build. Gates trigger
on a spec reaching `status: done` (smoke-check the phase suite; model called only on failure) and on
`handover.md` — never per code edit. **No model runs in these hooks** except the spec gate: the
Verifier is an *agent* that runs in chat and commits `verdict.json`, and the hook only checks that
artifact exists and passes. Mechanical gates in hooks and CI; model gates in chat. The implementer's
own `pytest tests/<feature>/<n>-<slug>/` is the inner loop: free, no model call. A gate also never re-judges an unchanged spec —
`scripts/spec_gate_cache.py` hashes the spec body per gate, so `status: done` can't re-roll a fresh
verdict over an approved spec.

### 6b. Implementer minimalism (`skills/ponytail`)
The implementers — and only they — climb a minimalism ladder before writing production code: does this
need to exist (YAGNI) → already in this codebase → stdlib → native platform feature → installed
dependency → one line → minimum that works. Vendored from `DietrichGebert/ponytail` (MIT), flattened
to one intensity and re-scoped.

Delivery is **`scripts/hook_ponytail.sh` on the `SubagentStart` event**, matching `agent_type` against
`PONYTAIL_AGENTS` (default `avenger-backend-architect|avenger-frontend-developer`). SessionStart
context never reaches subagents, so a SessionStart hook would inject it everywhere *except* where code
is written; a passive `SKILL.md` self-activates ~never. The hook **fails closed** — bad payload,
unknown agent, bad regex or missing skill injects nothing, so `avenger-verifier`, `avenger-breaker` and
`avenger-bug-hunter` never receive a "write less code" persona while their job is to demand more.
`PONYTAIL_OFF=1` kills it.

**Production code only.** It never removes a test, a negative case or a seam, and rung 1 never applies
to a requirement in an approved spec. On conflict `skills/tdd`, `pipeline-conventions` and the spec
win. **It is not a gate**: `/ponytail-review` is advisory (no artifact, no verdict), and `/ponytail`
loads the ladder into the main thread for inline implementation the hook cannot reach — deliberately
off by default there, since the main thread also writes specs and runs verifier triage. opencode has no
subagent-start event; its implementers get the ladder from the agent prompt line only.

**The second `SubagentStart` hook is `scripts/hook_lessons.sh`**, and it exists for the same reason:
`docs/lessons/` shipped with a complete written procedure and zero invocations, because a directive in
a skill reaches only the agents that load that skill. It matches `agent_type` against `LESSONS_AGENTS`
(default `avenger-`, unanchored) and injects a short **pointer** — the entry count in
`docs/lessons/lessons.json` plus "read the index, filter by role, open only what matters". It never
inlines the log or a prose file; it fires on every spawn. The two hooks differ in **reach on purpose**:
ponytail excludes the Verifier, Breaker and bug-hunter because "write less code" fights their job,
while lessons reach **all** of them because prior lessons never do. Same fail-closed discipline — bad
payload, unmatched agent, bad regex, missing/unparseable/empty index inject nothing, so a project with
no lessons sees no change. `LESSONS_OFF=1` kills it, and opencode's agents get no injection.

### 6c. The pipeline learns from itself — two logs, kept apart
`docs/lessons/` (`skills/self-improvement`) is **per-project** and about the **work** — a pytest trap,
a migration gotcha. Any agent reads the index at start and appends when something is learning-worthy.
It was dormant until now; `pipeline-conventions` is where every agent picks it up, so it needs no
per-agent wiring.

**Every lesson states its `cost`** — tests added, agent invocations, runtime, tokens — and, where the
rule could justify unbounded growth, its limit. One pipeline wrote ten lessons about one feature and
not one was about cost: ten ways to be more correct, zero ways to be cheaper, which is how a suite
reaches 4.87:1 without anyone deciding to. The live case is a true lesson — *"'it already works' is
exactly the state that precedes a silent regression"* — that, unbounded, licenses writing tests
forever, since there are infinitely many correct behaviours. The fix is a limit, not a deletion: the
spec's `binding:` set is the budget.

`docs/features/<feature>/pipeline-observations.md` (`skills/pipeline-retrospective`) is about the
**machinery** — a gate that misfires, a stage that churns — and its destination is **this repo**. The
orchestrator appends observations *as they happen* (a run resumes across sessions, so end-of-run
recall is not reliable), including **successes**: a gate that caught something real is the evidence
for keeping it. At `done` they are rendered as a lavish triage; whatever the human selects becomes a
`pipeline-improvement` issue. Nothing is filed without an explicit selection.

`--auto` **records but never triages** — no human to poll. The log stays `triage: pending` and the
next interactive run's **preflight sweep** finds it; that sweep is the only recovery path, because
`done` is terminal and will never re-fire. `hook_autoapprove.sh` denies `gh`/`gh-axi` issue creation
outright while auto is armed, so the no-auto-filing rule is enforced mechanically, not just written.

### 6d. The pipeline measures itself as it runs — the record is firstmate's
Both logs above are prose, and neither answers **"did the pipeline get better?"** — that was
archaeology across commits and chat, so it was mostly not answered. It is answered now by a
**per-phase metrics record firstmate owns**: schema, units, absence semantics and every writing
command live in firstmate's `docs/pipeline-metrics.md` + `bin/fm-pipeline-metrics.sh`. **This repo
owns no part of that schema** — `scripts/metrics_sink.py` shells out to that CLI so the producer
contract (write during the run, keep every key, make repetition converge, add no key) is enforced by
their code. No second store, no second format; a missing field is a change to *their* schema.

Two properties outrank recording anything, both tested. **Emitted as the run happens** — each fact
written by the stage that observes it, at the moment it observes it, so a phase that dies mid-run
still leaves its numbers. **Writing metrics can never fail a phase** — no writer, an unwritable
record, a refusal, a hang or a crash is swallowed, logged to `.avenger-metrics.log`, and reported as
"not recorded"; every metrics CLI call in a hook exits 0. An unwritable record makes firstmate's CLI
*block* rather than fail, so one timeout abandons the writer for that process: fail-open has to hold
in wall clock, not just exit codes.

**Emission attaches to the fact, never to the caller.** `record_gate_call` lives in
`gate_runner.py` — the one point every gate call passes — and reads its stage off the rubric, so a
new gate is instrumented by existing. `record_spec_round` is idempotent by **content** (it reuses the
rebuildable gate cache), so any caller may report any spec write. A seeded skill requirement never
overwrites an observed load, in either hook order. Points: gate calls + causes (`gate_runner.py`) ·
a harness-killed gate (the hook's own signal trap, since the runner it killed cannot speak) · spec
rounds, byte size and requirement count (`hook_spec_gate.sh`) · the spec gate's own arithmetic —
observations in, blocking out, notes out (`spec_gate_triage.py`, where the verdict is derived) ·
the verification attempt **count** (`verifier_review.sh`, **derived from `verdict.json` and its
archives, never counted per invocation** — that script runs several times inside one attempt, and one
phase recorded **8** against a real attempt of 1 and a cap of 3 that had never fired, which reads as
the cap having failed; the retries stay visible in `gate_calls[]` with their `failure_cause`, and only
the attribution was wrong) · tests before/after, counted the *same
static way* at both ends (`hook_spec_gate.sh`, `hook_verifier.sh`) · **which stage found each defect** (`verifier_review.sh`,
`hook_mutation.sh`, and `pipeline_metrics.py defect` for stages no script sees) · which skills each
stage actually loaded (`hook_skill_load.sh`, `hook_ponytail.sh` — an instruction to load is not a
load). `found_by` is the field the record exists for and the only one unrecoverable afterwards. A
defect summary is author-written free text, so it follows §6 — `--summary "$(cat <file>)"`, never
inline prose. **Off unless `fm-pipeline-metrics.sh` is on `PATH` or `AVENGER_METRICS_CMD` names it**,
and an unconfigured run says so once rather than recording nothing in silence; `AVENGER_METRICS_OFF=1`
disables it. opencode records everything its adapter drives, minus skill loads (no read/spawn event).

**One asked-for fact is deliberately not built, and this is where that is said.** "Verification
attempts, **and what each one changed**" was asked for; only the attempt **count** is recorded.
firstmate's schema has no field for the per-attempt delta and that schema is closed by design — a
record that accepts arbitrary keys is not an authoritative answer to "did this get better", it is
whatever its last writer left there. No declared hypothesis needs it either: H4 measures the bare
`verification_attempts` count and predicts "3 or fewer", and `defects[]` already carries most of the
analytical value through `found_by`, `real`, `stage_reached` and `severity`. The one thing genuinely
missing is the attempt index. Deferred as **`fm-metrics-attempt-detail`**, which is firstmate's
decision to make, not this repo's.

### 7. Canonical-source driven
Edit `agents/`, `skills/`, `commands/`, `prompts/`, `scripts/`, `hooks/`; regenerate the opencode
adapter with `python3 scripts/sync_opencode.py`. Never hand-edit `.opencode/` — `agents/` and
`skills/` are generated, and `plugin/pipeline-gates.ts` is a thin adapter that shells out to the same
`scripts/hook_*.sh` the Claude Code hooks run. **The gates have one implementation.** Add or change a
gate in `scripts/` + `hooks/hooks.json`; the plugin needs no edit and must not grow logic of its own.

### 8. Canonical agents are project-agnostic
Agents in `agents/` carry pipeline mechanics only; they learn a project's rules by reading its
`CLAUDE.md`, spec, and `codebase/MOC.md` at run time. Never hardcode one project's stack into a
canonical agent — use `avenger-agent-factory` to ground a copy per repo. See `examples/jarvis/` for a
worked example of what grounding looks like.
