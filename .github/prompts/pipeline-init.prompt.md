---
mode: agent
description: 'Scaffold the plan-build-verify pipeline into this repository (dirs, gitignore, conventions, prereq check, optional multi-runtime vendoring).'
---

Set up the plan-build-verify pipeline in the current repository. Parse `$ARGUMENTS` for an optional
feature id and an optional `--runtime` (default: `claude`). Do each step, report a short summary, and
write no production code.

1. **Artifact tree.** Create `docs/features/`; if a feature id was given, also
   `docs/features/<id>/` and `docs/features/<id>/phases/`.

2. **gitignore.** Ensure `.gitignore` contains `.mutmut-cache/` and `**/.pytest_cache/`.

3. **Conventions in context.** Read the `pipeline-conventions` skill and make sure the rules are
   present for the chosen runtime(s): `CLAUDE.md` (Claude), `AGENTS.md` (opencode), and/or
   `.github/copilot-instructions.md` (Copilot). Create/append the section if missing.

4. **Runtime files.**
   - `claude` (default): nothing to vendor — the installed plugin already provides agents, skills,
     and the in-session hooks.
   - `opencode` | `copilot` | `all`: run
     `bash "${CLAUDE_PLUGIN_ROOT}/scripts/vendor_runtime.sh" "$PWD" <runtime>`
     to copy the adapter(s) plus the git enforcement floor into this repo.

5. **Prereq check.** Report the status of `python3`, `pytest`, `mutmut`, `jq`, and a cross-family
   provider (`OPENROUTER_API_KEY` set, or `opencode` on PATH). List anything missing with its fix.

6. **Code-path note.** If source is not under `src/`, remind the user to update the path glob in both
   `hook_verifier.sh` and `gate_ci.sh`.

7. **Summary.** Print what changed and the first commands:
   `/task-analyst "<feature brief>"` -> `/solution-architect` -> `/impl-planner` -> `/spec-writer`.
   For `opencode`/`copilot`/`all`, also print:
   `pip install pre-commit && pre-commit install`, and add `OPENROUTER_API_KEY` as a CI secret.
