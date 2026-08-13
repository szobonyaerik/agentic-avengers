"""The spec writer is primed from the rubric the gate judges by - and from nowhere else.

Phase 9 of one measured feature ran **fourteen** gate rounds on its first spec and one, three and one
on the next three. Total spec writes barely moved against phase 8 (16 -> 19), so collapsing the two
gates into one did not reduce the work at all. The writer learned what the collapsed gate blocks by
being rejected fourteen times, and nothing carried that learning into the next phase.

`scripts/spec_rubric.py` hands it over up front. What these tests hold is the property that makes
that safe rather than harmful: **one source, not two.** A second copy of the rubric drifts, and a
drifted copy primes the writer against a standard nobody applies - strictly worse than no priming.
So the rendered brief must be derived from the gate's own artifacts, it must fail closed rather than
render half of itself, and the writer's agent definition must not restate what it is handed.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# `scripts/hook_spec_rubric.sh` is bash and `spec_rubric.py --sources` is a CLI: driving either any
# other way would test a reimplementation of the thing under test. Declared per
# scripts/subprocess_check.py, the pipeline's only cost gate.
pytestmark = pytest.mark.subprocess(
    "the subject is a bash hook and a CLI; running them in-process would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import spec_rubric  # noqa: E402
from requirement_cap import DEFAULT_MAX  # noqa: E402
from spec_gate_triage import BLOCKING, NOTE  # noqa: E402

WRITER = ROOT / "agents" / "avenger-spec-writer.md"
HOOK = ROOT / "scripts" / "hook_spec_rubric.sh"


# --- the rubric is derived, never authored -------------------------------------------------------

def test_every_blocking_category_reaches_the_writer_with_its_own_definition() -> None:
    """The four are handed over as the table states them, so the two cannot say different things."""
    rendered = spec_rubric.render()
    for category, meaning in BLOCKING.items():
        assert category in rendered, f"{category} is in the closed set but not in the writer's brief"
        assert meaning in rendered, (
            f"{category}'s meaning is paraphrased rather than rendered from spec_gate_triage."
            f"BLOCKING - that paraphrase is the copy that drifts"
        )


def test_the_note_category_is_named_so_a_writer_knows_what_does_not_block() -> None:
    rendered = spec_rubric.render()
    assert f"`{NOTE}`" in rendered
    assert "blocking nothing" in rendered.lower()


def test_the_requirement_cap_is_the_one_the_counter_enforces(monkeypatch) -> None:
    """The number in the brief is read from requirement_cap, not typed into the prose."""
    assert f"**{DEFAULT_MAX} requirements**" in spec_rubric.render()
    monkeypatch.setenv("SPEC_REQUIREMENT_MAX", "7")
    assert "**7 requirements**" in spec_rubric.render()


def test_the_brief_tells_the_writer_size_can_never_block_it() -> None:
    """The measured failure mode: the only answer a rejected spec has is more text."""
    rendered = spec_rubric.render().lower()
    assert "no gate will ever block your spec for being large" in rendered
    assert "split" in rendered


def test_each_prompt_section_is_carried_verbatim() -> None:
    """Lifted, not summarised. A summary of a rubric is a second rubric."""
    for relative, _why, body in spec_rubric.lifted():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert body in source, f"{relative}'s section is not present verbatim in the prompt"
        assert body in spec_rubric.render()


@pytest.mark.parametrize("relative,heading", [(r, h) for r, h, _ in spec_rubric.LIFTED])
def test_the_headings_this_depends_on_still_exist(relative: str, heading: str) -> None:
    """Renaming a prompt heading is a change to the rubric, and it goes red here rather than
    silently emptying the brief the writer is primed with."""
    assert spec_rubric.section((ROOT / relative).read_text(encoding="utf-8"), heading)


# --- it fails closed -----------------------------------------------------------------------------

def _fake_prompts(tmp_path: Path, keep: str | None = None) -> Path:
    """A prompts tree with the lifted sections stripped out of one or both files."""
    (tmp_path / "prompts").mkdir()
    for relative, heading, _why in spec_rubric.LIFTED:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if Path(relative).name != keep:
            text = text.replace(f"## {heading}", "## Something Else Entirely")
        (tmp_path / "prompts" / Path(relative).name).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_renamed_section_renders_nothing_rather_than_a_partial_rubric(tmp_path: Path) -> None:
    with pytest.raises(spec_rubric.RubricError) as caught:
        spec_rubric.render(_fake_prompts(tmp_path))
    assert caught.value.cause == "missing-section"


def test_a_missing_prompt_renders_nothing_rather_than_a_partial_rubric(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    with pytest.raises(spec_rubric.RubricError) as caught:
        spec_rubric.render(tmp_path)
    assert caught.value.cause == "unreadable-prompt"


def test_a_partial_render_never_reaches_stdout(tmp_path: Path) -> None:
    """The half-rubric is the dangerous output, so it must not be emitted with a warning beside it."""
    try:
        spec_rubric.render(_fake_prompts(tmp_path, keep="spec-gate-observe.md"))
    except spec_rubric.RubricError as exc:
        assert "spec-gate-triage.md" in str(exc)
    else:  # pragma: no cover - the fixture strips one section on purpose
        pytest.fail("a rubric missing one of its two lifted sections rendered anyway")


def test_the_cli_renders_and_lists_its_sources() -> None:
    rendered = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / "spec_rubric.py")],
        capture_output=True, text=True, check=False,
    )
    assert rendered.returncode == 0
    assert rendered.stdout.strip()

    sources = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / "spec_rubric.py"), "--sources"],
        capture_output=True, text=True, check=False,
    )
    assert sources.returncode == 0
    assert "scripts/spec_gate_triage.py" in sources.stdout
    for relative, _heading, _why in spec_rubric.LIFTED:
        assert relative in sources.stdout


# --- one source, not two -------------------------------------------------------------------------

def test_the_writers_own_definition_does_not_restate_the_blocking_set() -> None:
    """The whole point. The agent file used to carry its own copy of the four categories and the
    cap; a copy the gate does not read is the one that goes stale while still being obeyed."""
    text = WRITER.read_text(encoding="utf-8")
    for meaning in BLOCKING.values():
        assert meaning not in text, (
            "agents/avenger-spec-writer.md restates a blocking category's definition. It is handed "
            "the rubric at spawn; a second copy here is what drifts."
        )


def test_the_writer_is_pointed_at_the_renderer_for_runtimes_without_the_hook() -> None:
    """opencode has no SubagentStart event, so the pointer is the only delivery it gets."""
    text = WRITER.read_text(encoding="utf-8")
    assert "scripts/spec_rubric.py" in text
    assert "hook_spec_rubric.sh" in text


# --- delivery ------------------------------------------------------------------------------------

def _run_hook(agent_type: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", str(HOOK)],
        input='{"agent_type":"%s"}' % agent_type,
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(ROOT), **(env or {})},
    )


@pytest.mark.parametrize(
    "agent_type", ["avenger-spec-writer", "plan-build-verify:avenger-spec-writer"]
)
def test_the_hook_delivers_the_rubric_to_the_spec_writer(agent_type: str) -> None:
    result = _run_hook(agent_type)
    assert result.returncode == 0
    for category in BLOCKING:
        assert category in result.stdout


@pytest.mark.parametrize("agent_type", ["avenger-verifier", "avenger-breaker", ""])
def test_the_hook_delivers_nothing_to_any_other_stage(agent_type: str) -> None:
    """A rubric about writing specs has nothing to say to a stage that reads code."""
    assert _run_hook(agent_type).stdout.strip() == ""


def test_the_off_switch_kills_it() -> None:
    assert _run_hook("avenger-spec-writer", {"SPEC_RUBRIC_OFF": "1"}).stdout.strip() == ""


def test_an_unreadable_payload_injects_nothing() -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", str(HOOK)], input="not json", capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(ROOT)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_bad_agent_regex_injects_nothing_rather_than_everywhere() -> None:
    result = _run_hook("avenger-verifier", {"SPEC_RUBRIC_AGENTS": "["})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_rubric_that_cannot_render_is_REPORTED_not_swallowed(tmp_path: Path) -> None:
    """The one case that must not be silent. Injecting nothing would leave the writer unprimed and
    unaware of it - the state this hook exists to end - so it delivers the cause instead."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")   # no prompts/ beside it

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", str(tmp_path / "scripts" / "hook_spec_rubric.sh")],
        input='{"agent_type":"avenger-spec-writer"}',
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)},
    )

    assert result.returncode == 0, "a hook that cannot render must not stop the spawn"
    assert "COULD NOT BE RENDERED" in result.stdout
    assert "unreadable-prompt" in result.stdout
    for category in BLOCKING:
        assert category not in result.stdout, "a partial rubric is worse than a reported failure"


def test_the_hook_is_registered_on_subagent_start() -> None:
    hooks = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "hook_spec_rubric.sh" in hooks
