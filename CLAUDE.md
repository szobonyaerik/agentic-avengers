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
readers: <who reads this, and when>   # every document; `none (archive of <x>)` is a valid answer
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
- `overview.md` gains a stable `## Contracts and Decisions` header. **Spec-review reads the header;
  the spec writer still reads the whole file.**
- `test-mapping.md` is **the table**; mutation evidence, route-back history, build order and
  deviations move to `test-evidence.md`, read **on route-back only**.
- `verdict.json` archives a superseded attempt to `verdict-attempt-<n>.json` instead of nesting it,
  and caps `report` at 1500 chars. The schema is frozen — a bespoke top-level key is a finding.
- **A locked phase leaves the read path.** Later phases read its contract card, not its specs.
- **Every document declares `readers:`. A document no stage reads does not get written.** This is
  the rule that stops the recurrence, and `doc_read_path.py check --sources` is its teeth: it scans
  `agents/`, `skills/`, `commands/`, `prompts/` and fails when a stage instruction re-acquires a
  removed read. **Change the directive at the table, never one caller at a time.**

`docs/lessons/` is untouched and stays at full price — under 2% of the bill, 16 of 18 entries cited
elsewhere, one drove test design across three phases. It is not where economising belongs.

### 2. Multi-spec phases + ID scheme
A phase is an independently verifiable slice holding one or more numbered specs `<n>.<k>`; requirement
ids are `R<n>.<k>.<m>`. **The Verifier runs once per phase**, after every spec in it is green.

### 3. Composed quality wall (per spec)
Both gates, in order: (1) automated **Fidelity Gate** on spec write → sets `fidelity_verdict`; NO-GO
routes back. (2) **spec-review** → sets `review_status: approved`, in either **HITL** mode
(`/spec-review` grill-me) or **automated** mode (`/spec-review --auto` / `SPEC_REVIEW_MODE=auto`, a
cross-family AI reviewer). A spec reaches the implementer only when `fidelity_verdict != NO-GO` AND
`review_status: approved`. Both gates are also what pre-agrees the **seams** the tests get written at.
The Fidelity Gate is this repo's only automated model gate and is the main deliberate divergence from
`klm-agentic-pipeline`, which has no such gate.

Spec-review also carries the pipeline's **only cost gate**, in two parts. Mechanically,
`scripts/subprocess_check.py` walks `tests/` for spawners lacking
`@pytest.mark.subprocess("<why>")` — it runs on every spec write in **both** modes via
`hook_spec_review.sh`, no model, and it is the only stage that can see cost at all, since fidelity,
cross-family review and verification all read for *correctness* and an expensive test is not
incorrect. Deliberately not a wall-clock budget: seven runs of one unchanged suite spanned 66.43s to
137.76s, so a runtime gate would fail green suites at random. By judgement, the checklist asks what a
requirement's tests will cost before approving it. A project whose tests are not at `tests/` points
the check at them with **`SUBPROC_CHECK_PATHS`**; an absent root scans nothing, which is CLEAN but
always said on stderr rather than passing invisibly.

**A spec already approved and implemented is re-gated on its changes only.** Unchanged text was
passed by this gate before and is not a finding — one spec drew REVIEW, REVIEW, then a NO-GO naming
requirements the same model had approved twice, unchanged. `scripts/spec_gate_cache.py` keeps the
body each gate last **approved** — a rejection records its hash, its verdict and its report but never
replaces that reference, since rejected text is not approved text; the hook hands the reviewer a
`## CHANGES SINCE APPROVAL` diff, and with no kept body gates the whole spec. A full re-gate is still owed when the diff changes the requirement
set, Scope, Interfaces / contracts, `work_kind`, or any `binding:`, and when the Verifier routed the
phase back with a **coverage gap** — there the question is what the spec failed to require, and
unchanged text is exactly where to look; a first gate is always full.

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
was killed before it could answer and read for a day as a model size ceiling); a timeout kills the
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
shared *family* blind spots. It is not a break-glass bypass, and every **per-phase** gate (fidelity,
spec-review, verifier) stays cross-family. While a run is active it owns both findings and fixes, so
the route-back-to-implementer rule is suspended for its duration.

It is wired as **`/avenger-run` §4a**, before the retrospective triage so that what it catches feeds
the retrospective — a defect the ship gate finds that no avenger stage covers is the most useful
observation the pipeline gets about itself. **It runs under `--auto` too**, and the only thing
`--auto` changes is `ask-user`: the gate drives its own `auto-fix`/`no-op` findings, but an
`ask-user` finding **halts the run** with the finding recorded verbatim, the same way `--auto`
already halts on a spec-review NO-GO — no-mistakes marks a finding `ask-user` because it challenges
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

**Mutation is optional and OFF by default**: `MUTATION_POLICY` = `off` (default) · `advisory` (runs
and reports the score + survivors, never blocks) · `enforce` (fails closed). It is an *extra* signal,
**not** the independence mechanism — that is the Verifier's test-quality review. When off, no mutation
tool runs anywhere. The score itself is deterministic (`scripts/mutation_score.py`, diff-scoped via
`cr-filter-git`); the Verifier interprets survivors in chat using `skills/mutation-interpret`.

### 6a. Gates fire on "done", not on every edit
The implementer runs a red → green loop, so red is an expected state throughout a build. Gates trigger
on a spec reaching `status: done` (smoke-check the phase suite; model called only on failure) and on
`handover.md` — never per code edit. **No model runs in these hooks** except the Fidelity Gate: the
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
