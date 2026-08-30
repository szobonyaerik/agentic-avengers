"""Tests for the non-greenfield survey `/pipeline-init` runs before it writes anything.

Issue #108: the command assumed a greenfield repository. It copied its own template over the
project's `.env.example`, and it assumed `cosmic-ray.toml` existed while two later steps require it
- so a repo that already has its own tooling learned about the second one two steps later, and lost
the first one immediately.

This is a REPORT, never a gate: it always exits 0. What it must never do is answer wrongly.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pipeline_init  # noqa: E402
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

    def test_the_merge_names_the_pipelines_template_and_the_live_file_only(
        self, tmp_path
    ):
        """The project's own example is NOT a merge source. Last-wins protects the keys the live
        `.env` declares and does nothing for the ones it omits, so an example's dummy would arrive
        as live configuration with nothing reporting it."""
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)
        text = "\n".join(report_lines(found))

        assert found.template_target == PIPELINE_EXAMPLE
        assert f"--out .env {PIPELINE_EXAMPLE} .env --force" in text
        assert f"--out .env {PROJECT_EXAMPLE}" not in text


class TestTheMergeNamesTheOperatorsOwnPipelineConfig:
    """The merge names exactly two files, so its one non-live source must be the file carrying the
    operator's decisions - never whichever pristine copy happens to be on disk first.

    Answering that with "which file holds the shipped template" dropped them: with a pristine
    `.env.example` beside a filled-in `.env.pipeline.example`, the merge named the pristine one and
    the assembled `.env` came back with shipped defaults for the operator's chosen model and
    mutation policy. Nothing reported it - the values parse, the read-back passes because a source
    declared them, and a default carries no `REPLACE_ME`. The CREATE branch got that state right, so
    the two branches disagreed about one file in one repository, and this is the branch that writes
    a live `.env`.
    """

    def live(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("OPENROUTER_API_KEY=sk-or-v1-real\n", encoding="utf-8")
        return path

    def merged(self, tmp_path, found):
        assemble(
            [tmp_path / name for name in found.merge_sources],
            tmp_path / ".env",
            force=True,
        )
        return parse((tmp_path / ".env").read_text())

    def test_a_pristine_project_example_never_displaces_an_edited_pipeline_one(
        self, tmp_path
    ):
        """The reported reproduction, end to end through the real assembler."""
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\nMUTATION_POLICY=enforce\n",
            encoding="utf-8",
        )
        self.live(tmp_path)

        found = survey(tmp_path)

        assert found.merge_sources == (PIPELINE_EXAMPLE, ".env")
        merged = self.merged(tmp_path, found)
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["MUTATION_POLICY"] == "enforce"
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-real"

    def test_the_mirror_state_names_the_pipelines_file_too(self, tmp_path):
        """Neither file is privileged by name: with the project's own example edited and the
        pipeline's pristine, the merge still names the PIPELINE's, because an edited
        `.env.example` is a file the project owns and its dummies must not reach a live `.env`."""
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "DRY_RUN=false\nBINANCE_API_KEY=your_api_key_here\n", encoding="utf-8"
        )
        (tmp_path / PIPELINE_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        self.live(tmp_path)

        found = survey(tmp_path)

        assert found.merge_sources == (PIPELINE_EXAMPLE, ".env")
        merged = self.merged(tmp_path, found)
        assert "DRY_RUN" not in merged
        assert "BINANCE_API_KEY" not in merged
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-real"

    def test_both_edited_names_the_pipelines_file_and_agrees_with_the_create_branch(
        self, tmp_path
    ):
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "GATE_MODEL=google/gemini-3.1-pro-preview\n", encoding="utf-8"
        )
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\n", encoding="utf-8"
        )
        self.live(tmp_path)

        found = survey(tmp_path)

        assert found.merge_sources == (PIPELINE_EXAMPLE, ".env")
        # The create branch names the same file LAST, so it wins there too: one answer, two shapes.
        assert found.assemble_sources[-1] == PIPELINE_EXAMPLE
        assert self.merged(tmp_path, found)["GATE_MODEL"] == "deepseek/deepseek-chat"

    def test_a_greenfield_init_wrote_the_template_to_env_example_and_that_is_named(
        self, tmp_path
    ):
        """A repository that had no examples gets the template at `.env.example`, so THERE that
        file is the pipeline's own - and the merge names it rather than a `.env.pipeline.example`
        nothing created."""
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        self.live(tmp_path)

        found = survey(tmp_path)

        assert found.merge_sources == (PROJECT_EXAMPLE, ".env")
        assert (tmp_path / found.merge_sources[0]).is_file()

    def test_an_edited_project_example_alone_still_waits_for_the_pipelines_own(
        self, tmp_path
    ):
        """The project owns `.env.example` here, so it is never the merge's source; step 2a is
        about to write the template to `.env.pipeline.example` and that is what is named."""
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=false\n", encoding="utf-8")
        self.live(tmp_path)

        found = survey(tmp_path)

        assert found.merge_sources == (PIPELINE_EXAMPLE, ".env")


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

    def test_only_the_projects_own_example_puts_the_template_before_it(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert found.assemble_sources == (PIPELINE_EXAMPLE, PROJECT_EXAMPLE)

    def test_only_an_existing_pipeline_example_is_the_pipelines_example(self, tmp_path):
        """The reported defect: the target resolved to a FRESH `.env.example`, shadowing this
        file, and the printed command then dropped it."""
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\n", encoding="utf-8"
        )

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert found.assemble_sources == (PIPELINE_EXAMPLE,)

    def test_a_fresh_copy_of_the_shipped_template_loses_to_a_configured_example(
        self, tmp_path
    ):
        """Both files are on disk, so PRESENCE cannot tell them apart - content can.

        `.env.pipeline.example` here is exactly what step 2a's `cp -n` writes: the shipped
        template, unedited. `.env.example` is the team's, filled in and committed. They OVERLAP on
        every pipeline key, so the assertion is about who won rather than about order alone.
        """
        (tmp_path / PIPELINE_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME\n"
            "GATE_MODEL=deepseek/deepseek-chat\n"
            "GATE_PROVIDER=opencode\n"
            "MUTATION_POLICY=enforce\n",
            encoding="utf-8",
        )

        found = survey(tmp_path)

        assert found.assemble_sources == (PIPELINE_EXAMPLE, PROJECT_EXAMPLE)

        assemble(
            [tmp_path / name for name in found.assemble_sources], tmp_path / ".env"
        )
        merged = parse((tmp_path / ".env").read_text())
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["GATE_PROVIDER"] == "opencode"
        assert merged["MUTATION_POLICY"] == "enforce"

    def test_both_examples_edited_keep_existing_order_and_the_pipelines_wins(
        self, tmp_path
    ):
        """Neither file matches the shipped template, so both are operator-owned and they keep
        `existing_examples` order. The fixture OVERLAPS on `GATE_MODEL` so the assertion is about
        who won, not about order alone."""
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "DRY_RUN=true\nGATE_MODEL=google/gemini-3.1-pro-preview\n", encoding="utf-8"
        )
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\n", encoding="utf-8"
        )

        found = survey(tmp_path)

        assert found.template_target == PIPELINE_EXAMPLE
        assert found.assemble_sources == (PROJECT_EXAMPLE, PIPELINE_EXAMPLE)

        assemble(
            [tmp_path / name for name in found.assemble_sources], tmp_path / ".env"
        )
        merged = parse((tmp_path / ".env").read_text())
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["DRY_RUN"] == "true"

    def test_a_filled_in_pipeline_example_is_never_displaced_by_an_older_env_example(
        self, tmp_path
    ):
        """The measured trace, end to end, for the state where NOTHING is created.

        Init #1 wrote the shipped template to `.env.example` and the team committed it, editing
        some values and leaving the secret as `REPLACE_ME`. Init #2 routed the template to
        `.env.pipeline.example` and the operator filled THAT one in. Both are committed; a fresh
        worktree has no `.env`. Both derive from the same template, so they share every pipeline
        key - naming the pipeline's example first handed the assembled `.env` the older, less
        edited copy and discarded the operator's, silently.
        """
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        (tmp_path / PIPELINE_EXAMPLE).write_text(
            "OPENROUTER_API_KEY=sk-or-v1-real\n"
            "GATE_MODEL=deepseek/deepseek-chat\n"
            "MUTATION_POLICY=enforce\n",
            encoding="utf-8",
        )

        found = survey(tmp_path)
        # `cp -n` creates nothing: the target is already on disk, and never overwritten.
        assert found.template_target == PIPELINE_EXAMPLE
        assert found.pipeline_example_exists

        assemble(
            [tmp_path / name for name in found.assemble_sources], tmp_path / ".env"
        )

        merged = parse((tmp_path / ".env").read_text())
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-real"
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["MUTATION_POLICY"] == "enforce"
        # ...and the older example still contributes every key the operator's copy omits.
        shipped = parse(SHIPPED_TEMPLATE)
        for key in set(shipped) - {
            "OPENROUTER_API_KEY",
            "GATE_MODEL",
            "MUTATION_POLICY",
        }:
            assert merged[key] == shipped[key]

    def test_a_freshly_copied_template_never_beats_a_configured_example(self, tmp_path):
        """The measured trace, end to end through the real assembler.

        An earlier init wrote the pipeline template to `.env.example`; the team filled it in and
        committed it. A new worktree has no `.env` (gitignored) and no `.env.pipeline.example`, so
        step 2a's `cp -n` writes the SHIPPED template there unmodified. Named LAST it won, and the
        assembled `.env` carried the shipped defaults instead of the team's choices - with nothing
        reporting it, since the values parse, survive the read-back and carry no `REPLACE_ME`.
        """
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME\n"
            "GATE_MODEL=deepseek/deepseek-chat\n"
            "GATE_PROVIDER=opencode\n"
            "MUTATION_POLICY=enforce\n",
            encoding="utf-8",
        )

        found = survey(tmp_path)
        assert found.template_target == PIPELINE_EXAMPLE
        # Step 2a: `cp -n` writes the shipped template, unmodified, to the target step 0 named.
        (tmp_path / found.template_target).write_text(
            SHIPPED_TEMPLATE, encoding="utf-8"
        )

        assemble(
            [tmp_path / name for name in found.assemble_sources], tmp_path / ".env"
        )

        merged = parse((tmp_path / ".env").read_text())
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["GATE_PROVIDER"] == "opencode"
        assert merged["MUTATION_POLICY"] == "enforce"
        # ...and the template still fills in every pipeline key the repository does not declare.
        shipped = parse(SHIPPED_TEMPLATE)
        undeclared = set(shipped) - set(parse((tmp_path / PROJECT_EXAMPLE).read_text()))
        assert undeclared, "the fixture must leave the template something to contribute"
        for key in undeclared:
            assert merged[key] == shipped[key]

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

        assert found.merge_sources == (found.template_target, ".env")
        assert self.command(found) == list(found.merge_sources)
        assert self.command(found)[-1] == ".env"

    def test_the_merge_form_never_names_the_projects_own_example(self, tmp_path):
        """A merge imports the PIPELINE's template and the operator's own file, and nothing else.
        The project's example is a source only when a `.env` is being CREATED."""
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=false\n", encoding="utf-8")
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")

        found = survey(tmp_path)

        assert PROJECT_EXAMPLE in found.assemble_sources
        assert PROJECT_EXAMPLE not in found.merge_sources
        assert found.merge_sources == (PIPELINE_EXAMPLE, ".env")

    def test_a_project_examples_dummy_never_reaches_a_live_env(self, tmp_path):
        """The measured shape: a committed `.env.example` drifts ahead of a live `.env`, so a key
        the live file OMITS would arrive carrying the example's dummy. Last-wins cannot help - the
        live file never declares it - so the example must not be a merge source at all."""
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "DRY_RUN=false\nBINANCE_API_KEY=your_api_key_here\n", encoding="utf-8"
        )
        (tmp_path / ".env").write_text(
            "OPENROUTER_API_KEY=sk-or-v1-live\n", encoding="utf-8"
        )
        found = survey(tmp_path)
        (tmp_path / found.template_target).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\n", encoding="utf-8"
        )

        assemble(
            [tmp_path / name for name in found.merge_sources],
            tmp_path / ".env",
            force=True,
        )

        merged = parse((tmp_path / ".env").read_text())
        assert "DRY_RUN" not in merged
        assert "BINANCE_API_KEY" not in merged
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-live"
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"

    def test_every_named_source_is_a_file_that_exists_after_init_copies_the_template(
        self, tmp_path
    ):
        """The property the hardcoded path broke: what the report names must be readable.

        Re-surveyed AFTER the copy, because that is the state the report is read in: step 2a runs
        between step 0 and the assemble, and `commands/pipeline-init.md` tells every later writing
        step to read the report again.
        """
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")
        found = survey(tmp_path)
        # `cp -n` writes the SHIPPED template, byte for byte - the survey's answer is derived from
        # exactly those bytes, so a stand-in with different content is not step 2a.
        (tmp_path / found.template_target).write_text(
            SHIPPED_TEMPLATE, encoding="utf-8"
        )

        for path in self.command(survey(tmp_path)):
            assert (tmp_path / path).is_file()

    @pytest.mark.parametrize(
        "present, expected",
        [
            ((), (PROJECT_EXAMPLE,)),
            ((PROJECT_EXAMPLE,), (PIPELINE_EXAMPLE, PROJECT_EXAMPLE)),
            ((PIPELINE_EXAMPLE,), (PIPELINE_EXAMPLE,)),
            ((PROJECT_EXAMPLE, PIPELINE_EXAMPLE), (PROJECT_EXAMPLE, PIPELINE_EXAMPLE)),
        ],
    )
    def test_the_printed_create_command_names_the_sources_in_order(
        self, tmp_path, present, expected
    ):
        """The report is what an operator runs, so it must not state a different order.

        All four example-presence states at once, every example EDITED (none matches the shipped
        template): an unedited copy of the template is named first, and everything an operator has
        touched follows in `existing_examples` order.
        """
        for name in present:
            (tmp_path / name).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert found.assemble_sources == expected
        assert self.command(found) == list(expected)

    #: Every example-presence state x content class, as `{filename: bytes}`. `SHIPPED_TEMPLATE`
    #: means a file `cp -n` wrote and nobody touched; anything else is operator-owned.
    EXAMPLE_STATES = [
        pytest.param({}, id="no-example-at-all"),
        pytest.param({PROJECT_EXAMPLE: "GATE_MODEL=a\n"}, id="project-edited"),
        pytest.param({PROJECT_EXAMPLE: SHIPPED_TEMPLATE}, id="project-untouched"),
        pytest.param({PIPELINE_EXAMPLE: "GATE_MODEL=b\n"}, id="pipeline-edited"),
        pytest.param({PIPELINE_EXAMPLE: SHIPPED_TEMPLATE}, id="pipeline-untouched"),
        pytest.param(
            {PROJECT_EXAMPLE: "GATE_MODEL=a\n", PIPELINE_EXAMPLE: "GATE_MODEL=b\n"},
            id="both-edited",
        ),
        pytest.param(
            {PROJECT_EXAMPLE: "GATE_MODEL=a\n", PIPELINE_EXAMPLE: SHIPPED_TEMPLATE},
            id="project-edited-pipeline-untouched",
        ),
        pytest.param(
            {PROJECT_EXAMPLE: SHIPPED_TEMPLATE, PIPELINE_EXAMPLE: "GATE_MODEL=b\n"},
            id="project-untouched-pipeline-edited",
        ),
    ]

    @pytest.mark.parametrize("examples", EXAMPLE_STATES)
    @pytest.mark.parametrize("live_env", [False, True], ids=["create", "merge"])
    def test_step_2as_own_copy_never_changes_the_printed_command(
        self, tmp_path, examples, live_env
    ):
        """The command step 0 prints is IDENTICAL before and after step 2a's `cp -n`, and every
        file it names EXISTS when the operator runs it. Every example-presence state, both branches.

        This is the property the content rule exists to provide, and the one a presence rule could
        not: `existing_examples` is read off disk and the copy is what changes that disk state, so
        the survey answered one way before it and the reverse after it - two contradictory answers
        for one repository from the single authority. `commands/pipeline-init.md` tells every later
        writing step to read this report, and a second `/pipeline-init` on an already-initialised
        repository reaches the after state with no copy of its own at all.

        The state that used to fail both halves is `no-example-at-all`: the report named
        `.env.example` before the copy and `.env.example .env.pipeline.example` after it, the second
        naming a file nothing ever created, so `assemble` refused with `cannot read
        .env.pipeline.example` and wrote nothing.
        """
        for name, body in examples.items():
            (tmp_path / name).write_text(body, encoding="utf-8")
        if live_env:
            (tmp_path / ".env").write_text(
                "OPENROUTER_API_KEY=sk-live\n", encoding="utf-8"
            )

        before = survey(tmp_path)
        # Step 2a, verbatim: `cp -n <shipped template> <the target step 0 named>` - `-n` keeps an
        # existing file, so the copy only lands where there is nothing.
        target = tmp_path / before.template_target
        if not target.exists():
            target.write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        after = survey(tmp_path)

        assert self.command(after) == self.command(before)
        for name in self.command(after):
            assert (tmp_path / name).is_file(), (
                f"the report names {name}, which does not exist for the operator to read"
            )

    @pytest.mark.parametrize("examples", EXAMPLE_STATES)
    def test_the_printed_create_command_assembles_without_refusing(
        self, tmp_path, examples
    ):
        """The end of that property: what the survey prints after step 2a actually runs.

        Naming a file that was never created is not a silent wrong config - it is an
        `AssemblyError` and nothing written - but init then finishes with the pipeline's keys in no
        file any gate reads, which is the state issue #108 is about.
        """
        for name, body in examples.items():
            (tmp_path / name).write_text(body, encoding="utf-8")

        found = survey(tmp_path)
        target = tmp_path / found.template_target
        if not target.exists():
            target.write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        found = survey(tmp_path)

        assemble([tmp_path / name for name in self.command(found)], tmp_path / ".env")

        merged = parse((tmp_path / ".env").read_text())
        for name in self.command(found):
            for key in parse((tmp_path / name).read_text()):
                assert key in merged
        if found.fresh_examples:
            # A copy of the template landed somewhere, so its keys reach the run.
            assert merged["AUTHOR_FAMILY"] == parse(SHIPPED_TEMPLATE)["AUTHOR_FAMILY"]

    def test_a_second_copy_of_the_shipped_template_is_not_named_twice(self, tmp_path):
        """Two byte-identical copies declare the same values, so naming the second adds no key and
        only makes the answer depend on how many copies happen to be on disk."""
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        (tmp_path / PIPELINE_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")

        found = survey(tmp_path)

        assert found.assemble_sources == (PROJECT_EXAMPLE,)
        assert found.merge_sources == (PROJECT_EXAMPLE, ".env")

    def test_the_reported_trace_end_to_end_across_step_2a(self, tmp_path):
        """The measured sequence, run the way an operator runs it.

        A committed, filled-in `.env.example`, no `.env.pipeline.example`, no `.env`. Step 2a
        copies the shipped template; assembling with what the survey prints AFTER that copy must
        keep the team's three values and must not hand the run a shipped default.
        """
        (tmp_path / PROJECT_EXAMPLE).write_text(
            "GATE_MODEL=deepseek/deepseek-chat\n"
            "GATE_PROVIDER=opencode\n"
            "MUTATION_POLICY=enforce\n",
            encoding="utf-8",
        )

        found = survey(tmp_path)
        (tmp_path / found.template_target).write_text(
            SHIPPED_TEMPLATE, encoding="utf-8"
        )
        found = survey(tmp_path)

        assemble([tmp_path / name for name in self.command(found)], tmp_path / ".env")

        merged = parse((tmp_path / ".env").read_text())
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["GATE_PROVIDER"] == "opencode"
        assert merged["MUTATION_POLICY"] == "enforce"
        # ...while the template still contributes every key the team's file does not declare.
        shipped = parse(SHIPPED_TEMPLATE)
        assert merged["AUTHOR_FAMILY"] == shipped["AUTHOR_FAMILY"]

    def test_an_example_that_differs_by_one_byte_is_edited(self, tmp_path):
        """The comparison is on bytes, so a file that merely resembles the template is operator
        owned. This is the ordinary edited path, not a failure path."""
        (tmp_path / PROJECT_EXAMPLE).write_bytes(SHIPPED_TEMPLATE.encode() + b"\xff")

        found = survey(tmp_path)

        assert PROJECT_EXAMPLE not in found.fresh_examples
        assert found.assemble_sources == (PIPELINE_EXAMPLE, PROJECT_EXAMPLE)

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="root reads a chmod 000 file, so it is never unreadable",
    )
    def test_an_unreadable_example_is_treated_as_edited_and_still_reports(
        self, tmp_path
    ):
        """An unanswerable comparison resolves to operator-owned, the safe direction: it can only
        make an operator's file win a key, never lose one.

        And the survey still REPORTS. Step 0 runs before anything else in `/pipeline-init` and its
        stated contract is that it always exits 0 - raising out of it would abort init with a
        traceback instead of describing the repository.
        """
        unreadable = tmp_path / PROJECT_EXAMPLE
        unreadable.write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        os.chmod(unreadable, 0o000)
        try:
            found = survey(tmp_path)

            assert found.fresh_examples == frozenset()
            assert found.assemble_sources == (PIPELINE_EXAMPLE, PROJECT_EXAMPLE)
            assert report_lines(found)
        finally:
            os.chmod(unreadable, 0o600)

    def test_a_missing_shipped_template_leaves_every_example_edited_and_still_reports(
        self, tmp_path, monkeypatch
    ):
        """A vendored install without `docs/templates/env.example` reaches exactly this. Nothing can
        be shown to be a fresh template, so nothing is - and step 0 still describes the repository
        rather than aborting it."""
        (tmp_path / PROJECT_EXAMPLE).write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        monkeypatch.setattr(
            pipeline_init, "SHIPPED_TEMPLATE", tmp_path / "nowhere" / "env.example"
        )

        found = survey(tmp_path)

        assert found.fresh_examples == frozenset()
        assert found.assemble_sources == (PIPELINE_EXAMPLE, PROJECT_EXAMPLE)
        assert report_lines(found)

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
