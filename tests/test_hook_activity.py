"""Tests for the agent-activity hook.

The hook is pure observability, so the property that matters most is the same one the other
SubagentStart hooks pin: it FAILS CLOSED. Garbage payloads, foreign events and the kill switch
must leave no trace — a broken hook that spams or crashes agent spawns would be worse than no
telemetry at all.
"""

import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hook_activity.sh"


def run_hook(tmp_path, payload: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    full_env.update(env or {})
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        env=full_env, timeout=15,
    )


def read_log(tmp_path) -> list[dict]:
    log = tmp_path / ".agent-activity.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_start_and_stop_events_append_records(tmp_path):
    start = json.dumps({
        "hook_event_name": "SubagentStart", "agent_type": "avenger-verifier",
        "session_id": "s1", "transcript_path": "/tmp/t.jsonl",
    })
    stop = json.dumps({"hook_event_name": "SubagentStop", "agent_type": "avenger-verifier"})
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
    proc = run_hook(tmp_path, json.dumps({"hook_event_name": "PreToolUse", "agent_type": "x"}))
    assert proc.returncode == 0
    assert read_log(tmp_path) == []


def test_kill_switch(tmp_path):
    payload = json.dumps({"hook_event_name": "SubagentStart", "agent_type": "avenger-breaker"})
    proc = run_hook(tmp_path, payload, env={"ACTIVITY_OFF": "1"})
    assert proc.returncode == 0
    assert read_log(tmp_path) == []


def test_rotation_trims_to_newest_half(tmp_path):
    log = tmp_path / ".agent-activity.jsonl"
    log.write_text("".join(json.dumps({"event": "SubagentStart", "n": i}) + "\n" for i in range(10)))
    payload = json.dumps({"hook_event_name": "SubagentStop", "agent_type": "avenger-handover"})
    run_hook(tmp_path, payload, env={"ACTIVITY_MAX_LINES": "10"})
    records = read_log(tmp_path)
    # 11 lines exceeded the cap of 10 -> trimmed to the newest 5; the new record survives
    assert len(records) == 5
    assert records[-1]["event"] == "SubagentStop"


def test_absent_keys_are_omitted_not_guessed(tmp_path):
    run_hook(tmp_path, json.dumps({"hook_event_name": "SubagentStart", "agent_type": "avenger-x"}))
    (record,) = read_log(tmp_path)
    assert "session_id" not in record
    assert "transcript_path" not in record
