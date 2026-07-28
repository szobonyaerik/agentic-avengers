# docs/lessons — the pipeline's self-improvement log

A **committed, team-shared** record of lessons the pipeline learns while running in this project.
Distinct from the ephemeral, per-machine `/memories/` tool: everything here lives in git and is shared
across every engineer and every agent.

## Layout
- `lessons.json` — a cheap-to-load index (array of `{id, date, title, summary, tags, scope, path}`).
  Loaded at session start; filtered by `tags`/`scope`.
- `<id>-<slug>.md` — one prose file per lesson, holding the full detail and the concrete rule.

## How it's used
- **Read:** at session start, agents load `lessons.json`, filter to what's relevant to their role/task,
  and open only the prose files that matter.
- **Write:** whenever something learning-worthy happens (a user correction, a self-caught mistake, or a
  notable observation), an agent appends a lesson — deduping against existing entries and *refining*
  rather than duplicating. Content is only ever appended or refined, never overwritten.

The full procedure lives in `skills/self-improvement/SKILL.md`.
