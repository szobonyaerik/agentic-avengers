#!/usr/bin/env bash
# require_gate_runner <path> — refuse to gate through a runner that cannot identify itself.
#
# The gate used to be trusted by path: whatever sat at "$SD/gate_runner.py" formed the verdict. A
# scaffold on a temp path once printed a bare `GO` having checked nothing, and it was disbelieved
# only because an unrelated JSON-shape requirement happened to fail first. Nothing in the pipeline
# noticed that the gate was a stub — the pass looked exactly like a real pass, which is the shape of
# every defect in this batch.
#
# So the runner must say what it is, and its answer must match the file that answered:
#
#   1. `--identify` must print `<ABI> <sha256-of-the-runner-file>`;
#   2. the ABI must be the one this pipeline speaks;
#   3. the digest the runner reports must equal the digest WE compute over that same file — a stub
#      that hardcodes a plausible line cannot also be the file it names.
#
# What this does NOT do: prove the runner behaves. A deliberate double that implements `--identify`
# honestly passes, which is exactly what the test doubles in tests/ are. It bounds ACCIDENTS — a
# scaffold, a truncated copy, a half-vendored install — not a determined forgery. For a real pin,
# set GATE_RUNNER_SHA256 to the digest you expect and no other file can stand in at all.
#
# Sourced, not executed:
#     . "$SD/gate_runner_guard.sh"
#     require_gate_runner "$SD/gate_runner.py" || exit 2

GATE_RUNNER_ABI="agentic-avengers/gate_runner v1"

_gr_sha256 () {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum   >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}

require_gate_runner () {
  local runner="$1" reported actual expected_abi
  expected_abi="$GATE_RUNNER_ABI"

  if [ ! -f "$runner" ]; then
    echo "[gate_runner] FAIL cause=runner-untrusted: no gate runner at $runner" >&2
    echo "  meaning: the gate runner did not identify itself as the shipped runner" >&2
    return 2
  fi

  reported="$(python3 "$runner" --identify 2>/dev/null | head -1)"
  actual="$(_gr_sha256 "$runner")"

  if [ "$reported" != "$expected_abi $actual" ]; then
    echo "[gate_runner] FAIL cause=runner-untrusted: $runner is not the shipped gate runner." >&2
    echo "  meaning: the gate runner did not identify itself as the shipped runner" >&2
    echo "  expected --identify to print: $expected_abi $actual" >&2
    echo "  got:                          ${reported:-(nothing)}" >&2
    echo "  A runner that cannot identify itself may be a scaffold that returns GO having checked" >&2
    echo "  nothing. Refusing rather than trusting it." >&2
    return 2
  fi

  if [ -n "${GATE_RUNNER_SHA256:-}" ] && [ "$GATE_RUNNER_SHA256" != "$actual" ]; then
    echo "[gate_runner] FAIL cause=runner-untrusted: $runner does not match the pinned digest." >&2
    echo "  meaning: the gate runner did not identify itself as the shipped runner" >&2
    echo "  pinned GATE_RUNNER_SHA256: $GATE_RUNNER_SHA256" >&2
    echo "  actual:                    $actual" >&2
    return 2
  fi
  return 0
}
