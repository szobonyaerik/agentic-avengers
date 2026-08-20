---
title: AVENGERS.md — transformation brief for agentic-avengers
type: cold-session-playbook
purpose: Historical transformation brief (§1-§8, executed) plus the still-current project setup guide (§9). SUPERSEDED where it disagrees with pipeline-flow.png — see §0.1. Read the repo first; skills/pipeline-conventions is the canonical rulebook, not this file.
runtimes: Claude Code + opencode (Copilot is being removed)
mutation: cosmic-ray (replaces mutmut)
---

# AVENGERS — transformation brief

You are a cold session working inside **agentic-avengers**, the private, high-capability sibling of
the corporate `klm-agentic-pipeline`. Evolve this repo to the **target flow** here, in branches, in
the §6 order, verifying each block. Do not break the invariants in §7. When you are done, §9 is the
hands-on setup to configure it for a real project.

## 0.1 SUPERSEDED — read this first

§1-§8 record a transformation that has already been executed. Two later refactors changed its
decisions: `pipeline-flow.png` (2026-07-28) and, the same day, a **convergence onto the sibling
`klm-agentic-pipeline`**, which is now the reference for pipeline semantics. Where this file and
`skills/pipeline-conventions/SKILL.md` disagree, **the skill wins**. What changed:

| §  | This file says | Now |
|----|----------------|-----|
| §3, §4D, §7 | A separate **Test-Author** owns `tests/`; implementers are banned from writing tests | **Deleted.** The implementer writes tests and code test-first via `skills/tdd`, which carries all three `work_kind` modes inline |
| §3, §7 | Tests are a **frozen contract**, locked RED *before* implementation | **Locked-after-verify**: the implementer owns them until the Verifier passes the phase; after that, weakening one needs re-verification, adding is allowed |
| §3, §F | The Verifier is a hook that triages a failing suite | The Verifier is an **agent** (`agents/avenger-verifier.md`) that persists **`verdict.json`** with per-finding break-glass waivers. What it does there is the row below |
| §F | Gate models run in hooks and in CI | **Model gates run in chat; mechanical gates run in hooks/CI**, which only check committed artifacts. The spec gate is the one exception |
| §I | Mutation is always blocking | **`advisory` by default**: `MUTATION_POLICY` = `advisory` (runs, reports, never blocks) \| `enforce` \| `off`. An extra signal, not a dedicated reader for gamed tests; there is none |
| §3.2 | Three test-author skills (`tdd-red-author`, `migration-`, `brownfield-`) | One `skills/tdd`; the other three are deleted. Refactor means baseline-first parity, not a preserve/change partition |
| §1, §4B, §4C, §4F | Every requirement carries **paired pass/fail acceptance criteria**, and the Verifier traces every `R<n>.<k>.<m>` to a passing test | **Tiered `binding:`** per requirement — `e2e` (carried by a shared journey, never its own test) \| `integration` (its own test, and the spec says why an e2e cannot see it) \| `none` (no test). Suite size follows risk, not id count; coverage is judged per `binding:`, never per id |
| §4C | The spec-review gate reads for correctness only, and re-reviews the whole spec each time | The **spec gate** carries the **only cost gate** (`scripts/subprocess_check.py`, mechanical, both modes, on every spec write via `hook_spec_gate.sh`), and a spec already approved **and** implemented is re-gated on its **diff** only |
| §3, §3.2, §4D, §H | Artifacts are named but nobody governs **who reads them**: `work_kind` is looked up in `task-analysis.md`, `handover.md` is the phase's whole record, and `test-mapping.md` carries everything about the tests | **The document read path** — cost is `size x reads x turns resident`, so the read directives changed and nothing was deleted. `work_kind` rides in the spec's own frontmatter; `handover.md` is a **contract card under a hard byte cap** (rest → `handover-archive.md`, which no stage reads); the spec gate opens `overview.md`'s `## Contracts and Decisions` header only; `test-mapping.md` is the table (evidence → `test-evidence.md`, on route-back); a verified phase's specs leave the path; **every document declares `readers:`**. `scripts/doc_read_path.py` is the table and the two checks |
| §3, §4C, §7 | The quality wall is **two** model gates — an automated Fidelity Gate *and* an automated spec-review — composed in order | **One gate** (`scripts/hook_spec_gate.sh`), plus one human. The two asked overlapping questions of one document at one moment and a spec once passed one and failed the other on **byte-identical text**. It runs **observe → triage → decide**: the observe pass reports everything and has no verdict to give, a cheaper triage pass classifies against a **CLOSED** four-item blocking set, and `scripts/spec_gate_triage.py` derives the verdict — **no model decides whether a spec is blocked**. `spec_gate` replaces `fidelity_verdict`; `review_status` now means only the human sign-off |
| §4C | The gate answers "is everything covered?" across seven dimensions, and *"when unsure … choose NO-GO"* | **Report everything, then filter.** The tie-break is inverted: **when unsure, it is a note**, and **notes never block** — they land in `spec-notes.md`, read once by the implementer. Exactly four things block: missing requirement · contradiction · untestable criterion · unhandled critical edge case. The old framing was measured: spec 8.0 grew 25k → 51k characters and 8.2 grew 40k → 57k across four rejected rounds, because the only response available to a rejection is more text |
| §1, §4B | Nothing anywhere bounds a spec's size or its requirement count | **12 requirements per spec** (`scripts/requirement_cap.py`, `SPEC_REQUIREMENT_MAX`), counted **before any model call**, as a **SPLIT trigger** — over the cap the spec divides into siblings under the same phase. **No gate ever rejects a spec for being large**: a rejection for size is one more thing to grow around |
| §3, §4C | The spec writer learns what the gate blocks only by being rejected by it | **The writer is primed from the gate's own rubric before it writes**, from ONE source (`scripts/spec_rubric.py`, delivered by `scripts/hook_spec_rubric.sh` at `SubagentStart`). Nothing in the brief is authored: every line is data read out of the deciding module (`spec_gate_triage.BLOCKING`, the requirement cap) or a gate-prompt section lifted verbatim, and the agent definition no longer restates any of it - a drifted second copy is worse than no priming. Measured: phase 9 spent **fourteen** rounds on its first spec and one, three and one on the next three, and nothing carried that learning to the next phase |
| §3, §4E | A handover's forward-looking claims are prose, and nothing is owed an answer | **Carried items.** The card's existing `## Open items` table is widened to hold forward-looking claims (`FWD-<n>`) beside findings carried at the attempt cap (`OBS-<n>`), and made binding by `scripts/carried_items.py`: a phase states what it carries (**silence is not `none`**) and the next phase answers every row - `built`, `tested`, or `declined` with a stated reason - before it can close. Measured: phase 8 predicted, verbatim, the defect phase 9 shipped |
| §3, §4E | A change to a verified phase re-opens the phase | **Amendments** (`scripts/amendments.py`): a post-verification change names the requirement ids it touches and **only those re-verify**, carrying their own evidence. Batched at phase close; **security is never batched**. A verdict reads *verified at attempt N, plus amendments A1..An*. One measured phase spent verification rounds 3 through 8 on exactly this gap |
| §3, §F | The Verifier is the phase's whole judgement, looping until clean | **Narrowed to TWO jobs** — coverage per `binding:`, and adversarial execution on secrets/resource lifetimes/concurrency invariants — because only 3 of its 46 measured findings were user-visible defects nothing else could find, and **12 (26%) were bookkeeping about its own stamps**, now `scripts/verifier_precheck.py`. A third job, reading a green suite for gamed tests, **was removed with the cross-family reading pass and nothing inherits it** (what that leaves uncovered is named in `CLAUDE.md` §4). Both surviving jobs are recorded through `scripts/verifier_evidence.py`: a pass with no transcript is refused. The loop is **capped at 3 attempts** (`verifier_attempts.py`): **16 of 20 re-attempts were the Verifier routing back to itself** |
| §3.2, §H | Agents are **told** to load the skills that carry their procedure | **Skills are delivered, not requested — pointer plus OBSERVED load.** What each stage requires is **derived from its own `agents/<stage>.md`** (`scripts/skill_contract.py`), not restated in a table; the load is **observed** by `scripts/hook_skill_load.sh` into the per-phase metrics record, never self-reported. `scripts/hook_skills.sh` delivers on `SubagentStart`: under `SKILL_INJECT_MAX_BYTES` (8192) the body is injected and the injection is recorded as the load, over it the stage gets a **pointer** and opening the file is what records it. `required_skills.py audit` fails the phase on a required skill with no observed load, at handover and in CI. A required skill that is missing is a **loud blocker**, never a silent fallback — `docs/lessons/` shipped with a complete procedure and zero invocations under the old shape |

Deliberate differences from `klm-agentic-pipeline` are listed in `README.md` § *Relationship to
`klm-agentic-pipeline`*, which also names the two whose status against the sibling is unconfirmed.
That list is not a completeness claim.

Unchanged from this file: multi-spec phases and the `R<n>.<k>.<m>` ID scheme, cross-family fail-closed
gates, break-glass, codemap, and the versioned install in §9.

## 0. How to use this file
1. Read the current repo: `README.md`, `CLAUDE.md`, `AGENTS.md`, `agents/`, `skills/`, `commands/`,
   `prompts/`, `scripts/`, `hooks/`, `.opencode/`, `.github/`, `.claude-plugin/`.
2. Confirm the as-is in §1 still matches (written against a 27-commit snapshot).
3. Execute §3–§5 deltas in the §6 order; smoke-test after each block.
4. This repo is **canonical-source driven**: edit `agents/`, `skills/`, `commands/`, `prompts/`,
   `scripts/`, `hooks/` — then regenerate the opencode adapter with the sync script. Never hand-edit
   `.opencode/`.

## 1. Current state (as-is)
- **Tri-runtime**: Claude Code (plugin), opencode (generated `.opencode/`), GitHub Copilot (generated
  `.github/agents` + prompts).
- **Quality wall = automated Fidelity Gate only**: N isolated reviewers → GO / REVIEW / NO-GO via
  `gate_runner.py` + `prompts/fidelity-rubric.md` on a cross-family model.
- **Single test-author**: paired pass/fail RED tests, locked. No migration/refactor modes.
- **Per-phase build+verify**: Test-Author → Implementer → Verifier (runs suite) + Mutation (**mutmut**)
  → Breaker on critical paths.
- **Enforcement**: gates fire mid-session via `hooks/hooks.json` (Claude Code) and
  `.opencode/plugin/pipeline-gates.ts` (opencode); git floor (`pre-commit` +
  `.github/workflows/pipeline-gates.yml`) backstops. All call `gate_runner.py` on a cross-family
  OpenRouter model, decorrelated from the author. Gates fail closed.
- One spec per phase/slice. Cartographer produces `codebase/MOC.md`.

## 2. Target state (to-be) — the decisions
- **Runtimes: Claude Code + opencode only.** Remove the Copilot *runtime adapter*. **Keep the GitHub
  Actions CI** (`.github/workflows/pipeline-gates.yml`) — it is the git floor, not a Copilot artifact.
- **Quality wall = automated Fidelity Gate AND human grill-me review** (both, composed).
- **Multi-spec phases**: a phase holds one or more numbered specs `<n>.<k>`; requirement IDs
  `R<n>.<k>.<m>`; the **Verifier runs once per phase**, after every spec in it is green.
- **Three test-author modes**: greenfield · migration · refactor (brownfield). Plus **e2e-author**,
  once per feature at close — not selected by `work_kind`.
- **Tests are integration-level by default**; `narrow` needs a written justification. Requirements are
  pitched at a **seam**, enforced from the Spec Writer down through both spec gates.
- **Mutation via cosmic-ray** (replaces mutmut): session-based, **diff-scoped in every mode**,
  baseline-guarded, with a **deterministic verdict** at `MUTATION_MIN_SCORE` (default 0.85 — not 100%).
  Zero model calls on a clean phase; one call to interpret survivors only when below threshold.
- **codemap.py** (real, tree-sitter, multi-language) replaces the old cartographer.
- **Break-glass bypass**: logged + visible, never silent.
- **Versioned install with update detection**: `scripts/install.sh <target> [--check]` vendors the
  opencode + git-floor surface with a manifest, detecting new/updated/drifted/removed files; Claude
  Code updates natively via `/plugin`.
- Cross-family verifier (gate model family ≠ the author's); gates fail closed; tests frozen.

## 3. The target flow (self-contained spec)

```
PLAN
  task-analyst        → docs/features/<feat>/task-analysis.md   (loads grill-me for scope;
                                                                 sets work_kind: greenfield|migration|refactor)
  solution-architect  → docs/features/<feat>/overview.md        (shape: components/boundaries/contracts)
  implementation-planner → docs/features/<feat>/plan.md         (ordered phases; each phase = 1+ candidate specs;
                                                                 sequencing only, NO file-level code)
  spec-writer         → docs/features/<feat>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md

QUALITY WALL (per spec, both gates)         [REVISED — see §0.1, rows §3/§4C/§7 and §4C]
  1. Automated Fidelity Gate (gate_runner + prompts/fidelity-rubric.md, cross-family):
       NO-GO → route back to spec-writer ;  GO / REVIEW → proceed
  2. Human grill-me review (skills/grill-me + skills/spec-review-checklist):
       reviewer interrogated one question at a time; on success sets `review_status: approved`,
       else routes back. Tests do not lock until approved.

BUILD & VERIFY (looped per phase)          [REVISED — see §0.1]
  backend/frontend implementer → tests/... + test-mapping.md + src/...
       red → green, one vertical slice at a time (skills/tdd); mode by work_kind (§3.2)
  [repeat for each spec in the phase]
  avenger-verifier (per phase, cross-family) → suite + coverage trace + bounded TEST REVIEW
       → verdict.json ; PASS LOCKS THE PHASE SUITE
  mutation (cosmic-ray; MUTATION_POLICY: off (default) | advisory | enforce)
  breaker (critical paths only) → counterexample → implementer adds the test, fixes the code
  handover → docs/features/<feat>/phases/<n>-<slug>/handover.md

SHIP
  commit (pre-commit floor) → PR (CI floor). Break-glass overrides logged + visible.
```

### 3.1 The composed quality wall
Spec frontmatter gains `review_status: pending|approved` and `fidelity_verdict: GO|REVIEW|NO-GO`.
The Fidelity Gate is the cheap machine pre-filter (keep the existing N-isolated-reviewers design),
firing automatically on spec write; grill-me is the human judgment pass that follows, human-invoked
(`/spec-review <spec>`), never auto. A spec reaches the Test-Author only when `fidelity_verdict !=
NO-GO` **and** `review_status: approved`.

### 3.2 The three test-author modes (set by `work_kind`)
- **greenfield** (`skills/tdd-red-author`): ≥1 positive + ≥1 negative RED test per `R<n>.<k>.<m>`,
  each driven through the requirement's **seam**; confirm all RED; lock.
- **migration** (`skills/migration-test-author`): inventory → assess coverage (flag gaps) → port
  without changing assertions (parity) → characterize gaps → freeze. Mutation proves the inherited
  suite catches regressions. **Exempt from the integration default** — parity outranks it, so ported
  tests keep their original level.
- **refactor / brownfield** (`skills/brownfield-test-author`, NEW — pioneer it here): **partition the
  blast radius.** Characterize-and-freeze the surrounding behavior that must NOT regress; write fresh
  RED tests for the behavior that IS changing; the spec declares which requirements are *preserve* and
  which are *change*. Surface pre-existing failures; never adopt or fix-creep into them.
  (Diff-scoped mutation is no longer this mode's special case — see §3.3, every mode is scoped now.)

### 3.3 Test level: integration by default
Every test drives its requirement through a **seam** — the public entry point a caller actually uses
(HTTP handler, service method, CLI) — with real collaborators wired up. Mock only what crosses a trust
or cost boundary (third-party API, LLM, vault).

`test-mapping.md` carries a `level` column: `integration` (default) · `e2e` · `narrow`. A **`narrow`
test requires a written justification** and is permitted only when a requirement has no reachable seam.
Requirements with no integration surface of their own (pure parsers, mappers, helpers) get **no
dedicated test** — they are covered transitively; if that hides a blind spot, the mutation gate names it.

Why: tests bound to internal structure are the ones rewritten on every refactor, which is what made the
frozen contract expensive. The upstream half of this rule lives in the Spec Writer and both spec gates —
a requirement pitched *below* the seam mints a narrow test no matter what the Test-Author intends, so
"observable at a seam" is now part of the Testability bar.

### 3.4 Feature-level e2e (`skills/e2e-author`)
**1-3 tests, 5 is a hard ceiling**, written **once per feature after the final phase is green** — not
per phase, and not selected by `work_kind`. They prove the assembled system delivers the goal in
`overview.md`, trace to that goal rather than a spec id (the single exception to "no spec id → no
test"), live in `tests/e2e/<feature>/`, and are recorded in `e2e-mapping.md`. Excluded from the
mutation gate and from the per-edit verifier hook; they run at feature close and in CI.

## 4. Deltas to execute

### A. Remove the Copilot runtime adapter (keep CI)
- Delete `scripts/sync_copilot.py`; `.github/agents/`, `.github/prompts/`,
  `.github/copilot-instructions.md`. **Keep** `.github/workflows/pipeline-gates.yml`.
- `scripts/sync_runtimes.sh` → run only the opencode sync. Strip Copilot from README + the
  component-locations table + setup sections.

### B. Multi-spec phases + ID scheme
- `implementation-planner`: phases = independently verifiable slices, each listing candidate specs
  `<n>.<k>`; sequencing only, no file-level code.
- `spec-writer`: one-or-more numbered specs per phase at
  `docs/features/<feat>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`; IDs `R<n>.<k>.<m>`; paired
  pass/fail acceptance criteria; add the §3.1 frontmatter fields.
- `skills/pipeline-conventions`: update layout, ID scheme, "verifier runs once per phase".
- Update path assumptions in `hooks/`, `gate_ci.sh`, and `hook_verifier.sh` — the latter derives the
  phase slug from the written artifact path (`*/phases/<n>-<slug>/…`) rather than guessing.
  `.opencode/plugin/pipeline-gates.ts` no longer carries path logic of its own: it is a thin adapter
  that runs the same `scripts/hook_*.sh`, so routing lives in one place.

### C. Add grill-me to the quality wall
- New `skills/grill-me/SKILL.md` (interview one question at a time, each with a recommendation;
  resolve upstream deps first; explore the codebase when possible). Used by `task-analyst` (scope) and
  the spec-review step.
- New `skills/spec-review-checklist/SKILL.md` (the bar the reviewer defends: every requirement single
  + verifiable + ID'd + paired criteria; no contradiction with overview or a prior phase's delivered
  contract; migration specs name existing tests + parity; refactor specs declare preserve-vs-change).
- New `commands/spec-review.md` (+ opencode equivalent): runs grill-me against a spec and, on success,
  sets `review_status: approved`.
- `spec-writer`: do not hand off to `test-author` until `review_status: approved`. Keep the automated
  Fidelity Gate as-is, now firing per spec; NO-GO routes back.

### D. Three test-author modes
- `task-analyst`: capture `work_kind: greenfield|migration|refactor`; for migration record the
  existing-test situation; for refactor record preserve-vs-change intent.
- Skills: keep/clean `tdd-red-author`; add `migration-test-author`; add `brownfield-test-author`
  (§3.2); add `e2e-author` (§3.4). Mutation scope is no longer a per-mode concern — the gate
  diff-scopes every mode automatically (§I).
- `test-author` agent: select mode by `work_kind`; keep all three hard boundaries; apply the
  integration-level default (§3.3) across every mode except migration (parity outranks it).

### E. codemap
- Drop the real `scripts/codemap.py` in (tree-sitter; Python rich, Java, C; C++ needs a `cpp` spec).
  Output `codebase/MOC.md`. LLM purposes are **temporarily disabled**, so the map is structural only
  and the provider flags exit with an explanation:
  `python scripts/codemap.py . --lang <python|java|c> --output codebase`.
- Replace old `@codebase-cartographer` references; update the handover staleness check to
  `codebase/MOC.md`.

### F. Verifier: per-phase + cross-family + fail-closed
- Verifier fires **after every spec in a phase is green** (not per spec): full phase suite + traces
  every `R<n>.<k>.<m>` to a passing test + cosmic-ray mutation, via `gate_runner.py` on a family
  **decorrelated from the implementer**. Triage routes: code → implementer; test / surviving mutant /
  coverage gap → test-author. Fail closed.
- Assert cross-family in `gate_runner.py`/`gate_ci.sh`: gate model family ≠ author family.

### I. Swap mutmut → cosmic-ray
- Prereqs: add `cosmic-ray`, drop `mutmut`.
- Add a repo-root `cosmic-ray.toml` (template in §9). **Every mode is diff-scoped** (not just
  refactor): the gate appends a `[cosmic-ray.filters.git-filter]` section naming the diff base and runs
  `cr-filter-git`, which skips mutants outside the phase's changed **lines**.
- The session flow in `scripts/hook_mutation.sh` / `.opencode/plugin/pipeline-gates.ts` / `gate_ci.sh`:
  ```
  cosmic-ray baseline <scoped.toml>                  # suite must be green FIRST — see below
  cosmic-ray init     <scoped.toml> session.sqlite
  cr-filter-git --config <scoped.toml> session.sqlite # skip mutants outside the diff
  cosmic-ray exec     <scoped.toml> session.sqlite
  python3 scripts/mutation_score.py --min-score $MUTATION_MIN_SCORE session.sqlite
  cosmic-ray dump session.sqlite                     # survivors → model, only when below threshold
  ```
- **The verdict is deterministic, not a model call and not `cr-rate`.** `scripts/mutation_score.py`
  computes it; the model is called *only* when the score is already below threshold, purely to turn
  each survivor into the missing test case. A clean phase costs zero gate tokens.
- **Why not `cr-rate --fail-over`** (all three verified against cosmic-ray 8.4.6, not assumed):
  1. `WorkResult.is_killed` is `test_outcome != SURVIVED`, and filtered jobs are stored with
     `test_outcome=None` → **skipped mutants count as kills**; a fully-filtered session scores 1.0.
  2. `survival_rate()` returns 0 for an empty session → a broken `module-path` reports a clean run.
  3. `--fail-over 0` is falsy in `if fail_over and ...` → demanding a perfect score silently disables
     the check. (Confirmed live: `cr-rate --fail-over 0` exits 0 on a 100%-survival session.)
- **Baseline guard.** A mutant counts as killed whenever the test command fails, so a suite that is
  already broken scores a **perfect 1.0**. The gate runs `cosmic-ray baseline` first and refuses to
  score until the unmutated suite is green. (Observed: with an import error in the suite, all 7 fixture
  mutants reported `killed`.)
- **Threshold `MUTATION_MIN_SCORE`, default 0.85 — deliberately not 1.0.** Demanding zero survivors is
  what turns the Test-Author into a mutant-farming loop and multiplies narrow tests bound to internals.
  Below threshold → survivors route to the test-author. Python/Java fail-closed (cosmic-ray for Python,
  PIT for Java), C++ advisory.
- Fail closed: a cosmic-ray run that errors, a failing baseline, or a session that cannot be scored
  honestly (no mutants generated, nothing actually tested) stops.

### G. Break-glass bypass
- `gate_ci.sh` + hooks/plugin: `GATE_BYPASS="reason"` overrides a failing gate, appends
  who/when/which-gate/why to `gate-overrides.log`, surfaces a visible "bypassed" state, records it in
  the phase `handover.md`. Never silent.

### J. Versioned install + update detection
- Add `scripts/install.sh <target> [--check] [--prune]` (provided separately). It vendors the
  runtime-agnostic + opencode surface (`.opencode/`, `hooks/`, gate scripts, `codemap.py`,
  `cosmic-ray.toml`, `.pre-commit-config.yaml`, `.github/workflows/pipeline-gates.yml`, `AGENTS.md`)
  into a target repo and writes `.avengers/manifest` + `.avengers/version`.
- 3-way detection per file: NEW / UPDATE / SAME / **DRIFT** (locally modified → skipped, never
  clobbered) / **GONE** (upstream removed). `--check` = dry-run report; `--prune` removes
  upstream-deleted files you haven't modified.
- Claude Code updates natively: bump `version` in `.claude-plugin/plugin.json`, then `/plugin update`.
  Retire the old `vendor_runtime.sh` copilot path; `install.sh` supersedes the opencode vendor with
  update-awareness.

### H. Docs
- `README.md`: two runtimes; the §3 flow; quality wall = fidelity + grill-me; three modes; cosmic-ray;
  codemap; break-glass; versioned install. `CLAUDE.md` / `AGENTS.md`: match `pipeline-conventions`.

## 5. opencode / Claude Code specifics
- **Claude Code** consumes canonical sources natively (plugin: `agents/`, `skills/`, `commands/`,
  `hooks/`); conventions in `pipeline-conventions` + `CLAUDE.md`.
- **opencode** is generated (run the sync after editing canonical sources); conventions in `AGENTS.md`;
  in-session gates via `.opencode/plugin/pipeline-gates.ts`.
- Both fire gates mid-session on a cross-family OpenRouter model; the git floor backstops both.
  `OPENROUTER_API_KEY` exported (and a GH Actions secret for CI).

## 6. Execution order
1. Branch.
2. **A** — remove Copilot adapter (keep CI); regenerate opencode; confirm both runtimes load.
3. **B** — conventions + multi-spec phases + path updates.
4. **D** — three test-author modes (incl. new brownfield skill).
5. **C** — grill-me quality wall (compose with fidelity gate).
6. **E** — codemap swap.
7. **F** — verifier per-phase + cross-family.
8. **I** — mutmut → cosmic-ray (config + gate flow + rubric).
9. **G** — break-glass.
10. **J** — versioned install + update detection.
11. Regenerate `.opencode/`; update **H** docs.
12. **Smoke test**: run one **greenfield**, one **migration**, one **refactor** feature end to end on
    *both* Claude Code and opencode. Confirm: both quality-wall gates fire; verifier runs once per
    phase on a decorrelated family; cosmic-ray produces a survival rate and survivors route back; a
    deliberate `GATE_BYPASS` logs + shows; `R<n>.<k>.<m>` paths resolve; `scripts/install.sh <tmp>
    --check` reports cleanly on a fresh target and shows DRIFT on a hand-edited file.

## 7. Invariants — do not break
- **Tests lock at the Verifier** (revised — §0.1) — the implementer owns `tests/` during its own
  build and writes them test-first; once the Verifier passes the phase the suite is locked and later
  gates may only demand *added* tests. The Verifier's test review is not optional: it is the only
  independent judgement on a suite whose author also wrote the code.
- **Gates fail closed** — missing key, unreachable model, non-JSON verdict, or no verdict → stop.
- **Cross-family** — verifier/gate model family ≠ author family.
- **Canonical-source driven** — edit canonical; regenerate `.opencode/`; never hand-edit adapters.
- **Artifacts on disk** with YAML frontmatter — the chain survives cold sessions.

## 8. Known / experimental
- **brownfield mode is new** — validate the preserve-vs-change partition + diff-scoped cosmic-ray on a
  real refactor before trusting it; prove it here before any back-port to `klm-agentic-pipeline`.
- Mutation: Python = cosmic-ray; Java = PIT; C/C++ = mull/Dextool — wired only when that stack appears.

---

## 9. Set up & configure for your project (do this now)

Once §4 is done (or to configure the current repo), wire it to a project.

### 9.1 Prerequisites
```bash
# Cross-family gate provider
export OPENROUTER_API_KEY=sk-or-...            # add to your shell rc; and as a GH Actions secret for CI

# Python tooling in the target project
pip install pytest cosmic-ray                  # mutation = cosmic-ray (not mutmut)
pip install tree-sitter tree-sitter-python     # + tree-sitter-java / tree-sitter-c for those stacks
brew install jq                                # gate scripts use jq
pip install pre-commit                         # local git floor
```

### 9.2 Claude Code
```bash
/plugin marketplace add szobonyaerik/agentic-avengers
/plugin install plan-build-verify@erik-tools
chmod +x scripts/*.sh
/pipeline-init                                 # scaffold docs/features, gitignore, conventions
```
Hooks in `hooks/hooks.json` fire the gates mid-session. Update later with `/plugin update`.

### 9.3 opencode
```bash
python3 scripts/sync_opencode.py               # generate .opencode/agents + link skills
export OPENROUTER_API_KEY=...                   # the plugin's gates call OpenRouter
```
Drive agents with `@task-analyst "…"` (see `AGENTS.md`).

### 9.4 Configure cosmic-ray for the project
Create `cosmic-ray.toml` at the project root, pointing at your code and test command:
```toml
[cosmic-ray]
module-path = "src"                # your package/dir under test (string OR list of paths/files)
timeout = 30.0
excluded-modules = []
test-command = "pytest -x -q --ignore=tests/e2e"   # e2e is feature-level; not a mutation signal

[cosmic-ray.distributor]
name = "local"
```
Sanity check once:
```bash
cosmic-ray baseline cosmic-ray.toml \
  && cosmic-ray init cosmic-ray.toml s.sqlite \
  && cosmic-ray exec cosmic-ray.toml s.sqlite \
  && python3 scripts/mutation_score.py --json s.sqlite
```
This is the **base** config; the gate never runs it as-is. For **every** work_kind it copies this file,
appends a `[cosmic-ray.filters.git-filter]` section naming the diff base, and runs `cr-filter-git` so
only mutants on the phase's changed lines are scored.

Score with `scripts/mutation_score.py`, not `cr-rate` — `cr-rate` counts skipped mutants as kills,
reports 0% survival for an empty session, and ignores `--fail-over 0`. Tune with `MUTATION_MIN_SCORE`
(default `0.85`) and `MUTATION_BASE` (default: merge-base with the default branch).

### 9.5 Generate the codemap
```bash
python scripts/codemap.py . --lang python --output codebase     # → codebase/MOC.md (structural only; LLM purposes disabled)
```

### 9.6 Install into a target project — with update detection
```bash
scripts/install.sh /path/to/project              # vendor opencode + git floor + scripts (writes .avengers/manifest)
scripts/install.sh /path/to/project --check      # later: report what a re-install WOULD change
scripts/install.sh /path/to/project --prune      # apply, and remove files upstream deleted (unmodified only)
```
Re-running classifies each file NEW / UPDATE / SAME / **DRIFT** (your local edits — skipped) / **GONE**
(upstream removed). Claude Code itself updates via `/plugin update`; `install.sh` covers the rest.
Then enable the local floor: `cd /path/to/project && pre-commit install`.

### 9.7 Verify end to end
Run one greenfield feature: `@task-analyst "add a health endpoint"` → walk the chain. Confirm the
spec gate fires and stamps `spec_gate: approved`, `/spec-review` grills you and flips
`review_status: approved`, tests lock, the
verifier runs once for the phase on a different family, cosmic-ray reports a survival rate, and a
deliberate `GATE_BYPASS="testing" git commit` logs to `gate-overrides.log`. (Typing that yourself, the
inline prefix is fine. An agent under `/avenger-run --auto` must pass the reason from a file instead —
`skills/pipeline-conventions`, the prose-off-the-command-line rule.)
