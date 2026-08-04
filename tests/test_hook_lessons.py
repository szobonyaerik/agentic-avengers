"""Tests for the lessons SubagentStart hook.

This hook is the mechanical delivery of `docs/lessons/` — a prompt directive in a skill nobody loads
is exactly the dormancy bug the lessons log itself suffered from. Unlike ponytail it must reach ALL
canonical avenger agents (prior lessons never conflict with a stage's job), so the reach cases are
pinned per agent file.

The dangerous direction here is a hook that breaks a subagent spawn or bloats every spawn's context.
So every failure path (missing index, empty index, malformed JSON, malformed payload, bad regex)
is asserted to fail closed, and the payload is asserted to stay a pointer rather than inlined content.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hook_lessons.sh"

CANONICAL_AGENTS = sorted(p.stem for p in (ROOT / "agents").glob("avenger-*.md"))

NON_PIPELINE_AGENTS = ["Explore", "general-purpose", "statusline-setup"]

LESSON_ENTRY = {
    "id": "a1b2c3d4e5f6",
    "date": "2026-08-04",
    "title": "Never trust a bare pytest exit code in the mutation baseline",
    "summary": "Run cosmic-ray baseline first; a broken suite scores a perfect 1.0.",
    "tags": ["mutation", "pytest", "gates"],
    "scope": "backend-architect",
    "path": "docs/lessons/a1b2c3d4e5f6-mutation-baseline.md",
}
PROSE_BODY = "# Never trust a bare pytest exit code\n\nSECRET_PROSE_MARKER — the full story.\n"


def make_project(tmp_path: Path, lessons: str | None, prose: bool = True) -> Path:
    """Build a throwaway project root; `lessons` is the raw lessons.json body (None = no file)."""
    lessons_dir = tmp_path / "docs" / "lessons"
    lessons_dir.mkdir(parents=True)
    if lessons is not None:
        (lessons_dir / "lessons.json").write_text(lessons, encoding="utf-8")
    if prose:
        (lessons_dir / "a1b2c3d4e5f6-mutation-baseline.md").write_text(
            PROSE_BODY, encoding="utf-8"
        )
    return tmp_path


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


def run_for(
    agent_type: str, tmp_path: Path, lessons: str | None, env: dict[str, str] | None = None
) -> str | None:
    project = make_project(tmp_path, lessons)
    return injected_context(
        run_hook(
            json.dumps({"agent_type": agent_type}),
            {"CLAUDE_PROJECT_DIR": str(project), **(env or {})},
        )
    )


ONE_LESSON = json.dumps([LESSON_ENTRY])


@pytest.mark.parametrize("agent_type", CANONICAL_AGENTS)
def test_reaches_every_canonical_agent(agent_type: str, tmp_path: Path) -> None:
    """The whole point of this hook: one mechanism, all 11 agents — not just the implementers."""
    context = run_for(agent_type, tmp_path, ONE_LESSON)
    assert context is not None
    assert "docs/lessons/lessons.json" in context


@pytest.mark.parametrize(
    "agent_type",
    [
        "plan-build-verify:avenger-verifier",
        "AVENGER-BREAKER",
    ],
    ids=["plugin-scoped", "upper-case"],
)
def test_matcher_is_unanchored_and_case_insensitive(
    agent_type: str, tmp_path: Path
) -> None:
    assert run_for(agent_type, tmp_path, ONE_LESSON) is not None


@pytest.mark.parametrize("agent_type", NON_PIPELINE_AGENTS)
def test_unmatched_agent_injects_nothing(agent_type: str, tmp_path: Path) -> None:
    assert run_for(agent_type, tmp_path, ONE_LESSON) is None


def test_missing_lessons_file_injects_nothing(tmp_path: Path) -> None:
    """A project that never wrote a lesson must see no behaviour change at all."""
    assert run_for("avenger-verifier", tmp_path, None) is None


def test_empty_index_injects_nothing(tmp_path: Path) -> None:
    assert run_for("avenger-verifier", tmp_path, "[]") is None


@pytest.mark.parametrize(
    "lessons",
    ["", "not json at all", "{", '{"id": "x"}', "null", '"a string"', "[[]"],
    ids=["empty", "garbage", "truncated", "object", "null", "scalar", "broken-array"],
)
def test_unparseable_index_fails_closed(lessons: str, tmp_path: Path) -> None:
    assert run_for("avenger-verifier", tmp_path, lessons) is None


def test_missing_project_dir_fails_closed(tmp_path: Path) -> None:
    absent = tmp_path / "nope"
    assert (
        injected_context(
            run_hook(
                json.dumps({"agent_type": "avenger-verifier"}),
                {"CLAUDE_PROJECT_DIR": str(absent)},
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "payload",
    ["", "not json at all", "{}", '{"agent_type":""}', '{"agent_type":null}', "[]"],
    ids=["empty", "garbage", "no-agent-type", "blank", "null", "wrong-shape"],
)
def test_unusable_payload_fails_closed(payload: str, tmp_path: Path) -> None:
    project = make_project(tmp_path, ONE_LESSON)
    assert (
        injected_context(run_hook(payload, {"CLAUDE_PROJECT_DIR": str(project)})) is None
    )


def test_bom_prefixed_payload_still_matches(tmp_path: Path) -> None:
    project = make_project(tmp_path, ONE_LESSON)
    payload = "﻿" + json.dumps({"agent_type": "avenger-verifier"})
    assert (
        injected_context(run_hook(payload, {"CLAUDE_PROJECT_DIR": str(project)}))
        is not None
    )


def test_bad_matcher_regex_fails_closed(tmp_path: Path) -> None:
    assert run_for("avenger-verifier", tmp_path, ONE_LESSON, {"LESSONS_AGENTS": "["}) is None


def test_off_switch_disables_injection(tmp_path: Path) -> None:
    assert run_for("avenger-verifier", tmp_path, ONE_LESSON, {"LESSONS_OFF": "1"}) is None


def test_matcher_is_overridable(tmp_path: Path) -> None:
    assert run_for("Explore", tmp_path, ONE_LESSON, {"LESSONS_AGENTS": "explore"}) is not None


def test_payload_is_a_pointer_not_the_lessons(tmp_path: Path) -> None:
    """This fires on EVERY subagent spawn — it must never inline the log or a prose file."""
    context = run_for("avenger-verifier", tmp_path, ONE_LESSON)
    assert context is not None
    assert LESSON_ENTRY["title"] not in context
    assert LESSON_ENTRY["summary"] not in context
    assert "SECRET_PROSE_MARKER" not in context
    assert len(context) < 1200


def test_payload_reports_the_entry_count(tmp_path: Path) -> None:
    context = run_for("avenger-verifier", tmp_path, json.dumps([LESSON_ENTRY] * 3))
    assert context is not None
    assert "3" in context


def test_registered_on_subagent_start(tmp_path: Path) -> None:
    """Alongside ponytail — the hook only works if it is actually wired up."""
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        h["command"]
        for group in hooks["hooks"]["SubagentStart"]
        for h in group["hooks"]
    ]
    assert any("hook_lessons.sh" in c for c in commands)
    assert any("hook_ponytail.sh" in c for c in commands)
