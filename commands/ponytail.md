---
description: Load the ponytail minimalism ladder into the current thread, for implementing inline instead of through an implementer subagent.
allowed-tools: Read
---

Read `skills/ponytail/SKILL.md` from the plugin root and follow it for the rest of this thread.

**Why this command exists.** `skills/ponytail` is injected automatically into
`avenger-backend-architect` and `avenger-frontend-developer` by the `SubagentStart` hook. That hook
only fires when an implementer is *spawned*. When you implement inline in the main thread instead,
nothing injects it — this command closes that gap, on demand.

It is deliberately **not** on by default in the main thread: that thread also writes specs, drives
`/spec-review`, and runs verifier triage, and a resident "write less code" persona biases all three.

After loading, state in one line that ponytail is active, then continue with the task. Everything in
the skill's **Boundaries** section applies here too — production code only, never the tests, never a
requirement.
