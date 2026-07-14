---
title: AVENGERS.md — transformation brief for agentic-avengers
type: cold-session-playbook
purpose: Refactor this repo from its current flow to the target flow below, then configure it for a project (§9). Self-contained — you need only this file and the repo. Read the repo first, then execute the deltas in order, verifying each.
runtimes: Claude Code + opencode (Copilot is being removed)
mutation: cosmic-ray (replaces mutmut)
---

# AVENGERS — transformation brief

You are a cold session working inside **agentic-avengers**, the private, high-capability sibling of
the corporate `klm-agentic-pipeline`. Evolve this repo to the **target flow** here, in branches, in
the §6 order, verifying each block. Do not break the invariants in §7. When you are done, §9 is the
hands-on setup to configure it for a real project.

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
- **Three test-author modes**: greenfield · migration · refactor (brownfield).
- **Mutation via cosmic-ray** (replaces mutmut): session-based, diff-scopable for refactor mode; one
  model call per phase interpreting survivors.
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

QUALITY WALL (per spec, both gates)
  1. Automated Fidelity Gate (gate_runner + prompts/fidelity-rubric.md, cross-family):
       NO-GO → route back to spec-writer ;  GO / REVIEW → proceed
  2. Human grill-me review (skills/grill-me + skills/spec-review-checklist):
       reviewer interrogated one question at a time; on success sets `review_status: approved`,
       else routes back. Tests do not lock until approved.

BUILD & VERIFY (looped per phase)
  test-author  → tests/... + test-mapping.md   (mode by work_kind — §3.2; LOCKED contract)
  backend/frontend-architect → src/...          (implement to locked tests; never edit tests)
  [repeat for each spec in the phase]
  verifier (per phase, cross-family) → full phase suite + coverage trace + cosmic-ray mutation
  breaker (critical paths only) → counterexample → new locked test
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
- **greenfield** (`skills/tdd-red-author`): ≥1 positive + ≥1 negative RED test per `R<n>.<k>.<m>`;
  confirm all RED; lock.
- **migration** (`skills/migration-test-author`): inventory → assess coverage (flag gaps) → port
  without changing assertions (parity) → characterize gaps → freeze. Mutation proves the inherited
  suite catches regressions.
- **refactor / brownfield** (`skills/brownfield-test-author`, NEW — pioneer it here): **partition the
  blast radius.** Characterize-and-freeze the surrounding behavior that must NOT regress; write fresh
  RED tests for the behavior that IS changing; the spec declares which requirements are *preserve* and
  which are *change*. **Scope cosmic-ray to the changed surface** (diff-scoped). Surface pre-existing
  failures; never adopt or fix-creep into them.

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
- Update path assumptions in `hooks/`, `.opencode/plugin/pipeline-gates.ts`, `gate_ci.sh`,
  `hook_verifier.sh` (they match `*/src/*` and per-phase paths).

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
  (§3.2). Add a `mutation-scope` note: refactor mode runs cosmic-ray diff-scoped.
- `test-author` agent: select mode by `work_kind`; keep all three hard boundaries.

### E. codemap
- Drop the real `scripts/codemap.py` in (tree-sitter; Python rich, Java, C; C++ needs a `cpp` spec).
  Output `codebase/MOC.md`. avengers is private, so the codemap MAY use LLM purposes via
  OpenRouter/Ollama (not forced `--no-llm`):
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
- Add a repo-root `cosmic-ray.toml` (template in §9). Refactor mode generates a **diff-scoped** config
  (module-path/filters limited to the changed files).
- In `gate_runner.py` / `hook_verifier.sh` / `.opencode/plugin/pipeline-gates.ts` / `gate_ci.sh`,
  replace the mutmut call with the cosmic-ray session flow:
  ```
  cosmic-ray init cosmic-ray.toml session.sqlite
  cosmic-ray exec cosmic-ray.toml session.sqlite
  cosmic-ray dump session.sqlite        # survivors → fed to the model (one call per phase)
  cr-rate session.sqlite                # quick survival-rate score for the threshold check
  ```
  Keep the "send the report to the model, don't parse it" philosophy — feed `dump` output to
  `prompts/mutation-interpret.md`. Update that rubric to cosmic-ray's shape (survivors as job-id +
  location + operator). Mutation score = 1 − survival rate; below threshold → route survivors to the
  test-author. Python/Java fail-closed (cosmic-ray for Python, PIT for Java), C++ advisory.
- Fail closed: a cosmic-ray run that errors or yields no verdict stops.

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
- **Tests are a frozen contract** — only the Test-Author writes `tests/`; fixes go to code.
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
module-path = "src"                # your package/dir under test
timeout = 30.0
excluded-modules = []
test-command = "pytest -x -q"

[cosmic-ray.distributor]
name = "local"
```
Sanity check once: `cosmic-ray init cosmic-ray.toml s.sqlite && cosmic-ray exec cosmic-ray.toml s.sqlite && cr-rate s.sqlite`.
For **refactor** work the pipeline generates a diff-scoped copy of this config (module-path limited to
the changed files) so mutation only targets the changed surface.

### 9.5 Generate the codemap
```bash
python scripts/codemap.py . --lang python --output codebase     # → codebase/MOC.md (LLM purposes OK on the private profile)
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
Fidelity Gate fires, `/spec-review` grills you and flips `review_status: approved`, tests lock, the
verifier runs once for the phase on a different family, cosmic-ray reports a survival rate, and a
deliberate `GATE_BYPASS="testing" git commit` logs to `gate-overrides.log`.
