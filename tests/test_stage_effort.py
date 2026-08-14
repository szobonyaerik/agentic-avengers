"""Tests for the per-stage reasoning-effort table — the one that was documented and never applied.

`commands/avenger-run.md` called reasoning effort "the largest lever on cost that does not change
what any gate checks" and told the orchestrator to *pass* `effort` when it spawned a stage. The
delegation tool has no such parameter, so the instruction could not be obeyed by anything, and the
string `effort` appears in no phase metrics record. Every stage of every phase ran at the session
default.

The mechanism that does exist is the agent definition's own `effort:` frontmatter key, which the
harness reads and applies at spawn. These tests assert it against the REAL `agents/` and
`commands/` directories rather than a fixture copy, for the same reason `test_skill_contract.py`
does: a table restated in prose is a table that drifts from the mechanism, and the drift is the
defect. A synthetic tree is used only where the failure being asserted is one the real tree must
never be in.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stage_effort  # noqa: E402


def _tree(root: Path, effort: str | None = "low") -> None:
    """A minimal canonical tree: one agent, and the directories the source scan walks."""
    (root / "agents").mkdir(exist_ok=True)
    (root / "commands").mkdir(exist_ok=True)
    declared = f"effort: {effort}\n" if effort else ""
    (root / "agents" / "avenger-x.md").write_text(
        f"---\nname: avenger-x\nmodel: haiku\n{declared}---\n\nbody\n"
    )


# --- the mechanism itself ------------------------------------------------------------------------

def test_every_canonical_stage_declares_its_effort():
    """The defect, stated as a test: a stage with no declared effort runs at the session default.

    This goes RED when the mechanism is removed, and when a stage is added without one. It reads the
    real `agents/` directory, so there is nothing to remember.
    """
    undeclared = [stage for stage, level in stage_effort.declared(ROOT).items() if level is None]

    assert undeclared == [], (
        "these stages declare no `effort:` and therefore run at the session default: "
        + ", ".join(undeclared)
    )


def test_declared_levels_are_ones_the_harness_accepts():
    for stage, level in stage_effort.declared(ROOT).items():
        assert level in stage_effort.LEVELS, f"{stage} declares effort '{level}'"


def test_the_declared_effort_is_read_from_the_agent_definition_not_a_second_table(tmp_path):
    """One statement of a stage's effort, in the file the harness itself reads.

    A hand-maintained table in a script would be a second statement of the same fact, and the two
    would drift — which is the failure this whole item exists to close, one layer down.
    """
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "avenger-x.md").write_text(
        "---\nname: avenger-x\nmodel: haiku\neffort: low\n---\n\nbody\n"
    )

    assert stage_effort.declared_effort("avenger-x", root=tmp_path) == "low"
    assert stage_effort.declared_effort("plan-build-verify:avenger-x", root=tmp_path) == "low"


def test_an_agent_with_no_effort_key_reads_as_undeclared_not_as_a_default(tmp_path):
    """Guessing a default here would hide exactly the state the pipeline was in for ten phases."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "avenger-x.md").write_text("---\nname: avenger-x\nmodel: haiku\n---\n\nb\n")

    assert stage_effort.declared_effort("avenger-x", root=tmp_path) is None
    assert any("declares no `effort:`" in p for p in stage_effort.check(tmp_path))


def test_an_invalid_level_is_named_rather_than_silently_ignored(tmp_path):
    _tree(tmp_path, effort="enormous")

    assert any("'enormous'" in p for p in stage_effort.check(tmp_path))


# --- the docs may not restate the table, only agree with it --------------------------------------

def test_the_runbooks_effort_table_agrees_with_the_agent_definitions():
    """The live table, checked against the live mechanism. This is the item, as one assertion."""
    assert stage_effort.check(ROOT) == []


def test_a_documented_pair_that_disagrees_with_the_mechanism_is_a_failure(tmp_path):
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n| `avenger-x` | `high` |\n"
    )

    problems = stage_effort.check(tmp_path)

    assert any("says `high`" in p and "declares `low`" in p for p in problems), problems


def test_a_documented_table_with_no_mechanism_behind_it_is_a_failure(tmp_path):
    """Option B's guard, folded in: a per-stage effort table may not reappear as decoration.

    A row naming a stage whose definition declares nothing is the exact state this repository shipped
    for ten phases, and it is RED whether it arrives by deleting a frontmatter key or by writing a
    fresh table into a document.
    """
    _tree(tmp_path, effort=None)
    (tmp_path / "commands" / "run.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n| `avenger-x` | `high` |\n"
    )

    problems = stage_effort.check(tmp_path)

    assert any("declares no `effort:`" in p for p in problems), problems


def test_an_unbackticked_row_that_disagrees_is_still_a_failure(tmp_path):
    """Markup is not the subject. A row means what it says however its author marked it up.

    Requiring backticks on both columns left a documented pair that disagrees with the mechanism
    free to pass by dropping two characters — the decoration this module exists to end, one escape
    hatch over.
    """
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n| avenger-x | high |\n"
    )

    problems = stage_effort.check(tmp_path)

    assert any("says `high`" in p and "declares `low`" in p for p in problems), problems


def test_a_bare_level_beside_a_backticked_stage_is_still_compared(tmp_path):
    """The half-marked-up row: the stage was seen, the level was not, so nothing was compared."""
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n| `avenger-x` | high |\n"
    )

    problems = stage_effort.check(tmp_path)

    assert any("says `high`" in p and "declares `low`" in p for p in problems), problems


def test_a_row_naming_several_stages_in_one_cell_is_not_read_as_a_ghost_stage(tmp_path):
    """The live runbook pairs two stages in one cell; a cell is only a token when it is one."""
    _tree(tmp_path, effort="low")
    (tmp_path / "agents" / "avenger-y.md").write_text(
        "---\nname: avenger-y\neffort: low\n---\n\nbody\n"
    )
    (tmp_path / "commands" / "run.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n"
        "| `avenger-x`, `avenger-y` | `low` |\n"
    )

    assert stage_effort.check(tmp_path) == []


def test_the_root_mirrors_are_scanned_too(tmp_path):
    """CLAUDE.md and AGENTS.md are the documents a session actually reads.

    A per-stage table restated there with values no definition declares is the same "table with
    nothing behind it", one file over from the ones the directory scan covers.
    """
    _tree(tmp_path, effort="low")
    (tmp_path / "CLAUDE.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n| `avenger-x` | `high` |\n"
    )
    (tmp_path / "AGENTS.md").write_text("Pass `effort` when you spawn a subagent.\n")

    problems = stage_effort.check(tmp_path)

    assert any(p.startswith("CLAUDE.md:5:") and "says `high`" in p for p in problems), problems
    assert any(p.startswith("AGENTS.md:1:") and "cannot be obeyed" in p for p in problems), problems


def test_an_absent_root_mirror_is_skipped_rather_than_an_error(tmp_path):
    """A vendored install receives AGENTS.md and no CLAUDE.md; neither absence is a problem."""
    _tree(tmp_path, effort="low")

    assert stage_effort.check(tmp_path) == []


def test_a_row_outside_an_effort_section_is_not_read_as_one(tmp_path):
    """Scoped to sections about effort, so `criticality: high` elsewhere is not a false positive."""
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "## Breaker\n\n| Stage | criticality |\n|---|---|\n| `avenger-x` | `high` |\n"
    )

    assert stage_effort.check(tmp_path) == []


def test_a_row_naming_a_stage_that_is_not_an_agent_is_named(tmp_path):
    """A table row about a stage no definition backs cannot be satisfied by any mechanism."""
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "## Effort per stage\n\n| Stage | effort |\n|---|---|\n| `avenger-ghost` | `high` |\n"
    )

    assert any("avenger-ghost" in p for p in stage_effort.check(tmp_path))


# --- the instruction that nothing could obey -----------------------------------------------------

def test_an_instruction_to_pass_effort_at_spawn_is_a_failure(tmp_path):
    """The original defect, as a guard: the delegation tool exposes no effort parameter.

    An instruction to supply one at spawn time is unobeyable by construction, so it may not come
    back — not in the runbook that carried it, and not anywhere else a stage reads.
    """
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "Pass `effort` when you spawn a subagent, matched to the work.\n"
    )

    assert any("cannot be obeyed" in p for p in stage_effort.check(tmp_path))


def test_an_inline_effort_argument_on_an_invocation_is_a_failure(tmp_path):
    """The second live instance: `Invoke ... at \\`effort: high\\``, in the same runbook."""
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "Invoke `plan-build-verify:avenger-breaker` at `effort: high`.\n"
    )

    assert any("cannot be obeyed" in p for p in stage_effort.check(tmp_path))


def test_prose_describing_the_guard_is_not_itself_the_instruction(tmp_path):
    """A guard that cannot be described without tripping gets described less precisely.

    The first draft of this one fired on the runbook paragraph explaining it. A parameter is named in
    code voice; a sentence about the rule is not.
    """
    _tree(tmp_path, effort="low")
    (tmp_path / "commands" / "run.md").write_text(
        "`check` fails on any instruction to supply effort at spawn time, because the delegation "
        "tool has no effort parameter and passing effort could never work.\n"
    )

    assert stage_effort.check(tmp_path) == []


def test_the_frontmatter_key_itself_is_not_read_as_that_instruction(tmp_path):
    """`effort: low` in an agent's own frontmatter IS the mechanism, not a claim about one."""
    _tree(tmp_path, effort="low")

    assert stage_effort.check(tmp_path) == []


def test_a_definition_carrying_both_is_told_apart_at_the_right_line(tmp_path):
    """The same characters mean the mechanism above the `---` and the defect below it.

    The boundary is the only thing separating them, and a reported line number a reader cannot open
    is a finding they will not act on.
    """
    _tree(tmp_path, effort="low")
    (tmp_path / "agents" / "avenger-x.md").write_text(
        "---\nname: avenger-x\neffort: low\n---\n\nInvoke the breaker at `effort: high`.\n"
    )

    problems = stage_effort.check(tmp_path)

    assert len(problems) == 1 and problems[0].startswith("agents/avenger-x.md:6:"), problems


# --- the rendering -------------------------------------------------------------------------------

def test_the_rendered_table_is_derived_from_the_definitions(tmp_path):
    _tree(tmp_path, effort="low")

    rendered = stage_effort.render_table(tmp_path)

    assert "`avenger-x`" in rendered and "`low`" in rendered


def test_check_exits_nonzero_when_a_stage_is_undeclared(tmp_path, capsys):
    _tree(tmp_path, effort=None)

    assert stage_effort.main(["check", "--root", str(tmp_path)]) == 1
    assert "declares no `effort:`" in capsys.readouterr().err


def test_check_exits_zero_on_the_real_tree(capsys):
    assert stage_effort.main(["check", "--root", str(ROOT)]) == 0


def test_a_tree_with_no_agents_says_it_checked_nothing(tmp_path, capsys):
    """A vendored repo receives the generated runtimes and no `agents/`.

    "Nothing to read" must never print as "checked, and every stage declares its effort" — that is
    the shape of every silent gate this repository has had to remove.
    """
    assert stage_effort.main(["check", "--root", str(tmp_path)]) == 0
    assert "nothing checked" in capsys.readouterr().err
