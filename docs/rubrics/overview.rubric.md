# Quality rubric — overview.md

The bar the Solution Architect's output must clear. Used in tutorials and as a self-check.

- **Shape, not sequencing.** Defines components/boundaries/connections — NOT the build order (that's
  the plan) and NOT file-level code (that's implementation).
- **Every decision has a stated cost.** "We chose X because Y, at the cost of Z." A decision with no
  cost is hand-waving.
- **Grounded in the codemap.** References real modules/paths, not invented structure.
- **Phaseable.** It's obvious how this splits into dependency-ordered, independently verifiable phases.
- **Respects what exists.** Incremental evolution preferred; rewrites justified against alternatives.
- **Boundaries explicit.** States what the feature must NOT disturb.
