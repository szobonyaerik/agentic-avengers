# docs/lessons — the pipeline's self-improvement log

A **committed, team-shared** record of lessons the pipeline learns while running in this project.
Distinct from the ephemeral, per-machine `/memories/` tool: everything here lives in git and is shared
across every engineer and every agent.

## Layout
- `lessons.json` — a cheap-to-load index (array of
  `{id, date, title, summary, cost, tags, scope, path}`). Loaded at session start; filtered by
  `tags`/`scope`.
- `<id>-<slug>.md` — one prose file per lesson, holding the full detail, the concrete rule, and what
  following that rule costs.

## `cost` is required
A lesson is an instruction to your future self, and an instruction with no price attached gets
followed without limit. One pipeline wrote ten lessons about a single feature and not one was about
cost — ten ways to be more correct and zero ways to be cheaper, which is how a suite reaches 4.87
lines of test per line of source without anyone deciding to. So every entry states what following it
spends (tests added, agent invocations, runtime, tokens) and, where the rule could otherwise justify
unbounded growth, the limit that stops it. "None" is a real answer that has to be defended.

## How it's used
- **Read:** at session start, agents load `lessons.json`, filter to what's relevant to their role/task,
  and open only the prose files that matter.
- **Write:** whenever something learning-worthy happens (a user correction, a self-caught mistake, or a
  notable observation), an agent appends a lesson — deduping against existing entries and *refining*
  rather than duplicating. Content is only ever appended or refined, never overwritten.

The full procedure lives in `skills/self-improvement/SKILL.md`.
