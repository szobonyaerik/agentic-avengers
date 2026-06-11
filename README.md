# agentic-avengers · plan-build-verify

A spec-driven, test-first agentic development pipeline, packaged as a **Claude Code plugin** so it
drops into any repository. Specialised agents plan a feature; a cross-family gate decides whether
the spec is ready; locked RED tests are written before code; and every phase is verified by a fresh
model plus mutation testing before it ships.

It is designed to grow: adding a skill or an agent is dropping one file in a folder and bumping a
version — see [Extending the pipeline](#extending-the-pipeline).

---

## How it works (one paragraph)

`Plan → Quality wall → (Build & verify loop ×phases) → Ship`. Planning agents produce a spec; N
**isolated reviewers** inspect scoped slices; a **Fidelity Gate** (fresh cross-family model) returns
GO / REVIEW / NO-GO. Per phase, the **Test-Author** writes paired pass/fail RED tests and *locks*
them, the **Implementer** turns them green without touching tests, then two hook gates run on a
fresh model — the **Verifier** (runs the suite) and the **Mutation gate** (proves the tests catch
real bugs) — with the **Breaker** on critical paths. Any gate failure stops with a written report
and a route-back target; gates fail **closed**.

---

## Repository layout

```
agentic-avengers/                       # repo root = plugin root
├── .claude-plugin/
│   ├── plugin.json                     # manifest (only this + marketplace.json live here)
│   └── marketplace.json                # lets the repo be installed as a marketplace
├── agents/                             # auto-discovered subagents
│   ├── task-analyst.md                 # scope & route by risk
│   ├── solution-architect.md           # architecture + data model
│   ├── implementation-planner.md       # phases in dependency/risk order
│   ├── spec-writer.md                  # testable spec per phase
│   ├── backend-architect.md            # implements backend specs
│   ├── frontend-developer.md           # implements UI specs
│   ├── test-author.md                  # writes locked RED tests, never production code
│   ├── breaker.md                      # adversarial, critical paths only
│   ├── bug-hunter.md                   # (your agent — see note)
│   ├── handover.md                     # documents each phase
│   └── agent-factory.md                # templates the suite into a project
├── skills/                             # auto-discovered skills
│   ├── tdd-red-author/SKILL.md
│   ├── spec-isolation-review/SKILL.md  # context: fork (isolated)
│   ├── phase-handover/SKILL.md
│   └── pipeline-conventions/SKILL.md   # ships the rules into context (CLAUDE.md isn't loaded)
├── commands/
│   └── pipeline-init.md                # /pipeline-init — scaffolds a target repo
├── hooks/
│   └── hooks.json                      # the 4 gate hooks (paths via ${CLAUDE_PLUGIN_ROOT})
├── prompts/                            # cross-family rubric prompts (gate_runner reads these)
│   ├── fidelity-rubric.md
│   ├── verifier-triage.md
│   ├── mutation-interpret.md
│   └── project-setup.md                # (your addition — see note)
├── scripts/
│   ├── gate_runner.py                  # opencode (default) | openrouter verdict caller
│   ├── hook_fidelity.sh
│   ├── hook_verifier.sh
│   ├── hook_mutation.sh
│   └── hook_artifact_check.sh
└── README.md
```

> **The Verifier is not an agent** — the old `Verifier` role is `hook_verifier.sh`. Verification is
> enforced by hooks.
>
> **`CLAUDE.md` is not loaded from a plugin** — rules that must always be in context (test-lock,
> artifact paths, phase ordering) ship as the `pipeline-conventions` skill, and `/pipeline-init`
> can also copy them into the *target* repo's CLAUDE.md.
>
> **`docs/features/` and `tests/` are not in the plugin** — they're written into the target repo at
> runtime by `/pipeline-init`, not shipped here.

---

## Requirements

- **Claude Code** recent enough for Skills, `context: fork`, and the 4 hook types.
- **Python 3** (gate runner is stdlib-only) plus **`pytest`** and **`mutmut`** in the target repo.
- **`jq`** (the hook wrappers parse stdin JSON).
- A cross-family provider for the gates — **opencode** configured with your models (default), or
  **`OPENROUTER_API_KEY`** exported (when a gate uses `--provider openrouter`).

---

## Install

```text
# from a published repo:
/plugin marketplace add <your-github-user>/agentic-avengers
/plugin install plan-build-verify@erik-tools

# local development (point at the repo on disk):
/plugin marketplace add /abs/path/to/agentic-avengers
/plugin install plan-build-verify@erik-tools
```

Make the scripts executable once: `chmod +x scripts/*.sh`. Then, per target repo:

```text
/pipeline-init            # creates docs/features/, gitignore entries, conventions, prereq check
```

---

## Configure

- **Provider & models** — gates default to `--provider opencode`; pass `--provider openrouter` per
  gate to route there. Set model ids in the `hook_*.sh` wrappers (fidelity → `deepseek/deepseek-chat`,
  verifier/mutation → `google/gemini-2.5-pro`). Use ids that exist in the targeted provider.
- **Code path** — `hook_verifier.sh` matches `*/src/*`; if your code lives in `app/` or a package
  dir, edit that glob (it's commented).
- **Strictness** — tune `prompts/fidelity-rubric.md` (defaults to NO-GO when torn) and
  `prompts/mutation-interpret.md`.

---

## Use

Once per feature (Plan + wall):
```text
/task-analyst "<feature brief>"
/solution-architect
/impl-planner            # (implementation-planner)
/spec-writer             # writing spec.md fires the Fidelity Gate hook → GO to proceed
/spec-review slice=<slice>     # run once per slice
```
Per phase, in dependency order:
```text
use the test-author subagent for phase <n>-<slug>     # locked RED tests
/implement --phase <n>-<slug>                          # RED→GREEN; verifier hook runs
# writing implementation-report.md fires the mutation gate
use the breaker subagent on phase <n>-<slug>           # critical paths only
/handover --phase <n>-<slug>
```
Ship when every phase is green, mutation thresholds met, and all handovers present.

---

## Extending the pipeline

Plugins **auto-discover** `agents/`, `skills/`, and `commands/`. Growing the pipeline is: add a
file, bump `version` in `plugin.json`, commit. Consumers run `/plugin update`.

**Add a skill** — `skills/<name>/SKILL.md` with `name` + `description` frontmatter. Add
`context: fork` (+ optional `model:`) only for isolated workers like reviewers; inline skills inherit
the caller's model/context. Stack skills (e.g. `fastapi-conventions`, `pgvector-query`) go here too.

**Add an agent** — `agents/<name>.md` with `name`, `description` (the routing trigger), `tools`,
`model`. Keep boundaries explicit (e.g. "writes only `tests/`"). For a gate-like reviewer, pin a
model ≠ the work it reviews.

**Add a gate** (the only multi-file extension):
1. `prompts/<gate>-rubric.md` — reply as strict JSON `{"verdict","report","route_back"}`.
2. `scripts/hook_<gate>.sh` — path-filter, then call
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gate_runner.py" --rubric "${CLAUDE_PLUGIN_ROOT}/prompts/<gate>-rubric.md" --model <id>`.
3. Add a `command` entry to `hooks/hooks.json`.

---

## Plugin path convention

Inside a plugin, **plugin-owned files** use `${CLAUDE_PLUGIN_ROOT}` and the **target project** uses
`$CLAUDE_PROJECT_DIR`. If you wrote the wrappers against `$CLAUDE_PROJECT_DIR` during dev, change
only the plugin-owned references:

| Reference | Dev (single repo) | Plugin |
|---|---|---|
| `gate_runner.py`, rubric prompts | `$CLAUDE_PROJECT_DIR/scripts`, `/prompts` | `${CLAUDE_PLUGIN_ROOT}/scripts`, `/prompts` |
| `cd` for pytest/mutmut, `docs/features`, file being judged | `$CLAUDE_PROJECT_DIR` | `$CLAUDE_PROJECT_DIR` (unchanged) |

Both variables are available to plugin hooks.

---

## Notes
- Gates **fail closed**: a missing key, absent `opencode`, or network error → the gate's error and
  an exit-2 stop, never a silent pass.
- The mutation gate sends mutmut's output to the model rather than parsing it in bash, so it
  survives mutmut version changes (cost: one model call per phase).
- **`fidelity-rubric.md` must use a hyphen** to match `hook_fidelity.sh` (rename from `fidelity_rubric.md`).
- `bug-hunter.md` and `project-setup.md` are your additions — replace their one-liners above with
  their real roles. If `project-setup.md` is a gate rubric, wire it as a gate (see Add a gate); if
  it's an init prompt, reference it from `/pipeline-init`.
- License: MIT.