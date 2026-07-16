# agentic-avengers · plan-build-verify

A spec-driven, test-first agentic development pipeline that runs under **Claude Code** and
**opencode**. Specialised agents plan a feature; a **composed quality wall** (an automated fidelity
gate *and* a human grill-me review) decides whether each spec is ready; locked RED tests are written
before code; and every phase is verified once — by a fresh cross-family model plus cosmic-ray mutation
— before it ships.

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
recorded in the phase `handover.md`.

---

## How it works

`Plan → Quality wall → (Build & verify loop ×phases) → Ship`.

```
PLAN
  task-analyst        -> docs/features/<feat>/task-analysis.md   (scope via grill-me; sets work_kind)
  solution-architect  -> docs/features/<feat>/overview.md
  implementation-planner -> docs/features/<feat>/plan.md         (phases; each = 1+ candidate specs <n>.<k>)
  spec-writer         -> docs/features/<feat>/phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md

QUALITY WALL (per spec, both gates)
  1. Automated Fidelity Gate (cross-family)   NO-GO -> back to spec-writer
  2. Spec-review -> sets review_status: approved
       HITL:      /spec-review <spec>            (grill-me, one question at a time)
       Automated: /spec-review <spec> --auto     (cross-family AI reviewer; SPEC_REVIEW_MODE=auto = hands-off)
  Tests lock only when fidelity_verdict != NO-GO AND review_status: approved.

BUILD & VERIFY (looped per phase)
  test-author  -> tests/ + test-mapping.md   (mode by work_kind: greenfield | migration | refactor)
  backend/frontend-architect -> src/          (implement to locked tests; never edit tests)
  [repeat for each spec in the phase]
  verifier (once per phase, cross-family) -> full suite + R<n>.<k>.<m> trace + cosmic-ray mutation
  breaker (critical paths) -> counterexample -> new locked test
  handover -> docs/features/<feat>/phases/<n>-<slug>/handover.md

SHIP
  commit (pre-commit floor) -> PR (CI floor). Break-glass overrides logged + visible.
```

### The three test-author modes (set by `work_kind`)
- **greenfield** (`skills/tdd-red-author`) — paired positive/negative RED tests per `R<n>.<k>.<m>`.
- **migration** (`skills/migration-test-author`) — port existing tests without changing assertions;
  prove parity; characterize gaps; mutation proves the inherited suite still catches regressions.
- **refactor / brownfield** (`skills/brownfield-test-author`) — partition the blast radius:
  characterize-and-freeze *preserve* behavior, fresh RED for *change* behavior; **diff-scoped** cosmic-ray.

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
│                          spec-review-checklist, tdd/migration/brownfield test-author, …)
├── commands/              pipeline-init.md, spec-review.md
├── hooks/                 hooks.json  (Claude Code in-session gates)
├── prompts/               fidelity-rubric.md, verifier-triage.md, mutation-interpret.md, project-setup.md
├── cosmic-ray.toml        mutation config (session-based; diff-scoped for refactor)
├── scripts/
│   ├── gate_runner.py         cross-family verdict caller (opencode | openrouter), family-asserted
│   ├── gate_ci.sh             git/CI floor entry point (fidelity + tests + cosmic-ray + break-glass)
│   ├── bypass_log.sh          break-glass logger for hooks
│   ├── hook_*.sh              Claude Code hook wrappers
│   ├── codemap.py             tree-sitter codebase map -> codebase/MOC.md
│   ├── sync_opencode.py       canonical agents -> .opencode/agents + skills symlink
│   ├── sync_runtimes.sh       run the opencode transpiler
│   ├── install.sh             versioned vendor into a target repo (NEW/UPDATE/DRIFT/GONE)
│   └── vendor_runtime.sh      legacy copy (superseded by install.sh)
├── .opencode/
│   ├── agents/            generated
│   ├── skills/            symlink -> ../skills
│   └── plugin/pipeline-gates.ts   in-session gates for opencode
├── .github/workflows/pipeline-gates.yml   CI floor
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
scripts/install.sh /path/to/project           # vendor opencode + git floor + scripts (writes .avengers/manifest)
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

---

## codemap

Generate the map the Solution Architect and implementers read:
```text
python scripts/codemap.py . --lang python --output codebase     # -> codebase/MOC.md
```

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
