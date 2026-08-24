"""A completion signal that comes from the implementer FINISHING, not from a document being written.

## The defect this pins

Issue #68, clickup-agents phase 11: an implementer stamps its spec `status: done` and then keeps
working - test-mapping.md, test-evidence.md and the phase's mutation gate all land after the stamp.
A phase worker had armed a condition-wait on that stamp as a wedge guard. **It fired at 24 minutes
while the agent was still running.** Had the next spec's implementer been dispatched on it, two
implementers would have been running in one worktree against one database, which phase 9 measured
the cost of: a git stash from one swallowed the other's uncommitted work, and the shared database
produced foreign-key violations plus a spurious lint failure.

`scripts/spec_done_guard.py` made the stamp self-correcting. It did not make it a completion signal,
and nothing produced one. The settled lesson in this fleet is that the reliable signal in this
harness is the agent/task notification - never a written marker - and `SubagentStop` is that
notification. `.agent-activity.jsonl` has recorded it since the activity hook was written and
**nothing read it for this**.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** The decisive case below stamps a spec `status:
done` while the implementer is still live and asserts the lock does NOT open - the exact substitution
that would have put two implementers in one worktree.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import implementer_liveness as liveness  # noqa: E402

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def stamp(offset_minutes: int = 0) -> str:
    return (NOW - timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M:%S%z")


def log(root: Path, *events: dict) -> Path:
    path = root / liveness.LOG_NAME
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in events),
        encoding="utf-8",
    )
    return path


def start(
    agent: str = "avenger-backend-architect", agent_id: str = "a1", ago: int = 5
) -> dict:
    return {
        "ts": stamp(ago),
        "event": "SubagentStart",
        "agent_type": agent,
        "agent_id": agent_id,
    }


def stop(
    agent: str = "avenger-backend-architect", agent_id: str = "a1", ago: int = 1
) -> dict:
    return {
        "ts": stamp(ago),
        "event": "SubagentStop",
        "agent_type": agent,
        "agent_id": agent_id,
    }


# --- the signal ----------------------------------------------------------------------------------


def test_an_implementer_that_started_and_has_not_stopped_is_live(
    tmp_path: Path,
) -> None:
    log(tmp_path, start())
    assert [e["agent_id"] for e in liveness.live(tmp_path, now=NOW)] == ["a1"]


def test_the_stop_event_is_what_clears_it(tmp_path: Path) -> None:
    log(tmp_path, start(), stop())
    assert liveness.live(tmp_path, now=NOW) == []


def test_a_spec_stamped_done_does_not_clear_it(tmp_path: Path) -> None:
    """THE case. The stamp lands while the implementer is still working, so it must not be the
    thing anything waits on - and this signal cannot be moved by writing a document at all."""
    (tmp_path / "spec.md").write_text(
        "---\nspec: 1.1-a\nstatus: done\n---\n\nbody\n", encoding="utf-8"
    )
    log(tmp_path, start())
    assert [e["agent_id"] for e in liveness.live(tmp_path, now=NOW)] == ["a1"]


def test_a_second_implementer_is_seen_beside_the_first(tmp_path: Path) -> None:
    log(
        tmp_path,
        start(agent_id="a1"),
        start(agent="avenger-frontend-developer", agent_id="a2"),
    )
    assert {e["agent_id"] for e in liveness.live(tmp_path, now=NOW)} == {"a1", "a2"}


def test_a_stage_that_is_not_an_implementer_is_not_tracked(tmp_path: Path) -> None:
    """The Verifier and the Breaker run beside an implementer by design; the rule is about the two
    stages that write to the worktree."""
    log(tmp_path, start(agent="avenger-verifier", agent_id="v1"))
    assert liveness.live(tmp_path, now=NOW) == []


def test_a_plugin_scoped_agent_name_still_matches(tmp_path: Path) -> None:
    log(
        tmp_path,
        start(agent="plan-build-verify:avenger-backend-architect", agent_id="p1"),
    )
    assert [e["agent_id"] for e in liveness.live(tmp_path, now=NOW)] == ["p1"]


# --- the ways this must not wedge -----------------------------------------------------------------


def test_a_start_older_than_the_ceiling_is_not_live(tmp_path: Path) -> None:
    """A crashed subagent leaves a start with no stop. Read as live forever, that is a permanent
    wedge, so age bounds it - and the bound is named rather than silent."""
    log(tmp_path, start(ago=10_000))
    assert liveness.live(tmp_path, now=NOW) == []


def test_the_ceiling_is_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(liveness.MAX_AGE_ENV, "60")
    log(tmp_path, start(ago=30))
    assert liveness.live(tmp_path, now=NOW) == []
    monkeypatch.setenv(liveness.MAX_AGE_ENV, "3600")
    assert [e["agent_id"] for e in liveness.live(tmp_path, now=NOW)] == ["a1"]


def test_no_log_at_all_cannot_answer(tmp_path: Path) -> None:
    """Not 'nothing is running'. The activity hook may be off, or this may not be a pipeline run -
    and a lock that treats 'I cannot see' as 'all clear' is a lock with no meaning either way."""
    with pytest.raises(liveness.LivenessUnknown):
        liveness.live(tmp_path, now=NOW)


def test_an_unreadable_line_is_skipped_rather_than_failing_the_whole_read(
    tmp_path: Path,
) -> None:
    path = log(tmp_path, start())
    path.write_text("{ not json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert [e["agent_id"] for e in liveness.live(tmp_path, now=NOW)] == ["a1"]


def test_an_entry_with_no_timestamp_is_not_live(tmp_path: Path) -> None:
    log(
        tmp_path,
        {
            "event": "SubagentStart",
            "agent_type": "avenger-backend-architect",
            "agent_id": "a1",
        },
    )
    assert liveness.live(tmp_path, now=NOW) == []


def test_a_stop_with_no_id_clears_the_newest_start_of_its_type(tmp_path: Path) -> None:
    """Not every harness version supplies `agent_id`; hook_activity.sh omits absent keys rather
    than guessing, so this has to degrade rather than lock forever on a run that has none."""
    log(
        tmp_path,
        {
            "ts": stamp(5),
            "event": "SubagentStart",
            "agent_type": "avenger-backend-architect",
        },
        {
            "ts": stamp(1),
            "event": "SubagentStop",
            "agent_type": "avenger-backend-architect",
        },
    )
    assert liveness.live(tmp_path, now=NOW) == []
