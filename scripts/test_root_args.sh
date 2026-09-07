#!/usr/bin/env bash
# Sourced, never executed:
#   . "$SD/test_root_args.sh"
#   test_root_pytest_args "$SD" || echo "  $TEST_ROOT_SCOPE" >&2
#   pytest -q "${TEST_ROOT_ARGS[@]}"
#
# ONE assembly of the project's declared test roots into a pytest argument list, because two shell
# copies of it is the drift this exists to remove. `subprocess_check.test_roots()` owns the
# declaration; this owns the single translation of that answer into arguments, and both the
# in-session phase gate (`hook_verifier.sh`) and the gate floor (`gate_ci.sh`) ask here. They ran
# different populations before: the hook passed pytest no root and a hardcoded `--ignore=tests/e2e`,
# and the floor still did after the hook was fixed - so on any project whose tests are not at
# `tests/` the ignore matched nothing and the feature-level e2e suite both gates exclude by design
# was collected anyway.
#
# It decides NOTHING. It assembles arguments and reports whether the declaration resolved; what a
# red suite means is the caller's verdict, which is why this file carries no exit code of its own.
#
# bash 3.2-compatible (macOS default), and safe under `set -u`: `TEST_ROOT_ARGS` is never left empty
# at the point a caller expands it.

# Populates TEST_ROOT_ARGS (array) and TEST_ROOT_SCOPE (human-readable).
# Returns 0 when at least one declared root resolved on disk, 1 when the run fell back to the
# whole tree - which a caller SAYS out loud, because silently switching population is the defect.
test_root_pytest_args() {
  local sd="$1" root roots
  roots=$(python3 "$sd/subprocess_check.py" --print-roots 2>/dev/null)

  TEST_ROOT_ARGS=()
  while IFS= read -r root; do
    # A declared root that does not EXIST is skipped, never handed to pytest: pytest treats a
    # missing path as a usage error, which a caller reads as a RED suite, where a project with no
    # tests yet must simply collect nothing. Same rule `subprocess_check.py` applies to an absent
    # root - clean, but never silent.
    #
    # Each root and each `--ignore=` is ONE array element. Built as a string and word-split at the
    # call, a root containing whitespace became two nonexistent paths, so pytest exited on a usage
    # error and the caller reported failing tests over a quoting fault. `SUBPROC_CHECK_PATHS` is
    # operator-set, so that is reachable.
    [ -n "$root" ] && [ -d "$root" ] || continue
    TEST_ROOT_ARGS+=(--ignore="$root/e2e" "$root")
  done <<INNER
$roots
INNER

  if [ ${#TEST_ROOT_ARGS[@]} -gt 0 ]; then
    TEST_ROOT_SCOPE="declared test roots minus e2e"
    return 0
  fi

  # No declared root exists on disk (or none could be resolved). Keep the previous whole-tree form:
  # in a project with no tests it collects nothing and exits 5, which every caller treats as "not a
  # failure" rather than as a red suite.
  TEST_ROOT_ARGS=(--ignore=tests/e2e)
  TEST_ROOT_SCOPE="full suite minus e2e (no declared test root exists)"
  return 1
}
