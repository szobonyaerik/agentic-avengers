#!/usr/bin/env python3
"""The command the project DECLARES its suite is run with, against the command that actually ran.

Issue #119. A project's `.no-mistakes.yaml` declared `test: "pytest --cov"`; the step that was
supposed to run it logged `no test command configured, asking agent to run tests...` and paid an
agent to improvise an invocation instead. The step SUCCEEDED, so an improvised run that passes was
indistinguishable in the record from the declared run passing, and nothing compared the two.

That is not only waste. `clickup-agents` records three phase-1 tests that FAIL under a bare
`python3` and PASS under the venv interpreter (`clickup-agents-pytest-invocation-is-load-bearing`),
so which invocation runs decides the answer. An improvised invocation can silently select the
passing one.

**The half this module can decide is the pipeline's own.** `verifier_evidence.py` already records
the argv of every command the Verifier ran to reach its verdict, and the `suite` run among them is
the phase's own test run - the one thing a passing verdict cannot be reached without. So the
comparison is: what does the project declare, and what does the transcript say ran. The OTHER half -
whether no-mistakes' feature-close `test` step ran the declared command - is upstream: no-mistakes
1.57.0 reads `.no-mistakes.yaml` from `main` regardless of the repository's default branch, and a
project whose config lives only on another branch gets the "no test command configured" line. This
repository cannot fix that and does not pretend to; a clean result here says nothing about it.

**A command that cannot be read is `unknown`, never a guess.** Absent is a third state and it is
named: a project that declares no test command is untouched by all of this, and a transcript with no
`suite` run is `unknown` - refused only when there IS a declaration to be refused against.

Deliberately no CLI: this decides for `verifier_precheck.py` (which refuses the pass) and
`pipeline_metrics.py` (which records the pair), and both print their own results. A third printer
would be a second place the same verdict is worded.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verifier_evidence  # noqa: E402 — the one owner of "what did this phase actually run"

#: The project's declaration, at the repository root. The same file `commands/avenger-run.md`
#: already preflights for `REPLACE_ME` markers, read for the key it names.
CONFIG = ".no-mistakes.yaml"

#: A command neither the transcript nor the harness could state. A NAMED state, never a default:
#: recording an empty string or the declared command would be a guess, and the whole defect here is
#: an improvised run reading as the declared one.
UNKNOWN = "unknown"

#: `test:` at the start of a line, whatever its indentation - top level or under `commands:`. The
#: same shape `commands/avenger-run.md` already greps this file with (`^[[:space:]]*(lint|test):`),
#: deliberately, because a second spelling of "where the test key lives" is the copy that drifts.
#:
#: This is not a YAML parser and does not pretend to be one: these scripts are stdlib-only so they
#: run in a consumer repository with nothing installed. What it cannot decide it says out loud - two
#: `test:` lines that disagree are UNDECIDABLE rather than resolved to whichever came last.
TEST_KEY = re.compile(r"^[ \t]*test:[ \t]*(?P<value>\S.*?)[ \t]*$", re.MULTILINE)


class Declaration(NamedTuple):
    """What the project declares. Exactly one of `command` and `problem` is ever not None.

    Both being None is the third state and the important one: the project declares NO test command,
    which is not a fault and leaves every check here inert.
    """

    command: str | None
    problem: str | None


def _scalar(raw: str) -> str:
    """One YAML scalar as this file actually writes them: quoted, or bare with a trailing comment.

    A `#` inside quotes belongs to the command; outside them it starts a comment. Nothing more
    elaborate, because nothing more elaborate has ever appeared in this key.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text.split("#", 1)[0].strip()


def declared(root: Path | str = ".") -> Declaration:
    """What `.no-mistakes.yaml` at `root` declares the test command to be.

    No file, or a file with no `test:` line, declares nothing - `Declaration(None, None)`. A file
    that exists and cannot be read, or that states the key twice with different values, is a
    PROBLEM: reading either as "declares nothing" would let an unreadable config buy the silent
    clean pass this whole module exists to remove.
    """
    path = Path(root) / CONFIG
    if not path.is_file():
        return Declaration(None, None)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Declaration(
            None,
            f"{path}: unreadable ({exc}). Whether this project declares a test command is "
            f"UNDECIDABLE, which is not the same as declaring none.",
        )
    values = {_scalar(m.group("value")) for m in TEST_KEY.finditer(text)}
    values.discard("")
    if not values:
        return Declaration(None, None)
    if len(values) > 1:
        return Declaration(
            None,
            f"{path}: declares `test:` more than once, with different values "
            f"({', '.join(sorted(repr(v) for v in values))}). Which one the project means is "
            f"UNDECIDABLE; state it once.",
        )
    return Declaration(values.pop(), None)


class Observed(NamedTuple):
    """The argv of the run that actually happened, or why it could not be read."""

    argv: list[str] | None
    why: str | None

    @property
    def command(self) -> str:
        """The run as one line, for a record and for a finding. `unknown` when there is none."""
        return shlex.join(self.argv) if self.argv else UNKNOWN


def observed(phase_dir: Path | str) -> Observed:
    """The argv of the phase's own suite run, from the transcript `verifier_evidence.py` wrote.

    The LAST run of kind `suite`, and deliberately not filtered by `subject_digest` currency:
    `verifier_evidence.check` owns currency and already refuses a passing verdict that has no
    CURRENT suite run, so a stale transcript can never reach a pass through here. Re-deciding
    currency in this module would be a second copy of that rule, which is the drift defect.

    Everything that is not an argv is `unknown` with its reason attached - no transcript, a
    transcript that will not parse, a transcript with no suite run in it.
    """
    phase = Path(phase_dir)
    path = verifier_evidence.record_path(phase)
    if not path.is_file():
        return Observed(
            None, f"no {path.name} beside the verdict - nothing recorded what ran"
        )
    try:
        data = verifier_evidence.load(phase)
    except verifier_evidence.EvidenceError as exc:
        return Observed(None, f"{path} could not be read ({exc})")
    runs = [
        entry
        for entry in data.get("runs") or []
        if isinstance(entry, dict)
        and entry.get("kind") == verifier_evidence.REQUIRED_KIND
        and isinstance(entry.get("argv"), list)
        and entry.get("argv")
    ]
    if not runs:
        return Observed(
            None,
            f"{path} records no run of kind '{verifier_evidence.REQUIRED_KIND}' carrying an argv",
        )
    return Observed([str(word) for word in runs[-1]["argv"]], None)


def runs_declared(argv: list[str], command: str) -> bool:
    """Whether `argv` is the declared `command`, possibly with arguments appended.

    A PREFIX rule, and it is the strict direction on purpose. `pytest --cov tests/demo/1-core` is
    `pytest --cov` narrowed to a phase's tree and counts; `pytest -q tests/demo/1-core` is a
    different command and does not. What it therefore also refuses is a different SPELLING of the
    same intent - `python3 -m pytest --cov`, or the declared flags in another order - and that is
    the safe direction to be wrong in: the finding names both commands, so the remedy is to run
    what is declared or to declare what is run, and neither of those is a silent pass.
    """
    try:
        want = shlex.split(command)
    except ValueError:
        return False
    return bool(want) and argv[: len(want)] == want


def _passes(phase_dir: Path) -> bool:
    """Whether this phase's verdict claims a pass. An unreadable verdict is NOT read as one.

    Deliberately not a finding of its own: `verifier_precheck.verdict_problems` already opens the
    same file and names an unreadable verdict, and two checks reporting one unreadable artifact is
    two remedies for one fault.
    """
    path = Path(phase_dir) / "verdict.json"
    if not path.is_file():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("verdict") == "pass"
    except (OSError, ValueError, AttributeError):
        return False


def problems(phase_dir: Path | str, root: Path | str | None = None) -> list[str]:
    """Why this phase's pass may not stand on the command it ran. Empty means nothing to say.

    Three gates, in order, and each one is a real state rather than a shortcut. A project that
    DECLARES no test command is untouched - that is the pre-issue-#119 behaviour and it stays. A
    phase whose verdict is not a `pass` is making no claim yet, so there is nothing to refuse. Only
    a claimed pass, under a declaration, is held to the command it ran.
    """
    phase = Path(phase_dir)
    base = Path(root) if root is not None else repo_root(phase)
    declaration = declared(base)
    if declaration.problem is not None:
        return [declaration.problem]
    if declaration.command is None:
        return []
    if not _passes(phase):
        return []
    run = observed(phase)
    if run.argv is None:
        return [
            f"{phase}: verdict.json says `pass`, and the command its suite ran is {UNKNOWN} "
            f"({run.why}). This project's declared test command is `{declaration.command}`, so "
            f"the pass rests on a run nothing can name. Record the suite run through "
            f"scripts/verifier_evidence.py, running the declared command."
        ]
    if not runs_declared(run.argv, declaration.command):
        return [
            f"{phase}: verdict.json says `pass` on a suite run that is not this project's "
            f"declared test command. Declared: `{declaration.command}`. Ran: `{run.command}`. An "
            f"improvised invocation is not the declared one - which interpreter and which flags "
            f"run decides the answer - so re-run the suite with the declared command and record "
            f"it, or change the `test:` key in {CONFIG} to the command this project means."
        ]
    return []


def repo_root(phase_dir: Path | str) -> Path:
    """The repository root above `docs/features/<feature>/phases/<n>-<slug>`, else the cwd.

    Recognised by the layout itself rather than by counting parents from an assumed depth, which is
    the mistake `verifier_precheck._test_root` records having made: counting reached `docs/` and
    every lookup resolved against a path the canonical layout never has.
    """
    phase = Path(phase_dir).resolve()
    if (
        len(phase.parents) >= 5
        and phase.parents[0].name == "phases"
        and phase.parents[2].name == "features"
        and phase.parents[3].name == "docs"
    ):
        return phase.parents[4]
    return Path.cwd()
