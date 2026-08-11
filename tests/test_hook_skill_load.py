"""Tests for the hook that observes which skills a stage actually loaded.

The failure this hook exists to remove is a silence: an agent is *told* to load a skill, nothing
checks, and a stage runs on its own judgement with nobody aware. So the tests come in two halves.

The recording half asserts positive evidence produces a row with the evidence in it, and that a
stage's contract is seeded so a skill it owes and never loads is a row rather than an absence.

The other half is the dangerous direction: this hook fires on EVERY Read in a session and on every
subagent spawn. It must never block a turn, never inject context, and never write to stdout, since
a SubagentStart hook's stdout is a JSON protocol. Every degenerate payload is asserted inert.
"""

import json
import subprocess
from pathlib import Path

import pytest

from metrics_support import stored, stub_sink, write_spec  # noqa: F401 — fixture

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hook_skill_load.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash hook the harness invokes exactly this way, on stdin"
)


def fire(payload: dict) -> subprocess.CompletedProcess:
    """Run the hook the way the harness does."""
    return subprocess.run(  # noqa: S603
        ["bash", str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, check=False,
    )


@pytest.fixture
def phase(stub_sink):  # noqa: F811
    """A project with one phase in flight, which is where any observed load belongs."""
    project, store, _ = stub_sink
    write_spec(project, 8, "8.1", "- R8.1.1 one\n")
    return project, store


def test_reading_a_skill_file_is_a_load_with_evidence(phase):
    _, store = phase

    result = fire({
        "hook_event_name": "PostToolUse", "tool_name": "Read",
        "agent_type": "avenger-verifier",
        "tool_input": {"file_path": "/repo/skills/verifier-triage/SKILL.md"},
    })

    assert result.returncode == 0 and result.stdout == ""
    entry = stored(store, "08")["skill_loads"][0]
    assert entry["id"] == "avenger-verifier:verifier-triage"
    assert entry["loaded"] is True and entry["required"] is True
    assert "SKILL.md" in entry["evidence"]


def test_the_skill_tool_is_a_load_too(phase):
    _, store = phase

    fire({
        "hook_event_name": "PostToolUse", "tool_name": "Skill",
        "agent_type": "avenger-backend-architect",
        "tool_input": {"skill": "plan-build-verify:tdd"},
    })

    entry = stored(store, "08")["skill_loads"][0]
    assert entry["id"] == "avenger-backend-architect:tdd" and entry["loaded"] is True


def test_a_spawn_seeds_the_stages_contract_as_not_yet_observed(phase):
    _, store = phase

    result = fire({"hook_event_name": "SubagentStart", "agent_type": "avenger-verifier"})

    assert result.returncode == 0 and result.stdout == ""
    entries = {e["skill"]: e for e in stored(store, "08")["skill_loads"]}
    assert "verifier-triage" in entries
    assert entries["verifier-triage"]["required"] is True
    assert entries["verifier-triage"]["loaded"] is False


def test_a_seed_then_a_load_is_one_row_that_ends_up_answered(phase):
    _, store = phase
    fire({"hook_event_name": "SubagentStart", "agent_type": "avenger-verifier"})

    fire({
        "hook_event_name": "PostToolUse", "tool_name": "Read",
        "agent_type": "avenger-verifier",
        "tool_input": {"file_path": "/repo/skills/verifier-triage/SKILL.md"},
    })

    rows = [e for e in stored(store, "08")["skill_loads"] if e["skill"] == "verifier-triage"]
    assert len(rows) == 1 and rows[0]["loaded"] is True


def test_a_load_by_an_unnamed_agent_is_attributed_to_the_live_subagent(phase):
    """A PostToolUse payload does not always name the agent; the activity log already knows."""
    project, store = phase
    (project / ".agent-activity.jsonl").write_text(
        json.dumps({"event": "SubagentStart", "agent_type": "avenger-verifier"}) + "\n",
        encoding="utf-8",
    )

    fire({
        "hook_event_name": "PostToolUse", "tool_name": "Read",
        "tool_input": {"file_path": "/repo/skills/tdd/SKILL.md"},
    })

    assert stored(store, "08")["skill_loads"][0]["id"] == "avenger-verifier:tdd"


@pytest.mark.parametrize("payload", [
    {"hook_event_name": "PostToolUse", "tool_name": "Read",
     "tool_input": {"file_path": "/repo/src/app.py"}},
    {"hook_event_name": "PostToolUse", "tool_name": "Read",
     "tool_input": {"file_path": "/elsewhere/not-a-skill/SKILL.md"}},
    {"hook_event_name": "SubagentStart", "agent_type": "general-purpose"},
    {"hook_event_name": "SubagentStart"},
    {"tool_input": {}},
    {},
])
def test_nothing_else_is_recorded(phase, payload):
    _, store = phase

    result = fire(payload)

    assert result.returncode == 0 and result.stdout == ""
    assert not (store / "phase-08.json").exists()


def test_an_unreadable_payload_is_inert(phase):
    _, store = phase

    result = subprocess.run(  # noqa: S603
        ["bash", str(HOOK)], input="{not json", capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0 and result.stdout == ""
    assert not (store / "phase-08.json").exists()


def test_no_phase_in_flight_records_nothing(stub_sink):  # noqa: F811
    """A skill load belongs to a phase record or to none at all — never to a guessed one."""
    _, store, _ = stub_sink

    result = fire({
        "hook_event_name": "PostToolUse", "tool_name": "Read",
        "agent_type": "avenger-verifier",
        "tool_input": {"file_path": "/repo/skills/tdd/SKILL.md"},
    })

    assert result.returncode == 0
    assert list(store.iterdir()) == []


def test_the_off_switch_stops_it(phase, monkeypatch):
    _, store = phase
    monkeypatch.setenv("SKILL_LOAD_OFF", "1")

    fire({"hook_event_name": "SubagentStart", "agent_type": "avenger-verifier"})

    assert not (store / "phase-08.json").exists()


def test_an_unwritable_record_does_not_block_the_turn(phase, monkeypatch):
    """Measurement, not a gate: a hook that exits non-zero here would stop a spawn."""
    _, store = phase
    monkeypatch.setenv("DOUBLE_EXIT", "3")

    result = fire({"hook_event_name": "SubagentStart", "agent_type": "avenger-verifier"})

    assert result.returncode == 0 and result.stdout == ""
