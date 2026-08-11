"""Required skills are delivered, not requested — and the load is OBSERVED, never self-reported.

The pipeline delegates its core behaviour to thirteen skills and delegated by asking: "Load
`skills/tdd` before you start" is an instruction with no mechanism behind it. `docs/lessons/` shipped
with a complete written procedure and zero invocations for the same reason.

Three properties are pinned here, and each replaced a shape this module used to have:

  * the requirement is DERIVED from the stage's own `agents/<stage>.md`, never restated in a table —
    a second statement of a fact is what every promise-versus-enforcement gap turned out to be;
  * the load is OBSERVED — an injection records itself, a pointer is answered by actually opening the
    file, and nothing anywhere asks the agent to report its own compliance;
  * the audit BLOCKS. Detection without a gate is a log.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import DOUBLE, stored

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from required_skills import (  # noqa: E402
    DEFAULT_INJECT_MAX_BYTES,
    audit_gaps,
    delivery_for,
    inject_max_bytes,
    main,
    missing,
    required_for,
    skill_path,
    stages,
    to_deliver,
)

HOOK = ROOT / "scripts" / "hook_skills.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash SubagentStart hook; running it any other way would test a "
    "reimplementation of the delivery mechanism, which is the whole point"
)


# ── the contract is derived, not restated ────────────────────────────────────


def test_every_pipeline_agent_requires_the_shared_rulebook() -> None:
    """An agent that has not read it is an agent guessing at phases, gates and the ID scheme."""
    assert stages(ROOT), "there are canonical agents to check"
    for stage in stages(ROOT):
        assert "pipeline-conventions" in required_for(stage), stage


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


def test_adding_a_skill_to_an_agent_is_enough_to_make_it_required(tmp_path: Path) -> None:
    """The point of deriving it: there is no second list to remember, so the two cannot drift."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "skills" / "tdd").mkdir(parents=True)
    (tmp_path / "skills" / "tdd" / "SKILL.md").write_text("x")
    agent = tmp_path / "agents" / "avenger-new.md"
    agent.write_text("Load `skills/tdd` before you start.\n")

    import skill_contract

    assert skill_contract.required_skills("avenger-new", root=tmp_path) == frozenset({"tdd"})


def test_a_skill_whose_body_vanished_is_still_required() -> None:
    """The contract filters prose mentions of skills that do not exist — by DIRECTORY, never by a
    readable SKILL.md. Keying on the file would make a skill whose body went missing quietly stop
    being required, which is the silent fallback the loud blocker exists to replace: the delivery
    would have nothing to shout about and the audit nothing to fail on."""
    import skill_contract

    root = Path(__file__).resolve().parents[1]
    assert "tdd" in skill_contract.available_skills(root)
    assert "no-such-skill" not in skill_contract.available_skills(root)


def test_a_skill_another_hook_owns_is_not_delivered_twice() -> None:
    """`scripts/hook_ponytail.sh` owns ponytail and records its own load. Delivering it here as well
    would cost twice and would put the minimalism persona back after `PONYTAIL_OFF=1` — an off switch
    that does not switch anything off."""
    assert "ponytail" in required_for("avenger-backend-architect")
    assert "ponytail" not in to_deliver("avenger-backend-architect")
    assert "tdd" in to_deliver("avenger-backend-architect")


def test_every_required_skill_exists_in_this_repo() -> None:
    assert missing(ROOT) == []
    assert main(["verify", "--root", str(ROOT)]) == 0


def test_a_missing_required_skill_is_reported(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "skills" / "tdd").mkdir(parents=True)
    (tmp_path / "skills" / "tdd" / "SKILL.md").write_text("x")
    (tmp_path / "agents" / "avenger-x.md").write_text("uses `skills/tdd`\n")
    (tmp_path / "skills" / "tdd" / "SKILL.md").write_text("   ")

    assert main(["verify", "--root", str(tmp_path)]) == 1


# ── the hook: delivery, and the blocker ──────────────────────────────────────


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    """A plugin root with the real scripts, an agent definition, and a breakable skills tree."""
    root = tmp_path / "plugin"
    root.mkdir()
    shutil.copytree(ROOT / "scripts", root / "scripts")
    (root / "agents").mkdir()
    (root / "agents" / "avenger-backend-architect.md").write_text(
        "Load `skills/pipeline-conventions`, `skills/tdd` and `skills/self-improvement`.\n"
    )
    (root / "skills").mkdir()
    for skill in ("pipeline-conventions", "tdd", "self-improvement"):
        directory = root / "skills" / skill
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {skill}\n---\n\n# {skill}\n\nthe rule from {skill}\n"
        )
    return root


@pytest.fixture
def measured(tmp_path: Path):
    """A project with a phase in flight and a double metrics writer. Yields (project, env, read)."""
    project = tmp_path / "project"
    (project / "docs" / "features" / "demo" / "phases" / "1-demo").mkdir(parents=True)
    (project / "docs" / "features" / "demo" / "phases" / "1-demo" / "handover.md").write_text("x")
    store = tmp_path / "store"
    store.mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    env = {
        "AVENGER_METRICS_CMD": str(double),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp_path / "diagnostics.log"),
        "DOUBLE_LOG": str(tmp_path / "calls.log"),
        "DOUBLE_STORE": str(store),
    }
    return project, env, (lambda: stored(store, "01"))


def run_hook(plugin: Path, agent_type: str, *, project: Path | None = None, **env: str) -> dict:
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"agent_type": agent_type}),
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(plugin),
             "CLAUDE_PLUGIN_ROOT": str(plugin),
             "CLAUDE_PROJECT_DIR": str(project or plugin), **env},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_the_skill_body_is_injected_not_asked_for(plugin: Path) -> None:
    injected = run_hook(plugin, "avenger-backend-architect")[
        "hookSpecificOutput"]["additionalContext"]
    assert "the rule from tdd" in injected
    assert "the rule from pipeline-conventions" in injected
    assert "delivered, not requested" in injected


def test_an_injection_records_itself_as_an_observed_load(plugin: Path, measured) -> None:
    """An injected skill is never READ, so without this it seeds required-and-unobserved and never
    flips — and the audit reports a false gap on the skills whose load is guaranteed."""
    project, env, record_of = measured

    run_hook(plugin, "avenger-backend-architect", project=project, **env)

    loads = {entry["skill"]: entry for entry in record_of()["skill_loads"]}
    assert set(loads) == {"pipeline-conventions", "tdd", "self-improvement"}
    assert all(entry["required"] and entry["loaded"] for entry in loads.values())
    assert "injected" in loads["tdd"]["evidence"], "the evidence names what observed the load"
    assert all(e["stage"] == "avenger-backend-architect" for e in loads.values())


def test_a_missing_required_skill_is_a_loud_blocker_not_a_silent_fallback(
    plugin: Path, measured
) -> None:
    """A required skill that is absent is not a lighter version of the rules; it is no rules."""
    project, env, record_of = measured
    (plugin / "skills" / "tdd" / "SKILL.md").unlink()

    injected = run_hook(plugin, "avenger-backend-architect", project=project, **env)[
        "hookSpecificOutput"]["additionalContext"]

    assert "BLOCKER" in injected
    assert "tdd" in injected
    assert "Do not proceed by guessing" in injected
    loads = {entry["skill"]: entry["loaded"] for entry in record_of()["skill_loads"]}
    assert loads["tdd"] is False
    assert loads["pipeline-conventions"] is True


def test_an_agent_outside_the_contract_gets_nothing(plugin: Path) -> None:
    assert run_hook(plugin, "some-other-agent") == {}


def test_an_unreadable_payload_injects_nothing(plugin: Path) -> None:
    """Fail closed, the same rule as every other SubagentStart hook."""
    result = subprocess.run(
        ["bash", str(HOOK)], input="not json",
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(plugin),
             "CLAUDE_PLUGIN_ROOT": str(plugin), "CLAUDE_PROJECT_DIR": str(plugin)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_off_switch_works(plugin: Path) -> None:
    result = subprocess.run(
        ["bash", str(HOOK)], input=json.dumps({"agent_type": "avenger-backend-architect"}),
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(plugin), "SKILLS_OFF": "1",
             "CLAUDE_PLUGIN_ROOT": str(plugin), "CLAUDE_PROJECT_DIR": str(plugin)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_delivery_never_stops_a_spawn_when_nothing_can_be_recorded(plugin: Path, measured) -> None:
    """Measurement is never a gate: a writer that refuses everything must not cost a subagent its
    rules."""
    project, env, _ = measured

    injected = run_hook(plugin, "avenger-backend-architect", project=project,
                        DOUBLE_EXIT="3", **env)["hookSpecificOutput"]["additionalContext"]

    assert "the rule from tdd" in injected


def test_the_skill_path_helper_points_where_the_skills_live() -> None:
    assert skill_path(ROOT, "tdd") == ROOT / "skills" / "tdd" / "SKILL.md"


# ── pointer plus evidenced load ──────────────────────────────────────────────
#
# Injecting every required skill body GUARANTEES the load and costs the same order as the reads the
# read-path work had just removed. Observation DETECTS a missed load, and a required skill with no
# observed load blocks the phase anyway — so detection beats prevention when both end the same way.
# What is pinned here is that the cheap half cannot quietly become a suggestion.


def test_the_ceiling_is_read_but_never_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert inject_max_bytes() == DEFAULT_INJECT_MAX_BYTES == 8192
    monkeypatch.setenv("SKILL_INJECT_MAX_BYTES", "100")
    assert inject_max_bytes() == 100
    assert delivery_for(100) == "inject" and delivery_for(101) == "pointer"
    monkeypatch.setenv("SKILL_INJECT_MAX_BYTES", "lots")
    with pytest.raises(ValueError, match="not a whole number"):
        inject_max_bytes()


def test_a_skill_over_the_ceiling_is_a_pointer_the_stage_must_open(plugin: Path, measured) -> None:
    project, env, record_of = measured
    (plugin / "skills" / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: the red-green loop\n---\n\n" + "x" * 9000
    )

    injected = run_hook(plugin, "avenger-backend-architect", project=project, **env)[
        "hookSpecificOutput"]["additionalContext"]

    assert "x" * 9000 not in injected, "the whole point is that the body is NOT inlined"
    assert "the red-green loop" in injected, "the pointer carries the skill's own description"
    assert str(plugin / "skills" / "tdd" / "SKILL.md") in injected
    assert "BLOCKS THE PHASE" in injected
    assert "the rule from pipeline-conventions" in injected, "a small skill is still injected whole"
    loads = {entry["skill"]: entry for entry in record_of()["skill_loads"]}
    assert loads["pipeline-conventions"]["loaded"] is True, "the injection is the load"
    assert "tdd" not in loads, "a pointer is not a load; the contract seed is the other hook's job"


def test_the_pointer_never_asks_the_agent_to_report_its_own_load(plugin: Path) -> None:
    """A path that needs the agent to run a command to prove a load IS instruction-not-mechanism —
    the precise failure this whole mechanism exists to fix, one layer up."""
    (plugin / "skills" / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: d\n---\n\n" + "x" * 9000
    )

    injected = run_hook(plugin, "avenger-backend-architect")[
        "hookSpecificOutput"]["additionalContext"]

    assert "required_skills.py record" not in injected
    assert "Opening it is what records the load" in injected


def test_an_unparseable_ceiling_delivers_nothing(plugin: Path) -> None:
    """Fail closed: a ceiling nobody can read decides nothing, so it must not decide silently."""
    assert run_hook(plugin, "avenger-backend-architect", SKILL_INJECT_MAX_BYTES="lots") == {}


# ── the audit blocks, and it is phase-scoped by construction ─────────────────


def test_a_required_skill_with_no_observed_load_is_a_gap() -> None:
    gaps = audit_gaps({"skill_loads": [
        {"id": "avenger-backend-architect:tdd", "stage": "avenger-backend-architect",
         "skill": "tdd", "required": True, "loaded": False},
    ]})
    assert len(gaps) == 1
    assert "avenger-backend-architect" in gaps[0] and "skills/tdd" in gaps[0]


def test_one_stages_load_does_not_clear_another_stages_requirement() -> None:
    """Keyed `<stage>:<skill>`, so the Verifier reading the rulebook says nothing about whether the
    implementer did. Keyed on the skill alone this reported coverage it did not have."""
    gaps = audit_gaps({"skill_loads": [
        {"id": "avenger-verifier:pipeline-conventions", "stage": "avenger-verifier",
         "skill": "pipeline-conventions", "required": True, "loaded": True},
        {"id": "avenger-backend-architect:pipeline-conventions",
         "stage": "avenger-backend-architect", "skill": "pipeline-conventions",
         "required": True, "loaded": False},
    ]})
    assert len(gaps) == 1
    assert "avenger-backend-architect" in gaps[0]
    assert "avenger-verifier" not in gaps[0]


def test_a_load_that_was_never_required_is_not_a_gap() -> None:
    assert audit_gaps({"skill_loads": [
        {"stage": "avenger-verifier", "skill": "codemap", "required": False, "loaded": False},
    ]}) == []


def test_a_phase_with_no_record_is_not_a_gap() -> None:
    assert audit_gaps(None) == []


def test_the_audit_blocks_on_an_unobserved_required_load(measured, monkeypatch) -> None:
    """Detection without a gate is a log. This is the one thing observation alone never gave."""
    project, env, _ = measured
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("SKILLS_OFF", raising=False)
    import metrics_sink
    import pipeline_metrics

    monkeypatch.setattr(metrics_sink, "_writer_unusable", False)
    pipeline_metrics.record_skill_load(
        "01", stage="avenger-backend-architect", skill="tdd", evidence="", loaded=False
    )

    assert main(["audit"]) == 1

    pipeline_metrics.record_skill_load(
        "01", stage="avenger-backend-architect", skill="tdd", evidence="Read SKILL.md"
    )
    assert main(["audit"]) == 0


def test_the_audit_is_phase_scoped_without_any_session_id(measured, monkeypatch) -> None:
    """A pointer delivered in phase 1 cannot block phase 8: the evidence lives in the per-phase
    record and hook_skill_load.sh writes nothing when no phase is in flight, so the scoping needs no
    run id at all — the machinery that used to provide one is gone."""
    project, env, _ = measured
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("SKILLS_OFF", raising=False)
    import metrics_sink
    import pipeline_metrics

    monkeypatch.setattr(metrics_sink, "_writer_unusable", False)
    pipeline_metrics.record_skill_load(
        "01", stage="avenger-backend-architect", skill="tdd", evidence="", loaded=False
    )
    later = project / "docs" / "features" / "demo" / "phases" / "8-auth"
    later.mkdir(parents=True)
    (later / "handover.md").write_text("x")

    assert main(["audit"]) == 0, "phase 8 is in flight; phase 1's gap is not its problem"
    assert main(["audit", "--all"]) == 1, "--full still sweeps every phase"


def test_the_audit_honours_the_off_switch(measured, monkeypatch) -> None:
    """Delivery off means nothing was handed to a stage; auditing earlier residue would block a
    phase for a mechanism the operator switched off."""
    project, env, _ = measured
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    import metrics_sink
    import pipeline_metrics

    monkeypatch.setattr(metrics_sink, "_writer_unusable", False)
    pipeline_metrics.record_skill_load(
        "01", stage="avenger-backend-architect", skill="tdd", evidence="", loaded=False
    )
    assert main(["audit"]) == 1

    monkeypatch.setenv("SKILLS_OFF", "1")
    assert main(["audit"]) == 0


def test_no_writer_is_clean_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """CI without firstmate observed nothing, so nothing was missed — said out loud rather than
    passing invisibly, the same rule as an absent subprocess-check root."""
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    monkeypatch.delenv("SKILLS_OFF", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert main(["audit"]) == 0
    assert "never observed" in capsys.readouterr().err
