---
name: grill-me
description: Interview the user relentlessly about a plan, a task, or a spec until you reach shared understanding, resolving each branch of the decision tree one question at a time. Use whenever scope or requirements are ambiguous, when stress-testing a design, when a human reviews a spec before implementation begins, or when the user says "grill me". Always prefer grilling over assuming.
---

# grill-me

Interview the user relentlessly about every aspect of the thing in front of you (a task, a plan, or a
spec) until you reach a **shared, concrete understanding**. Walk down each branch of the decision
tree, resolving dependencies between decisions one by one.

## Rules
- **Ask one question at a time.** Never batch.
- **For each question, give your recommended answer** and a short reason, so the user can accept or
  push back rather than start from a blank page.
- **Resolve the most upstream dependency first.** If a question's answer changes later questions, ask
  it first.
- **If a question can be answered by exploring the codebase, explore instead of asking.**
- Stop when there are no unresolved branches that matter.

## Where this is used in the pipeline
- **task-analyst** loads it to remove scope/requirement ambiguity *before* the brief is written.
- **The spec-review gate** loads it so the human reviewer is *interrogated* about the spec — one
  question at a time, against `spec-review-checklist` — instead of passively approving. The reviewer
  only sets `review_status: approved` after they can answer for every decision. For a reviewer new to
  the system, being grilled is what turns review from a rubber stamp into a real gate.

## Spec-review framing
When grilling a spec reviewer, drive questions from the checklist: is every requirement verifiable and
ID'd? Does every requirement have paired pass/fail criteria? Does the spec contradict the overview or
a prior phase's delivered contract? For a migration: does the spec name the existing tests and the
parity it must preserve? Make the reviewer defend each "yes."
