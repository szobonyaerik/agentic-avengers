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

from pipeline_init import (  # noqa: E402
    PIPELINE_EXAMPLE,
    PROJECT_EXAMPLE,
    report_lines,
    survey,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "pipeline_init.py"


class TestGreenfield:
    def test_an_empty_repo_is_greenfield_and_the_template_owns_env_example(
        self, tmp_path
    ):
        found = survey(tmp_path)
        assert found.greenfield
        assert found.template_target == PROJECT_EXAMPLE
        assert not found.env_example_owned_by_project


class TestNonGreenfield:
    def test_an_existing_env_example_is_the_projects_own(self, tmp_path):
        (tmp_path / PROJECT_EXAMPLE).write_text("DRY_RUN=true\n", encoding="utf-8")

        found = survey(tmp_path)

        assert not found.greenfield
        assert found.env_example_owned_by_project
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

    def test_a_live_env_is_reported_and_never_a_target(self, tmp_path):
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")
        found = survey(tmp_path)
        assert found.env_exists
        assert "never overwrite" in "\n".join(report_lines(found)).lower()


class TestTheReportNamesBothHalves:
    def test_an_existing_env_example_is_reported_as_the_projects_own(self, tmp_path):
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
