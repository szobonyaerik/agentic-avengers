"""Tests for the SubagentStop skill audit — the same audit, at the point the stage ran.

The audit that blocks on a required skill with no observed load ran in exactly two places, both of
them at the end: the handover hook and CI. Measured on clickup-agents phase 10, that meant
`avenger-spec-writer` learned it had never loaded `skills/spec-review-checklist` at the contract
card, with every spec of the phase already written, gated, reviewed and implemented — the most
expensive moment the pipeline has, and the one moment at which the remedy (open one file, in that
stage) is no longer available, because the stage is gone.

So the same question is asked at `SubagentStop`, scoped to the stage that is finishing and still
alive to answer it. Nothing about the judgement moves: the gap is the same gap, computed by the same
`audit_gaps`, with the same wording. What moves is *when* it is asked, and therefore what it costs.

Two properties are pinned hardest here, because both are ways this could become a wedge rather than
a fix: it never blocks twice for the same stop (`stop_hook_active`), and every unknown — an
unreadable payload, a foreign agent, no phase, no writer — lets the stage finish. The close-time
audit is unchanged and remains the backstop for every path with no such event (opencode, the main
thread), so an early check that fails open loses nothing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import DOUBLE

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hook_skill_audit.sh"
ACTIVITY_HOOK = ROOT / "scripts" / "hook_activity.sh"

sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_metrics as metrics  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the subject is a bash SubagentStop hook and its exit code is the whole contract; "
    "running it any other way would test a reimplementation of it"
)


@pytest.fixture
def stage(tmp_path: Path):
    """A plugin root, a phase in flight, and a double writer. Yields (run, record_a_gap)."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    shutil.copytree(ROOT / "scripts", plugin / "scripts")
    (plugin / "agents").mkdir()
    (plugin / "agents" / "avenger-spec-writer.md").write_text(
        "> **Required skills.** `skills/pipeline-conventions`, `skills/spec-review-checklist` — "
        "load each before you start.\n",
        encoding="utf-8",
    )
    (plugin / "skills").mkdir()
    for skill in ("pipeline-conventions", "spec-review-checklist"):
        (plugin / "skills" / skill).mkdir()
        (plugin / "skills" / skill / "SKILL.md").write_text(
            f"---\nname: {skill}\n---\n\n# {skill}\n", encoding="utf-8"
        )

    project = tmp_path / "project"
    (project / "docs" / "features" / "demo" / "phases" / "1-demo").mkdir(parents=True)
    (project / "docs" / "features" / "demo" / "phases" / "1-demo" / "handover.md").write_text("x")

    store = tmp_path / "store"
    store.mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(plugin),
        "CLAUDE_PLUGIN_ROOT": str(plugin),
        "CLAUDE_PROJECT_DIR": str(project),
        "AVENGER_METRICS_CMD": str(double),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp_path / "diagnostics.log"),
        "DOUBLE_LOG": str(tmp_path / "calls.log"),
        "DOUBLE_STORE": str(store),
        "ACTIVITY_LOG": str(tmp_path / "activity.jsonl"),
    }

    def run(payload: dict, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env={**env, **extra},
        )

    def seed(loaded: bool, skill: str = "spec-review-checklist") -> None:
        subprocess.run(
            ["python3", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]);"
             "import pipeline_metrics as m;"
             "m.record_skill_load(sys.argv[2], stage=sys.argv[3], skill=sys.argv[4],"
             " evidence=sys.argv[5], loaded=sys.argv[6] == '1')",
             str(plugin / "scripts"), "01", "avenger-spec-writer", skill,
             "Read SKILL.md" if loaded else "", "1" if loaded else "0"],
            capture_output=True, text=True, check=True, env=env,
        )

    return run, seed


@pytest.fixture
def activity_log(tmp_path: Path) -> Path:
    """The same path the `stage` fixture points the hooks at — both derive from `tmp_path`."""
    return tmp_path / "activity.jsonl"


STOP = {"hook_event_name": "SubagentStop", "agent_type": "avenger-spec-writer"}


# ── the event this exists for ────────────────────────────────────────────────


def test_a_stage_finishing_with_an_unloaded_required_skill_is_told_while_it_can_still_act(
    stage,
) -> None:
    run, seed = stage
    seed(loaded=False)

    result = run(STOP)

    assert result.returncode == 2, "exit 2 is what returns the stage to work; 0 would be a log"
    assert "spec-review-checklist" in result.stderr
    assert "avenger-spec-writer" in result.stderr
    assert not result.stdout.strip(), "SubagentStop stdout is not this hook's channel"


def test_opening_the_file_is_what_clears_it(stage) -> None:
    """The remedy the message prescribes has to be the remedy that works — the whole reason to ask
    at the stage boundary rather than at the card, where it no longer exists."""
    run, seed = stage
    seed(loaded=False)
    assert run(STOP).returncode == 2

    seed(loaded=True)
    assert run(STOP).returncode == 0


def test_a_stage_that_owes_nothing_finishes(stage) -> None:
    run, seed = stage
    seed(loaded=True)
    assert run(STOP).returncode == 0


def test_another_stages_gap_does_not_stop_this_stage(stage) -> None:
    """Scoped to the stage that is finishing. Unscoped, every later subagent in the phase would be
    stopped by a gap it cannot fix — a close-time blocker moved earlier and multiplied."""
    run, seed = stage
    seed(loaded=False)
    result = run({**STOP, "agent_type": "avenger-verifier"})
    assert result.returncode == 0


# ── it must never become a wedge ─────────────────────────────────────────────


def test_it_blocks_at_most_once_per_stop(stage) -> None:
    """`stop_hook_active` means this hook already spoke and the stage came back. Blocking again on
    an unchanged record is a loop, and a loop here would cost more than the defect it replaces."""
    run, seed = stage
    seed(loaded=False)

    result = run({**STOP, "stop_hook_active": True})

    assert result.returncode == 0
    assert "spec-review-checklist" in result.stderr, "still said out loud, just not blocking"


def _lifecycle(payload: dict, log: Path) -> None:
    """The record `hook_activity.sh` writes for `payload` — the real writer, not a reimplementation
    of it, because the shape of the line is exactly what `observing_stage` reads."""
    subprocess.run(
        ["bash", str(ACTIVITY_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], "ACTIVITY_LOG": str(log)},
    )


def test_a_blocked_stop_leaves_the_stage_live_in_the_activity_log(
    stage, activity_log: Path, monkeypatch
) -> None:
    """A refused stop is not a stop, and the log has to say so.

    `hook_activity.sh` has already recorded the stop by the time this hook refuses it, so the log
    reads as though the stage ended while it is in fact going back to work. `observing_stage` then
    sees no live subagent and attributes the remedial `Read` of the named SKILL.md to
    `main-thread` — writing `main-thread:<skill>` and leaving the stage's own row `loaded: false`,
    so the remedy this hook prescribes cannot clear the gap it prescribed it for.
    """
    run, seed = stage
    seed(loaded=False)

    start = {"hook_event_name": "SubagentStart", "agent_type": "avenger-spec-writer"}
    _lifecycle(start, activity_log)
    _lifecycle(STOP, activity_log)

    assert run(STOP).returncode == 2

    monkeypatch.setenv("ACTIVITY_LOG", str(activity_log))
    assert metrics.observing_stage("") == "avenger-spec-writer"


def test_the_real_stop_that_follows_still_balances(
    stage, activity_log: Path, monkeypatch
) -> None:
    """Re-opening the lifecycle must not leave the stage live forever: the next stop closes it, and
    a stage stuck at live would misattribute every later load in the same log."""
    run, seed = stage
    seed(loaded=False)

    _lifecycle({"hook_event_name": "SubagentStart", "agent_type": "avenger-spec-writer"}, activity_log)
    _lifecycle(STOP, activity_log)
    assert run(STOP).returncode == 2

    # The stage comes back, still owes the skill, is told once more and finishes.
    reentry = {**STOP, "stop_hook_active": True}
    _lifecycle(reentry, activity_log)
    assert run(reentry).returncode == 0

    monkeypatch.setenv("ACTIVITY_LOG", str(activity_log))
    assert metrics.observing_stage("") == "main-thread"


def test_a_clean_stop_does_not_re_open_the_lifecycle(
    stage, activity_log: Path, monkeypatch
) -> None:
    """Only a refusal re-opens it. A stage that owes nothing has genuinely stopped."""
    run, seed = stage
    seed(loaded=True)

    _lifecycle({"hook_event_name": "SubagentStart", "agent_type": "avenger-spec-writer"}, activity_log)
    _lifecycle(STOP, activity_log)
    assert run(STOP).returncode == 0

    monkeypatch.setenv("ACTIVITY_LOG", str(activity_log))
    assert metrics.observing_stage("") == "main-thread"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        json.dumps({"hook_event_name": "SubagentStop"}),
        json.dumps({"hook_event_name": "SubagentStop", "agent_type": "general-purpose"}),
        json.dumps({"hook_event_name": "SubagentStop", "agent_type": "Explore"}),
    ],
)
def test_an_unknown_stop_lets_the_stage_finish(stage, payload: str) -> None:
    """Fails OPEN, deliberately and unlike its siblings: this is an EARLY copy of a check that still
    blocks at close. An early check that guessed would stop stages for no evidence, and the phase
    would still be caught by the audit that has always run."""
    run, _ = stage
    result = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "CLAUDE_PLUGIN_ROOT": str(ROOT)},
    )
    assert result.returncode == 0


def test_the_off_switch_works(stage) -> None:
    run, seed = stage
    seed(loaded=False)
    assert run(STOP, SKILL_AUDIT_OFF="1").returncode == 0
    assert run(STOP, SKILLS_OFF="1").returncode == 0, "delivery off means nothing was owed"


def test_no_metrics_writer_never_stops_a_stage(stage) -> None:
    """Nothing was ever observed, so nothing can be missing. Same rule as the close-time audit."""
    run, seed = stage
    seed(loaded=False)
    assert run(STOP, AVENGER_METRICS_OFF="1").returncode == 0


# ── the wiring is part of the fix ────────────────────────────────────────────


def test_the_hook_is_wired_to_subagent_stop() -> None:
    """A hook nothing invokes is the instruction-with-no-mechanism this pipeline keeps removing."""
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [
        entry["command"] for group in hooks["SubagentStop"] for entry in group["hooks"]
    ]
    assert any("hook_skill_audit.sh" in command for command in commands)


def test_the_close_time_audit_is_still_wired() -> None:
    """The early check is an addition, not a move: opencode and the main thread reach no
    `SubagentStop`, so the backstop that has always blocked the card still has to."""
    assert "required_skills.py\" audit" in (
        ROOT / "scripts" / "hook_verifier.sh"
    ).read_text(encoding="utf-8")
    assert "required_skills.py\" audit --all" in (
        ROOT / "scripts" / "gate_ci.sh"
    ).read_text(encoding="utf-8")
