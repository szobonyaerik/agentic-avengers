"""A helper agent is bounded by a declared budget - the decision, not the wiring.

Issue #118: five helper agents frozen for 90 minutes with identical timers, the largest 1h 8m and
341.4k tokens deciding whether two amendment ids were marked done. Three of the five were questions
a shell command answers.

What is pinned here is the half a static check can decide: a dispatch declares a budget, the
declaration is itself bounded, and a helper already past its own budget refuses the next dispatch.
The other half - whether the question had a one-command answer - is a judgement, carried by
`skills/pipeline-conventions`, and this file deliberately asserts nothing about it.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import helper_budget  # noqa: E402
import implementer_liveness as liveness  # noqa: E402

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def when(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S%z")


def activity(root: Path, *rows: dict) -> None:
    (root / liveness.LOG_NAME).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def payload(root: Path, stage: str, prompt: str) -> Path:
    path = root / "payload.json"
    path.write_text(
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Task",
                "tool_input": {"subagent_type": stage, "prompt": prompt},
            }
        ),
        encoding="utf-8",
    )
    return path


def dispatched(
    agent: str = "general-purpose", *, budget_s: int, seconds_ago: int
) -> dict:
    return {
        "ts": when(seconds_ago),
        "event": helper_budget.DISPATCH,
        "agent_type": agent,
        "budget_s": budget_s,
    }


def started(
    agent: str = "general-purpose", *, seconds_ago: int, agent_id: str = "h1"
) -> dict:
    return {
        "ts": when(seconds_ago),
        "event": liveness.START,
        "agent_type": agent,
        "agent_id": agent_id,
        "harness_pid": os.getpid(),
    }


# --- the declaration ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Budget: 90s", 90),
        ("Budget: 5m", 300),
        ("Budget: 1h", 3600),
        ("Budget: 300", 300),
        ("  Budget:   45m  ", 2700),
        ("> Budget: 2m", 120),
        ("- Budget: 2m", 120),
        ("do the thing", None),
        ("this has a budget: 5m inside a sentence", None),
    ],
)
def test_the_declaration_is_read_from_a_line_of_its_own(
    prompt: str, expected: int | None
) -> None:
    assert helper_budget.declared_budget(prompt) == expected


def test_the_authors_own_line_wins_over_a_quoted_example() -> None:
    """A prompt that quotes the rule above its own declaration must be bounded by the declaration."""
    prompt = "Follow the format:\n\nBudget: 5m\n\nNow the real one.\n\nBudget: 2m\n"
    assert helper_budget.declared_budget(prompt) == 120


def test_a_dispatch_with_no_budget_is_refused(tmp_path: Path) -> None:
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Check whether A10 and A11 are done."),
        tmp_path,
        now=NOW,
    )
    assert code == helper_budget.REFUSE


def test_the_refusal_names_the_line_to_add(tmp_path: Path, capsys) -> None:
    helper_budget.check(
        payload(tmp_path, "general-purpose", "do it"), tmp_path, now=NOW
    )
    err = capsys.readouterr().err
    assert helper_budget.REFUSE_MARKER in err
    assert "Budget: 5m" in err


def test_a_dispatch_with_a_budget_is_clean(tmp_path: Path) -> None:
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Re-run the suite.\n\nBudget: 5m\n"),
        tmp_path,
        now=NOW,
    )
    assert code == helper_budget.OK


# --- the ceiling on the declaration ---------------------------------------------------------------


def test_a_budget_over_the_ceiling_is_refused(tmp_path: Path) -> None:
    """`Budget: 10h` satisfies a presence check and bounds nothing, which is the whole defect."""
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 10h\n"), tmp_path, now=NOW
    )
    assert code == helper_budget.REFUSE


def test_the_ceiling_is_configurable_and_deliberate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(helper_budget.MAX_ENV, "36000")
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 10h\n"), tmp_path, now=NOW
    )
    assert code == helper_budget.OK


def test_an_unusable_ceiling_is_undecidable_not_clean(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(helper_budget.MAX_ENV, "soon")
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 5m\n"), tmp_path, now=NOW
    )
    assert code == helper_budget.UNKNOWN


# --- who is bound ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage", ["avenger-verifier", "plan-build-verify:avenger-backend-architect"]
)
def test_a_pipeline_stage_is_not_a_helper(tmp_path: Path, stage: str) -> None:
    """The `avenger-*` chain is the pipeline's own sequence, bounded by its phase."""
    assert not helper_budget.is_helper(stage)
    assert (
        helper_budget.check(payload(tmp_path, stage, "verify it"), tmp_path, now=NOW)
        == helper_budget.OK
    )


@pytest.mark.parametrize(
    "stage", ["general-purpose", "Explore", "caveman:cavecrew-investigator"]
)
def test_every_other_spawn_is_a_helper(stage: str) -> None:
    assert helper_budget.is_helper(stage)


def test_an_unusable_agents_pattern_is_undecidable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(helper_budget.AGENTS_ENV, "(")
    assert (
        helper_budget.check(
            payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
        )
        == helper_budget.UNKNOWN
    )


# --- the overrun ----------------------------------------------------------------------------------


def test_a_helper_past_its_budget_refuses_the_next_dispatch(tmp_path: Path) -> None:
    """THE measured state: helpers accumulating while the earliest is already an hour over."""
    activity(
        tmp_path,
        dispatched(budget_s=300, seconds_ago=700),
        started(seconds_ago=690),
    )
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
    )
    assert code == helper_budget.REFUSE


def test_the_overrun_refusal_names_the_helper_and_the_numbers(
    tmp_path: Path, capsys
) -> None:
    activity(
        tmp_path, dispatched(budget_s=300, seconds_ago=700), started(seconds_ago=690)
    )
    helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
    )
    err = capsys.readouterr().err
    assert "id h1" in err
    assert "690s elapsed against a declared 300s" in err
    assert "TaskStop" in err


def test_a_helper_inside_its_budget_does_not_refuse(tmp_path: Path) -> None:
    activity(
        tmp_path, dispatched(budget_s=300, seconds_ago=200), started(seconds_ago=190)
    )
    assert (
        helper_budget.check(
            payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
        )
        == helper_budget.OK
    )


def test_a_helper_that_has_stopped_is_not_an_overrun(tmp_path: Path) -> None:
    activity(
        tmp_path,
        dispatched(budget_s=300, seconds_ago=700),
        started(seconds_ago=690),
        {
            "ts": when(10),
            "event": liveness.STOP,
            "agent_type": "general-purpose",
            "agent_id": "h1",
        },
    )
    assert (
        helper_budget.check(
            payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
        )
        == helper_budget.OK
    )


def test_a_run_with_no_paired_dispatch_is_left_unjudged(tmp_path: Path) -> None:
    """It declared no budget, and a check that invented one would decide on a fact nobody stated."""
    activity(tmp_path, started(seconds_ago=100000))
    assert (
        helper_budget.check(
            payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
        )
        == helper_budget.OK
    )


def test_a_dispatch_after_the_run_started_does_not_pair_with_it(tmp_path: Path) -> None:
    activity(
        tmp_path, started(seconds_ago=700), dispatched(budget_s=60, seconds_ago=10)
    )
    assert (
        helper_budget.check(
            payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
        )
        == helper_budget.OK
    )


def test_a_different_agent_types_budget_does_not_bind_this_one(tmp_path: Path) -> None:
    activity(
        tmp_path,
        dispatched("Explore", budget_s=60, seconds_ago=700),
        started("general-purpose", seconds_ago=690),
    )
    assert (
        helper_budget.check(
            payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
        )
        == helper_budget.OK
    )


# --- absent is named, never read as clear ----------------------------------------------------------


def test_no_activity_log_reports_the_live_half_as_not_run(
    tmp_path: Path, capsys
) -> None:
    """Outside a pipeline run there is no log, which is ordinary - and must not read as all clear."""
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
    )
    assert code == helper_budget.OK
    assert "live helpers were NOT checked" in capsys.readouterr().err


def test_an_unusable_age_ceiling_reports_the_live_half_as_not_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """`live()` raises its own exception type, and an uncaught one would leave the hook reading a
    traceback instead of a stated absence."""
    activity(
        tmp_path, dispatched(budget_s=300, seconds_ago=700), started(seconds_ago=690)
    )
    monkeypatch.setenv(liveness.MAX_AGE_ENV, "never")
    code = helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
    )
    assert code == helper_budget.OK
    assert "live helpers were NOT checked" in capsys.readouterr().err


def test_an_unreadable_payload_is_undecidable_not_clean(tmp_path: Path) -> None:
    bad = tmp_path / "payload.json"
    bad.write_text("{not json", encoding="utf-8")
    assert helper_budget.check(bad, tmp_path, now=NOW) == helper_budget.UNKNOWN


def test_a_payload_with_no_subagent_type_is_not_a_spawn(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text(json.dumps({"tool_input": {"prompt": "hi"}}), encoding="utf-8")
    assert helper_budget.check(path, tmp_path, now=NOW) == helper_budget.OK


# --- the dispatch is recorded, and only when it is allowed -----------------------------------------


def test_a_clean_dispatch_records_the_row_a_later_pairing_reads(tmp_path: Path) -> None:
    helper_budget.check(
        payload(tmp_path, "general-purpose", "Budget: 5m"), tmp_path, now=NOW
    )
    rows = helper_budget.dispatch_rows(tmp_path)
    assert [(r["agent_type"], r["budget_s"]) for r in rows] == [
        ("general-purpose", 300)
    ]


def test_a_refused_dispatch_records_nothing(tmp_path: Path) -> None:
    """The spawn is blocked, so a row for it would bind a run that never started."""
    activity(tmp_path)
    helper_budget.check(
        payload(tmp_path, "general-purpose", "no budget here"), tmp_path, now=NOW
    )
    assert helper_budget.dispatch_rows(tmp_path) == []


# --- what a returning helper cost ------------------------------------------------------------------


def test_spend_pairs_elapsed_time_against_the_declared_budget(tmp_path: Path) -> None:
    activity(
        tmp_path, dispatched(budget_s=300, seconds_ago=700), started(seconds_ago=690)
    )
    spend = helper_budget.spend_for(
        tmp_path, agent_type="general-purpose", agent_id="h1", now=NOW
    )
    assert spend["elapsed_s"] == 690
    assert spend["budget_s"] == 300


def test_spend_names_an_absent_budget_rather_than_inventing_one(tmp_path: Path) -> None:
    activity(tmp_path, started(seconds_ago=100))
    spend = helper_budget.spend_for(tmp_path, agent_type="general-purpose", now=NOW)
    assert spend["budget_s"] is None


def test_spend_with_no_start_row_is_undecidable(tmp_path: Path) -> None:
    activity(tmp_path, dispatched(budget_s=300, seconds_ago=700))
    with pytest.raises(helper_budget.BudgetUnknown):
        helper_budget.spend_for(tmp_path, agent_type="general-purpose", now=NOW)


# --- the rule itself is delivered, not merely written ----------------------------------------------


AGENTS = sorted((ROOT / "agents").glob("avenger-*.md"))
RULE_SECTION = "## Delegation is bounded"


def test_the_rule_is_stated_once_in_the_shared_skill() -> None:
    """Outcome 1 of issue #118. Eleven copies of a rule drift; one required skill does not."""
    text = (ROOT / "skills" / "pipeline-conventions" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert text.count(RULE_SECTION) == 1
    section = text[text.index(RULE_SECTION) :]
    for example in (
        "file's contents",
        "field's value",
        "test summary",
        "timestamp",
        "whether an id is marked done",
    ):
        assert example in section, example


def test_the_shared_skill_says_which_half_is_instruction_and_which_is_mechanism() -> (
    None
):
    """A sentence claiming behaviour is a promise; the rule must say where the promise ends."""
    text = (ROOT / "skills" / "pipeline-conventions" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    section = text[text.index(RULE_SECTION) :]
    assert "INSTRUCTION, not mechanism" in section


@pytest.mark.parametrize("agent", AGENTS, ids=lambda p: p.stem)
def test_every_stage_agent_points_at_the_rule(agent: Path) -> None:
    text = agent.read_text(encoding="utf-8")
    assert "Do not delegate a question a command answers" in text
    assert 'skills/pipeline-conventions` \u00a7 "Delegation is bounded"' in text
    assert "Budget: <n>m" in text
