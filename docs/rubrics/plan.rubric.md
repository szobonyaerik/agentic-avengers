# Quality rubric — plan.md

The bar the Implementation Planner's output must clear.

- **Phase = one independently verifiable slice.** Each phase ends in a clean Verifier pass. If two
  parts can't be verified together coherently, they're two phases.
- **Specs are listed and numbered `<n>.<k>`.** Candidate specs per phase are named; the Spec Writer
  finalizes the count.
- **Strict dependency order.** A phase depends only on earlier phases. Riskiest/foundational first.
- **Real paths.** Each phase names the files/modules it touches.
- **Outcome-level acceptance.** "Done when" is an outcome, not a test list (that's the Spec Writer).
- **No file-level implementation.** The plan sequences; it does not prescribe code.
