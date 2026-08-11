#!/usr/bin/env python3
"""Run a child in its OWN process group, so a timeout actually stops the work.

`subprocess.run(cmd, capture_output=True, timeout=300)` stops the process it started and nothing
else. The gate's child is a CLI that spawns its own workers, and those workers:

  * inherit the stdout/stderr pipes, so the pipes stay open after the direct child is killed, and
  * keep running — keep talking to the provider, keep spending — after the gate has reported the
    call dead and moved on.

Gate runs REPORTING a 300s timeout were observed against 569s, 3818s and 4276s of real provider
activity. The gate was not wrong about having given up; it was wrong that giving up stopped
anything.

So: `start_new_session=True` puts the child in a fresh process group, and a timeout signals the
GROUP — SIGTERM, a short grace, then SIGKILL — before draining and closing the pipes. Whatever the
child spawned goes with it, unless it deliberately escaped into a session of its own, which no
provider CLI does.

The result also carries `elapsed`, measured. The old failure message quoted the configured
constant ("timed out after 300 seconds"), which is a statement about the config, not about the run;
a measured number cannot drift from what happened.

Stdlib only — imported by gate_runner.py, which ships vendored.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass

#: Seconds a signalled group gets to exit on SIGTERM before SIGKILL. Long enough for a CLI to flush
#: and close, short enough that a wedged one does not extend the timeout it already blew.
TERM_GRACE_S = 5.0

#: Seconds to wait for the pipes to drain after the group is dead. On a normal kill this is
#: instant; a bound is kept anyway so a pipe held by a process that escaped the group cannot turn
#: the cleanup into the very hang this module exists to remove.
DRAIN_S = 5.0


@dataclass(frozen=True)
class ChildResult:
    """What a child run produced, including how long it really took."""

    returncode: int
    stdout: str
    stderr: str
    elapsed: float
    timed_out: bool


def _signal_group(pgid: int, proc: subprocess.Popen, sig: int) -> None:
    """Signal the child's whole process group, tolerating a group that is already empty."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        # The group is gone entirely. Fall back to the direct child so the common case still dies.
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


def _kill_group(pgid: int, proc: subprocess.Popen) -> None:
    """SIGTERM the group, wait out TERM_GRACE_S, then SIGKILL whatever is left.

    The wait watches the DIRECT child only, because that is all `poll()` can see; the SIGKILL is
    therefore unconditional. A group whose leader exited early still has members — that is the whole
    leak — so "the child is gone" is never evidence that the work stopped.
    """
    _signal_group(pgid, proc, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    _signal_group(pgid, proc, signal.SIGKILL)


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    """Collect whatever output survived the kill, without ever blocking indefinitely."""
    try:
        return proc.communicate(timeout=DRAIN_S)
    except subprocess.TimeoutExpired:
        # A pipe is still held by something outside the group. Closing our ends releases us; the
        # output is lost, which is the correct trade against hanging the gate.
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return "", ""


class _KillGroupOnSignal:
    """While a child is running, take the group down with us if WE are killed.

    A timeout is not the only way the call ends early: the hook wrapping this can be killed by the
    harness, and until now that left the provider call running exactly as a timeout did. Same leak,
    different trigger. The handler tears the group down, then restores the previous disposition and
    re-raises, so the process still dies the way the signal says it should.

    Installing a handler is only legal on the main thread; off it, this is a no-op rather than an
    error, because a gate that cannot install a handler must still run.
    """

    def __init__(self, pgid: int, proc: subprocess.Popen) -> None:
        self._pgid, self._proc, self._previous = pgid, proc, {}

    def __enter__(self) -> _KillGroupOnSignal:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                self._previous[sig] = signal.signal(sig, self._handle)
            except (ValueError, OSError):  # not the main thread, or signal unavailable
                pass
        return self

    def _handle(self, signum, _frame) -> None:
        _kill_group(self._pgid, self._proc)
        signal.signal(signum, self._previous.get(signum, signal.SIG_DFL))
        os.kill(os.getpid(), signum)

    def __exit__(self, *_exc) -> None:
        for sig, previous in self._previous.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError):
                pass


def run_bounded(cmd: list[str], timeout: float) -> ChildResult:
    """Run `cmd` in its own process group and return its result, or a killed-group timeout.

    Never raises on timeout: a timeout is a result with `timed_out=True` and a measured `elapsed`,
    which the caller turns into a `timeout`-caused GateError. FileNotFoundError still propagates —
    a missing binary is a different failure and the caller names it differently.
    """
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    # Read the group id NOW, while the child certainly exists. `communicate(timeout=…)` reaps a
    # child that exits early, and `os.getpgid()` on a reaped pid raises — which is precisely the
    # case that leaks: the CLI returns immediately and leaves its workers holding the pipe. Asking
    # for the group after the timeout answered "no such process" and the group survived untouched.
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = proc.pid  # start_new_session made the child its own leader, so this is the group
    with _KillGroupOnSignal(pgid, proc):
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(pgid, proc)
            stdout, stderr = _drain(proc)
            return ChildResult(
                returncode=proc.returncode if proc.returncode is not None else -signal.SIGKILL,
                stdout=stdout or "",
                stderr=stderr or "",
                elapsed=time.monotonic() - start,
                timed_out=True,
            )
    return ChildResult(
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        elapsed=time.monotonic() - start,
        timed_out=False,
    )
