---
description: Scaffold the plan-build-verify pipeline into this repository (dirs, gitignore, conventions, prereq check).
allowed-tools: Bash, Read, Write, Edit
argument-hint: "[feature-id]  # optional: also scaffold docs/features/<feature-id>/"
---

Set up the plan-build-verify pipeline in the current repository. Do each step and report a short
summary at the end. Do not write any production code.

1. **Artifact tree.** Create `docs/features/` if missing. If a feature id was passed
   (`$ARGUMENTS`), also create `docs/features/$ARGUMENTS/` and `docs/features/$ARGUMENTS/phases/`.

2. **gitignore.** Ensure `.gitignore` contains these lines (append any that are missing):
   ```
   .mutmut-cache/
   **/.pytest_cache/
   ```

3. **Conventions in context.** The pipeline rules live in the `pipeline-conventions` skill. Read
   that skill and ensure the project's own `CLAUDE.md` has a "## Pipeline conventions" section
   reproducing them (create `CLAUDE.md` if absent, append the section if missing, leave it alone if
   already present). This makes the rules always-on as project context, not just on skill load.

4. **Prerequisite check.** Verify and report the status of each, without failing the command:
   - `python3`, `pytest`, `mutmut`, `jq` on PATH
   - a cross-family provider: either `OPENROUTER_API_KEY` is set, or `opencode` is on PATH
   - the gate hooks are active (the plan-build-verify plugin is installed/enabled)
   List anything missing with the one-line fix.

5. **Code-path note.** Check where source lives (`src/`, `app/`, or a package dir). If it is not
   `src/`, remind the user to update the path glob in `hook_verifier.sh`.

6. **Summary.** Print what was created/changed and the first commands to run:
   `/task-analyst "<feature brief>"` → `/solution-architect` → `/impl-planner` → `/spec-writer`.