---
feature: <feature>
type: architecture-overview
status: draft
created: YYYY-MM-DD
readers: avenger-implementation-planner @ once; avenger-spec-writer @ per spec (whole); spec gate @ per spec (## Contracts and Decisions header only); spec-review (human grill) @ per spec (## Contracts and Decisions header only); e2e-author @ feature close (the goal)
---

# <Feature> — Architecture Overview

## Contracts and Decisions
<!-- A STABLE HEADER, and the only section some readers open. The Spec Writer reads this file whole;
     the spec gate reads THIS SECTION ONLY, once per spec. So everything a later spec can
     CONTRADICT belongs here: interfaces and their signatures, decisions with what each one costs,
     and the invariants that hold across phases. One line each, pointing at where it is defined.
     Do not rename this heading — it is a read target. -->

## Summary
<!-- 2–4 sentences: what it does and the SHAPE of the solution. -->

## Context & fit
<!-- modules touched, boundaries respected, and what must NOT be disturbed -->

## Components & responsibilities
<!-- one line per piece. GOOD: "RunsRouter — exposes /runs, delegates to RunService." -->

## Data / control flow

## Key decisions & trade-offs
<!-- Each: the choice, why it wins, what it COSTS. The spec reviewer checks specs against these —
     a decision with no stated cost is hand-waving. -->

## Interfaces & contracts

## Phase breakdown (dependency / risk order)
<!-- candidate phases, riskiest/most foundational first, one line each. The Planner refines this. -->

## Risks & assumptions

## Out of scope
