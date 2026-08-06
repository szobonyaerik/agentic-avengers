# E2E-Only Testing Policy — portable instruction block

Copy everything below the marker into any project's `CLAUDE.md` / `AGENTS.md`. It replaces the
default agent testing behavior entirely.

**The premise.** Agents left to their own devices write tests *from* the code — they read the
implementation, snapshot what it does, and call that a test. Those tests prove nothing (the code is
testing itself), and they immediately become the most expensive thing in the repo: the agent burns
turns "fixing tests" instead of the product, and the environment grows fixtures, mocks, factories
and harnesses whose only purpose is servicing tests that never caught a bug. Observed failure
modes that produced this policy: tautological suites, a test suite nested inside itself running 4×
for 50 minutes, and a test whose teardown killed the operator's real tmux server because its
sandbox was cosmetic.

The fix is not better unit tests. It is **fewer, harder tests at the only boundary that matters**:
what the user does, checked in a sandbox, derived from the requirement — never from the code.

---

## Testing policy for agents (e2e-only)

These rules override all default testing behavior, including any instinct to "add unit tests",
"improve coverage", or "test the edge cases of this function". Coverage of internals is
explicitly a non-goal.

### 1. The only tests that exist are end-to-end tests at the user boundary

A test drives the product exactly the way a user does — a real CLI invocation, a real HTTP
request against a running instance, a real UI action — inside a sandbox, and asserts on what the
user would observe: output, response, exit code, resulting state.

Everything else is banned. Do not write unit tests, internal integration tests, or tests of
private functions, classes, or modules. Do not mock, stub, or fake any code that belongs to this
project. The only permitted fakes are for **trust/cost boundaries** the sandbox genuinely cannot
contain: third-party APIs, LLM calls, payments, email.

### 2. Tests are derived from the requirement, never from the code

Write the test — inputs and expected outcomes — from the task description alone, before or
without reading the implementation. The expected value is what the requirement says *should*
happen, stated by you or the user in plain terms.

Hard ban: running the code and pasting its output into the assertion. That is not a test, it is a
snapshot of the current bugs, and it inverts the entire relationship — the code must match the
test, never the other way around.

### 3. Hard budget: few tests, fast suite

- **Per feature: 1–3 e2e tests, 5 absolute maximum.** One test per user-visible behavior the
  feature promises. Edge cases of internals do not get tests.
- **The whole suite must run in minutes, not tens of minutes.** If it doesn't, delete tests —
  don't parallelize, cache, or shard your way around the budget.
- Adding a test beyond the budget requires deleting one or an explicit user decision. Duplicated
  coverage is deleted on sight.

The budget is the anti-bloat mechanism. It is deliberate that some behavior goes untested; a
small suite that gets read and trusted beats a large one that gets serviced.

### 4. A failing test indicts the code, never the test

When a test fails, the code is wrong until proven otherwise. You may not edit, weaken, skip,
delete, or add tolerance to a test to make it pass. The only legitimate reason a test changes is
that the *requirement* changed — and that is a user decision, confirmed explicitly in the
conversation, never inferred.

Corollary: since tests only touch the user boundary, **no refactor may ever require a test
edit**. If your refactor broke a test, you changed behavior — that's the test doing its one job.

### 5. Zero test infrastructure

The entire testing apparatus is: the stock test runner, a temp directory, and the product's own
public entry point. Nothing else. No fixture hierarchies, no factory libraries, no custom
harnesses, no conftest webs, no test-only config flags or hooks added to the product.

If a test is hard to set up, that is a product defect — the entry point is hard to use — and the
fix belongs in the product, not in test scaffolding. Test-driven environment complexity is
exactly the failure this policy exists to stop.

### 6. Sandbox invariants (non-negotiable)

Every test runs in a sandbox it created, and its blast radius on any failure is that sandbox:

- Filesystem: the runner's temp dir only. Never `$HOME`, dotfiles, or the repo working tree.
- Servers/daemons: start a private instance scoped to the temp dir; never connect to one already
  running. Bind port 0; never hardcode ports.
- **Scrub the inherited environment.** Unset or override every env var the tools you touch
  consult — an inherited variable can silently defeat your sandbox and land every call on the
  developer's real system. Prove isolation before the first destructive call: assert the sandbox
  contains only what this test created, and fail right there if it doesn't.
- **Teardown by exact name only.** Banned with no exceptions: `kill-server`, `pkill`, `killall`,
  signaling process groups you didn't create, `rm -rf` outside the temp dir, `docker system
  prune`/"all containers" operations, and any global config mutation (`git config --global`,
  `~/.ssh`, shell rc files, crontabs, `systemctl`).
- **Never invoke the project's own test runner from inside a test**, nor any wrapper that
  resolves to it (`make test`, `npm test`). No test re-runs another test.
- Every wait and subprocess call has an explicit timeout in seconds; timeout cleanup kills the
  child's whole process group. Spawn children with their own process group/session.
- Missing sandbox tooling (no tmux, no docker) → skip with a stated reason. Never fall back to a
  real server or shared instance to get green.

### Self-check before committing any test

1. Does this test drive the product through the same entry point a user would, and nothing else?
2. Did the expected values come from the requirement — or did I peek at what the code produces?
3. Is the feature at or under its test budget? Does the suite still finish in minutes?
4. Run on a stranger's laptop mid-workday, could this file destroy or alter anything they'd miss?
5. Did I add *any* file, flag, or helper that exists only to serve tests? If yes, remove it and
   fix the entry point instead.
