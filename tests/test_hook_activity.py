"""Tests for the agent-activity hook.

The hook is pure observability, so the property that matters most is the same one the other
SubagentStart hooks pin: it FAILS CLOSED. Garbage payloads, foreign events and the kill switch
must leave no trace — a broken hook that spams or crashes agent spawns would be worse than no
telemetry at all.
"""

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hook_activity.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash hook invoked by the harness exactly this way"
)


def run_hook(
    tmp_path, payload: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    full_env = {
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    full_env.update(env or {})
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=full_env,
        timeout=15,
    )


def read_log(tmp_path) -> list[dict]:
    log = tmp_path / ".agent-activity.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_start_and_stop_events_append_records(tmp_path):
    start = json.dumps(
        {
            "hook_event_name": "SubagentStart",
            "agent_type": "avenger-verifier",
            "session_id": "s1",
            "transcript_path": "/tmp/t.jsonl",
        }
    )
    stop = json.dumps(
        {"hook_event_name": "SubagentStop", "agent_type": "avenger-verifier"}
    )
    assert run_hook(tmp_path, start).returncode == 0
    assert run_hook(tmp_path, stop).returncode == 0

    records = read_log(tmp_path)
    assert [r["event"] for r in records] == ["SubagentStart", "SubagentStop"]
    assert records[0]["agent_type"] == "avenger-verifier"
    assert records[0]["session_id"] == "s1"
    assert records[0]["transcript_path"] == "/tmp/t.jsonl"
    assert all("ts" in r for r in records)


def test_garbage_payload_fails_closed(tmp_path):
    proc = run_hook(tmp_path, "not json at all {{{")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert read_log(tmp_path) == []


def test_non_lifecycle_event_is_ignored(tmp_path):
    proc = run_hook(
        tmp_path, json.dumps({"hook_event_name": "PreToolUse", "agent_type": "x"})
    )
    assert proc.returncode == 0
    assert read_log(tmp_path) == []


def test_kill_switch(tmp_path):
    payload = json.dumps(
        {"hook_event_name": "SubagentStart", "agent_type": "avenger-breaker"}
    )
    proc = run_hook(tmp_path, payload, env={"ACTIVITY_OFF": "1"})
    assert proc.returncode == 0
    assert read_log(tmp_path) == []


def test_rotation_trims_to_newest_half(tmp_path):
    log = tmp_path / ".agent-activity.jsonl"
    log.write_text(
        "".join(
            json.dumps({"event": "SubagentStart", "n": i}) + "\n" for i in range(10)
        )
    )
    payload = json.dumps(
        {"hook_event_name": "SubagentStop", "agent_type": "avenger-handover"}
    )
    run_hook(tmp_path, payload, env={"ACTIVITY_MAX_LINES": "10"})
    records = read_log(tmp_path)
    # 11 lines exceeded the cap of 10 -> trimmed to the newest 5; the new record survives
    assert len(records) == 5
    assert records[-1]["event"] == "SubagentStop"


def test_absent_keys_are_omitted_not_guessed(tmp_path):
    run_hook(
        tmp_path,
        json.dumps({"hook_event_name": "SubagentStart", "agent_type": "avenger-x"}),
    )
    (record,) = read_log(tmp_path)
    assert "session_id" not in record
    assert "transcript_path" not in record


# --- the outcome, not the attempt (issue #98) -----------------------------------------------------
#
# A killed implementer held its lock for four hours because `TaskStop` fires no `SubagentStop`. Two
# facts are now recorded that the liveness check can hold against the world: the harness pid a
# subagent runs inside, and the harness's own report that a TaskStop SUCCEEDED.


def test_a_start_row_carries_the_harness_pid(tmp_path):
    run_hook(
        tmp_path,
        json.dumps(
            {
                "hook_event_name": "SubagentStart",
                "agent_type": "avenger-backend-architect",
            }
        ),
        env={"ACTIVITY_HARNESS_PID": "4242"},
    )
    (record,) = read_log(tmp_path)
    assert record["harness_pid"] == 4242


def test_the_harness_pid_is_a_real_ancestor_when_nothing_overrides_it(tmp_path):
    """No override: the hook walks its own ancestry, so the pid it records exists right now."""
    import os

    run_hook(
        tmp_path,
        json.dumps({"hook_event_name": "SubagentStart", "agent_type": "avenger-x"}),
    )
    (record,) = read_log(tmp_path)
    os.kill(record["harness_pid"], 0)  # raises ProcessLookupError if it named nothing


def test_a_successful_taskstop_is_recorded_against_the_stopped_id(tmp_path):
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskStop",
            "tool_input": {"task_id": "a73db8cb6419fa75d"},
            "tool_response": {
                "message": "Successfully stopped task: a73db8cb6419fa75d (Phase 4)"
            },
        }
    )
    assert run_hook(tmp_path, payload).returncode == 0
    (record,) = read_log(tmp_path)
    assert record["event"] == "TaskStop"
    assert record["agent_id"] == "a73db8cb6419fa75d"


def test_a_taskstop_that_stopped_nothing_is_not_recorded_as_a_stop(tmp_path):
    """The attempt is not the outcome. A TaskStop naming a task the harness could not find stopped
    nothing, and a row for it would release a lock on an implementer that is still writing."""
    for response in (
        {"message": "No task found with id a1"},
        {"error": "task a1 is not running"},
        None,
    ):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "TaskStop",
            "tool_input": {"task_id": "a1"},
        }
        if response is not None:
            payload["tool_response"] = response
        run_hook(tmp_path, json.dumps(payload))
    assert read_log(tmp_path) == []


def test_a_post_tool_use_for_any_other_tool_is_ignored(tmp_path):
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "TaskStop a1"},
            "tool_response": {"stdout": "Successfully stopped task: a1"},
        }
    )
    run_hook(tmp_path, payload)
    assert read_log(tmp_path) == []


def test_the_taskstop_event_is_wired_to_this_hook():
    hooks = json.loads((HOOK.parents[1] / "hooks" / "hooks.json").read_text())["hooks"]
    wired = [
        h["command"]
        for group in hooks["PostToolUse"]
        if group.get("matcher") == "TaskStop"
        for h in group["hooks"]
    ]
    assert any("hook_activity.sh" in cmd for cmd in wired), wired
