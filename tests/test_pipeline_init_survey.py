"""Tests for the non-greenfield survey `/pipeline-init` runs before it writes anything.

Issue #108: the command assumed a greenfield repository. It copied its own template over the
project's `.env.example`, and it assumed `cosmic-ray.toml` existed while two later steps require it
- so a repo that already has its own tooling learned about the second one two steps later, and lost
the first one immediately.

This is a REPORT, never a gate: it always exits 0. What it must never do is answer wrongly.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from env_assemble import assemble  # noqa: E402
from env_file import parse  # noqa: E402
from pipeline_init import (  # noqa: E402
    PIPELINE_EXAMPLE,
    PROJECT_EXAMPLE,
    report_lines,
    survey,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "pipeline_init.py"

#: The template `/pipeline-init` copies. Reading it here is reading the command's own input, so a
#: re-init can be set up exactly as it occurs: the file on disk IS what an earlier init wrote.
SHIPPED_TEMPLATE = (REPO / "docs" / "templates" / "env.example").read_text(
    encoding="utf-8"
)


class TestGreenfield:
    def test_an_empty_repo_is_greenfield_and_the_template_owns_env_example(
        self, tmp_path
    ):
        found = survey(tmp_path)
        assert found.greenfield
        assert found.template_target == PROJECT_EXAMPLE
        assert not found.env_example_exists


class TestNonGreenfield:
    def test_an_existing_env_example_is_never_the_templates_target(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert not found.greenfield
        assert found.env_example_exists
        assert found.template_target == PIPELINE_EXAMPLE

    def test_a_missing_cosmic_ray_config_names_the_steps_that_need_it(self, tmp_path):
        found = survey(tmp_path)
        assert not found.cosmic_ray_present
        assert found.cosmic_ray_required_by  # non-empty: the later steps, by name

        text = "\n".join(report_lines(found))
        assert "cosmic-ray.toml" in text
        for step in found.cosmic_ray_required_by:
            assert step in text

    def test_a_present_cosmic_ray_config_reports_no_obligation(self, tmp_path):
        (tmp_path / "cosmic-ray.toml").write_text("[cosmic-ray]\n", encoding="utf-8")
        assert survey(tmp_path).cosmic_ray_present

    def test_a_live_env_is_reported_with_the_merge_in_place_remedy(self, tmp_path):
        """The remedy used to offer `--force` (which destroys the credentials the same sentence
        forbade destroying) or "a different path" (which no gate reads), so init on the exact repo
        shape issue #108 is about finished with the pipeline's keys never reaching a run."""
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")

        found = survey(tmp_path)
        text = "\n".join(report_lines(found))

        assert found.env_exists
        assert "--out .env .env.example .env --force" in text
        assert "OWN LAST SOURCE" in text

    def test_the_merge_names_every_example_that_exists_and_the_template_where_it_landed(
        self, tmp_path
    ):
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert f"--out .env {PROJECT_EXAMPLE} {PIPELINE_EXAMPLE} .env --force" in (
            "\n".join(report_lines(found))
        )


class TestTheAssembleCommandNamesFilesThatExist:
    """Which example files exist decides the template's target AND the source list.

    Two ways this answered wrongly. A hardcoded `.env.pipeline.example` named a file that does not
    exist in a repo without its own `.env.example`. Then, deriving the sources from the target
    alone, an existing `.env.pipeline.example` was dropped from the command entirely while the same
    report said it was being kept - so an operator's filled-in model or provider silently reverted
    to a template default, with no placeholder anywhere for the gate refusal to catch.
    """

    def command(self, found):
        line = next(line for line in report_lines(found) if line.startswith(".env:"))
        return line.split("`")[1].split("--out .env ")[1].split("--force")[0].split()

    def test_no_example_at_all_names_the_target_step_2a_creates(self, tmp_path):
        found = survey(tmp_path)

        assert found.template_target == PROJECT_EXAMPLE
        assert found.assemble_sources == (PROJECT_EXAMPLE,)

    def test_only_the_projects_own_example_puts_the_template_after_it(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert found.assemble_sources == (PROJECT_EXAMPLE, PIPELINE_EXAMPLE)

    def test_only_an_existing_pipeline_example_is_the_pipelines_example(self, tmp_path):
        """The reported defect: the target resolved to a FRESH `.env.example`, shadowing this
        file, and the printed command then dropped it."""
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\n", encoding="utf-8"
        )

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert found.assemble_sources == (PIPELINE_EXAMPLE,)

    def test_both_examples_are_sources_template_last(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")
        (tmp_path / PIPELINE_EXAMPLE).write_text("GATE_MODEL=x\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert found.assemble_sources == (PROJECT_EXAMPLE, PIPELINE_EXAMPLE)

    @pytest.mark.parametrize(
        "present",
        [
            (),
            (PROJECT_EXAMPLE,),
            (PIPELINE_EXAMPLE,),
            (PROJECT_EXAMPLE, PIPELINE_EXAMPLE),
        ],
    )
    def test_the_merge_form_ends_with_the_live_env_in_every_state(
        self, tmp_path, present
    ):
        for name in present:
            (tmp_path / name).write_text("DRY_RUN=true\n", encoding="utf-8")
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.merge_sources == found.assemble_sources + (".env",)
        assert self.command(found) == list(found.merge_sources)
        assert self.command(found)[-1] == ".env"

    def test_every_named_source_is_a_file_that_exists_after_init_copies_the_template(
        self, tmp_path
    ):
        """The property the hardcoded path broke: what the report names must be readable."""
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")
        found = survey(tmp_path)
        (tmp_path / found.template_target).write_text(
            "GATE_MODEL=x\n", encoding="utf-8"
        )

        for path in self.command(found):
            assert (tmp_path / path).is_file()

    def test_an_operators_filled_in_pipeline_example_survives_the_assembled_env(
        self, tmp_path
    ):
        """End to end, through the real assembler: the state the finding measured. Following the
        survey must not revert a chosen model to the shipped template's default."""
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "OPENROUTER_API_KEY=sk-or-v1-real\nGATE_MODEL=deepseek/deepseek-chat\n",
            encoding="utf-8",
        )
        found = survey(tmp_path)
        # Step 2a's `cp -n` keeps the existing target, so nothing else is created here.
        assert (tmp_path / found.template_target).is_file()

        assemble(
            [tmp_path / name for name in found.assemble_sources], tmp_path / ".env"
        )

        merged = parse((tmp_path / ".env").read_text())
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-real"


class TestBothExampleFilesAreLeftAlone:
    """`commands/pipeline-init.md` promises never to overwrite EITHER example file. The survey has
    to carry the data that makes the promise keepable, or it is an instruction with no mechanism."""

    def test_an_existing_pipeline_example_is_reported_and_left_alone(self, tmp_path):
        (tmp_path / PIPELINE_EXAMPLE).write_text("GATE_MODEL=x\n", encoding="utf-8")

        found = survey(tmp_path)
        text = "\n".join(report_lines(found))

        assert found.pipeline_example_exists
        assert not found.greenfield
        assert f"{PIPELINE_EXAMPLE}: EXISTS - leave it alone too" in text

    def test_an_absent_pipeline_example_is_reported_too(self, tmp_path):
        found = survey(tmp_path)

        assert not found.pipeline_example_exists
        assert f"{PIPELINE_EXAMPLE}: absent" in "\n".join(report_lines(found))


class TestOneRuleForAnExistingExample:
    """An existing `.env.example` is never overwritten, WHOEVER wrote it.

    A pipeline-written one was briefly told apart by its content and "refreshed in place". Any such
    classification is wrong in the case that costs something: a repo initialised once whose operator
    then added their own keys still matches the template, so the refresh destroys them - the
    ownership assumption issue #108 is about. There is no third state, so nothing has to classify.
    """

    def test_an_env_example_that_is_the_shipped_template_is_still_never_the_target(
        self, tmp_path
    ):
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")

        found = survey(tmp_path)

        assert found.env_example_exists
        assert found.template_target == PIPELINE_EXAMPLE
        assert not found.greenfield

    def test_an_operator_who_added_keys_to_a_pipeline_written_example_keeps_them(
        self, tmp_path
    ):
        example = tmp_path / PROJECT_EXAMPLE
        example.write_text(SHIPPED_TEMPLATE + "MY_OWN_KEY=mine\n", encoding="utf-8")
        before = example.read_text(encoding="utf-8")

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert example.read_text(encoding="utf-8") == before

    def test_the_projects_own_file_gets_the_same_answer(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.env_example_exists
        assert found.template_target == PIPELINE_EXAMPLE

    def test_the_report_states_the_rule_without_claiming_who_wrote_it(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")

        text = "\n".join(report_lines(survey(tmp_path)))

        assert "never overwrite it, whoever wrote it" in text
        assert PIPELINE_EXAMPLE in text


class TestTheCosmicRayRemedyTravels:
    """The remedy pointed at `AVENGERS.md`, which `install.sh` does not vendor while it DOES vendor
    this script - so a vendored consumer got a remedy naming a file it does not have."""

    def test_the_report_carries_the_config_rather_than_a_path_to_it(self, tmp_path):
        text = "\n".join(report_lines(survey(tmp_path)))

        assert "AVENGERS.md" not in text
        assert "[cosmic-ray]" in text
        assert "module-path" in text
        assert "test-command" in text


class TestTheReportNamesBothHalves:
    def test_an_existing_env_example_names_both_paths(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")
        text = "\n".join(report_lines(survey(tmp_path)))
        assert PIPELINE_EXAMPLE in text
        assert PROJECT_EXAMPLE in text


@pytest.mark.subprocess(
    "pins the CLI's report and its exit code, which only a process has"
)
class TestCLI:
    def run(self, *argv, cwd):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def test_survey_reports_both_findings_and_stays_non_fatal(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        proc = self.run("survey", "--root", ".", cwd=tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert PIPELINE_EXAMPLE in proc.stdout
        assert "cosmic-ray.toml" in proc.stdout
