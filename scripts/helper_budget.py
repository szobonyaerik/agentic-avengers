#!/usr/bin/env python3
"""A helper agent is bounded by a DECLARED budget, and the bound is checked where it can refuse.

## The defect

Issue #118, measured with the harness panel's own timers. One stage held five helper agents at once.
Two captures ninety minutes apart showed identical timers and token counts on all five - frozen, not
progressing - while the parent's own progress artifact had not moved for 64 minutes and its process
stayed alive, so every liveness check read the run as healthy. The largest helper had spent
**1h 8m and 341.4k tokens deciding whether two amendment ids were marked done**, which is one `grep`.
Three of the five were questions a shell command answers.

Nothing bounded any of them. A question answerable by one command was dispatched exactly like a
question needing a full re-measurement, and the parent then blocked on the result. The cost landed
on the account rather than on any recorded metric, so an hour of it left no trace in the retro; the
only visible symptom was the parent going quiet, which every other explanation also produces. A
human noticed, read the panel, and told the parent to abandon the helpers and run the checks itself.
It resumed within a minute. That is a person doing timeout detection.

## What this decides, and where

The RULE - *do not delegate a question a command answers* - is one paragraph in
`skills/pipeline-conventions`, required and audited for every stage. A rule is not a mechanism, so
two halves of it are decided here, at `PreToolUse` on the spawn, which is the one event that can
refuse and the last moment the remedy still exists:

* **A helper dispatch declares a budget.** No `Budget:` line in the prompt is a refusal naming the
  line to add. Without it "declare a budget" is a sentence claiming behaviour nothing enforces, and
  the ceiling below would have nothing to bind. `HELPER_BUDGET_MAX_S` bounds the declaration itself,
  because `Budget: 10h` satisfies a presence check and bounds nothing.
* **A live helper past its own declared budget refuses the NEXT dispatch.** This is the observed
  state exactly: five helpers accumulating while the earliest was already an hour over. The remedy
  it prints is the one the operator used - abandon the overrunning helper and answer the question
  directly - and it is available at the moment it is printed.

**What it does NOT decide is the interesting half.** Whether a question has a one-command answer is
a judgement no static rule makes, so this never reads the prompt for that; it is carried by the rule
the stages load. And the harness exposes no timeout on a delegation and fires no event inside a
parent that is blocked waiting, so ABANDONING an overrunning helper mid-wait is instruction, not
mechanism - what is mechanised is that the next dispatch cannot proceed over the top of it.

**Liveness is not restated here.** `implementer_liveness.live()` already owns what "still running"
means - a start with no stop, whose harness process still exists, that no `TaskStop` ended, inside
an age ceiling - and this passes it a different pattern rather than keeping a second copy of it.

Usage:
    helper_budget.py check --payload-file <PreToolUse payload> [--root .]
        exit 0 = clean or not applicable, 1 = REFUSE (named), 2 = cannot tell
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402
import implementer_liveness  # noqa: E402

OK = 0
REFUSE = 1
UNKNOWN = 2

#: The marker the hook greps, so a traceback - which also exits 1 - is never read as a finding. The
#: same discipline `implementer_liveness.LIVE_MARKER` and `plugin_release`'s `STALE:` run on.
REFUSE_MARKER = "[helper-budget] REFUSE:"

#: THE declaration. One line in the delegation prompt, a whole number with an optional unit:
#: `Budget: 90s`, `Budget: 5m`, `Budget: 300` (seconds when bare). Deliberately a line of its own -
#: a budget mentioned inside a sentence is prose, and this must be readable without interpreting one.
BUDGET_LINE = re.compile(
    r"^[ \t>*-]*Budget:[ \t]*(\d+)[ \t]*([smh])?[ \t]*$", re.M | re.I
)

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, None: 1}

#: Which spawns are HELPERS. Everything that is not a pipeline stage: the `avenger-*` chain is the
#: pipeline's own sequence, dispatched by the orchestrator and bounded by the phase, not an ad-hoc
#: sub-question. Unanchored spellings do not work here - the rule is about what a name STARTS with -
#: so this is anchored and matched with `search` against a plugin-qualified type
#: (`plan-build-verify:avenger-verifier`) as well as a bare one.
AGENTS_ENV = "HELPER_AGENTS"
DEFAULT_HELPER_AGENTS = r"^(?!(?:[A-Za-z0-9_.-]+:)?avenger-).+"

#: The ceiling on a DECLARED budget. A declaration nothing bounds is a presence check, and the
#: measured helpers ran 27 to 68 minutes; 30 minutes is above every legitimate helper this pipeline
#: has dispatched and below every one it lost an hour to.
MAX_ENV = "HELPER_BUDGET_MAX_S"
DEFAULT_MAX_S = 30 * 60

#: The activity log's own event for "a helper was dispatched, with this budget". It rides in
#: `.agent-activity.jsonl` beside the lifecycle rows rather than in a store of its own: pairing a
#: budget to a run needs the run's own start row, and a second log would be a second answer to
#: "what is running".
DISPATCH = "HelperDispatch"

#: What a run with no paired dispatch row is judged by - nothing. A helper spawned before this rule
#: existed, or while `HELPER_BUDGET_OFF=1`, has no declared budget, and inventing one for it would
#: be this check deciding on a fact nobody stated.
UNRECORDED = "unrecorded"


class BudgetUnknown(Exception):
    """The question could not be answered. Never the same as `within budget`."""


def helper_pattern() -> re.Pattern[str]:
    raw = (os.environ.get(AGENTS_ENV) or "").strip() or DEFAULT_HELPER_AGENTS
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error as exc:
        raise BudgetUnknown(
            f"{AGENTS_ENV}={raw!r} is not a usable regular expression ({exc}), so which spawns this "
            f"binds cannot be decided"
        ) from exc


def max_budget_s() -> int:
    raw = (os.environ.get(MAX_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_S
    try:
        value = int(raw)
    except ValueError as exc:
        raise BudgetUnknown(
            f"{MAX_ENV}={raw!r} is not an integer number of seconds"
        ) from exc
    if value <= 0:
        raise BudgetUnknown(f"{MAX_ENV}={raw!r} must be a positive number of seconds")
    return value


def is_helper(stage: str) -> bool:
    """Whether this spawn is a helper at all. A pipeline stage is not."""
    return bool(helper_pattern().search((stage or "").strip()))


def declared_budget(prompt: str) -> int | None:
    """The budget the delegation prompt declares, in seconds, or None when it declares none.

    The LAST declaration wins. A prompt that carries a quoted example above its own line would
    otherwise be bounded by the example, and the author's own line is the one at the end.
    """
    matches = BUDGET_LINE.findall(prompt or "")
    if not matches:
        return None
    amount, unit = matches[-1]
    return int(amount) * _UNIT_SECONDS[(unit or "").lower() or None]


def read_payload(path: Path) -> tuple[str, str]:
    """`(subagent_type, prompt)` out of a PreToolUse payload. Raises `BudgetUnknown` on anything else.

    Decided on `subagent_type` and never on the tool's NAME: the spawn tool is `Task` in some harness
    versions and `Agent` in others, and a check keyed to the wrong name would silently never fire.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BudgetUnknown(f"{path} could not be read ({exc})") from exc
    try:
        payload = json.loads(raw.lstrip("﻿"))
    except ValueError as exc:
        raise BudgetUnknown(f"the hook payload is not readable JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise BudgetUnknown("the hook payload is not an object")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    return (
        str(tool_input.get("subagent_type") or "").strip(),
        str(tool_input.get("prompt") or ""),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    """The activity log's own timestamp format, so one reader parses every row in it."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S%z")


def dispatch_rows(root: Path) -> list[dict]:
    """Every recorded helper dispatch, oldest first. Raises when the log cannot be read."""
    path = implementer_liveness.log_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BudgetUnknown(f"{path} could not be read ({exc})") from exc
    rows: list[dict] = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # one malformed line is not a reason to stop reading the rest
        if isinstance(entry, dict) and str(entry.get("event") or "") == DISPATCH:
            rows.append(entry)
    return rows


def budget_for(run: dict, rows: list[dict]) -> int | None:
    """The budget declared for one run: the newest dispatch of its type at or before it started.

    Pairing by type and time rather than by id because the harness gives a spawn no id until its
    `SubagentStart`, and the dispatch is observed one event earlier. Nothing is consumed as it
    pairs: a dispatch row for a spawn another hook went on to refuse would otherwise shift every
    later pairing by one, and an unpaired row that simply loses is harmless.
    """
    started = implementer_liveness.when_of(run)
    if started is None:
        return None
    agent = str(run.get("agent_type") or "")
    best: tuple[datetime, int] | None = None
    for row in rows:
        if str(row.get("agent_type") or "") != agent:
            continue
        when = implementer_liveness.when_of(row)
        if when is None or when > started:
            continue
        try:
            budget = int(row.get("budget_s"))
        except (TypeError, ValueError):
            continue
        if best is None or when >= best[0]:
            best = (when, budget)
    return None if best is None else best[1]


class Overrun(dict):
    """One live helper past its declared budget: the run's row plus `budget_s` and `elapsed_s`."""


def overruns(root: Path, *, now: datetime | None = None) -> list[Overrun]:
    """Every live helper whose elapsed time exceeds the budget its dispatch declared.

    A run with no paired dispatch row is NOT judged: it has no declared budget, and a check that
    invented one would be deciding on a fact nobody stated. Raises `BudgetUnknown` when the log
    cannot be read at all - which outside a pipeline run is the ordinary case, and is why the
    caller reports that half as not run rather than as clear.

    It inherits `live()`'s age ceiling (`IMPLEMENTER_MAX_AGE_S`, four hours), so a start older than
    that is presumed dead and is not reported here. That is the same bound the implementer lock
    runs on and it is deliberate: a crashed agent must not hold a refusal forever. The measured
    helpers ran 27 to 68 minutes, well inside it.
    """
    moment = now or _now()
    rows = dispatch_rows(root)
    found: list[Overrun] = []
    for run in implementer_liveness.live(root, pattern=helper_pattern(), now=moment):
        budget = budget_for(run, rows)
        if budget is None:
            continue
        started = implementer_liveness.when_of(run)
        if started is None:
            continue
        elapsed = int((moment - started).total_seconds())
        if elapsed > budget:
            found.append(Overrun(run, budget_s=budget, elapsed_s=elapsed))
    return found


def record_dispatch(
    root: Path, *, agent_type: str, budget_s: int, now: datetime | None = None
) -> bool:
    """Append the dispatch row a later pairing reads. Never raises, never changes a verdict."""
    moment = now or _now()
    try:
        path = implementer_liveness.log_path(root)
        row = {
            "ts": _stamp(moment),
            "event": DISPATCH,
            "agent_type": agent_type,
            "budget_s": int(budget_s),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError):
        # An unwritable log costs the pairing for this one run, which then goes unjudged. It may not
        # cost the dispatch: this is measurement wearing a gate's clothes, and the gate's verdict was
        # already reached above.
        return False


def dispatch_token(agent_type: str, when: str) -> str:
    """A stable id for one dispatch, so a re-run of the same hook converges instead of appending."""
    return hashlib.sha256(f"{agent_type}|{when}".encode()).hexdigest()[:8]


def start_row(root: Path, *, agent_type: str, agent_id: str = "") -> dict | None:
    """The newest `SubagentStart` for one agent, or None.

    Read directly rather than through `live()` because this answers a helper that has just STOPPED:
    `hook_activity.sh` writes the stop row before this runs, so the run is correctly no longer live
    and its start row is the only thing left that says when it began.
    """
    path = implementer_liveness.log_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BudgetUnknown(f"{path} could not be read ({exc})") from exc
    newest: dict | None = None
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event") or "") != implementer_liveness.START:
            continue
        if str(entry.get("agent_type") or "") != agent_type:
            continue
        if agent_id and str(entry.get("agent_id") or "") not in ("", agent_id):
            continue
        newest = entry
    return newest


class Spend(dict):
    """What one returning helper cost: `elapsed_s`, `budget_s` (may be None) and a stable `token`."""


def spend_for(
    root: Path, *, agent_type: str, agent_id: str = "", now: datetime | None = None
) -> Spend:
    """Elapsed time and declared budget for a helper that has just stopped.

    Raises `BudgetUnknown` when the log cannot be read or carries no start for this run - there is
    then no moment to measure from, and a spend row invented from nothing is worse than none.
    """
    moment = now or _now()
    run = start_row(root, agent_type=agent_type, agent_id=agent_id)
    if run is None:
        raise BudgetUnknown(
            f"no {implementer_liveness.START} row for {agent_type!r} to measure from"
        )
    started = implementer_liveness.when_of(run)
    if started is None:
        raise BudgetUnknown(
            f"the {implementer_liveness.START} row for {agent_type!r} carries no usable timestamp"
        )
    return Spend(
        elapsed_s=max(0, int((moment - started).total_seconds())),
        budget_s=budget_for(run, dispatch_rows(root)),
        token=dispatch_token(
            agent_type, str(run.get("agent_id") or run.get("ts") or "")
        ),
    )


def _emit_dispatch_metric(agent_type: str, budget_s: int, when: str) -> None:
    """Record the dispatch in the phase record, at the moment it happens. Never raises.

    Imported late and swallowed whole: measurement may never fail a phase, and it may certainly
    never move a gate's verdict (CLAUDE.md §6d). A dispatch is recorded here rather than at the
    close because a helper that never returns is exactly the case this issue is about, and a
    dispatch with no matching spend row is the only trace such a helper leaves.
    """
    try:
        import pipeline_metrics

        phase = pipeline_metrics.current_phase()
        if phase:
            pipeline_metrics.record_helper_dispatch(
                phase,
                agent_type=agent_type,
                budget_s=budget_s,
                token=dispatch_token(agent_type, when),
            )
    except Exception:  # noqa: BLE001 - measurement never fails the thing it measures
        pass


def _refuse(*lines: str) -> int:
    print(f"{REFUSE_MARKER} {lines[0]}", file=sys.stderr)
    for line in lines[1:]:
        print(line, file=sys.stderr)
    return REFUSE


def check(payload_file: Path, root: Path, *, now: datetime | None = None) -> int:
    """Decide one helper dispatch. 0 clean or not applicable, 1 refused, 2 undecidable."""
    moment = now or _now()
    try:
        stage, prompt = read_payload(payload_file)
        if not stage:
            return (
                OK  # not a spawn at all: not applicable, and not a clean result either
            )
        if not is_helper(stage):
            print(
                f"  (helper budget: {stage} is a pipeline stage, not a helper - not applicable)",
                file=sys.stderr,
            )
            return OK
        ceiling = max_budget_s()
    except BudgetUnknown as exc:
        print(f"[helper-budget] cannot decide: {exc}", file=sys.stderr)
        return UNKNOWN

    budget = declared_budget(prompt)
    if budget is None:
        return _refuse(
            f"the dispatch of '{stage}' declares no budget.",
            "",
            "A helper that declares no budget cannot be abandoned on one, and five of them measured",
            "at once stayed frozen for 90 minutes while the parent read as healthy (issue #118). The",
            "largest spent 1h 8m and 341.4k tokens deciding whether two ids were marked done.",
            "",
            "FIRST ask whether this needs a helper at all. A question a command answers - a file's",
            "contents, a field's value, a test summary, a timestamp, whether an id is marked done -",
            "is answered by running the command, not by delegating it.",
            "",
            "If it genuinely needs one, put a line of its own in the prompt:",
            "",
            "  Budget: 5m",
            "",
            f"Seconds when bare; s/m/h accepted; at most {ceiling}s ({MAX_ENV}).",
        )
    if budget > ceiling:
        return _refuse(
            f"the dispatch of '{stage}' declares Budget: {budget}s, over the {ceiling}s ceiling.",
            "",
            "A budget nothing bounds is a presence check. The measured helpers ran 27 to 68 minutes;",
            "the ceiling is above every legitimate helper and below every lost hour. Lower the",
            f"budget, or raise {MAX_ENV} deliberately and say why.",
        )

    try:
        past = overruns(root, now=moment)
    except (BudgetUnknown, implementer_liveness.LivenessUnknown) as exc:
        # Outside a pipeline run there is no activity log, which is ordinary. The half that COULD be
        # decided was, and the half that could not is named rather than passed over: "could not
        # look" and "all clear" must never arrive looking alike.
        print(
            f"  (helper budget: live helpers were NOT checked - {exc})",
            file=sys.stderr,
        )
        past = []

    if past:
        described = [
            f"  ✗ {implementer_liveness.describe(run)} - {run['elapsed_s']}s elapsed against a "
            f"declared {run['budget_s']}s"
            for run in past
        ]
        return _refuse(
            f"a helper is already past its declared budget, so '{stage}' would be dispatched over "
            f"the top of it.",
            "",
            *described,
            "",
            "Abandon it and answer its question directly. That is the remedy an operator applied by",
            "hand to the measured case, and the run resumed within a minute; waiting was what cost",
            "the hour. Stop it with the TaskStop tool, then run the command yourself.",
            "",
            '  GATE_BYPASS_GATES="helper-budget" GATE_BYPASS="<reason>" <the spawn>',
            "",
            "waives THIS gate alone and is logged to gate-overrides.log. GATE_BYPASS on its own",
            "waives every gate the run reaches, which is far more than one helper.",
        )

    when = _stamp(moment)
    record_dispatch(root, agent_type=stage, budget_s=budget, now=moment)
    _emit_dispatch_metric(stage, budget, when)
    print(
        f"  helper budget: '{stage}' dispatched with a declared {budget}s budget; no live helper is "
        f"over its own.",
        file=sys.stderr,
    )
    return OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_check = sub.add_parser(
        "check", help="decide one helper dispatch from its PreToolUse payload"
    )
    p_check.add_argument("--payload-file", required=True, type=Path)
    p_check.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()),
    )
    args = parser.parse_args(argv)
    return check(args.payload_file, args.root)


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
