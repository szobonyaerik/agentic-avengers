---
feature: <feature>
phase: <n>-<slug>
stage: handover-archive
created: YYYY-MM-DD
readers: none (archive of handover.md)
---

<!-- The phase's written record, kept in full and committed — and deliberately OFF the read path.
     No pipeline stage is instructed to open this file. That is what makes the 6 KB cap on the
     contract card affordable rather than lossy: the record survives, the read cost does not.

     `readers: none (archive of handover.md)` is not boilerplate. It is the declaration the
     read-path check looks for — a document with no reader has to say so, which is what stops the
     next artifact class being invented without anyone asking who opens it.

     Writing this file is not a licence to write more. If a section failed the reader test in the
     card, the honest version here is usually two lines and a link, not four pages. -->

# Phase <n>-<slug> — archive

## Verification narrative
<!-- Attempts, what each one found, how it was resolved. The structured form is verdict.json;
     this is the prose nobody is required to read. -->

## Warnings — disposition
<!-- verdict.json `warnings[]` carries the structured form. -->

## Prior findings, re-proved closed
<!-- verdict-attempt-<n>.json carries the superseded attempts themselves. -->

## Findings and evidence worth preserving in full

## Commits
<!-- `git log --oneline <range>` is authoritative and cannot go stale. Prefer the range to a list. -->

## Lessons written this phase
<!-- docs/lessons/ + lessons.json is the index, read index-first at preflight. Ids only. -->
