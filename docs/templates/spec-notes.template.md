---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
stage: spec-gate
readers: implementer @ once, before building this spec
links: spec.md
created: <ISO-8601-timestamp>
---

# Known-open notes

<!--
GENERATED. `scripts/spec_notes.py` writes this file from the spec gate's triage decision, and
rewrites it from scratch on every gate run — it is the current gate's view, not a log. Do not edit
it by hand: the next gate run will overwrite it, and a note you added would vanish without anyone
noticing. A run that produces no notes deletes this file rather than leaving a stale one.

This template exists because every document class on the read path names the source that makes its
writer emit `readers:`. That source is scripts/spec_notes.py, and this is what it emits.
-->

Observations the spec gate recorded and **deliberately did not block on**. None of these stops you
building this spec. Read them once, use your judgement, and do not treat any of them as a
requirement: the spec's `## Requirements` section is the contract, not this file.

- **<requirement id or heading>** (<area>) — <what the gate observed>
  - triaged as a note because: <why the triage pass classified it as a note rather than a blocker>
