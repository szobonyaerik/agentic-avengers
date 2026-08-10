"""Tests for the ponytail SubagentStart hook.

The dangerous direction is injecting a "write less code" persona into an agent whose job is to demand
MORE — the Verifier, the Breaker, the bug-hunter. So most of these pin "must NOT inject" cases, and
every failure path (bad payload, bad regex, missing skill) is asserted to fail closed.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hook_ponytail.sh"

IMPLEMENTERS = [
    "avenger-backend-architect",
    "avenger-frontend-developer",
    "plan-build-verify:avenger-backend-architect",
    "AVENGER-FRONTEND-DEVELOPER",
]

NON_IMPLEMENTERS = [
    "avenger-verifier",
    "avenger-breaker",
    "avenger-bug-hunter",
    "avenger-spec-writer",
    "avenger-task-analyst",
    "avenger-handover",
    "Explore",
    "general-purpose",
]

pytestmark = pytest.mark.subprocess(
    "the subject is a bash hook invoked by the harness exactly this way"
)


def run_hook(payload: str, env: dict[str, str] | None = None) -> str:
    """Run the hook with `payload` on stdin and return its stdout."""
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "CLAUDE_PLUGIN_ROOT": str(ROOT),
            **(env or {}),
        },
        check=False,
    )
    assert result.returncode == 0, f"hook must never fail the spawn: {result.stderr}"
    return result.stdout


def injected_context(stdout: str) -> str | None:
    """Return the injected context, or None when the hook injected nothing."""
    if not stdout.strip():
        return None
    payload = json.loads(stdout)["hookSpecificOutput"]
    assert payload["hookEventName"] == "SubagentStart"
    return payload["additionalContext"]


@pytest.mark.parametrize("agent_type", IMPLEMENTERS)
def test_injects_into_implementers(agent_type: str) -> None:
    context = injected_context(run_hook(json.dumps({"agent_type": agent_type})))
    assert context is not None
    assert "The ladder" in context


@pytest.mark.parametrize("agent_type", NON_IMPLEMENTERS)
def test_never_injects_into_non_implementers(agent_type: str) -> None:
    assert injected_context(run_hook(json.dumps({"agent_type": agent_type}))) is None


@pytest.mark.parametrize(
    "payload",
    ["", "not json at all", "{}", '{"agent_type":""}', '{"agent_type":null}', "[]"],
    ids=["empty", "garbage", "no-agent-type", "blank", "null", "wrong-shape"],
)
def test_unusable_payload_fails_closed(payload: str) -> None:
    assert injected_context(run_hook(payload)) is None


def test_bom_prefixed_payload_still_matches() -> None:
    payload = "﻿" + json.dumps({"agent_type": "avenger-backend-architect"})
    assert injected_context(run_hook(payload)) is not None


def test_bad_matcher_regex_fails_closed() -> None:
    payload = json.dumps({"agent_type": "avenger-backend-architect"})
    assert injected_context(run_hook(payload, {"PONYTAIL_AGENTS": "["})) is None


def test_off_switch_disables_injection() -> None:
    payload = json.dumps({"agent_type": "avenger-backend-architect"})
    assert injected_context(run_hook(payload, {"PONYTAIL_OFF": "1"})) is None


def test_matcher_is_overridable() -> None:
    payload = json.dumps({"agent_type": "Explore"})
    assert (
        injected_context(run_hook(payload, {"PONYTAIL_AGENTS": "explore"})) is not None
    )


def test_missing_skill_file_fails_closed(tmp_path: Path) -> None:
    payload = json.dumps({"agent_type": "avenger-backend-architect"})
    assert (
        injected_context(run_hook(payload, {"CLAUDE_PLUGIN_ROOT": str(tmp_path)}))
        is None
    )


def test_frontmatter_is_stripped() -> None:
    context = injected_context(
        run_hook(json.dumps({"agent_type": "avenger-backend-architect"}))
    )
    assert context is not None
    assert "name: ponytail" not in context
    assert not context.lstrip().startswith("---")


def test_injected_body_keeps_the_test_carve_out() -> None:
    """The carve-out is why this skill is safe to inject at all — it must survive stripping."""
    context = injected_context(
        run_hook(json.dumps({"agent_type": "avenger-backend-architect"}))
    )
    assert context is not None
    assert "Tests are out of scope" in context
    assert "Requirements are out of scope" in context
