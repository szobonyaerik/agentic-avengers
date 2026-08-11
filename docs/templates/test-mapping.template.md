---
feature: <feature>
phase: <n>-<slug>
spec: <n>.<k>-<subslug>
stage: test-mapping
created: YYYY-MM-DD
readers: avenger-verifier @ per phase; verifier bundle @ changed specs only
---

<!-- The TABLE and nothing else. This file is re-bundled to the cross-family reviewer on every
     verifier attempt, so a phase pays for it once per attempt — which is why mutation evidence,
     route-back history, build order, deviations and tests covering no requirement live in
     test-evidence.md beside it, opened on route-back only. Nothing is deleted; the sidecar is
     committed.

     `binding:` says whether a requirement gets a test and where; `level` says how it is driven.
     Every requirement not marked `binding: none` appears in some row. A journey is one row at
     `level: e2e` listing SEVERAL ids. `narrow` requires the written justification in the last
     column and is allowed only when a requirement has no reachable seam. -->

# <n>.<k>-<subslug> — test mapping

| requirement id(s) | test name(s) | level | why |
|---|---|---|---|
| R<n>.<k>.<m>, R<n>.<k>.<m+1> | test_<journey> | e2e | the user path both `binding: e2e` ids sit on |
| R<n>.<k>.<m> | test_<name> | integration | drives <seam> with real collaborators |
| R<n>.<k>.<m> | test_<name> | narrow | <mandatory: the requirement has no reachable seam, because …> |
