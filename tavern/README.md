# The Grinning Gate — pixel-art agent tavern

A read-only monitor for **every agent this machine runs**, drawn as a D&D fantasy tavern:

- the **first mate** ([firstmate](https://github.com/kunchenguid/firstmate)) is the **barkeeper**;
- each **crewmate / job** is a **patron at a table** (a table = a worktree);
- the **avenger subagents** of a `/avenger-run` sit at the table of the job running them, each with
  a fixed archetype sprite but their **real agent name** on the label;
- **gates are the door**: the gate flashes green on a pass, red on a NO-GO/route-back, and the
  whole room shakes on a break-glass;
- **speech bubbles** carry each agent's live status sentence.

```
python3 tavern/server.py            # live: watches the roots in tavern/tavern.toml
python3 tavern/server.py --demo     # synthetic 3-job fleet, no setup needed
python3 tavern/server.py --root ~/code/proj --fm-home ~/fm --fm-bin ~/src/firstmate/bin
```

Open http://127.0.0.1:8377/. Click any character for a detail panel; crewmates get a
**⚔ Focus terminal** button that jumps your tmux client to their window. In-session subagents have
no terminal of their own — their panel shows their recent doings (transcript tail) instead, which
is the honest limit of what an in-process subagent is.

## Archetype mapping

Names are the real `agents/` names and stay on every label; the archetype is only the sprite +
epithet, so re-skinning personas later means editing this table and `ARCHETYPES` in `index.html`.

| agent | archetype |
|---|---|
| avenger-task-analyst | the Fortune Teller |
| avenger-solution-architect | the Wizard |
| avenger-implementation-planner | the Cartographer |
| avenger-spec-writer | the Scribe |
| avenger-backend-architect | the Blacksmith |
| avenger-frontend-developer | the Bard |
| avenger-verifier | the Judge |
| avenger-breaker | the Barbarian |
| avenger-bug-hunter | the Ranger |
| avenger-handover | the Courier |
| avenger-agent-factory | the Artificer |

The two gate stages (fidelity, spec-review) and the ship gate are deliberately **not** patrons —
gates are moments at the door, not people at a table.

## Data sources (all optional, all read-only)

| source | file/command | what it feeds |
|---|---|---|
| activity | `<root>/.agent-activity.jsonl` (written by `scripts/hook_activity.sh`) | who is in the tavern right now; enter/leave moments |
| pipeline | `<root>/scripts/pipeline_state.py` + `docs/features/**` artifacts | per-feature stage flag, spec stamps, phase verdicts |
| fleet | `fm-fleet-snapshot.sh --json` when `fm_bin` is set, else `$FM_HOME/state/<id>.meta`/`.status` directly | patrons, worktrees, tmux windows, wake-event tails |

> **`fm_bin` is optional and off is the safe default.** Setting it makes the tavern *execute
> firstmate's scripts* on every poll — foreign code with its own tmux interactions, run against
> whatever state the fleet is in. The state-file fallback reads the same crew data with zero code
> execution. Use `fm_bin` only when you specifically want the structured snapshot schema.
| break-glass | `<root>/gate-overrides.log` | the room-shake moment |

A missing source renders as `absent` in the footer — the tavern never fakes a population. Demo
mode is stamped `DEMO` in the header for the same reason.

**Auto-discovery (default on, `--no-scan` to disable):** you don't have to name every path. The
server also watches every crewmate worktree found in `$FM_HOME/state/*.meta`, and every recently
active Claude Code session on the machine — found via transcript mtime under
`~/.claude/projects/`, with the session's own `cwd` read from its transcript tail. Sessions the
fleet doesn't own get their own table as adventurers; `python3 tavern/server.py` with no flags is
a valid way to run it.

## API

- `GET /api/state` — merged snapshot `{mode, sources, crew, features, live_agents, moments}`
- `GET /api/agent/crew:<id>` — meta, wake-event tail, brief/report from `$FM_HOME/data/<id>/`
- `GET /api/agent/live:<agent_id|agent_type>` — activity record + transcript-tail doings
- `POST /api/focus` `{"id":"crew:<id>"}` — `tmux select-window` to the crewmate's window

Binds `127.0.0.1` by default on purpose (worktree paths + transcript excerpts are shown).

## Extraction

Everything in this directory talks to the rest of the world only through the four sources above —
no imports from the wider repo except copying `pipeline_state.py`'s CLI contract. Moving `tavern/`
to its own repo is a `git mv` plus keeping those feeds reachable.

## Runtime notes

- **The tavern sees the machine it runs on.** Remote sessions — Claude Code on the web/mobile,
  cloud containers — leave no transcript under `~/.claude/projects/` on your disk, so they cannot
  be discovered. What appears is everything local: sessions, crewmate worktrees, the fleet home.
- **Raising your terminal:** the ⚔ focus button switches tmux windows; set `terminal_app` in
  `tavern.toml` (e.g. `"Terminal"`, `"iTerm"`, `"Visual Studio Code"`) so it also brings that app
  to the front on macOS — the server can't guess which app hosts your tmux client.

- Claude Code: full fidelity — `SubagentStart`/`SubagentStop` hooks feed the activity log.
- opencode: has **no subagent lifecycle events**, so opencode sessions surface through the
  pipeline artifacts and fleet state only; their in-session subagents don't appear individually.
