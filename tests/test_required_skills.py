"""Required skills are injected, not requested — and a missing one is loud, not a silent fallback.

The pipeline delegates its core behaviour to thirteen skills and delegated by asking: "Load
`skills/tdd` before you start" is an instruction with no mechanism behind it. `docs/lessons/` shipped
with a complete written procedure and zero invocations for the same reason.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from required_skills import (  # noqa: E402
    DEFAULT_INJECT_MAX_BYTES,
    REQUIRED,
    audit_gaps,
    delivery_for,
    inject_max_bytes,
    load_record,
    main,
    missing,
    required_for,
    skill_path,
)

HOOK = ROOT / "scripts" / "hook_skills.sh"


# ── the table ────────────────────────────────────────────────────────────────


def test_every_pipeline_agent_requires_the_shared_rulebook() -> None:
    """An agent that has not read it is an agent guessing at phases, gates and the ID scheme."""
    for pattern, skills in REQUIRED:
        assert "pipeline-conventions" in skills, pattern


def test_the_implementers_require_the_tdd_procedure() -> None:
    """They write both the tests and the code, so it is not optional for them."""
    for agent in ("avenger-backend-architect", "avenger-frontend-developer"):
        assert "tdd" in required_for(agent)


def test_the_verifier_requires_triage_and_the_anti_patterns_it_reads_for() -> None:
    required = required_for("avenger-verifier")
    assert "verifier-triage" in required
    assert "tdd" in required, "the gamed-test patterns it reads a green suite for are defined there"


def test_plugin_scoped_names_match() -> None:
    """`plan-build-verify:avenger-verifier` is how the runtime names it."""
    assert required_for("plan-build-verify:avenger-verifier") == required_for("avenger-verifier")


def test_an_agent_this_pipeline_does_not_own_requires_nothing() -> None:
    assert required_for("some-other-agent") == ()
    assert required_for("") == ()


def test_every_required_skill_exists_in_this_repo() -> None:
    assert missing(ROOT) == []
    assert main(["verify", "--root", str(ROOT)]) == 0


def test_a_missing_required_skill_is_reported(tmp_path: Path) -> None:
    assert main(["verify", "--root", str(tmp_path)]) == 1


# ── the hook: injection, and the blocker ─────────────────────────────────────


def run_hook(
    root: Path, agent_type: str, log: Path, *, agent_id: str | None = None, **env: str
) -> dict:
    payload = {"agent_type": agent_type}
    if agent_id:
        payload["agent_id"] = agent_id
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(root),
             "CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PROJECT_DIR": str(root),
             "SKILL_LOAD_LOG": str(log), **env},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    """A plugin root with the real scripts and a skills tree we can break on purpose."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "skills").mkdir()
    for skill in ("pipeline-conventions", "tdd", "self-improvement"):
        directory = tmp_path / "skills" / skill
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {skill}\n---\n\n# {skill}\n\nthe rule from {skill}\n"
        )
    return tmp_path


pytestmark = pytest.mark.subprocess(
    "the subject is a bash SubagentStart hook; running it any other way would test a "
    "reimplementation of the delivery mechanism, which is the whole point"
)


def test_the_skill_body_is_injected_not_asked_for(plugin: Path, tmp_path: Path) -> None:
    context = run_hook(plugin, "avenger-backend-architect", tmp_path / "loads.jsonl")
    injected = context["hookSpecificOutput"]["additionalContext"]
    assert "the rule from tdd" in injected
    assert "the rule from pipeline-conventions" in injected
    assert "delivered, not requested" in injected


def test_the_injection_is_recorded_as_evidence(plugin: Path, tmp_path: Path) -> None:
    log = tmp_path / "loads.jsonl"
    run_hook(plugin, "avenger-backend-architect", log)
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert {r["skill"] for r in records} == {"pipeline-conventions", "tdd", "self-improvement"}
    assert all(r["required"] and r["loaded"] for r in records)
    assert all(r["agent_type"] == "avenger-backend-architect" for r in records)


def test_a_missing_required_skill_is_a_loud_blocker_not_a_silent_fallback(
    plugin: Path, tmp_path: Path
) -> None:
    """A required skill that is absent is not a lighter version of the rules; it is no rules."""
    (plugin / "skills" / "tdd" / "SKILL.md").unlink()
    log = tmp_path / "loads.jsonl"
    injected = run_hook(plugin, "avenger-backend-architect", log)[
        "hookSpecificOutput"]["additionalContext"]
    assert "BLOCKER" in injected
    assert "tdd" in injected
    assert "Do not proceed by guessing" in injected
    records = {json.loads(line)["skill"]: json.loads(line)["loaded"]
               for line in log.read_text().splitlines()}
    assert records["tdd"] is False
    assert records["pipeline-conventions"] is True


def test_an_agent_outside_the_table_gets_nothing(plugin: Path, tmp_path: Path) -> None:
    assert run_hook(plugin, "some-other-agent", tmp_path / "loads.jsonl") == {}


def test_an_unreadable_payload_injects_nothing(plugin: Path, tmp_path: Path) -> None:
    """Fail closed, the same rule as every other SubagentStart hook."""
    result = subprocess.run(
        ["bash", str(HOOK)], input="not json",
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(plugin),
             "CLAUDE_PLUGIN_ROOT": str(plugin), "CLAUDE_PROJECT_DIR": str(plugin),
             "SKILL_LOAD_LOG": str(tmp_path / "loads.jsonl")},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_off_switch_works(plugin: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(HOOK)], input=json.dumps({"agent_type": "avenger-verifier"}),
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(plugin), "SKILLS_OFF": "1",
             "CLAUDE_PLUGIN_ROOT": str(plugin), "CLAUDE_PROJECT_DIR": str(plugin)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_skill_path_helper_points_where_the_skills_live() -> None:
    assert skill_path(ROOT, "tdd") == ROOT / "skills" / "tdd" / "SKILL.md"


# ── pointer plus evidenced load ──────────────────────────────────────────────
#
# Injecting every required skill body GUARANTEES the load and costs the same order as the reads the
# read-path work had just removed. The evidence record DETECTS a missed load, and a required skill
# with no recorded load blocks the phase anyway — so detection beats prevention when both end the
# same way. What is pinned here is that the cheap half cannot quietly become a suggestion.


def test_the_ceiling_is_read_but_never_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert inject_max_bytes() == DEFAULT_INJECT_MAX_BYTES == 8192
    monkeypatch.setenv("SKILL_INJECT_MAX_BYTES", "100")
    assert inject_max_bytes() == 100
    assert delivery_for(100) == "inject" and delivery_for(101) == "pointer"
    monkeypatch.setenv("SKILL_INJECT_MAX_BYTES", "lots")
    with pytest.raises(ValueError, match="not a whole number"):
        inject_max_bytes()


def test_a_skill_over_the_ceiling_is_a_pointer_the_stage_must_load(
    plugin: Path, tmp_path: Path
) -> None:
    (plugin / "skills" / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: the red-green loop\n---\n\n" + "x" * 9000
    )
    log = tmp_path / "loads.jsonl"
    injected = run_hook(plugin, "avenger-backend-architect", log)[
        "hookSpecificOutput"]["additionalContext"]
    assert "x" * 9000 not in injected, "the whole point is that the body is NOT inlined"
    assert "the red-green loop" in injected, "the pointer carries the skill's own description"
    assert str(plugin / "skills" / "tdd" / "SKILL.md") in injected
    assert "required_skills.py record" in injected
    assert "BLOCKS THE PHASE" in injected
    assert "the rule from pipeline-conventions" in injected, "a small skill is still injected whole"

    records = {json.loads(line)["skill"]: json.loads(line) for line in log.read_text().splitlines()}
    assert records["tdd"]["delivery"] == "pointer" and records["tdd"]["loaded"] is False
    assert records["pipeline-conventions"]["delivery"] == "inject"
    assert records["pipeline-conventions"]["loaded"] is True


def test_a_pointer_with_no_recorded_load_fails_the_audit(plugin: Path, tmp_path: Path) -> None:
    """A pointer nothing checks is the instruction-with-no-mechanism the injection replaced."""
    (plugin / "skills" / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: the red-green loop\n---\n\n" + "x" * 9000
    )
    log = tmp_path / "loads.jsonl"
    run_hook(plugin, "avenger-backend-architect", log)
    assert main(["audit", "--log", str(log)]) == 1
    assert main(["record", "avenger-backend-architect", "tdd", "--log", str(log)]) == 0
    assert main(["audit", "--log", str(log)]) == 0


def test_the_audit_matches_on_the_agent_id_when_the_payload_carries_one(
    plugin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An audit that silently matches more loosely than it claims is the same defect class as
    everything else here, so the key it used is said out loud."""
    (plugin / "skills" / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: d\n---\n\n" + "x" * 9000
    )
    log = tmp_path / "loads.jsonl"
    run_hook(plugin, "avenger-backend-architect", log, agent_id="spawn-7")
    assert main(["audit", "--log", str(log)]) == 1
    # A load recorded against a DIFFERENT spawn does not clear this one.
    main(["record", "avenger-backend-architect", "tdd", "--agent-id", "spawn-9", "--log", str(log)])
    assert main(["audit", "--log", str(log)]) == 1
    main(["record", "avenger-backend-architect", "tdd", "--agent-id", "spawn-7", "--log", str(log)])
    assert main(["audit", "--log", str(log)]) == 0
    assert "agent_id" in capsys.readouterr().err


def test_an_injected_skill_needs_no_separate_load_record(plugin: Path, tmp_path: Path) -> None:
    """For an injected skill the injection IS the load — demanding a second record would be theatre."""
    log = tmp_path / "loads.jsonl"
    run_hook(plugin, "avenger-backend-architect", log)
    assert main(["audit", "--log", str(log)]) == 0


def test_a_required_skill_that_never_loaded_fails_the_audit() -> None:
    gaps, _ = audit_gaps([
        load_record("avenger-verifier", "tdd", event="delivery", delivery="inject", loaded=False)
    ])
    assert gaps and "missing or unreadable" in gaps[0]


def test_a_log_written_before_this_shape_existed_cannot_fail_an_audit() -> None:
    """Old records carry no `event` or `delivery`; reading them as injected deliveries is what stops
    an audit failing over evidence it predates."""
    gaps, key = audit_gaps([
        {"agent_type": "avenger-verifier", "skill": "tdd", "required": True, "loaded": True}
    ])
    assert gaps == [] and key == "n/a"


def test_no_evidence_file_is_clean_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI checks out a repo with no gitignored scratch log. Nothing was delivered, so nothing was
    missed — but it is said out loud rather than passing invisibly."""
    assert main(["audit", "--log", str(tmp_path / "absent.jsonl")]) == 0
    assert "no spawns recorded" in capsys.readouterr().err


def test_a_corrupt_evidence_line_is_an_error_never_a_silent_skip(tmp_path: Path) -> None:
    log = tmp_path / "loads.jsonl"
    log.write_text('{"skill": "tdd"}\nnot json at all\n')
    assert main(["audit", "--log", str(log)]) == 2


def test_an_unparseable_ceiling_delivers_nothing(plugin: Path, tmp_path: Path) -> None:
    """Fail closed: a ceiling nobody can read decides nothing, so it must not decide silently."""
    assert run_hook(
        plugin, "avenger-backend-architect", tmp_path / "loads.jsonl",
        SKILL_INJECT_MAX_BYTES="lots",
    ) == {}
