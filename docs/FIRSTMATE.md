# Running the pipeline under firstmate — fleet layering + the tavern

How [firstmate](https://github.com/kunchenguid/firstmate) sits **above** this pipeline, how
[treehouse](https://github.com/kunchenguid/treehouse) isolates each job, and how to get a
three-job fleet visible in the pixel tavern (`tavern/`). Placeholders throughout:
`<project-a>` = a repo this pipeline manages (vendored via `scripts/install.sh` or the plugin),
`<project-b>` = any other repo, `<feature-id>` = a feature id for `/avenger-run`.

## Why the layering is natural

Firstmate's default delivery mode **is** `no-mistakes` — the same tool this repo already uses as
its feature-close ship gate (`.no-mistakes.yaml`, `/avenger-run` §4a). So a crewmate that runs
`/avenger-run <feature-id> --auto` inside a worktree ships through exactly the machinery both
systems already agree on. The division of labour:

| layer | owns | speaks |
|---|---|---|
| **you (captain)** | intent, merges, `ask-user` findings | firstmate only |
| **firstmate** | dispatch, supervision, teardown, backlog | `bin/fm-*` + tmux |
| **crewmate** | one task in one treehouse worktree | its harness (claude/opencode) |
| **avenger pipeline** | plan → build → verify inside that worktree | `/avenger-run --auto` |
| **no-mistakes** | lint, docs, push, PR, CI at feature close | its own daemon worktree |

Two rules to respect at the boundary:
- Under `--auto` the pipeline **halts** on an `ask-user` ship-gate finding and on a spec-review
  NO-GO. That halt surfaces as the crewmate going quiet with the halt in its transcript —
  firstmate's stale/wake cycle will escalate it to you. `--ship-yes` pre-consents per run;
  deliberately not the default.
- Firstmate hard rule: crewmates never address you directly. Typing into a crewmate window (the
  tavern's ⚔ Focus button) is **authoritative captain intervention** — allowed, visible, and
  reconciled at the next supervision review. Prefer steering through the first mate.

## 0. Prerequisites (once per machine)

```bash
# treehouse — pooled, reusable git worktrees (how crewmates get isolation)
curl -fsSL https://kunchenguid.github.io/treehouse/install.sh | sh
treehouse init          # in each project you'll fleet over; creates treehouse.toml

# firstmate — clone and let AGENTS.md take over in your harness
git clone https://github.com/kunchenguid/firstmate ~/src/firstmate
cd ~/src/firstmate
gh auth login           # PR-based delivery modes need it
claude                  # (or your harness) — the operating contract boots via AGENTS.md
```

Per `<project-a>` (pipeline-managed), the pipeline's own preflight must already hold — these are
the same three `no-mistakes` states `/avenger-run` §1 checks, plus the gate key:

```bash
no-mistakes doctor            # binary + runnable pipeline agent
no-mistakes axi               # repo initialised (else: no-mistakes init)
grep REPLACE_ME .no-mistakes.yaml && echo "fill me in"   # config filled
# OPENROUTER_API_KEY in the project .env — fidelity/spec-review/verifier gates fail closed without it
```

## 1. Register the projects with firstmate

Tell the first mate (in chat — it maintains `data/projects.md` itself):

> Register `<project-a>` at `~/code/<project-a>`, delivery mode **no-mistakes**, yolo off.
> Register `<project-b>` at `~/code/<project-b>`, delivery mode **direct-PR**, yolo off.

An unregistered project resolves to `no-mistakes` with yolo off anyway — registration just makes
the posture explicit and pre-agreed.

## 2. Dispatch the three jobs

All three are one message each to the first mate. It writes the brief (`bin/fm-brief.sh`), leases
a treehouse worktree, spawns the crewmate (`bin/fm-spawn.sh`) in its own tmux window, and
supervises via the zero-token watcher.

**Job 1 — ship: the pipeline, end to end, in a worktree of `<project-a>`:**

> Ship task on `<project-a>`: run `/plan-build-verify:avenger-run <feature-id> "<one-line brief>"
> --auto`. The run halts on ask-user findings — escalate them to me rather than answering.

**Job 2 — scout: a second worktree of the same repo, in parallel:**

> Scout task on `<project-a>`: audit `docs/` for drift against the current code; leave a report.

(Scout tasks deliver a standalone `data/<id>/report.md` that survives teardown — nothing pushes.)

**Job 3 — ship or scout on the other repo:**

> Ship task on `<project-b>`: <the task>. Delivery per its registered mode.

Because job 1 and job 2 both lease from `<project-a>`'s treehouse pool they get **distinct**
worktrees (`~/.treehouse/<project-a>-…/1` and `/2`) and never collide; the primary checkout stays
untouched — firstmate refuses ship briefs that resolve to the primary checkout.

## 3. Open the tavern

```bash
python3 tavern/server.py \
  --root ~/code/<project-a> --root ~/code/<project-b> \
  --fm-home <FM_HOME, default is the firstmate checkout> \
  --fm-bin  ~/src/firstmate/bin
# → http://127.0.0.1:8377/
```

What you should see fill in as the fleet spins up:

1. three patrons at three tables, labelled with the crewmate ids and projects, speech bubbles
   carrying their last status line;
2. job 1's table gains an avenger beside the patron — the Fortune Teller first, then the Wizard,
   the Cartographer… — with a `⚑ <stage>` flag on the table label as `pipeline_state.py` advances
   (this needs `<project-a>` vendored with the activity hook, i.e. a current `scripts/install.sh`
   run, for the live avenger sprites; the ⚑ stage flag works regardless);
3. the door flashing on gate verdicts, and the room shaking if anything break-glasses;
4. click a patron → **⚔ Focus terminal** jumps tmux to that crewmate when you need to speak in
   person (see the intervention caveat above).

The footer's `sources:` line is the honesty check: anything `absent` means that feed isn't wired
yet on this machine.

## 4. When things wobble

| symptom | likely cause | fix |
|---|---|---|
| no patrons | `--fm-home` wrong (no `state/*.meta` there) | point at the home the first mate actually runs from |
| patrons but no avengers on job 1 | `<project-a>` lacks the activity hook | re-run `scripts/install.sh <project-a>` (vendors `hook_activity.sh` + hooks.json) |
| job 1 quiet for long | `--auto` halted on ask-user / NO-GO, or preflight failed | read its window (⚔ Focus); preflight §0 above |
| `⚑ stage` never moves | resolver can't run in that root | `python3 scripts/pipeline_state.py <feature-id> --root <root>` by hand and read the error |
| tavern empty, sources all `absent` | wrong `--root`s | roots must be the project checkouts (worktrees inherit the artifact tree per-worktree; add the active worktree path as a root too if you want per-worktree boards) |

One honest limitation, one honest note: **worktree roots.** Job 1's pipeline artifacts are written
inside its *treehouse worktree*, not the primary checkout — until the branch lands. Add the leased
worktree path as an extra `--root` (ask the first mate for it, or `treehouse status`) if you want
job 1's stage flag live during the run. And the tavern is a **window, not a control plane**: every
action it offers is either read-only or a tmux focus; dispatch and merges stay with the first mate
and you.
