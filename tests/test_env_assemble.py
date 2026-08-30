"""Tests for safe `.env` assembly - the defect issue #108 measured.

The documented setup line was `cat .env.example .env.pipeline.example > .env`. When the first file
does not end in a newline its last key FUSES onto the first line of the second, and the parser reads
the merged line as a different value. On grid-bot-platform that turned `DRY_RUN=true` into
`DRY_RUN=false` on every worktree assembled that way, silently, for the life of the task.

Two properties are pinned here, and the first one is checked against the OLD shape as well: a bare
`cat` must fail the same test that `assemble` passes, or the test is not about the defect.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from env_assemble import (  # noqa: E402
    PLACEHOLDER_MARKER,
    AssemblyError,
    assemble,
    declared,
    join_text,
    placeholder_keys,
    run_placeholders,
)
from env_file import parse  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "env_assemble.py"

# The measured shape: a project `.env.example` with NO trailing newline, whose last key is the
# safety flag, followed by the pipeline's own example.
PROJECT_EXAMPLE_NO_NEWLINE = "API_KEY=abc\nDRY_RUN=true"
PIPELINE_EXAMPLE = "GATE_PROVIDER=openrouter\nMUTATION_POLICY=advisory\n"


def write_sources(tmp_path, first=PROJECT_EXAMPLE_NO_NEWLINE, second=PIPELINE_EXAMPLE):
    a = tmp_path / ".env.example"
    b = tmp_path / ".env.pipeline.example"
    a.write_text(first, encoding="utf-8")
    b.write_text(second, encoding="utf-8")
    return a, b


class TestTheMeasuredDefect:
    def test_a_bare_cat_silently_changes_the_safety_flag(self, tmp_path):
        """The old shape, kept as a fixture: this is what the fix has to beat."""
        a, b = write_sources(tmp_path)
        fused = parse(a.read_text() + b.read_text())

        assert fused.get("DRY_RUN") != "true"  # the value nobody chose
        assert "GATE_PROVIDER" not in fused  # and a key that vanished with it

    def test_assemble_preserves_every_declared_value_across_the_join(self, tmp_path):
        a, b = write_sources(tmp_path)
        out = tmp_path / ".env"

        assemble([a, b], out)

        assert parse(out.read_text())["DRY_RUN"] == "true"
        assert parse(out.read_text())["GATE_PROVIDER"] == "openrouter"

    def test_join_text_separates_sources_that_do_not_end_in_a_newline(self):
        assert join_text(["A=1", "B=2\n"]) == "A=1\nB=2\n"

    def test_a_later_source_may_override_an_earlier_key(self, tmp_path):
        a, b = write_sources(tmp_path, "DRY_RUN=true", "DRY_RUN=false\n")
        out = tmp_path / ".env"

        assemble([a, b], out)

        assert parse(out.read_text())["DRY_RUN"] == "false"
        assert declared([a, b])["DRY_RUN"] == "false"


class TestReadBackIsFatal:
    def test_a_key_that_does_not_survive_assembly_stops_the_run(
        self, tmp_path, monkeypatch
    ):
        a, b = write_sources(tmp_path)
        out = tmp_path / ".env"
        # Force the fused shape past the joiner: the read-back is the second line of defence, and
        # it must be the one that refuses, not a comment saying the joiner handles it.
        monkeypatch.setattr("env_assemble.join_text", lambda texts: "".join(texts))

        with pytest.raises(AssemblyError) as exc:
            assemble([a, b], out)

        assert "DRY_RUN" in str(exc.value)
        assert not out.exists()  # nothing half-written is left behind

    def test_a_missing_source_is_named_and_nothing_is_written(self, tmp_path):
        out = tmp_path / ".env"
        with pytest.raises(AssemblyError) as exc:
            assemble([tmp_path / "absent.example"], out)
        assert "absent.example" in str(exc.value)
        assert not out.exists()

    def test_an_existing_output_is_never_overwritten_without_force(self, tmp_path):
        a, b = write_sources(tmp_path)
        out = tmp_path / ".env"
        out.write_text("OPENROUTER_API_KEY=sk-live\n", encoding="utf-8")

        with pytest.raises(AssemblyError):
            assemble([a, b], out)
        assert "sk-live" in out.read_text()

        assemble([a, b], out, force=True)
        assert "sk-live" not in out.read_text()


class TestPlaceholders:
    def test_a_replace_me_value_is_named(self):
        values = {"OPENROUTER_API_KEY": f"sk-or-v1-{PLACEHOLDER_MARKER}", "OK": "fine"}
        assert placeholder_keys(values) == ["OPENROUTER_API_KEY"]

    def test_a_filled_in_config_names_nothing(self):
        assert placeholder_keys({"OPENROUTER_API_KEY": "sk-or-v1-real"}) == []


@pytest.mark.subprocess("pins the CLI's exit codes, which only a process has")
class TestCLI:
    def run(self, *argv, cwd, **env_over):
        env = {
            key: value
            for key, value in os.environ.items()
            # A pipeline variable inherited from the developer's own shell would decide these
            # answers instead of the case under test.
            if not key.startswith(("GATE_", "OPENROUTER_", "AUTHOR_FAMILY"))
        }
        return subprocess.run(
            [sys.executable, str(SCRIPT), *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
            env={**env, **env_over},
        )

    def test_assemble_exits_zero_and_writes_the_file(self, tmp_path):
        write_sources(tmp_path)
        proc = self.run(
            "assemble",
            "--out",
            ".env",
            ".env.example",
            ".env.pipeline.example",
            cwd=tmp_path,
        )
        assert proc.returncode == 0, proc.stderr
        assert parse((tmp_path / ".env").read_text())["DRY_RUN"] == "true"

    def test_check_refuses_a_placeholder_value(self, tmp_path):
        (tmp_path / ".env").write_text(
            f"OPENROUTER_API_KEY=sk-or-v1-{PLACEHOLDER_MARKER}\n", encoding="utf-8"
        )
        proc = self.run("check", "--root", ".", cwd=tmp_path)
        assert proc.returncode == 1
        assert "OPENROUTER_API_KEY" in proc.stderr

    def test_check_is_clean_on_a_filled_in_env(self, tmp_path):
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-real\n", encoding="utf-8")
        assert self.run("check", "--root", ".", cwd=tmp_path).returncode == 0

    def test_check_with_no_env_file_is_clean_and_says_so(self, tmp_path):
        proc = self.run("check", "--root", ".", cwd=tmp_path)
        assert proc.returncode == 0
        assert ".env" in proc.stderr

    def test_check_asks_what_a_run_asks_of_the_real_environment(self, tmp_path):
        """`check` parsed `<root>/.env` alone, so it disagreed with a run in both directions: it
        refused a placeholder the real environment already overrode, and it passed a pipeline
        variable exported as a placeholder with no file at all. One rule, one implementation."""
        proc = self.run(
            "check",
            "--root",
            ".",
            cwd=tmp_path,
            GATE_MODEL=f"vendor/{PLACEHOLDER_MARKER}",
        )
        assert proc.returncode == 1
        assert "GATE_MODEL" in proc.stderr

    def test_check_lets_the_real_environment_win_over_the_file(self, tmp_path):
        (tmp_path / ".env").write_text(
            f"GATE_MODEL={PLACEHOLDER_MARKER}\n", encoding="utf-8"
        )
        proc = self.run(
            "check", "--root", ".", cwd=tmp_path, GATE_MODEL="google/gemini-3.1-pro"
        )
        assert proc.returncode == 0, proc.stderr

    def test_check_names_the_provider_it_assumed_when_none_was_given(self, tmp_path):
        proc = self.run("check", "--root", ".", cwd=tmp_path)
        assert proc.returncode == 0
        assert "opencode" in proc.stderr

    def test_check_scopes_the_openrouter_key_to_the_named_provider(self, tmp_path):
        env = {"OPENROUTER_API_KEY": f"sk-or-v1-{PLACEHOLDER_MARKER}"}
        assert (
            self.run(
                "check", "--root", ".", "--provider", "opencode", cwd=tmp_path, **env
            ).returncode
            == 0
        )
        refused = self.run(
            "check", "--root", ".", "--provider", "openrouter", cwd=tmp_path, **env
        )
        assert refused.returncode == 1
        assert "OPENROUTER_API_KEY" in refused.stderr


class TestWhatARunRefuses:
    """`run_placeholders` is scoped: the project's own `.env` keys, plus the pipeline's variables."""

    def test_a_placeholder_in_the_projects_env_is_refused(self, tmp_path):
        (tmp_path / ".env").write_text("DRY_RUN=REPLACE_ME\n", encoding="utf-8")
        assert run_placeholders({"DRY_RUN": "REPLACE_ME"}, tmp_path) == ["DRY_RUN"]

    def test_a_pipeline_variable_from_the_real_environment_is_refused(self, tmp_path):
        env = {"GATE_MODEL": f"vendor/{PLACEHOLDER_MARKER}"}
        assert run_placeholders(env, tmp_path) == ["GATE_MODEL"]

    def test_an_unrelated_tools_placeholder_never_wedges_the_gate(self, tmp_path):
        env = {"SOME_OTHER_TOOL_TOKEN": PLACEHOLDER_MARKER}
        assert run_placeholders(env, tmp_path) == []


class TestScopedToTheResolvedProvider:
    """A key the run's provider never reads cannot stop that run.

    `OPENROUTER_API_KEY` is read by `gate_runner.call_openrouter` alone. Under `opencode` - the
    runner's own default - an operator who correctly never obtained an OpenRouter key would
    otherwise get `cause=config` on every gate call with a remedy that is not theirs to apply.
    """

    def test_openrouters_key_is_ignored_when_the_run_uses_opencode(self, tmp_path):
        env = {"OPENROUTER_API_KEY": f"sk-or-v1-{PLACEHOLDER_MARKER}"}
        assert run_placeholders(env, tmp_path, provider="opencode") == []

    def test_openrouters_key_is_refused_when_the_run_uses_openrouter(self, tmp_path):
        env = {"OPENROUTER_API_KEY": f"sk-or-v1-{PLACEHOLDER_MARKER}"}
        assert run_placeholders(env, tmp_path, provider="openrouter") == [
            "OPENROUTER_API_KEY"
        ]

    @pytest.mark.parametrize("provider", ["opencode", "openrouter"])
    def test_a_provider_independent_variable_is_refused_under_both(
        self, tmp_path, provider
    ):
        env = {"GATE_MODEL": f"vendor/{PLACEHOLDER_MARKER}"}
        assert run_placeholders(env, tmp_path, provider=provider) == ["GATE_MODEL"]

    @pytest.mark.parametrize("provider", ["opencode", "openrouter"])
    def test_the_projects_own_env_is_in_scope_under_both(self, tmp_path, provider):
        """The operator wrote that file; every key in it is theirs to fill in."""
        (tmp_path / ".env").write_text(
            f"OPENROUTER_API_KEY={PLACEHOLDER_MARKER}\n", encoding="utf-8"
        )
        env = {"OPENROUTER_API_KEY": PLACEHOLDER_MARKER}
        assert run_placeholders(env, tmp_path, provider=provider) == [
            "OPENROUTER_API_KEY"
        ]

    def test_the_real_environment_wins_over_the_file(self, tmp_path):
        (tmp_path / ".env").write_text(
            f"GATE_MODEL={PLACEHOLDER_MARKER}\n", encoding="utf-8"
        )
        assert (
            run_placeholders({"GATE_MODEL": "google/gemini-3.1-pro-preview"}, tmp_path)
            == []
        )
