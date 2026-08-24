"""The lock that acts on the completion signal - driven through the real hook.

`scripts/implementer_liveness.py` decides who is still in the worktree and its own tests cover that
decision. What is pinned HERE is that the decision is ACTED ON at the moment a second implementer
would be spawned, which is the only moment the remedy exists.

Issue #68's own fix direction: "Do not fix this by telling people not to wait on the stamp. That is a
sentence claiming behaviour nothing enforces, which this repository has now produced five instances
of in two days." So the rule is not a sentence in a runbook, it is a `PreToolUse` refusal.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import implementer_liveness as liveness  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    return tmp_path


def when(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


def activity(project: Path, *events: dict) -> None:
    (project / liveness.LOG_NAME).write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )


def started(agent: str = "avenger-backend-architect", agent_id: str = "a1") -> dict:
    return {
        "ts": when(5),
        "event": "SubagentStart",
        "agent_type": agent,
        "agent_id": agent_id,
    }


def stopped(agent: str = "avenger-backend-architect", agent_id: str = "a1") -> dict:
    return {
        "ts": when(1),
        "event": "SubagentStop",
        "agent_type": agent,
        "agent_id": agent_id,
    }


def spawn(project: Path, stage: str = "avenger-backend-architect", **env: str):
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_implementer_lock.sh")],
        input=json.dumps({"tool_input": {"subagent_type": stage}}),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            **env,
        },
    )


# --- the refusal ----------------------------------------------------------------------------------


def test_a_second_implementer_is_refused_while_the_first_is_running(
    project: Path,
) -> None:
    activity(project, started())
    result = spawn(project)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "avenger-backend-architect" in result.stderr
    assert "id a1" in result.stderr


def test_a_done_stamp_does_not_open_the_lock(project: Path) -> None:
    """THE case, and the one that would have put two implementers in one worktree: the stamp lands
    while the implementer is still working, so nothing may open on it."""
    spec_dir = (
        project / "docs" / "features" / "demo" / "phases" / "1-a" / "specs" / "1.1-a"
    )
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        "---\nspec: 1.1-a\nstatus: done\n---\n\nbody\n", encoding="utf-8"
    )
    activity(project, started())
    assert spawn(project).returncode == 2


def test_the_stop_event_is_what_opens_it(project: Path) -> None:
    activity(project, started(), stopped())
    result = spawn(project)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stage_that_does_not_write_to_the_worktree_is_not_blocked(
    project: Path,
) -> None:
    """The Verifier runs beside an implementer by design, and this rule is about two writers."""
    activity(project, started())
    assert spawn(project, "avenger-verifier").returncode == 0


def test_a_foreign_subagent_is_none_of_this_hooks_business(project: Path) -> None:
    activity(project, started())
    assert spawn(project, "general-purpose").returncode == 0


def test_a_plugin_scoped_implementer_name_is_still_bound(project: Path) -> None:
    activity(project, started())
    assert (
        spawn(project, "plan-build-verify:avenger-frontend-developer").returncode == 2
    )


# --- everything that is not a verdict fails open ---------------------------------------------------


def test_no_activity_log_lets_the_spawn_through_and_says_so(project: Path) -> None:
    result = spawn(project)
    assert result.returncode == 0
    assert "cannot tell" in (result.stdout + result.stderr).lower(), result.stderr


def test_a_payload_with_no_subagent_type_is_ignored(project: Path) -> None:
    activity(project, started())
    result = subprocess.run(
        ["bash", str(project / "scripts" / "hook_implementer_lock.sh")],
        input='{"tool_input": {"command": "ls"}}',
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
        },
    )
    assert result.returncode == 0


def test_an_unusable_agents_pattern_lets_the_spawn_through(project: Path) -> None:
    activity(project, started())
    result = spawn(project, IMPLEMENTER_AGENTS="(unclosed")
    assert result.returncode == 0
    assert "cannot tell" in (result.stdout + result.stderr).lower()


def test_the_off_switch_disables_it(project: Path) -> None:
    activity(project, started())
    assert spawn(project, IMPLEMENTER_LOCK_OFF="1").returncode == 0


def test_break_glass_proceeds_and_is_audited(project: Path) -> None:
    activity(project, started())
    result = spawn(
        project, GATE_BYPASS="the other implementer is a zombie I already killed"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    logged = (project / "gate-overrides.log").read_text(encoding="utf-8")
    assert "implementer-lock" in logged
    assert "zombie" in logged


# --- the hook is wired to an event that can actually refuse ---------------------------------------


def test_the_hook_is_registered_on_the_one_event_that_can_block() -> None:
    """`SubagentStart` is where every other stage-scoped hook here lives and CANNOT block, so a rule
    written there would be one more thing that looks enforced and is not (issue #68's own warning)."""
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))[
        "hooks"
    ]
    spawn_hooks = [
        h["command"]
        for group in hooks["PreToolUse"]
        if group.get("matcher") == "Task|Agent"
        for h in group["hooks"]
    ]
    assert any("hook_implementer_lock.sh" in c for c in spawn_hooks), spawn_hooks
