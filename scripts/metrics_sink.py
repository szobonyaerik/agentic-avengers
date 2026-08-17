#!/usr/bin/env python3
"""The fail-open bridge from this pipeline to firstmate's per-phase metrics record.

firstmate owns the record: its shape, its units, its absence semantics, and every command that
writes it (`docs/pipeline-metrics.md`, `bin/fm-pipeline-metrics.sh`). This module owns nothing about
the schema. It shells out to that CLI so the four producer properties — write during the run, keep
every key present, make repetition converge, add no key — are enforced by their code rather than
restated in ours. There is deliberately no second store, no second file format, and no local schema.

TWO PROPERTIES GOVERN EVERYTHING HERE.

**This is measurement, not a gate.** No failure of this module may ever fail a phase. A missing CLI,
an unwritable record, a refusal, a crash, a hang — each is caught, written to the diagnostics log,
and reported to the caller as `False`. Nothing raises past `add`/`set_fields`/`ensure`, and nothing
here ever calls `sys.exit`. A metrics bug that blocked delivery would be a self-inflicted outage in
the thing meant to make delivery cheaper.

**Nothing is ever written to stdout.** Several callers are hooks whose stdout is a JSON protocol
(`hook_ponytail.sh` returns `hookSpecificOutput` there). A stray diagnostic line on stdout would
corrupt a hook's contract, so every message from this module goes to stderr and to the log file, and
every subprocess has its stdout captured.

Environment:
    AVENGER_METRICS_CMD      path to `fm-pipeline-metrics.sh`; else it is looked up on PATH
    AVENGER_METRICS_OFF=1    disable emission entirely (still fail-open, just silent)
    AVENGER_METRICS_PROJECT  the record's `project`; else the git repository's name
    AVENGER_METRICS_LOG      diagnostics path (default `<project>/.avenger-metrics.log`)
    AVENGER_METRICS_TIMEOUT  seconds allowed per CLI call (default 10)

Emission is OFF when the CLI is not resolvable, because agentic-avengers ships standalone and most
repositories have no firstmate home. That absence is logged once per process rather than passed over
in silence: a measurement layer that is quietly doing nothing is the failure this record exists to
remove.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404 — the firstmate CLI is the only supported writer of the record
import sys
from pathlib import Path

#: Written before every diagnostic so a reader can tell these lines from a gate's.
PREFIX = "[metrics]"

#: Whole seconds any single CLI call may take. A healthy call answers in tens of milliseconds, so
#: this is two orders of magnitude of headroom and is only ever reached when something is wrong. A
#: hung writer must not hold a hook open: the hook's budget is spent on the gate, and measurement
#: has no claim on it.
DEFAULT_TIMEOUT = 10

#: Set once the "no CLI resolvable" note has been made, so a hook that emits ten facts logs it once.
_announced = False

#: Set when a call fails in a way that says the writer itself is unusable — it hung, or it would not
#: start. Emission then stops for the rest of the process.
#:
#: This is not tidiness, it is the fail-open property holding under its worst case. Making the
#: record unwritable does not make firstmate's CLI fail; it makes it BLOCK, on a lock it cannot
#: take. Every emission then costs the full timeout, and a stage that records a dozen facts pays it
#: a dozen times inside a hook budget that exists for the gate. One strike is enough evidence: a
#: writer that has already proven unresponsive will not answer the next call either.
_writer_unusable = False


def _project_dir() -> Path:
    """The repository the pipeline is running in."""
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())


def note(message: str) -> None:
    """Record a diagnostic on stderr and in the log. Never raises, never touches stdout."""
    line = f"{PREFIX} {message}"
    try:
        print(line, file=sys.stderr)
    except Exception:  # noqa: BLE001 — a closed stderr must not break the caller
        pass
    try:
        path = os.environ.get("AVENGER_METRICS_LOG") or str(_project_dir() / ".avenger-metrics.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — an unwritable log is not worth a second failure
        pass


def cli() -> str | None:
    """Path to firstmate's `fm-pipeline-metrics.sh`, or None when emission is not configured."""
    if os.environ.get("AVENGER_METRICS_OFF") == "1" or _writer_unusable:
        return None
    explicit = os.environ.get("AVENGER_METRICS_CMD")
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    return shutil.which("fm-pipeline-metrics.sh")


def configured() -> bool:
    """Whether a writer is NAMED for this run, whether or not it currently works.

    `cli()` answers "can anything be written right now" and folds four different states into one
    `None`: emission turned off, no writer anywhere, a named writer that is not executable, and a
    writer that already proved unusable. Only the second is a standing condition of the environment
    that the stage running the command cannot repair by trying again - which is the one distinction
    `pipeline_metrics.py defect` has to draw before telling anyone to re-run it.
    """
    if os.environ.get("AVENGER_METRICS_CMD"):
        return True
    return shutil.which("fm-pipeline-metrics.sh") is not None


def _unresolvable_reason() -> str:
    """Why `cli()` resolved to nothing, in the words of the state that is actually true.

    `cli()` returns `None` for two unconfigured shapes, and they have different remedies. Reporting
    the first for the second is not a cosmetic slip: `pipeline_metrics.py defect` sends its reader to
    this line for the cause, and a reader told "AVENGER_METRICS_CMD is unset" over a variable they
    just set has been handed a remedy already applied, so they re-run and get the same pair back.
    """
    named = os.environ.get("AVENGER_METRICS_CMD")
    if named:
        return (
            f"AVENGER_METRICS_CMD names {named}, which is not an executable file (it is missing, or "
            "it has no exec bit) - this run records no pipeline metrics. Make that path executable, "
            "or point the variable at firstmate's own fm-pipeline-metrics.sh."
        )
    return (
        "fm-pipeline-metrics.sh is not on PATH and AVENGER_METRICS_CMD is unset — this run "
        "records no pipeline metrics. Set AVENGER_METRICS_CMD to firstmate's copy to enable it."
    )


def enabled() -> bool:
    """Whether a record can be written at all, announcing the answer once if it cannot."""
    global _announced
    if cli() is not None:
        return True
    # Not when the breaker tripped: it has already said why, and repeating "no writer is configured"
    # over a writer that IS configured and hung sends the reader to their PATH for a stuck lock.
    if not _announced and not _writer_unusable and os.environ.get("AVENGER_METRICS_OFF") != "1":
        _announced = True
        note(_unresolvable_reason())
    return False


def timeout() -> int:
    """Whole seconds any single CLI call may take. Public because it is a budget others spend.

    `gate_timeouts.py` checks the worst-case metrics wall clock of a gate hook against that hook's
    headroom, and it has to check the bound that is actually in effect rather than a second copy of
    this resolution.
    """
    try:
        return max(1, int(os.environ.get("AVENGER_METRICS_TIMEOUT") or DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def run(*args: str) -> tuple[int, str, str] | None:
    """Call the firstmate CLI. Returns (rc, stdout, stderr), or None when it could not run.

    Every exception is swallowed by design: a subprocess that will not start, a binary that hangs,
    an OS that refuses the fork. The caller learns only that no measurement was taken.
    """
    command = cli()
    if command is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 — fixed executable, list argv, no shell
            [command, *args],
            capture_output=True,
            text=True,
            timeout=timeout(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        _stop_writing(f"timed out after {timeout()}s on `{' '.join(args[:3])}`")
        return None
    except Exception as exc:  # noqa: BLE001 — measurement never propagates a failure
        _stop_writing(f"could not run {command}: {type(exc).__name__}: {exc}")
        return None
    return proc.returncode, proc.stdout, proc.stderr


def _stop_writing(reason: str) -> None:
    """Give up on the writer for the rest of this process, saying so once."""
    global _writer_unusable
    if not _writer_unusable:
        _writer_unusable = True
        note(f"{reason} — no further metrics are recorded in this process")


def project() -> str | None:
    """The record's `project`: the grouping key every firstmate lookup is scoped by.

    A phase number means nothing outside the project that measured it, so this is identity rather
    than a measurement. It is the repository's own name unless the environment names another.
    """
    explicit = os.environ.get("AVENGER_METRICS_PROJECT")
    if explicit:
        return explicit.strip() or None
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(_project_dir()), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=timeout(), check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).name
    except Exception:  # noqa: BLE001 — not a git repo, no git, or a hung git
        pass
    return _project_dir().name or None


def show(phase: str) -> dict | None:
    """The record for `phase`, or None when it does not exist or cannot be read."""
    result = run("show", phase)
    if result is None or result[0] != 0:
        return None
    try:
        record = json.loads(result[1])
    except ValueError:
        note(f"phase {phase}: `show` returned output that is not JSON")
        return None
    return record if isinstance(record, dict) else None


def _nearest_earlier(phase: str, name: str | None) -> str | None:
    """The highest-numbered existing record of THIS project below `phase`.

    This is firstmate's definition of "the previous phase" and it is not always phase minus one: one
    home may hold several pipelines interleaved in the same filename namespace. Opening from it is
    what carries the ledger — findings left open and hypotheses left pending — so a phase opened
    without it would read as its project's first and answer for nothing.
    """
    try:
        current = int(phase)
    except ValueError:
        return None
    for candidate in range(current - 1, -1, -1):
        record = show(f"{candidate:02d}")
        if record is not None and record.get("project") == name:
            return f"{candidate:02d}"
    return None


def ensure(phase: str) -> bool:
    """Make sure a record exists for `phase`, opening it from this project's predecessor if any."""
    if not enabled():
        return False
    if show(phase) is not None:
        return True
    name = project()
    argv = ["init", phase]
    if name:
        argv += ["--project", name]
    previous = _nearest_earlier(phase, name)
    if previous is not None:
        argv += ["--from", previous]
    result = run(*argv)
    if result is not None and result[0] == 0:
        return True
    # A concurrent hook may have opened it between the check and the call; that is a success, not a
    # failure. Anything else is reported with firstmate's own words, which name the remedy.
    if show(phase) is not None:
        return True
    if result is not None:
        note(f"phase {phase}: could not open a record (exit {result[0]}): {result[2].strip()}")
    return False


def assignment(key: str, value: object) -> str:
    """Encode one field for `set`/`add`: `k=<string>` for text, `k:=<json>` for everything else."""
    if isinstance(value, str):
        return f"{key}={value}"
    return f"{key}:={json.dumps(value)}"


def _write(verb: str, phase: str, head: list[str], fields: dict[str, object]) -> bool:
    if not ensure(phase):
        return False
    argv = [verb, phase, *head, *(assignment(k, v) for k, v in fields.items())]
    result = run(*argv)
    if result is None:
        return False
    if result[0] != 0:
        note(f"phase {phase}: {verb} refused (exit {result[0]}): {result[2].strip()}")
        return False
    return True


def set_fields(phase: str, **fields: object) -> bool:
    """Set top-level scalars on the phase record."""
    return _write("set", phase, [], fields)


def add(phase: str, collection: str, **fields: object) -> bool:
    """Upsert one entry into a collection, by its identity field.

    Re-emitting the same identity converges on one entry rather than appending a second, which is
    what lets an emission point fire on every observation without the caller tracking what it has
    already said.
    """
    return _write("add", phase, [collection], fields)
