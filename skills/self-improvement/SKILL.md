---
name: self-improvement
description: The write-and-read procedure for the pipeline's committed, team-shared lessons log (docs/lessons/). Use whenever something learning-worthy happens (a user correction, a self-caught mistake, or a notable observation) and at session start to recall prior lessons. Always append or refine — never override existing lessons.
---

# self-improvement

How every agent captures and recalls durable lessons so the pipeline improves over time. Lessons live
on disk under `docs/lessons/`, are **committed to git**, and are **shared across the whole team** —
they are deliberately different from the ephemeral, per-machine `/memories/` tool.

> **This log is about the *work*, not the *machinery*.** A gate that misfires or a stage that churns
> is a `pipeline-retrospective` observation, filed upstream against the pipeline itself - see
> `skills/pipeline-retrospective` and the "two learning logs" table in `skills/pipeline-conventions`.
> Test: would this help someone building *this* project again? → lesson.

## The shape on disk
```
docs/lessons/
  lessons.json              # cheap-to-load index (array of entries) — the machine source of truth
  <id>-<slug>.md            # one prose file per lesson (the full detail)
  README.md                 # what this folder is
```

Index entry (`lessons.json`):
```json
{
  "id": "<12-char id>",
  "date": "YYYY-MM-DD",
  "title": "<short imperative title>",
  "summary": "<one line — the rule, not the story>",
  "cost": "<one line — what following this costs, and its limit>",
  "tags": ["<free tags: e.g. verifier, migration, pytest, ci>"],
  "scope": "<agent | stack | area this applies to, e.g. backend-architect, python, gates>",
  "path": "docs/lessons/<id>-<slug>.md"
}
```
Keep entries to summaries + pointers so the index stays cheap to load as it grows. The `id` is a short
stable hash — `sha1(title)[:12]` is fine; the point is that dedup can match on it.

## Every lesson states its cost — `cost` is required

A lesson is an instruction to your future self, and an instruction with no price attached gets
followed without limit. One pipeline wrote **ten** lessons about a single feature and **not one was
about cost**: it had taught itself ten ways to be more correct and zero ways to be cheaper, which is
how a suite reaches 4.87 lines of test per line of source without anyone ever deciding to.

So `cost` is not optional, and "none" is a real answer that has to be defended. State what following
the lesson spends, in whichever of these it actually spends:

- **tests added** — per requirement, per phase, or per occurrence
- **agent invocations** — an extra gate, review pass, or route-back
- **runtime** — suite seconds, and whether it scales with suite size
- **tokens** — a larger bundle, an extra model call

**And where the lesson could justify unbounded growth, it must carry its own limit.** The live
example is a real lesson that reads *"'it already works' is exactly the state that precedes a silent
regression."* That is correct in principle and worth keeping. Applied without a budget it justifies
writing tests forever, because every correct behavior is unbound until you write a test and there are
infinitely many correct behaviors. The fix is not to delete the lesson; it is to bound it — *"…so
bind it **when the spec has a requirement for it**; an unbound behavior is a route-back to the
spec-writer, not a test you add on your own initiative."*

A lesson whose `cost` says "none" and whose rule begins with "always" is almost certainly one of
these. Write the limit before you write the rule.

## At session start (read)
For a known project, if `docs/lessons/lessons.json` exists:
1. Load the index only (not the prose files) — it is small by design.
2. Filter to entries whose `tags`/`scope` are relevant to your role and the task at hand.
3. Open only the handful of prose files that matter. Do not read the whole folder.
4. Apply the lessons as you work.

If the file is missing, skip silently — the first lesson written creates it. Neither `/pipeline-init`
nor `scripts/install.sh` seeds it, and `scripts/hook_lessons.sh` (the Claude Code delivery) injects
nothing for a missing, unparseable or empty index, so a project that has never written a lesson sees
no change.

## When to write
Write a lesson the moment something learning-worthy happens — **not only when corrected**:
- **User correction** — capture the pattern and a rule that prevents recurrence.
- **Self-caught mistake** — same, even if you fixed it before the user noticed.
- **Notable observation** — a non-obvious gotcha, a dead end worth remembering, or a confirmed-good
  approach.

## How to write (append or refine — never override)
1. **Dedup first.** Scan `lessons.json` for an existing entry with a matching `title` or overlapping
   `tags`/`scope`. 
   - **If a match exists:** open its prose file and *refine* it (sharpen the rule, add the new
     evidence). Update the entry's `date`/`summary` if the rule changed. Do **not** add a duplicate,
     and never delete prior content — append or tighten.
   - **If no match:** create a new prose file `docs/lessons/<id>-<slug>.md` and append a new entry to
     `lessons.json`.
2. **Prose file** holds the full lesson: what happened, why it matters, the concrete rule to follow
   next time, and **what following it costs**. Keep it short and actionable.
3. **Index entry** is the summary + `cost` + pointer described above. Append it to the JSON array;
   keep the file valid JSON. An entry without a `cost` line is incomplete — when you refine an older
   lesson that predates this field, add one while you are in there.
4. Never rewrite or reorder existing entries beyond the one you touched. The log is additive.

## Prose file shape
```markdown
# <title>

- **Date:** YYYY-MM-DD
- **Scope:** <agent | stack | area>
- **Tags:** <comma-separated>

## What happened
<brief>

## Rule
<the concrete, do-this-next-time rule>

## Cost
<what following this spends: tests added, agent invocations, runtime, tokens — and, if the rule
 could otherwise justify unbounded growth, the limit that stops it>
```
