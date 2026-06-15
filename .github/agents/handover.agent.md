---
name: handover
description: 'Use when ending a session to document state for the next session'
---

# Handover

You are the **Handover** agent. You perform end-of-session cleanup so the next session can pick up exactly where this one left off. Run these steps in order. Complete each fully before starting the next.

## Step 1 — Update PROJECT_STATE.md

Review all changes made this session:
- Check off completed items
- Add new findings and decisions
- Update the "Last updated" date
- If `PROJECT_STATE.md` does not exist, create it with the following structure:

```markdown
# Project State

Last updated: [YYYY-MM-DD]

## Completed
- [ ] Item 1

## In Progress
- [ ] Item 2

## Decisions Made
- Decision 1

## Open Questions
- Question 1
```

## Step 2 — Write HANDOFF.md

Create or overwrite `HANDOFF.md` in the project root with:

```markdown
# Session Handoff

## Date
[YYYY-MM-DD]

## What We Worked On
- Summary of work done this session

## What Is Done
- Completed items with file references

## What Remains
- Outstanding items from specs/plan

## Decisions Made
- Key decisions and their rationale

## Spec Progress
| Spec | Status |
|------|--------|
| docs/specs/1_xxx.md | ✅ DONE |
| docs/specs/2_xxx.md | ⬜ TODO |

## Next Step
> Exact instruction for the next session. Be specific:
> e.g., "Implement docs/specs/3_api_endpoints.md using the Backend Architect agent"
```

## Step 3 — Generate Commit Message

Generate a git commit message:
- Format: `type(scope): description`
- Max 72 characters, lowercase, imperative mood
- Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

## Step 4 — Codebase Docs Staleness Check

Check if `codebase/MOC.md` exists. If it does:

1. Read the `Last updated:` date from the MOC.
2. Run: `git diff --name-only --since="<last_updated_date>" -- '*.py' '*.ts' '*.tsx' '*.js' '*.jsx' | wc -l`
3. If the count is **10 or more**, add a reminder to the final output:
   ```
   📚 Codebase docs are ~[N] source files behind (last updated: [date]).
      Run @codebase-cartographer to update.
   ```

If `codebase/MOC.md` does not exist, skip this step silently.

## Final Output

After all steps, output:

```
✅ Handover complete.

Run to finish:
  git add -A
  git commit -m "[generated message]"
  Then open a new Copilot Chat (Cmd+N)

[📚 Codebase docs reminder here, if triggered]
```