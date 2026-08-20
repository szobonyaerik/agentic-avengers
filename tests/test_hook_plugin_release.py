"""Tests for the release-drift halt: `scripts/hook_plugin_release.sh`.

`scripts/plugin_release.py check` was falsification-tested on 2026-08-20 and is correct - and
nothing in the shipped payload called it. The STOP was a sentence in `commands/avenger-run.md`
telling the orchestrator to halt, so a run whose orchestrator never read that line executed every
phase against stale code with no signal anywhere. These tests are about the half that was missing:
that something EXECUTES the refusal.

Two properties carry the whole design, and both are asserted in both directions, per issue #69's
standing rule that a guard proven only by passing is not proven:

* **A confirmed STALE is refused.** RED first - a genuinely divergent pair of trees must make the
  hook exit 2 - then GREEN on the same pair once their content matches.
* **An honest UNKNOWN is not a halt.** agentic-avengers ships standalone and most repositories using
  it cannot resolve a source repository at all; turning "cannot tell" into "stop" would break every
  one of them. So `unknown`, an unreadable payload, a crashed checker and a foreign subagent each
  proceed, and the stale case above is what proves those exits are not vacuous.

The subject is a bash hook, driven here with the JSON payload shape the harness puts on its stdin.
Nothing writes outside `tmp_path`: every `CLAUDE_PROJECT_DIR`, plugin root and source repository is
a fixture, so the break-glass test's `gate-overrides.log` lands in a temp directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_plugin_release import make_plugin  # noqa: E402 - one fixture builder, not two

HOOK = ROOT / "scripts" / "hook_plugin_release.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash PreToolUse hook and the halt IS its exit code - running the logic any "
    "other way would test something the harness never executes"
)

STALE_EXIT = 2  # the harness's blocking code: PreToolUse exit 2 refuses the tool call


def spawn_payload(subagent_type: str = "avenger-backend-architect") -> str:
    """The PreToolUse payload for a stage spawn. `tool_name` is deliberately present but never what
    the hook decides on - the spawn tool is `Task` in some harness versions and `Agent` in others."""
    return json.dumps({
        "tool_name": "Agent",
        "tool_input": {"subagent_type": subagent_type, "prompt": "implement spec 1.0"},
    })


def run_hook(payload: str, *, project: Path, env: dict[str, str] | None = None):
    return subprocess.run(  # noqa: S603
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            **(env or {}),
        },
    )


def stale_pair(tmp_path: Path) -> tuple[Path, Path]:
    """An executing copy that is genuinely behind the merged repository."""
    repo = make_plugin(tmp_path / "repo", version="0.10.3")
    executing = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")
    return executing, repo


def drift_env(executing: Path, source: Path) -> dict[str, str]:
    return {"CLAUDE_PLUGIN_ROOT": str(executing), "AVENGER_SOURCE_REPO": str(source)}


# ── the halt itself ────────────────────────────────────────────────────────────────────────────


def test_a_stale_executing_copy_refuses_the_stage_red_then_green(tmp_path):
    """RED: a stale copy must actually stop a stage spawn. GREEN: the same spawn proceeds the moment
    the executing copy's content matches the merged repository - so exit 2 above is the drift and
    nothing else."""
    executing, repo = stale_pair(tmp_path)

    red = run_hook(spawn_payload(), project=tmp_path, env=drift_env(executing, repo))

    assert red.returncode == STALE_EXIT, red.stderr
    assert "STALE" in red.stderr
    assert "avenger-backend-architect" in red.stderr

    released = tmp_path / "cache" / "0.10.3"
    shutil.copytree(repo, released)
    green = run_hook(spawn_payload(), project=tmp_path, env=drift_env(released, repo))

    assert green.returncode == 0, green.stderr


def test_the_stop_names_the_remedy_in_the_operators_words(tmp_path):
    """The person who has to fix this cuts a release; the message has to say that, not point at an
    internal check."""
    executing, repo = stale_pair(tmp_path)

    result = run_hook(spawn_payload(), project=tmp_path, env=drift_env(executing, repo))

    assert "not the merged repository" in result.stderr.lower()
    assert "plugin_release.py cut" in result.stderr
    assert "restart Claude Code" in result.stderr
    assert "GATE_BYPASS" in result.stderr


def test_every_pipeline_stage_is_bound_not_just_the_implementers(tmp_path):
    executing, repo = stale_pair(tmp_path)

    for stage in (
        "avenger-spec-writer",
        "avenger-verifier",
        "plan-build-verify:avenger-breaker",
        "AVENGER-FRONTEND-DEVELOPER",
    ):
        result = run_hook(spawn_payload(stage), project=tmp_path, env=drift_env(executing, repo))
        assert result.returncode == STALE_EXIT, f"{stage} was not refused: {result.stderr}"


# ── what must NEVER halt ───────────────────────────────────────────────────────────────────────


def test_an_honest_unknown_is_not_a_halt(tmp_path):
    """No source repository resolvable is the documented normal state of a standalone install. It
    proceeds exactly as it did before this hook existed."""
    executing = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")

    result = run_hook(
        spawn_payload(), project=tmp_path, env={"CLAUDE_PLUGIN_ROOT": str(executing)}
    )

    assert result.returncode == 0, result.stderr


def test_a_foreign_subagent_is_not_this_hooks_business(tmp_path):
    executing, repo = stale_pair(tmp_path)

    for stage in ("Explore", "general-purpose", "code-reviewer"):
        result = run_hook(spawn_payload(stage), project=tmp_path, env=drift_env(executing, repo))
        assert result.returncode == 0, f"{stage} was refused: {result.stderr}"


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}),
        json.dumps({"tool_name": "Agent", "tool_input": {}}),
        json.dumps({"tool_name": "Agent"}),
        "not json at all",
        "",
    ],
    ids=["another-tool", "no-subagent-type", "no-tool-input", "unparseable", "empty"],
)
def test_anything_that_is_not_a_stage_spawn_fails_open(tmp_path, payload):
    executing, repo = stale_pair(tmp_path)

    result = run_hook(payload, project=tmp_path, env=drift_env(executing, repo))

    assert result.returncode == 0, result.stderr


def test_a_bad_stage_regex_fails_open_rather_than_refusing_everything(tmp_path):
    executing, repo = stale_pair(tmp_path)

    result = run_hook(
        spawn_payload(),
        project=tmp_path,
        env={**drift_env(executing, repo), "PLUGIN_RELEASE_STAGES": "["},
    )

    assert result.returncode == 0, result.stderr


def test_a_checker_that_cannot_answer_is_reported_but_never_a_halt(tmp_path):
    """`plugin_release.py check` exits 1 for a stale copy - and a traceback exits 1 too. A crash
    arriving as a halt would prescribe "cut a release" for a defect a release cannot fix, so the
    verdict is read as the exit code AND the checker's own marker."""
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    stub = stub_bin / "python3"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "Traceback (most recent call last):" >&2\n'
        'echo "RuntimeError: broken checker" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    executing, repo = stale_pair(tmp_path)

    result = run_hook(
        spawn_payload(),
        project=tmp_path,
        env={
            **drift_env(executing, repo),
            "PATH": f"{stub_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "could not reach a verdict" in result.stderr
    assert "RuntimeError: broken checker" in result.stderr, "the checker's own words are the report"


# ── break-glass, and the wiring that makes any of this run ─────────────────────────────────────


def test_break_glass_proceeds_and_is_audited(tmp_path):
    executing, repo = stale_pair(tmp_path)

    result = run_hook(
        spawn_payload(),
        project=tmp_path,
        env={**drift_env(executing, repo), "GATE_BYPASS": "captain ordered: release cut is queued"},
    )

    assert result.returncode == 0, result.stderr
    log = (tmp_path / "gate-overrides.log").read_text(encoding="utf-8")
    assert "gate:plugin-release" in log
    assert "captain ordered: release cut is queued" in log


def test_hooks_json_runs_the_halt_at_an_event_that_can_actually_block():
    """The check was mechanical and the stop was prose; this is the line that changes that. It is
    pinned at PreToolUse deliberately - `SubagentStart`, where every other stage-scoped hook here
    lives, is documented as unable to block, so a halt wired there would be prose again."""
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    groups = [
        group for group in hooks["PreToolUse"]
        if any("hook_plugin_release.sh" in hook["command"] for hook in group["hooks"])
    ]

    assert groups, "nothing in hooks.json runs the release-drift halt"
    matcher = groups[0].get("matcher", "")
    for tool in ("Task", "Agent"):
        assert tool in matcher, f"the spawn tool is named {tool} in some harness versions"
    assert "hook_plugin_release.sh" not in json.dumps(hooks.get("SubagentStart", []))
