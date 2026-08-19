# agentic-avengers · plan-build-verify

A spec-driven, test-first agentic development pipeline that runs under **Claude Code** and
**opencode**. Specialised agents plan a feature; a **quality wall** (one automated spec
gate *and* a grill-me review) decides whether each spec is ready; the implementer then builds it
**test-first**, one vertical slice at a time; and every phase is verified once — by a fresh
cross-family model that reviews the tests as well as running them, plus cosmic-ray mutation — before
the suite locks and it ships.

The skill files (`SKILL.md`) and the gate brain (`gate_runner.py` + the rubric prompts) are a shared,
portable core; the opencode surface is a thin adapter generated from canonical sources.

---

## Enforcement model (the important part)

Gates fire **mid-session by default** in both runtimes, and the git floor (pre-commit + CI) backstops
them everywhere.

| Runtime | In-session gates | Backstop |
|---|---|---|
| Claude Code | native hooks (`hooks/hooks.json`) | git floor |
| opencode | native plugin (`.opencode/plugin/pipeline-gates.ts`) | git floor |

Both gate paths call the same `gate_runner.py` on a **cross-family** model (DeepSeek/Gemini via
OpenRouter), decorrelated from whatever model authored the work. Gates **fail closed** — a missing
key, an unreachable model, a non-JSON verdict, or a same-family model all **stop**. The only override
is **break-glass** (`GATE_BYPASS="reason"`): logged to `gate-overrides.log`, shown visibly, and
recorded in the phase `handover.md`. That reason is prose, so an agent under `/avenger-run --auto`
passes it from a file (`GATE_BYPASS="$(cat <file>)" git commit …`) like every other free-text
argument — a multi-line reason file is fine, since every writer of that log normalises it,
`scripts/bypass_log.sh` (the Verifier's per-finding waiver routes through it too), which normalises
the reason through `scripts/bypass_reason.sh` so it stays one parseable record. See
`skills/pipeline-conventions`.

---

## How it works

`Plan → Quality wall → (Build & verify loop ×phases) → Ship`.

```mermaid
flowchart TD
    start(["New task"]) --> ta["task-analyst"]
    ta -.-> taA[/"task-analysis.md · read once, by solution-architect"/]
    ta --> sa["solution-architect"]
    sa -.-> saA[/"overview.md"/]
    sa --> ip["implementation-planner"]
    ip -.-> ipA[/"plan.md · ordered phases"/]
    ip --> sw

    subgraph phase ["per phase — specs iterate · the verifier runs once, after all specs are green"]
        sw["spec-writer"] -.-> swA[/"spec.md · R n.k.m + binding: e2e|integration|none"/]
        sw --> cap{"requirement cap?<br/>mechanical · before any model"}
        cap -->|"over 12 — SPLIT, never a rejection"| sw
        cap -->|"at or under"| fg{"spec gate<br/>observe → triage → decide<br/>closed blocking set · cross-family"}
        fg -->|"blocked (1 of 4 things)"| sw
        fg -.->|"notes — never block"| notes[/"spec-notes.md · known-open"/]
        fg -->|"approved"| sr{"human sign-off?<br/>grill-me + checklist<br/>re-gate = diff only"}
        sr -->|"rework"| sw
        sr -->|"approved"| impl["backend / frontend implementer<br/>writes tests + code · test-first · skills-tdd"]
        impl -.-> implA[/"tests + src + test-mapping.md (the table) + test-evidence.md (route-back only)"/]
        impl -->|"next spec"| sw
    end

    impl -->|"all specs green"| ver{"avenger-verifier · family B is not A<br/>suite + coverage per binding + bounded TEST REVIEW"}
    ver -->|"cannot reach verdict"| stop(["fail-closed / stop"])
    ver -->|"senior override"| bg[/"break-glass · gate-overrides.log · bypassed on PR"/]
    ver -->|"code issue · wrong test · coverage gap"| impl
    ver -.-> verA[/"verdict.json · findings + waivers"/]
    ver -->|"capped at 3 attempts — then carry / waive / escalate"| stop
    ver -->|"pass — TESTS LOCK"| mut{"mutation<br/>advisory (default) / enforce / off"}
    mut -->|"survivors · enforce only"| impl
    mut --> brk["breaker<br/>critical paths · breaker.json or no close"]
    brk -->|"counterexample"| impl
    brk --> ho["handover"]
    ho -.-> hoA[/"handover.md · contract card, capped at 6144 bytes · rest → handover-archive.md · PROJECT_STATE"/]
    ho --> done(["phase done — next phase / shipped"])
```

```
PLAN
  task-analyst        -> docs/features/<feat>/task-analysis.md   (scope via grill-me; feature default
                         work_kind. READ ONCE, by solution-architect — each spec then carries its own
                         work_kind in frontmatter, so no per-spec stage opens this file)
  solution-architect  -> docs/features/<feat>/overview.md
  implementation-planner -> docs/features/<feat>/plan.md         (phases; each = 1+ candidate specs <n>.<k>)

PER PHASE (specs iterate; the verifier runs once, after all specs are green)
  spec-writer         -> .../phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md
      primed FIRST with the gate's own rubric (scripts/spec_rubric.py, delivered by
      scripts/hook_spec_rubric.sh) - one source, never a second copy - and it discharges what the
      previous phase's card carried (scripts/carried_items.py)
  QUALITY WALL (per spec): ONE machine gate, then one human
    0. MECHANICAL, before any model call:
         - requirement cap (scripts/requirement_cap.py): over 12 the spec SPLITS into siblings.
           Never a rejection — a rejection for size is one more thing for a spec to grow around.
         - the only COST gate (scripts/subprocess_check.py): a test that spawns a process needs
           @pytest.mark.subprocess("<why>")
    1. THE SPEC GATE (cross-family, two passes, one verdict)   blocked -> back to spec-writer
         observe  (prompts/spec-gate-observe.md)  reports everything, has NO verdict to give
         triage   (prompts/spec-gate-triage.md)   classifies against a CLOSED set, cheaper model
         decide   (scripts/spec_gate_triage.py)   derives the verdict — no model decides this
       Exactly four things block: missing requirement · contradiction · untestable criterion ·
       unhandled critical edge case. Everything else is a NOTE; notes never block and land in
       spec-notes.md. An already-approved, already-implemented spec is re-gated on its DIFF only.
    2. Human spec review -> sets review_status: approved
         /spec-review <spec>   (grill-me, one question at a time)
         under --auto / SPEC_REVIEW_MODE=auto the machine gate carries it — nobody is there
    A spec reaches the implementer only when spec_gate: approved AND review_status: approved.
    That wall is also what pre-agrees the SEAMS the tests will be written at.

  backend/frontend implementer -> tests/<feat>/<n>-<slug>/<n>.<k>-<subslug>/ + src/
                 + that spec's test-mapping.md (the TABLE) and test-evidence.md
                   (mutation evidence, route-back history, build order — read on route-back only)
                 skills/tdd, mode by work_kind:
                   greenfield -> red -> green, one vertical slice at a time
                   migration  -> parity-first; the EXISTING suite is the contract
                   refactor   -> baseline-first; behavior unchanged
  [repeat for each spec in the phase]

  avenger-verifier (once per phase, cross-family) -> verdict.json
                 full suite + coverage traced per requirement `binding:` (an e2e id is covered by
                 its journey; `none` is never a gap) + a BOUNDED test-quality review:
                 the tests mapped to the phase + test files it changed + their direct helpers.
                 Tautological / implementation-coupled / missing-negative = fail, even when green.
                 route-backs: code | wrong-gamed test | coverage gap -> implementer
  PASS -> THE PHASE SUITE LOCKS (locked-after-verify). Weakening a test then needs re-verification;
          adding one a later gate demands is always allowed.
  mutation (MUTATION_POLICY advisory by default — runs, reports, never blocks | enforce | off)
                 an extra signal, NOT the independence mechanism
  breaker (criticality: critical) -> counterexample -> implementer adds the test, fixes the code
                 persists breaker.json beside verdict.json - a `clean` verdict naming what it
                 ATTACKED, or `found` naming its counterexample. A critical phase does not close
                 without one, and a vacuous record is refused like a missing one
                 (scripts/breaker_gate.py).
  handover -> .../phases/<n>-<slug>/handover.md   CONTRACT CARD, hard cap 6144 bytes
                 binding contracts + decisions + artifact links + next phase; mirrors the verdict
                 and any waived findings. Everything else -> handover-archive.md, which NO stage
                 reads. Checked by scripts/doc_read_path.py, not merely asked for.
                 Its Open items table states what the phase carries forward - a row per item, or an
                 explicit `none`; SILENCE IS NOT NONE - and on the LAST card a forward claim must
                 name an issue, since no phase follows to answer it (scripts/carried_items.py).

FEATURE CLOSE (once, after the final phase is green)
  implementer (e2e-author mode) -> tests/e2e/<feature>/ + e2e-mapping.md
                 1-3 tests (5 max) proving overview.md's goal through the assembled system
  commit 1: the e2e stage output, so the ship gate starts from a clean committed tree
  SHIP GATE: no-mistakes (/avenger-run 4a) -> lint, docs, push, PR, CI
                 the one sanctioned same-family gate; runs in BOTH modes; stops at
                 checks-passed and never merges. Its findings are logged as observations.
  RETROSPECTIVE TRIAGE (/avenger-run 4b, interactive only) -> lavish triage of those
                 observations; only what the human selects is filed as an issue.
  commit 2: the observation log, after triage (branch_sync decides how, never whether)

SHIP
  The ship gate opened the PR. The user reviews and merges — the orchestrator never does.
  Break-glass overrides logged + visible.
```

### Who writes the tests
The **implementer** does, test-first, using `skills/tdd` (vendored from
[mattpocock/skills](https://github.com/mattpocock/skills), MIT — see `skills/tdd/ATTRIBUTION.md`):
one seam, one failing test, the minimal code to pass it, repeat. Never the whole suite up front.

That means the author of the code also authors its judge, so two controls buy the independence back:
1. **The Verifier reviews the tests**, not just the run — on a *green* suite as well as a red one.
   It is the only outside judgement that suite gets, and passing it is what locks it.
2. **The suite locks at the Verifier.** Before it, the implementer owns `tests/`; after it, nobody
   edits them — later gates may demand *added* tests, never weakened ones.

### The three test modes (set by `work_kind`, all inside `skills/tdd`)
- **greenfield** — red → green per vertical slice, at the requirement's seam.
- **migration** — parity-first. The **existing suite is the contract**: record its baseline, run it
  against the migrated code, and add characterization tests only at genuine gaps on critical seams.
- **refactor** — baseline-first, behavior unchanged. The migration procedure without a port; an
  intentional behavior change is greenfield work with its own requirement.

### Relationship to `klm-agentic-pipeline`
This pipeline is the sibling of `klm-agentic-pipeline` and deliberately shares its semantics. Known
intended differences: this one runs on **Claude Code + opencode** rather than GitHub Copilot; it adds
the automated **spec gate**; it keeps a **feature-level e2e** stage and **spec-isolation-review**;
and its mutation gate has a **deterministic, diff-scoped scorer** (`scripts/mutation_score.py` +
`cr-filter-git`).

Two further mechanisms live here whose status against the sibling is **unconfirmed**: the mechanical
**subprocess cost gate** (`scripts/subprocess_check.py`, run from the spec-gate hook in both modes)
and **diff-scoped re-gating** of a spec already approved and implemented. Whether
`klm-agentic-pipeline` has either was not checkable from this repository, so this list is not a
completeness claim in either direction — a divergence absent from it is not thereby drift.

---

## Component locations

| Component | Claude Code | opencode |
|---|---|---|
| Skills (`SKILL.md`) | `skills/` (plugin) | `.opencode/skills/` (symlink → `../skills`) |
| Agents | `agents/*.md` | `.opencode/agents/*.md` (generated) |
| Conventions | `pipeline-conventions` skill + `CLAUDE.md` | `AGENTS.md` |
| Commands | `commands/*.md` | (n/a — drive with `@agent`) |
| Gates (in-session) | `hooks/hooks.json` | `.opencode/plugin/pipeline-gates.ts` |
| Gates (floor, all) | git pre-commit + CI → `gate_runner.py` | same |
| Distribution | `/plugin install` / `/plugin update` | `scripts/install.sh` (versioned, update-aware) |

> `agents/`, `skills/`, `prompts/`, `scripts/`, `commands/`, `hooks/` are the **canonical sources**.
> The opencode copies under `.opencode/` are **generated** — do not hand-edit them.

---

## Canonical layout

```
agentic-avengers/
├── .claude-plugin/        plugin.json, marketplace.json
├── agents/                canonical subagents (Claude format)
├── skills/                portable SKILL.md skills (pipeline-conventions, grill-me,
│                          spec-review-checklist, tdd, verifier-triage,
│                          mutation-interpret, codemap, self-improvement,
│                          pipeline-retrospective, e2e-author, …)
├── commands/              pipeline-init.md, spec-review.md
├── hooks/                 hooks.json  (Claude Code in-session gates)
├── prompts/               spec-gate-observe.md, spec-gate-triage.md, verifier-review.md,
│                          project-setup.md
├── docs/templates/        spec / plan / overview / task-analysis / handover (+ archive) /
│                          test-mapping / test-evidence / verdict templates
├── docs/rubrics/          overview + plan rubrics
├── docs/lessons/          committed, team-shared lessons log (see the self-improvement skill)
├── cosmic-ray.toml        mutation base config (the gate diff-scopes a copy per phase)
├── scripts/
│   ├── gate_runner.py         cross-family verdict caller (opencode | openrouter), family-asserted
│   ├── gate_runner_guard.sh   refuses a runner that cannot identify itself as the shipped one
│   ├── gate_errors.py         the failure taxonomy: every gate failure names its own cause
│   ├── gate_timeouts.py       asserts each hook's budget outlives the provider call inside it,
│   │                          and the metrics writers that share its headroom
│   ├── model_vendors.py       the one vendor table; an unknown vendor is a loud refusal
│   ├── proc_group.py          a child a timeout actually stops (own process group, no orphans)
│   ├── gate_ci.sh             git/CI floor entry point (spec-gate stamps + requirement cap + tests
│   │                          + read path + overview contracts heading + stage effort
│   │                          + verifier pre-check + amendments + carried items + breaker record
│   │                          + cosmic-ray + break-glass)
│   ├── spec_gate_triage.py    the CLOSED blocking set, and the verdict derived from it (no model)
│   ├── spec_gate_state.py     the one place a spec's gate stamp is read (legacy stamps included)
│   ├── spec_gate_context.py   the gate's `## CONTEXT (reference only)` block: the overview's
│   │                          `## Contracts and Decisions` section + the prior phase's card. Three
│   │                          shapes are DEGRADED, not absent - no such heading, a heading holding
│   │                          only boilerplate, or no readable overview.md at all - exit 3, and the
│   │                          hook folds the builder's own cause into the persisted report; any
│   │                          other non-zero exit banners as UNAVAILABLE, never as a clean pass.
│   │                          `check [--all]` (diff-scoped even under --full, from gate_ci.sh)
│   │                          finds every feature in one of those three states
│   ├── requirement_cap.py     12 requirements per spec, counted before the gate — a SPLIT trigger
│   ├── spec_notes.py          the known-open list: notes that never block, read once by the implementer
│   ├── spec_rubric.py         the gate's rubric rendered for the WRITER, from the gate's own
│   │                          sources - data plus verbatim prompt sections, never a second copy
│   ├── carried_items.py       a handover's forward-looking claims, answered by the next phase or
│   │                          the phase does not close
│   ├── amendments.py          change a verified phase; only the named requirement ids re-verify
│   ├── breaker_gate.py        the Breaker's record: a phase declaring `criticality: critical` does
│   │                          not close without a valid breaker.json (a vacuous one is refused like
│   │                          a missing one). Authoritative at the handover hook, diff-scoped in CI
│   │                          (`check --all` audits), and the `stage: breaker` the resolver reports
│   ├── applicability.py       the one boundary every mechanical check binds on: OPEN it blocks,
│   │                          CLOSED it counts and names (untouched | shipped | excepted). Owns
│   │                          the diff scope and the per-phase exceptions.json ledger
│   ├── verifier_precheck.py   the Verifier's bookkeeping, mechanically, on every commit over what
│   │                          the diff touches (whole phase at handover; everything under --full)
│   ├── verifier_attempts.py   the 3-attempt cap on the verification loop, and the finding series
│   ├── required_skills.py     delivery + the blocking audit; WHICH skills is derived from each
│   │                          agent's own definition (skill_contract.py). Small ones injected
│   │                          whole, large ones a pointer (SKILL_INJECT_MAX_BYTES); `audit`
│   │                          fails a phase on a required skill with no OBSERVED load. The
│   │                          evidence is per-phase, so phase 1 cannot block phase 8
│   │                          (`--all` sweeps every phase, under `gate_ci.sh --full`)
│   ├── subprocess_check.py    the cost gate: unjustified subprocess spawners in tests (no model)
│   ├── doc_read_path.py       the read-path table + its two checks (artifact caps/`readers:`,
│   │                          diff-scoped; and `--sources`, so a removed read cannot come back)
│   ├── stage_effort.py        each stage's reasoning effort, read out of the `effort:` key in its
│   │                          own `agents/<stage>.md` - the key the harness applies at spawn, never
│   │                          a value a caller passes. `check` (every commit, NOT diff-scoped)
│   │                          fails on a stage that declares none, a document claiming a level no
│   │                          definition backs, and any instruction to supply it at spawn time;
│   │                          `table` renders the allocation. It does NOT observe what a stage ran
│   │                          at, and opencode does not carry the key at all
│   ├── spec_gate_cache.py     body each gate last approved + the verdict it last reached, so a re-gate stays in the diff
│   ├── spec_done_guard.py     a spec's `status: done` is not believed on sight: on the stamp's
│   │                          arrival the verifier hook checks that spec's test-mapping row and its
│   │                          phase suite, and REVERTS the stamp to `in-progress` if either fails.
│   │                          Binds only the transition into `done` (already `done` at committed
│   │                          HEAD = shipped, counted not reverted); `binding: none`-only specs,
│   │                          an unscoped red suite and `GATE_BYPASS` fail without rewriting it
│   ├── verifier_bundle_scope.py  sends the Verifier only the specs that changed; carries the rest
│   ├── mutation_score.py      deterministic mutation verdict (baseline-guarded; no model call)
│   ├── mutation_target.py     is there anything to mutate? (the gate's only legal skip)
│   ├── pipeline_metrics.py    the emission points: gate calls, spec rounds, defects, skill loads
│   ├── metrics_sink.py        fail-open bridge to firstmate's per-phase metrics CLI (no schema here)
│   ├── plugin_release.py      the executing plugin copy vs. the merged repository: `check` (the
│   │                          /avenger-run preflight: STALE stops a run, UNKNOWN is unenforced)
│   │                          and `cut`, the one release step into the plugin cache
│   ├── bypass_log.sh          break-glass logger for hooks
│   ├── hook_*.sh              Claude Code hook wrappers
│   ├── codemap.py             tree-sitter codebase map -> codebase/MOC.md
│   ├── sync_opencode.py       canonical agents -> .opencode/agents + skills symlink
│   ├── sync_runtimes.sh       run the opencode transpiler
│   ├── install.sh             versioned vendor into a target repo (NEW/UPDATE/DRIFT/GONE)
│   └── vendor_runtime.sh      legacy copy (superseded by install.sh)
├── tavern/                pixel-art fleet monitor (tavern/README.md; docs/FIRSTMATE.md for fleet layering)
├── .opencode/
│   ├── agents/            generated
│   ├── skills/            symlink -> ../skills
│   └── plugin/pipeline-gates.ts   in-session gates for opencode
├── .github/workflows/pipeline-gates.yml   CI floor
├── .no-mistakes.yaml      feature-close ship gate config (see skills/pipeline-conventions)
├── AGENTS.md              opencode conventions
├── .pre-commit-config.yaml
├── CLAUDE.md
└── README.md
```

---

## Requirements

- **Claude Code** (Skills, hooks) and/or **opencode**.
- **Python 3**, **`pytest`**, **`cosmic-ray`** (mutation), **`jq`**, and **`tree-sitter`**
  (+ `tree-sitter-python`/`-java`/`-c` for codemap) in the target repo.
- A cross-family provider: **`OPENROUTER_API_KEY`** exported (gates use OpenRouter), and/or opencode
  configured. Set **`AUTHOR_FAMILY`** (default `anthropic`) so the cross-family assertion knows the
  build family.
- **`no-mistakes`**, in **three** separate states — the feature-close ship gate (`/avenger-run` §4a)
  needs all of them, in interactive *and* `--auto` runs, and preflight checks each one:
  1. **on PATH with a runnable pipeline agent** — `no-mistakes doctor`.
  2. **the repo initialised** — `no-mistakes init` creates the bare gate repo, the post-receive hook,
     the `no-mistakes` remote and the DB record. A `.no-mistakes.yaml` existing implies **none** of
     that. `no-mistakes axi` exits 1 with `error: repo not initialized` when it is missing.
  3. **a filled-in `.no-mistakes.yaml`** at the repo root. `/pipeline-init` scaffolds it with
     `REPLACE_ME` placeholders; **fill in `commands.lint` and `commands.test` before the first run**,
     because preflight checks the content, not just the file's existence.
- **`lavish-axi`** on PATH — the plan-approval stop (§3) and the retrospective triage (§4b).
  Interactive runs only; `--auto` skips both surfaces.

Neither of those two has a fallback, on purpose: **preflight stops the run** when one is missing or
unfilled, rather than silently degrading a gate.

---

## Setup per runtime

**Claude Code**
```text
/plugin marketplace add szobonyaerik/agentic-avengers
/plugin install plan-build-verify@erik-tools
chmod +x scripts/*.sh
/pipeline-init                       # scaffold docs/features, gitignore, conventions, codemap, prereqs
```
Hooks in `hooks/hooks.json` fire the gates mid-session. Update later with `/plugin update`.

**opencode**
```text
python3 scripts/sync_opencode.py     # generate .opencode/agents + link .opencode/skills
export OPENROUTER_API_KEY=...          # the plugin's gates call OpenRouter
```
Drive agents with `@avenger-task-analyst "…"`, etc. (see `AGENTS.md`).

**Into another repo** — with update detection:
```text
scripts/install.sh /path/to/project           # vendor opencode + git floor + scripts + docs/templates (writes .avengers/manifest)
scripts/install.sh /path/to/project --check    # report what a re-install WOULD change
scripts/install.sh /path/to/project --prune    # apply + remove upstream-deleted (unmodified) files
cd /path/to/project && pre-commit install
```
Re-running classifies each file NEW / UPDATE / SAME / **DRIFT** (your local edits — skipped) / **GONE**.

---

## Keeping runtimes in sync

Canonical sources are `agents/`, `skills/`, `commands/`. After editing any of them:
```text
scripts/sync_runtimes.sh             # regenerates the .opencode/ adapter
```
`AGENTS.md` carries the opencode conventions and is hand-maintained from
`skills/pipeline-conventions/SKILL.md` — update it only when the rules themselves change.

A merged edit is still not what a run executes: phases run from the plugin release cached under
`$CLAUDE_PLUGIN_ROOT`. Release it, then restart Claude Code so the harness re-reads the cache:
```text
python3 scripts/plugin_release.py check   # is the executing copy stale against this repo? (also /avenger-run's preflight)
python3 scripts/plugin_release.py cut     # the one release step: copy the payload into the cache and re-point the install registry
```
Rule, guarantees and env overrides: `skills/pipeline-conventions` (*Closing the release loop*).

---

## codemap

Generate the map the Solution Architect and implementers read:
```text
python scripts/codemap.py . --lang python --output codebase     # -> codebase/MOC.md
```
The structural map (tree-sitter: exports, dependencies, used-by) is the whole product right now.
The optional LLM-backed one-line **purpose** backfill is **temporarily disabled**. An existing
`.codemap-manifest.json` is still read, so a purpose an earlier model-backed run cached keeps
rendering for as long as that file's hash matches; `(undocumented …)` is what a file with no
docstring/KDoc/Javadoc *and* no matching cache entry renders. No new purpose is resolved and the
manifest is never rewritten while disabled. Passing `--provider` / `--model` / `--base-url` /
`--api-key` exits with an explanation rather than silently producing an undocumented map.

---

## Notes
- Gates **fail closed** — missing key, unreachable model, non-JSON verdict, or same-family model stops.
- The mutation gate feeds cosmic-ray's `dump` (survivors) to the model rather than parsing it, so it
  survives tool version changes (one model call per phase). Score = 1 − survival rate.
- Break-glass is the only override, and it is never silent (logged + shown + recorded in handover).
- Skills are symlinked into `.opencode/` (`SKILL.md` is identical). On Windows, copy instead.
- `hook_verifier.sh`, `gate_ci.sh`, the opencode plugin, and `cosmic-ray.toml` assume code under
  `src/`; change that to your layout if it isn't.
- License: MIT.
