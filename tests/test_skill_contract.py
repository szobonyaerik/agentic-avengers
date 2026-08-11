"""Tests for the required-skill contract, which is READ from the agent definitions.

The point of deriving it is that there is no second list to rot. So the tests assert the derivation
against the real `agents/` and `skills/` directories rather than against a fixture copy of them: if
an agent stops naming a skill, this moves, which is exactly what a hand-maintained table would not.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import skill_contract  # noqa: E402


def test_the_contract_is_what_the_agent_definition_names():
    required = skill_contract.required_skills("avenger-verifier")

    assert "verifier-triage" in required
    assert "tdd" in required
    assert "ponytail" not in required   # the Verifier is deliberately outside ponytail's reach


def test_a_qualified_agent_type_names_the_same_contract():
    assert skill_contract.required_skills(
        "plan-build-verify:avenger-backend-architect"
    ) == skill_contract.required_skills("avenger-backend-architect")


def test_an_agent_that_is_not_ours_has_no_contract():
    assert skill_contract.required_skills("general-purpose") == frozenset()
    assert skill_contract.required_skills("") == frozenset()


def test_only_skills_that_exist_count():
    """A prose mention of a removed skill is not a requirement nobody can ever satisfy."""
    for stage in (p.stem for p in (ROOT / "agents").glob("avenger-*.md")):
        assert skill_contract.required_skills(stage) <= skill_contract.available_skills()


def test_available_skills_matches_the_directory():
    assert skill_contract.available_skills() == {
        p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file()
    }


def test_a_missing_repository_yields_nothing_rather_than_raising(tmp_path):
    assert skill_contract.required_skills("avenger-verifier", root=tmp_path) == frozenset()
    assert skill_contract.available_skills(root=tmp_path) == set()
