# agentic-avengers · plan-build-verify

A spec-driven, test-first agentic development pipeline that runs under **Claude Code**, **opencode**,
and **GitHub Copilot**. Specialised agents plan a feature; a cross-family gate decides whether the
spec is ready; locked RED tests are written before code; and every phase is verified by a fresh
model plus mutation testing before it ships.

The skill files (`SKILL.md`) and the gate brain (`gate_runner.py` + the rubric prompts) are a shared,
portable core; everything else is a thin per-runtime adapter generated from canonical sources.

---

## Enforcement model (the important part)

Gates fire **mid-session by default** in Claude Code and opencode, and at **commit/PR time** in
Copilot. The git floor (pre-commit + CI) runs everywhere as a backstop, and is the *source of truth*
for Copilot since it has no in-session tool hooks.

| Runtime | In-session gates | Backstop |
|---|---|---|
| Claude Code | native hooks (`hooks/hooks.json`) | git floor |
| opencode | native plugin (`.opencode/plugin/pipeline-gates.ts`) | git floor |
| GitHub Copilot | — (none) | **git floor = source of truth** (pre-commit + CI) |

All three gate paths call the same `gate_runner.py` on a **cross-family** model (DeepSeek/Gemini via
OpenRouter), decorrelated from whatever model authored the work. Gates fail **closed**.

---

## How it works (one paragraph)

`Plan → Quality wall → (Build & verify loop ×phases) → Ship`. Planning agents produce a spec; N
isolated reviewers inspect scoped slices; a Fidelity Gate returns GO / REVIEW / NO-GO. Per phase, the
Test-Author writes paired pass/fail RED tests and *locks* them, the Implementer turns them green
without touching tests, then the Verifier (runs the suite) and the Mutation gate (proves the tests
catch real bugs) run on a fresh model, with the Breaker on critical paths. Any gate failure stops
with a report and a route-back target.

---

## Component locations

| Component | Claude Code | opencode | GitHub Copilot |
|---|---|---|---|
| Skills (`SKILL.md`) | `skills/` (plugin) | `.opencode/skills/` (symlink → `../skills`) | `.github/skills/` (symlink → `../skills`) |
| Agents | `agents/*.md` | `.opencode/agents/*.md` | `.github/agents/*.agent.md` |
| Conventions | `pipeline-conventions` skill | `AGENTS.md` | `.github/copilot-instructions.md` |
| Commands | `commands/*.md` | (n/a) | `.github/prompts/*.prompt.md` |
| Gates (in-session) | `hooks/hooks.json` | `.opencode/plugin/pipeline-gates.ts` | — |
| Gates (floor, all) | git pre-commit + CI → `gate_runner.py` | same | same |
| Distribution | `/plugin install` | vendor into repo | vendor into `.github/` |

> `agents/`, `skills/`, `prompts/`, `scripts/`, `commands/` are the **canonical sources**. The
> opencode and Copilot copies are **generated** — do not hand-edit them.

---

## Canonical layout

```
agentic-avengers/
├── .claude-plugin/        plugin.json, marketplace.json
├── agents/                canonical subagents (Claude format)
├── skills/                portable SKILL.md skills (incl. pipeline-conventions)
├── commands/              pipeline-init.md
├── hooks/                 hooks.json  (Claude Code in-session gates)
├── prompts/               fidelity-rubric.md, verifier-triage.md, mutation-interpret.md, project-setup.md
├── scripts/
│   ├── gate_runner.py         cross-family verdict caller (opencode | openrouter)
│   ├── gate_ci.sh             git/CI floor entry point
│   ├── hook_*.sh              Claude Code hook wrappers
│   ├── sync_opencode.py       canonical agents -> .opencode/agents + skills symlink
│   ├── sync_copilot.py        canonical agents -> .github/agents (+ handoffs) + skills + prompts
│   ├── sync_runtimes.sh       run both transpilers
│   └── vendor_runtime.sh      copy the pipeline into a target repo (opencode/copilot)
├── .opencode/
│   ├── agents/            generated
│   ├── skills/            symlink -> ../skills
│   └── plugin/pipeline-gates.ts   in-session gates for opencode
├── .github/
│   ├── agents/            generated (*.agent.md, with handoffs)
│   ├── skills/            symlink -> ../skills
│   ├── prompts/           generated
│   ├── copilot-instructions.md
│   └── workflows/pipeline-gates.yml   CI floor
├── AGENTS.md              opencode conventions
├── opencode.json
├── .pre-commit-config.yaml
├── CLAUDE.md
└── README.md
```

---

## Requirements

- **Claude Code** (Skills, `context: fork`, hooks) and/or **opencode** and/or **GitHub Copilot**.
- **Python 3**, **`pytest`**, **`mutmut`**, **`jq`** in the target repo.
- A cross-family provider: **`OPENROUTER_API_KEY`** exported (gates use OpenRouter), and/or opencode
  configured. The opencode in-session plugin uses OpenRouter for gate calls.

---

## Setup per runtime

**Claude Code**
```text
/plugin marketplace add <you>/agentic-avengers
/plugin install plan-build-verify@erik-tools
chmod +x scripts/*.sh
/pipeline-init                       # scaffold docs/features, gitignore, conventions, prereqs
```
Hooks in `hooks/hooks.json` fire the gates mid-session.

**opencode**
```text
python3 scripts/sync_opencode.py     # generate .opencode/agents + link .opencode/skills
# .opencode/plugin/pipeline-gates.ts loads at startup -> mid-session gates
export OPENROUTER_API_KEY=...         # the plugin's gates call OpenRouter
```
Drive agents with `@task-analyst "…"`, etc. (see `AGENTS.md`).

**GitHub Copilot**
```text
python3 scripts/sync_copilot.py      # generate .github/agents (+handoffs), prompts, link skills
pip install pre-commit && pre-commit install
# add OPENROUTER_API_KEY as a GitHub Actions secret
```
Gates run via pre-commit + the `pipeline-gates` workflow (the source of truth for Copilot).

**Into another repo** (opencode/Copilot can't use `/plugin install`):
```text
scripts/vendor_runtime.sh /path/to/target  opencode|copilot|all
```

---

## Keeping runtimes in sync

The canonical sources are `agents/`, `skills/`, `commands/`. After editing any of them:
```text
scripts/sync_runtimes.sh             # regenerates .opencode/ and .github/ adapters
```
`AGENTS.md` and `.github/copilot-instructions.md` carry the conventions and are hand-maintained from
`skills/pipeline-conventions/SKILL.md` — update them only when the rules themselves change.

---

## Extending the pipeline
Add a skill (`skills/<name>/SKILL.md`) or an agent (`agents/<name>.md`), then run
`scripts/sync_runtimes.sh` and bump `version` in `plugin.json`. Adding a gate is three files: a
rubric in `prompts/`, a path branch in the gate scripts/plugin, and (for Claude Code) an entry in
`hooks/hooks.json`.

---

## Notes
- Gates **fail closed** — a missing key, unreachable model, or non-JSON verdict stops, never passes.
- The mutation gate sends mutmut's output to the model rather than parsing it, so it survives mutmut
  version changes (one model call per phase).
- Skills are symlinked across runtimes (`SKILL.md` is identical). On Windows, copy instead.
- `hook_verifier.sh`, `gate_ci.sh`, and the opencode plugin all match `*/src/*`; change that to your
  code layout if it isn't `src/`.
- License: MIT.