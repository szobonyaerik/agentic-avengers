---
name: pipeline-retrospective
description: How the orchestrator observes the pipeline's own behaviour during a run and turns it into improvement issues on the agentic-avengers repo. Use while driving /avenger-run (to record an observation the moment it happens) and at feature close (to triage the accumulated set with the human). Always record successes as well as defects.
---

# pipeline-retrospective

The pipeline improves itself by noticing how *it* behaved, not just whether the feature shipped.
The orchestrator is the only agent that sees every stage, every route-back and every gate verdict,
so it is the one that keeps this log.

> **This is not `docs/lessons/`.** That log (the `self-improvement` skill) is **per-project** and
> about the **work** — a pytest trap, a migration gotcha. This one is about the **machinery**, and
> its destination is the **agentic-avengers repo**. A note like "the fidelity rubric NO-GO'd the
> same spec three times" is useless to the project you happen to be building; it is a change
> request against the pipeline. Keep them apart — if you find yourself writing about the project's
> code here, it belongs in `docs/lessons/`.

## What to record

All of these are already on disk. You are reading signals, not instrumenting anything new.

| Kind | Signal | Reads as |
|---|---|---|
| `gate-friction` | `fidelity_verdict: NO-GO` repeatedly on one spec | rubric too strict, or spec-writer guidance too thin |
| `route-back` | `findings[].kind` in `verdict.json` | a spike in *wrong/gamed test* means the `tdd` guidance is failing, not the implementer |
| `bypass` | `bypassed: true`, `break_glass`, `waiver_reason` | which gate people route around |
| `stage-churn` | the same stage resolved repeatedly by `pipeline_state.py` | a stage that cannot get it right first time |
| `success` | a gate that caught something real | evidence for **keeping** a gate — record these |
| `other` | anything notable that does not fit | — |

**Record successes.** A log of only complaints argues for deleting every gate. "The verifier caught
a tautological test here" is the evidence that justifies the gate's cost, and it is the first thing
you will want when someone proposes removing it.

**One observation, one entry.** Do not batch a run's worth of friction into a single note — each
becomes a separate issue with its own evidence, and a merged note cannot be triaged individually.

## Recording (during the run — as it happens)

Never wait until the end to write these up from memory: `/avenger-run` resumes across sessions, and
a `/clear` or a closed laptop between phase 2 and phase 6 takes your recollection with it.

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" append <feature> \
  --root "${CLAUDE_PROJECT_DIR}" \
  --kind <gate-friction|route-back|bypass|stage-churn|success|other> \
  --note "<what happened, and what it suggests about the pipeline>" \
  --evidence "<path>" [--evidence "<path>" ...]
```

Write the note so it is actionable **without** the run's context — it will be read weeks later, in a
different repo. "Fidelity NO-GO'd 1.2 three times on wording, not substance; the rubric may be
penalising terse acceptance criteria" beats "fidelity gate annoying".

## Preflight sweep (start of every interactive `/avenger-run`)

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" pending --root "${CLAUDE_PROJECT_DIR}"
```

Any feature it lists has observations no human has seen. **Triage those first**, before starting new
work — including features other than the one being run.

This exists because of `--auto`. An unattended run skips the triage, and `pipeline_state.py` makes
`done` a **terminal** state, so nothing will ever re-trigger that feature's own close. Without this
sweep, everything an `--auto` run learned is stranded on disk forever.

## Triage (at feature close, stage `done`)

1. **Final sweep.** Re-read `verdict.json` for every phase, `gate-overrides.log`, and the specs'
   `fidelity_verdict` stamps. Add anything the run revealed that you did not catch live.
2. **Render a lavish triage artifact.** Load the `lavish` skill and open its **`input` playbook** —
   it exists precisely to collect a structured selection from inside the artifact. Show one card per
   observation with its kind, its evidence paths, and *what change it implies*. Then:
   ```bash
   lavish-axi .lavish/<feature>-retrospective.html
   lavish-axi poll .lavish/<feature>-retrospective.html
   ```
3. **The human selects.** Nothing is filed that they did not pick. Selecting nothing is a valid,
   complete triage.
4. **File the selected ones** as issues on the pipeline repo (`gh-axi` skill):
   ```bash
   gh-axi issue create --repo szobonyaerik/agentic-avengers \
     --title "<imperative: the change, not the complaint>" \
     --label pipeline-improvement \
     --body "<observation + evidence paths + the concrete change proposed>"
   ```
   Title the issue as the **change** ("loosen the fidelity rubric on terse acceptance criteria"),
   not the symptom ("fidelity gate is annoying"). Carry the evidence paths into the body so it is
   actionable without re-deriving them.
5. **Close the loop** — always, even if nothing was selected:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/pipeline_observations.py" resolve <feature> \
     --root "${CLAUDE_PROJECT_DIR}"
   ```
   Dismissing *is* a decision. Without this the same set is re-presented on every future run and the
   triage becomes noise people learn to skip.

## Hard rules

- **Never file an issue without an explicit human selection.** Issue creation is outward-facing and
  permanent; the triage step *is* the confirmation gate. No selection → no issue.
- **Under `--auto`: record, never triage.** There is no human to poll and a foreground `lavish-axi
  poll` would hang the run. Leave the log `triage: pending` and let the next interactive run's
  preflight sweep find it. Do **not** auto-file issues to compensate.
- **Never edit the `triage:` flag by hand.** Use `resolve`; it is idempotent, and hand-editing
  frontmatter is how a log ends up unparseable (which the sweep then reports as pending forever).
- **The log is additive.** Append observations; never rewrite or delete an earlier one to tidy up.
