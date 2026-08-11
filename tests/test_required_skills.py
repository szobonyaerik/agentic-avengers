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
    assert main(["audit", "--all", "--log", str(log)]) == 1
    assert main(["record", "avenger-backend-architect", "tdd", "--log", str(log)]) == 0
    assert main(["audit", "--all", "--log", str(log)]) == 0


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
    assert main(["audit", "--all", "--log", str(log)]) == 1
    # A load recorded against a DIFFERENT spawn does not clear this one.
    main(["record", "avenger-backend-architect", "tdd", "--agent-id", "spawn-9", "--log", str(log)])
    assert main(["audit", "--all", "--log", str(log)]) == 1
    main(["record", "avenger-backend-architect", "tdd", "--agent-id", "spawn-7", "--log", str(log)])
    assert main(["audit", "--all", "--log", str(log)]) == 0
    assert "agent_id" in capsys.readouterr().err


def test_an_injected_skill_needs_no_separate_load_record(plugin: Path, tmp_path: Path) -> None:
    """For an injected skill the injection IS the load — demanding a second record would be theatre."""
    log = tmp_path / "loads.jsonl"
    run_hook(plugin, "avenger-backend-architect", log)
    assert main(["audit", "--all", "--log", str(log)]) == 0


def test_one_stages_load_does_not_clear_another_stages_pointer() -> None:
    """The audit is the whole mechanism that justified dropping full injection, and it settles H9.
    Keyed on the skill alone it detected "nobody loaded X" while reporting "this stage did not load
    X" — coverage it did not have, settling a hypothesis with an instrument that never ran."""
    records = [
        {"event": "delivery", "agent_type": "avenger-verifier",
         "skill": "pipeline-conventions", "required": True, "delivery": "pointer"},
        {"event": "delivery", "agent_type": "avenger-backend-architect",
         "skill": "pipeline-conventions", "required": True, "delivery": "pointer"},
        {"event": "load", "agent_type": "avenger-verifier", "skill": "pipeline-conventions"},
    ]
    gaps, key = audit_gaps(records)
    assert len(gaps) == 1, "the implementer was pointed at it and never loaded it"
    assert "avenger-backend-architect" in gaps[0]
    assert "avenger-verifier" not in gaps[0], "the verifier did load it"
    assert key == "agent_type"


def test_a_load_by_another_spawn_does_not_clear_this_spawns_pointer() -> None:
    records = [
        {"event": "delivery", "agent_type": "avenger-backend-architect", "agent_id": "spawn-b",
         "skill": "tdd", "required": True, "delivery": "pointer"},
        {"event": "load", "agent_type": "avenger-backend-architect", "agent_id": "spawn-a",
         "skill": "tdd"},
    ]
    gaps, key = audit_gaps(records)
    assert len(gaps) == 1 and key == "agent_id"


def test_the_reported_key_names_both_when_both_were_used() -> None:
    """An audit that says one thing and matches another is the defect, whichever way it leans."""
    records = [
        {"event": "delivery", "agent_type": "a", "agent_id": "s1", "skill": "tdd",
         "required": True, "delivery": "pointer"},
        {"event": "delivery", "agent_type": "b", "skill": "tdd", "required": True,
         "delivery": "pointer"},
        {"event": "load", "agent_type": "a", "agent_id": "s1", "skill": "tdd"},
        {"event": "load", "agent_type": "b", "skill": "tdd"},
    ]
    gaps, key = audit_gaps(records)
    assert gaps == []
    assert key == "agent_id where the delivery carried one, agent_type otherwise"


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


def test_a_session_id_is_never_used_as_a_spawn_id(plugin: Path, tmp_path: Path) -> None:
    """A session id is RUN-scoped. Substituting it for a spawn id would make every delivery in one
    run share a key, so one spawn's load would clear every other spawn's pointer."""
    (plugin / "skills" / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: d\n---\n\n" + "x" * 9000
    )
    log = tmp_path / "loads.jsonl"
    subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"agent_type": "avenger-backend-architect", "session_id": "run-1"}),
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(plugin),
             "CLAUDE_PLUGIN_ROOT": str(plugin), "CLAUDE_PROJECT_DIR": str(plugin),
             "SKILL_LOAD_LOG": str(log)},
    )
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(r["agent_id"] is None for r in records), "a session id is not a spawn id"
    assert all(r["session_id"] == "run-1" for r in records), "it is kept as the run correlator"
    gaps, key = audit_gaps(records)
    assert key == "agent_type" and len(gaps) == 1


def test_the_audit_is_scoped_to_one_run(tmp_path: Path) -> None:
    """An unscoped audit lets a pointer nobody recorded in phase 1 block phase 8, and a different
    feature besides — the same hostage failure diff-scoping removes everywhere else here."""
    log = tmp_path / "loads.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"event": "delivery", "agent_type": "avenger-verifier", "session_id": "old-run",
         "skill": "tdd", "required": True, "delivery": "pointer"},
        {"event": "delivery", "agent_type": "avenger-verifier", "session_id": "this-run",
         "skill": "tdd", "required": True, "delivery": "pointer"},
        {"event": "load", "agent_type": "avenger-verifier", "session_id": "this-run",
         "skill": "tdd"},
    ]) + "\n")
    assert main(["audit", "--session", "this-run", "--log", str(log)]) == 0
    assert main(["audit", "--session", "old-run", "--log", str(log)]) == 1
    assert main(["audit", "--all", "--log", str(log)]) == 1, "--full still sweeps everything"


def test_a_scope_that_matches_nothing_over_a_non_empty_log_is_never_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty scope is not a clean scope. hook_skills.sh reads session_id from a SubagentStart
    payload that may not carry one while hook_verifier.sh reads it from a PostToolUse payload that
    does, so filtering to a session that matches nothing reported coverage over a log holding an
    unrecorded pointer — the false clean this whole mechanism exists to refuse."""
    log = tmp_path / "loads.jsonl"
    log.write_text(json.dumps(
        {"event": "delivery", "agent_type": "avenger-backend-architect", "session_id": None,
         "skill": "tdd", "required": True, "delivery": "pointer"}
    ) + "\n")
    assert main(["audit", "--session", "run-42", "--log", str(log)]) == 1
    err = capsys.readouterr().err
    assert "could not be applied" in err
    assert "WHOLE LOG by agent_type" in err


def test_a_run_that_delivered_nothing_stays_clean_and_names_what_it_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The phase-1-must-not-block-phase-8 case: the scope APPLIED and this run delivered nothing.
    That is clean, and the out-of-scope count is named rather than silently dropped."""
    log = tmp_path / "loads.jsonl"
    log.write_text(json.dumps(
        {"event": "delivery", "agent_type": "avenger-backend-architect", "session_id": "phase-1",
         "skill": "tdd", "required": True, "delivery": "pointer"}
    ) + "\n")
    assert main(["audit", "--session", "phase-8", "--log", str(log)]) == 0
    assert "1 delivery/ies from other runs counted, not enforced" in capsys.readouterr().err


def test_the_printed_remedy_actually_clears_the_gap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A remedy that cannot clear the gate that printed it is worse than no remedy: it loops the
    operator and then sends them to the bypass. So the loop closing IS the test — the printed line is
    parsed and run verbatim, and the audit must then pass."""
    log = tmp_path / "loads.jsonl"
    log.write_text(json.dumps(
        {"event": "delivery", "agent_type": "avenger-backend-architect", "agent_id": "spawn-7",
         "session_id": "run-42", "skill": "tdd", "required": True, "delivery": "pointer"}
    ) + "\n")
    assert main(["audit", "--session", "run-42", "--log", str(log)]) == 1
    printed = [line.strip() for line in capsys.readouterr().err.splitlines()
               if "required_skills.py record" in line]
    assert printed, "the gap must print the command that clears it"
    argv = printed[0].split("required_skills.py", 1)[1].split()
    assert "--session-id" in argv and "--agent-id" in argv, "every match key must be in the remedy"
    assert main(argv) == 0
    assert main(["audit", "--session", "run-42", "--log", str(log)]) == 0


def test_an_unscoped_audit_enforces_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "loads.jsonl"
    log.write_text(json.dumps(
        {"event": "delivery", "agent_type": "avenger-verifier", "skill": "tdd",
         "required": True, "delivery": "pointer"}
    ) + "\n")
    assert main(["audit", "--log", str(log)]) == 0
    assert "unknowable" in capsys.readouterr().err


def test_the_audit_honours_the_off_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Delivery off means nothing was handed to a stage; auditing earlier residue would block a
    phase for a mechanism the operator switched off."""
    log = tmp_path / "loads.jsonl"
    log.write_text(json.dumps(
        {"event": "delivery", "agent_type": "avenger-verifier", "session_id": "r", "skill": "tdd",
         "required": True, "delivery": "pointer"}
    ) + "\n")
    assert main(["audit", "--session", "r", "--log", str(log)]) == 1
    monkeypatch.setenv("SKILLS_OFF", "1")
    assert main(["audit", "--session", "r", "--log", str(log)]) == 0
    assert "SKILLS_OFF=1" in capsys.readouterr().err


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
    assert main(["audit", "--all", "--log", str(log)]) == 2


def test_an_unparseable_ceiling_delivers_nothing(plugin: Path, tmp_path: Path) -> None:
    """Fail closed: a ceiling nobody can read decides nothing, so it must not decide silently."""
    assert run_hook(
        plugin, "avenger-backend-architect", tmp_path / "loads.jsonl",
        SKILL_INJECT_MAX_BYTES="lots",
    ) == {}
