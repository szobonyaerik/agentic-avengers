# Worked example — project-grounded agents (Jarvis)

The agents in `agents/` at the repo root are **canonical and generic**: they carry pipeline mechanics
(the test-first loop, the Verifier lock, the spec workflow, the boundaries) and learn each project's rules by reading
that project's `CLAUDE.md`, spec, and `codebase/MOC.md` at run time. That is what makes the plugin
installable into any repo.

The files here are the opposite end of that trade: the same agents **saturated with one real
project's knowledge** — Jarvis (FastAPI + Postgres + Telegram + an Obsidian vault + a swappable LLM
provider). They are kept as a worked example of what `avenger-agent-factory` should produce, because
the difference is instructive:

| generic (`agents/`) | grounded (here) |
|---|---|
| "follow the project's stated rules in `CLAUDE.md`" | the 10 named invariants, spelled out |
| "read the module notes for the module you touch" | the real `src/` tree, with the do-not-touch paths marked |
| "use the project's test runner" | `pytest -xvs`, real Postgres never SQLite, LLM mocked, vault as a temp dir |
| "most bugs violate a project invariant" | the actual hotspot list: `asyncio.Lock` races in `vault/`, naive datetimes in `scheduler/` |

**Grounded beats generic every time** — "follow best practices" is worthless next to "use the
repository pattern in `src/db/repositories.py` with async sessions". The generic agent is the
starting point, not the destination.

## Using these

Don't install these directly unless you are working on Jarvis itself. For your own repo:

```
@avenger-agent-factory "ground the backend architect for this codebase"
python3 scripts/sync_opencode.py
```

The factory reads your actual code and produces `agents/<name>.md` grounded the way these are. Then
your project's real rules belong in your repo's `CLAUDE.md` — the agent reads them from there, so
there is one source of truth rather than a copy inside an agent that silently rots.

## Why they were moved here

They used to *be* the canonical agents. That meant the pipeline claimed to be project-agnostic while
three of its agents were hardcoded to Jarvis — on any other repo they were confidently, specifically
wrong: telling an implementer to wire singletons into `src/main.py`'s lifespan, dual-write to a vault
that doesn't exist, and never use SQLite in a project whose database is SQLite.
