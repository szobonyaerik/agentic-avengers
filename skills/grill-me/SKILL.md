---
name: grill-me
description: Use to interrogate a human one question at a time — for scoping a task or defending a spec — resolving upstream dependencies first, exploring the codebase before asking, and each question carrying a recommendation.
---

# grill-me

Interrogate the human **one question at a time** to pin down what a document (a task's scope, or a
spec under review) must nail. This is the human-judgment half of the pipeline: it is deliberately
slow and adversarial where the automated gate is fast and shallow. Never dump a wall of questions;
ask, get the answer, then ask the next informed by it.

## Where it is used
- **Scoping** — the Task Analyst loads this to nail a task's boundaries before writing `task-analysis.md`.
- **Spec review** — the `/spec-review` step loads this to defend a spec against
  `spec-review-checklist` before tests may lock (sets `review_status: approved`).

## Rules of the interrogation
1. **Explore before you ask.** Read the relevant code and artifacts first. Never ask what the codebase
   or an existing artifact already answers — asking a question you could have looked up wastes the
   human's judgment. Ground every question in something concrete you found.
2. **Resolve upstream dependencies first.** Order questions so that anything a later answer depends on
   is settled earlier. Don't ask about an edge case before its behavior even exists in scope.
3. **One question at a time.** Ask exactly one, wait for the answer, then decide the next question from
   it. The order is part of the value — it lets a single answer collapse several downstream questions.
4. **Every question carries a recommendation.** State the option you'd pick and why, so the human can
   confirm fast or push back deliberately — never a bare open prompt. ("I'd dedupe on `delivery_id` and
   make a replay a 200 no-op — matches the existing intake path in `receiver.py`. Agree, or is there a
   reason to 409?")
5. **Chase the gap, don't accept hand-waving.** If an answer is vague, ask the sharper follow-up. "It
   should handle errors" is not resolved until you know *which* errors and *what* the observable
   behavior is.
6. **Stop when the bar is met.** For spec review that bar is `spec-review-checklist`; for scoping it is
   a task whose in/out boundaries and `work_kind` are unambiguous. Don't pad with questions past it.

## Output
- **Scoping**: the resolved answers flow into `task-analysis.md` (scope, out-of-scope, `work_kind`,
  and the mode-specific detail). Note in chat which questions materially changed the brief.
- **Spec review**: see `/spec-review` — on a clean pass, set the spec's `review_status: approved`;
  otherwise record the unresolved items and route back to the Spec Writer. Never approve a spec whose
  checklist items you could not get answered.

## Done when
Every open question the document's bar demands has a concrete, recorded answer — or, if one can't be
answered, the document is routed back rather than approved.
