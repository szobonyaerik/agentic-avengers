# Test Sandbox Rules — portable instruction block

Copy everything below the marker into any project's `CLAUDE.md` / `AGENTS.md` (or vendor it as a
skill). It is self-contained and stack-agnostic; the examples use pytest/tmux because that is where
the incidents that produced these rules actually happened (`tests/test_tavern.py:385` in
`agentic-avengers` — a test whose teardown ran `tmux kill-server` against the operator's **real**
tmux server, four times, because `$TMUX` inherited from the developer's own pane silently overrode
the test's `TMUX_TMPDIR` sandbox; and agent-authored tests that invoked the project's own test
runner from inside a test, multiplying the suite 4× into a 50-minute run).

---

## Testing rules for agents (sandbox-only, e2e-first)

These rules override any default testing behavior. They exist because agent-written tests have
destroyed real developer state before. Violating the **hard bans** is never acceptable, even to
make a test pass.

### 1. Every test runs inside a sandbox it created

A test may only touch resources it created this run, inside a per-test temporary directory or a
private server it started itself. Concretely:

- Filesystem: only the framework's temp dir (`tmp_path`, `t.TempDir()`, `mktemp -d`). Never
  `$HOME`, dotfiles, the repo working tree, or any shared path.
- Servers/daemons (tmux, docker, databases, dev servers): start a **private instance** scoped to
  the temp dir. Never connect to one that was already running.
- Network: bind port 0 and read back the assigned port. Never hardcode a port; never call a real
  external service — fake anything that crosses a trust or cost boundary (third-party API, LLM,
  payment, email).
- Processes: spawn children in their own process group / session (`start_new_session=True`), and
  reap them in teardown by killing **that group** — never by name-matching system-wide.

### 2. Treat the inherited environment as hostile

The environment the test runner inherits can silently defeat your sandbox. The canonical failure:
a test set `TMUX_TMPDIR` to a temp dir, but pytest was itself running inside a tmux pane, so the
inherited `$TMUX` overrode it and every tmux call landed on the developer's real server — which
teardown then killed. Therefore:

- Before touching any external tool, **unset or override every environment variable that tool
  consults** (`monkeypatch.delenv` / explicit `env=` dict for subprocesses). Do not assume the
  sandbox variable you set wins.
- Prefer passing a scrubbed, explicit environment to subprocesses over mutating the test
  process's own environment.

### 3. Prove isolation before acting — and stop if the proof fails

Immediately after creating the sandbox and before the first mutating or destructive call, assert
the sandbox contains **only what this test created** (e.g. `list-sessions` returns exactly your
fixture; the temp DB has exactly your schema; the directory listing is exactly your files).
Anything unexpected means you are on somebody's real system — the test must fail *there*, before
touching anything.

### 4. Teardown by exact name, never by scope

Clean up by enumerating the resources this test created and removing each **by its exact
identifier**. Hard bans in test code and test-spawned commands, with no exceptions:

- `tmux kill-server`, `pkill`, `killall`, `kill -1`/`kill 0`, signaling process groups you did
  not create
- `rm -rf` on any path outside the test's own temp dir
- `docker system prune`, `docker rm -f $(docker ps -q)` or any "all containers/volumes" operation
- Global config mutation: `git config --global`, writes to `~/.ssh`, `~/.config`, shell rc files,
  OS keychains, crontabs, `systemctl`
- Dropping/truncating a database the test did not create this run

The point is blast radius: if isolation ever regresses, targeted teardown costs a couple of ghost
fixtures — scoped teardown costs the operator their machine.

### 5. Never invoke the test runner from inside a test

A test must not shell out to `pytest`, `npm test`, `go test`, `cargo test`, `make test`, or any
command that resolves to the project's own suite. This nests the suite inside itself — observed
in the field as the same suite running 4× back-to-back for 50 minutes. If the thing under test
*is* a test-running CLI, point it at a tiny dedicated fixture suite (2–3 trivial test files
written into the temp dir), never at the project's `tests/`.

Corollary: no test re-runs a sibling test "to be sure", and no test triggers CI, git hooks, or
`git push`.

### 6. Bound everything

- Every wait, poll, and subprocess call carries an explicit timeout (seconds, not minutes).
- On timeout, kill the child's whole process group — killing only the direct child leaves
  grandchildren piling up.
- The suite must be parallel-safe: no fixed ports, no shared lock/state files, no ordering
  dependencies between tests.

### 7. Missing sandbox tooling means skip, never fall back

If the tool a sandbox needs is unavailable (`shutil.which("tmux") is None`, no docker daemon),
**skip the test with a stated reason**. Never "fall back" to a real server, a real home
directory, or a shared instance to get a green run.

### 8. Test at the seam, inside the sandbox (e2e-first)

Tests drive requirements through the public entry point a real caller uses — the HTTP handler,
CLI, or service method — with real collaborators inside the sandbox. Faking is reserved for
trust/cost boundaries (rule 1). Do not write tests bound to private internals to avoid building
the sandbox; a test that can only pass by touching shared state is a test that must not exist.

### Self-check before committing any test

1. Could this test file, run on a stranger's laptop mid-workday, destroy or alter anything they
   would miss? If yes, it violates these rules.
2. Does teardown name every resource it removes? Grep the diff for the rule-4 hard bans.
3. Does anything here execute the project's own test suite, directly or via a wrapper script?
4. Is every environment variable the code under test reads either overridden or removed?
5. Does every blocking call have a timeout, and does timeout cleanup kill the whole process group?
