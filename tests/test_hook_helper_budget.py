"""The bound that acts on a helper dispatch - driven through the real hooks.

`scripts/helper_budget.py` decides whether a dispatch is bounded and its own tests cover that.
What is pinned HERE is that the decision is ACTED ON at the moment the helper would be spawned,
which is the only moment the remedy exists - and that a spend row lands the moment one returns,
because issue #118's third half is that an hour of helper time left no trace in any record.
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

import helper_budget  # noqa: E402
import implementer_liveness as liveness  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    return tmp_path


def when(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


def activity(project: Path, *rows: dict) -> None:
    (project / liveness.LOG_NAME).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def spawn(
    project: Path, stage: str = "general-purpose", prompt: str = "do it", **env: str
):
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_helper_budget.sh")],
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Task",
                "tool_input": {"subagent_type": stage, "prompt": prompt},
            }
        ),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            "AVENGER_METRICS_OFF": "1",
            **env,
        },
    )


# --- the refusal ----------------------------------------------------------------------------------


def test_a_helper_with_no_declared_budget_is_refused(project: Path) -> None:
    result = spawn(project)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "declares no budget" in result.stderr


def test_a_helper_already_past_its_budget_refuses_the_next_one(project: Path) -> None:
    activity(
        project,
        {
            "ts": when(700),
            "event": helper_budget.DISPATCH,
            "agent_type": "general-purpose",
            "budget_s": 300,
        },
        {
            "ts": when(690),
            "event": liveness.START,
            "agent_type": "general-purpose",
            "agent_id": "h1",
            "harness_pid": os.getpid(),
        },
    )
    result = spawn(project, prompt="Budget: 5m")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "already past its declared budget" in result.stderr


def test_a_declared_budget_lets_the_dispatch_through(project: Path) -> None:
    result = spawn(project, prompt="Re-run the suite.\n\nBudget: 5m\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_pipeline_stage_is_not_bound(project: Path) -> None:
    assert spawn(project, "avenger-verifier").returncode == 0


def test_the_clean_result_states_what_it_does_not_establish(project: Path) -> None:
    """A later stage reads output, not source (issue #97)."""
    result = spawn(project, prompt="Budget: 5m")
    assert "SCOPE OF A CLEAN RESULT" in result.stderr


# --- everything that is not a verdict fails open ---------------------------------------------------


def test_a_checker_that_crashes_does_not_block_the_spawn(project: Path) -> None:
    """A traceback exits 1 too; only the REFUSE marker is a finding."""
    (project / "scripts" / "helper_budget.py").write_text(
        "import sys\nsys.stderr.write('boom\\n')\nraise SystemExit(1)\n",
        encoding="utf-8",
    )
    result = spawn(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cannot tell" in result.stderr


def test_the_off_switch_disables_the_refusal(project: Path) -> None:
    assert spawn(project, HELPER_BUDGET_OFF="1").returncode == 0


def test_a_scoped_bypass_proceeds_and_is_audited(project: Path) -> None:
    result = spawn(
        project,
        GATE_BYPASS="a genuine re-measurement that needs the whole suite",
        GATE_BYPASS_GATES="helper-budget",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    log = project / "gate-overrides.log"
    assert log.is_file() and "helper-budget" in log.read_text(encoding="utf-8")


# --- what a returning helper cost is recorded ------------------------------------------------------


def stop_payload(agent: str = "general-purpose", agent_id: str = "h1") -> str:
    return json.dumps(
        {"hook_event_name": "SubagentStop", "agent_type": agent, "agent_id": agent_id}
    )


def spend_hook(project: Path, payload: str, **env: str):
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_helper_spend.sh")],
        input=payload,
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


def test_the_spend_hook_never_fails_a_phase(project: Path) -> None:
    """Writing metrics may never fail a phase (CLAUDE.md 6d), whatever it finds or cannot find."""
    for payload in (
        stop_payload(),
        "{not json",
        json.dumps({"hook_event_name": "Other"}),
    ):
        assert spend_hook(project, payload).returncode == 0


def test_the_spend_hook_records_elapsed_time_against_the_declared_budget(
    project: Path, tmp_path: Path
) -> None:
    phase = project / "docs" / "features" / "demo" / "phases" / "1-a"
    phase.mkdir(parents=True)
    (phase / "verdict.json").write_text("{}", encoding="utf-8")
    activity(
        project,
        {
            "ts": when(700),
            "event": helper_budget.DISPATCH,
            "agent_type": "general-purpose",
            "budget_s": 300,
        },
        {
            "ts": when(690),
            "event": liveness.START,
            "agent_type": "general-purpose",
            "agent_id": "h1",
            "harness_pid": os.getpid(),
        },
    )
    writer = tmp_path / "writer.sh"
    calls = tmp_path / "calls.txt"
    writer.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {calls}\n'
        'if [ "$1" = "show" ]; then echo "{}"; fi\nexit 0\n',
        encoding="utf-8",
    )
    writer.chmod(0o755)
    result = spend_hook(project, stop_payload(), AVENGER_METRICS_CMD=str(writer))
    assert result.returncode == 0
    written = calls.read_text(encoding="utf-8") if calls.is_file() else ""
    assert "helper-spend" in written, written
    assert "elapsed_s=690" in written, written
    assert "budget_s=300" in written, written


def test_a_pipeline_stage_leaves_no_helper_spend_row(
    project: Path, tmp_path: Path
) -> None:
    calls = tmp_path / "calls.txt"
    writer = tmp_path / "writer.sh"
    writer.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {calls}\nexit 0\n',
        encoding="utf-8",
    )
    writer.chmod(0o755)
    spend_hook(
        project,
        stop_payload("avenger-verifier", "v1"),
        AVENGER_METRICS_CMD=str(writer),
    )
    assert not calls.is_file() or "helper-spend" not in calls.read_text(
        encoding="utf-8"
    )
