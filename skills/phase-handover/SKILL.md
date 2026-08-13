---
name: phase-handover
description: Use at the end of a phase to document it.
---

# phase-handover

Close out a finished phase: write a short, durable record and point to what comes next. This
runs after the Verifier passes the phase. Writing `handover.md` is also
what satisfies the Stop-hook artifact check, so the phase isn't considered done until it exists.

## `handover.md` is a contract card, not a record. Hard cap: **6144 bytes.**

This is the *enforced* form of the ≤5-line summary this skill has always asked for. It was asked for
and not enforced, and writers produced 37 KB averages — **85.6% of those bytes sat outside the three
sections this template sanctions.** That was not a template problem. It was an enforcement gap, and
`scripts/doc_read_path.py check` closes it: a card over the cap fails.

The cap exists because of what the card **costs to read**, not what it costs to store. Every spec
write and every spec review opens prior phases' cards; at 34 KB each that was a quadratic worth
485k-1,475k tokens over one feature, and phase 8 alone paid ~527k re-reading the seven handovers
before it. At 6 KB it is affordable to read all of them. **Nothing is deleted** — everything the
card cannot carry goes to `handover-archive.md` beside it, which **no pipeline stage is instructed
to read**.

### Calibrate the length deliberately — you write long by default

You are an Opus-family model, and left to your own judgement you will produce a thorough,
well-organised 30 KB document that no one will read. That instinct is wrong here and the cap is not
a budget you are meant to fill. **The card is ~80 lines. The summary is 5 lines, not 5 paragraphs.
Each binding contract is one table row. Each decision is one line plus the file that enforces it.**
If you find yourself explaining *how* the phase reached a result, you are writing the archive.

### Two tests, applied before any paragraph enters the card

1. **The restatement test.** If the fact is already in `verdict.json`, `gate-overrides.log`,
   `docs/lessons/`, `lessons.json` or `git log` — **link to it, do not re-narrate it.** Verification
   results, warnings disposition, attempt history, commits-in-this-phase and lessons-written are all
   in that category by construction. Measured: 83-100% of the requirement ids a handover names are
   already in that phase's specs, and 100% of the ids in `verdict.json` are. The duplication is not
   copy-paste — it is the *same facts re-narrated in fresh prose*, which costs output tokens to
   write and a second full pass to read.
2. **The reader test**, applied to whatever survives the first. Name the agent that will read this
   paragraph **and** the decision it will change. If you cannot name both, it goes in the archive.

### What only the card can carry

Two things in a handover exist nowhere else, and they are why the card exists at all:

- **Binding contracts** the next phase must not break — signatures, field names, error hierarchies,
  the single construction site for a thing. Not in `verdict.json`, not derivable from `git log`, and
  `spec-review-checklist` gates every later spec against them.
- **Decisions made and constraints locked in** — one line each, naming the file that *enforces* the
  decision rather than restating its reasoning.

## Precondition
The phase must actually be complete: the **Verifier passed** — `verdict.json` in the phase directory
says `verdict: pass` — which is what locks the phase suite (`pipeline-conventions`:
*locked-after-verify*). If it isn't green, stop and report what's outstanding instead of writing a
handover.

**No item the previous phase carried is unanswered.** `python3 scripts/carried_items.py due
<phase-dir>` must exit 0 - see *Open items carry your PREDICTIONS too* below. Discharge them as you
build; a batch of them discovered at the close is a phase that never read its predecessor's card.

**No amendment is owed re-verification.** `python3 scripts/amendments.py due <phase-dir>` must exit
0. A phase does not close over a pending security amendment, nor over a pending ordinary one on a
phase whose verdict already passes — that verdict is a claim about code that has since changed.
`scripts/hook_verifier.sh` enforces this on the handover write, so a card written over one will not
land.

Mirror the gate record out of `verdict.json`, `amendments.json` and `exceptions.json`:
- **Amendments folded into this phase** — one line each: id, the requirement ids it touched, and
  whether it was security-relevant. The verdict's own `amendments` array carries the ids; the card is
  where a later phase sees that the phase moved after it was first verified.
- **Open exceptions this phase closed under** — one line each: id, rule, subject and a one-clause
  reason (`python3 scripts/applicability.py list <phase-dir>`). Ids and a clause, never the reason
  prose — that lives in `exceptions.json` and `gate-overrides.log`, and the card is capped. An
  exception says a rule the pipeline would otherwise owe is not owed for this phase, and **recording
  one is manual**: nothing creates the entry, so a forgotten one stays invisible until a later phase
  wedges on it. Carrying them forward is what puts that in front of the next phase instead.
- **Findings carried as known-open at the verification attempt cap** — one line each, with the
  finding id. The cap (3 attempts, `scripts/verifier_attempts.py`) means some findings are carried
  rather than fixed, and this card is the only place that stays visible. A carried finding recorded
  nowhere is the cap turning into silent attrition.
- `verdict: pass` with `bypassed: true` means the phase passed only because findings were **waived**.
  Name each waived finding (id / who / when / reason) — a visible bypass, never a silent green.
  One line per finding on the card; the reasoning, if any, goes in the archive.
- Mutation is **`advisory` by default** — it runs and reports, and never blocks. If it ran, record the
  score and the policy on the frontmatter line; if it did not, write `n/a (off)` rather than leaving
  it blank.

## Inputs
- The finished phase (`<feature>`, `<phase>` slug).
- The phase plan — **the next phase's entry only**, to find what comes next.
- `verdict.json` for the phase, and `exceptions.json` beside it when the phase has one.
- The phase's specs and `test-mapping.md` files — for the contracts they settled, not to summarise.

## Procedure
1. Confirm the phase is green (precondition above).
2. Write `docs/features/<feature>/phases/<phase>/handover.md` in the card format below.
3. Write anything that failed the two tests to
   `docs/features/<feature>/phases/<phase>/handover-archive.md`. It carries
   `readers: none (archive of handover.md)` and no stage is instructed to open it. Write it if you
   have something to put in it; a phase with nothing to archive does not need the file.
4. Determine the next phase from the phase plan. If this was the last phase, set `next: e2e` (see
   below), and only after the e2e suite is green does the feature reach `ship`.
5. Check the card: `python3 scripts/doc_read_path.py check .` — over the cap is a fail, not a
   warning.

## If this was the LAST phase of the feature
The feature is not done yet. Every phase has proven its own slice at its own seam; nothing has yet
proven the slices add up to the feature's goal. Before the feature ships:
1. Set `next: e2e` in this handover.
2. Hand to the **implementer in `e2e-author` mode** (`skills/e2e-author`) — it writes the 1-3
   feature-level e2e tests that prove `overview.md`'s goal holds through the assembled system, into
   `tests/e2e/<feature>/` + `docs/features/<feature>/e2e-mapping.md`.
3. E2E tests are written **after** implementation, so they must be green on the first run. A red one
   is a real finding: the feature does not work end to end. Route it back rather than shipping.
4. Once they are green, the feature is `ship`.

Note the mutation gate (when a project turns it on) does not cover e2e — it is diff-scoped per phase,
and e2e tests must never be written to farm mutants. The phase verifier skips `tests/e2e/` too; they
run at feature close and in CI (`gate_ci.sh --full`).

## Output format — the card

```markdown
---
feature: <feature>
phase: <phase>
stage: handover
model: <model>
created: <date>
status: green
next: <next-phase-slug | e2e | ship>
verdict: pass | pass (bypassed) | fail
mutation: n/a (off) | <score> (policy advisory|enforce)
readers: avenger-spec-writer @ per spec (prior cards); spec gate @ the immediately prior card; spec-review (human grill) @ the immediately prior card; e2e-author @ feature close
---
## Phase <phase> — contract card

<5 lines max: what this phase delivered and what the next one depends on. No narration.>

### Binding contracts (later phases must not break these)
| contract | shape | defined in |
|---|---|---|
| <name> | <signature / fields / error mapping> | <repo-relative path> |

### Decisions locked in
- <decision, one line> — enforced by `<file that makes it true>`

### Artifacts
- verdict:        docs/features/<feature>/phases/<phase>/verdict.json
- specs:          docs/features/<feature>/phases/<phase>/specs/
- tests:          tests/<feature>/<phase>/
- implementation: docs/features/<feature>/phases/<phase>/implementation-report.md
- test-execution: docs/features/<feature>/phases/<phase>/test-execution-report.md
- archive:        docs/features/<feature>/phases/<phase>/handover-archive.md   (not on the read path)

<!-- Link every artifact that exists. A link is ~80 bytes and the artifact links are exactly what
     the card is for; omitting one makes this section lie by omission, which is not a saving. -->

### Open items
<One row per item, pointing at the structured record. Do not narrate. A row, or an explicit `none`.>
| id | kind | one-line title | where the detail lives |
|---|---|---|---|
| OBS-<n> | open-finding | <title> | verdict.json#observations[<n>] |
| FWD-<n> | forward-claim | <what a later phase must handle> | this card |

### Next phase
<next-phase-slug> — needs from this phase: <the one or two things it depends on>.
```

(Use repo-root-relative paths so links don't break when the file moves.)

**Open items are a table on purpose.** Measured over one feature: of 8 open items carried forward as
prose across 53.6 KB of *Open Items Phase N Inherits* sections, exactly **one** was ever picked up by
a later phase. The narrative was not what carried them; the id was.

### Open items carry your PREDICTIONS too, and they are owed an answer

This section is the card's only forward-looking slot, and it is binding. Phase 8 of one feature wrote
down, verbatim, that caller-supplied identifiers would become a problem in phases 9 to 12. Phase 9 was
the first such caller and **shipped exactly that defect** - a user-controlled path segment
interpolated unencoded - because the prediction was prose, and prose is owed to nobody. It was
correct, specific, actionable, and became nothing: no spec line, no test, no check.

So anything this phase believes a later phase must handle goes in this table as a row with a stable
id (`FWD-<n>`), beside the findings carried at the attempt cap (`OBS-<n>`). Two rules, both enforced
by `scripts/carried_items.py` from `scripts/hook_verifier.sh` and from `gate_ci.sh`:

- **Your own card must state what it carries** - a row per item, or an explicit `none` row.
  **Silence is not `none`.** A phase does not close on a card that says nothing here.
- **The next phase must answer every row you leave**, and does not close until it has: `built` into a
  spec, `tested`, or `declined` with a stated reason. An item that still applies further out is
  declined there and **re-carried on that phase's own card** - that is how a claim about phases 9-12
  survives without being owed to all four at once.

An id belongs to the card that declared it, so `OBS-1` here and `OBS-1` on the next phase's card are
different items; the ids you already use keep working. Never write a prediction into the summary, a
decision line or the archive instead: the archive is off the read path, so a claim put there is a
claim nobody will ever be asked about.

Your predecessor's items are the mirror obligation, and you discharge them **as you build**, not at
close: `python3 scripts/carried_items.py list <this-phase-dir>` shows what you owe, and

```bash
python3 scripts/carried_items.py discharge <this-phase-dir> <id> --as built --by <spec/requirement/test>
python3 scripts/carried_items.py discharge <this-phase-dir> <id> --as declined --reason-file <file>
```

records the answer in `carried.json` beside `verdict.json`. The reason goes in a **file** the command
reads, never inline on the command line (`pipeline-conventions` § *Gates*).

**What this does not check.** Nothing can tell that a claim you never wrote down was worth writing,
and nothing here catches the mirror defect - a card asserting a protection its own phase deleted. The
counterweight to that one is the rule this card already carries: a binding contract names the file
that *enforces* it, and a discharge names the artifact that answers it. Both are checkable by the
next reader; a sentence is not.

### Example (phase `1-webhook`)
```markdown
---
feature: clickup-intake
phase: 1-webhook
stage: handover
model: haiku
created: 2026-06-10
status: green
next: 2-analysis
verdict: pass
mutation: n/a (off)
readers: avenger-spec-writer @ per spec (prior cards); spec gate @ the immediately prior card; spec-review (human grill) @ the immediately prior card; e2e-author @ feature close
---
## Phase 1-webhook — contract card

Implemented the signed ClickUp webhook receiver with idempotent persistence keyed on delivery_id.
Valid deliveries return 200 and store exactly one row; replays are no-ops; forged signatures 401.
Phase 2 reads these rows; delivery_id is the dedup key it must not re-create.

### Binding contracts (later phases must not break these)
| contract | shape | defined in |
|---|---|---|
| persisted task row | `tasks(task_id, delivery_id, raw, received_at)` | `src/intake/models.py` |
| dedup key | `delivery_id`, unique | `migrations/0002_delivery_id.py` |

### Decisions locked in
- Signature verification happens in the handler, not middleware — enforced by `src/intake/webhook.py`

### Artifacts
- verdict:        docs/features/clickup-intake/phases/1-webhook/verdict.json
- specs:          docs/features/clickup-intake/phases/1-webhook/specs/
- tests:          tests/clickup-intake/1-webhook/
- archive:        docs/features/clickup-intake/phases/1-webhook/handover-archive.md   (not on the read path)

### Open items
| id | kind | one-line title | where the detail lives |
|---|---|---|---|
| FWD-1 | forward-claim | task_id reaches the URL path unencoded from phase 2 on | this card |

### Next phase
2-analysis — needs from this phase: the persisted task row and delivery_id.
```

## `handover-archive.md`

Everything the two tests sent out of the card: the verification narrative, warnings disposition,
attempt history, the rework arc, commits, lessons written, findings and evidence in full. It is
committed, it is durable, and **no stage reads it**. Give it frontmatter that says so:

```markdown
---
feature: <feature>
phase: <phase>
stage: handover-archive
created: <date>
readers: none (archive of handover.md)
---
```

Writing it is not a licence to write more. The archive is where the record goes, not a second
document to compose — if a section would have failed the reader test in the card, the honest version
is usually two lines here plus a link, not four pages.

## Done when
`handover.md` exists **under 6144 bytes**, with `status: green`, a `readers:` line, a ≤5-line
summary, the binding-contracts table, the decisions list, the artifact links, the phase's Verifier
verdict (plus any waived findings), the mutation line, an **Open items section that says what this
phase carries forward or explicitly says `none`**, and a `next` value (a phase slug, `e2e` if this
was the last phase, or `ship`). `python3 scripts/doc_read_path.py check .` is clean, and both
`python3 scripts/carried_items.py declared <phase-dir>` and `… due <phase-dir>` exit 0.

**And the codemap has been regenerated** — `python3 scripts/codemap.py . --lang <langs> --output
codebase`, unconditionally, as the last action of the phase (`avenger-handover` Step 4). The phase you
just closed is what made `codebase/MOC.md` stale; the next phase reads that map to learn which module
owns what. A phase that ends without it hands the next one a map of a codebase that no longer exists.

If any gate was overridden with `GATE_BYPASS`, that is recorded here too — never silently. Name the
gate and the reason, exactly as it appears in `gate-overrides.log`. One line on the card; anything
longer belongs in the archive with the card pointing at it.
