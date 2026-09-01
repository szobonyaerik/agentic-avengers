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
from fnmatch import fnmatch
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import env_assemble  # noqa: E402
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

#: The template `/pipeline-init` copies, read as the shipped artifact it is: what an operator's own
#: `.env` is actually merged against, and the reason source order decides real values.
SHIPPED_TEMPLATE = (REPO / "docs" / "templates" / "env.example").read_text(
    encoding="utf-8"
)


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

    def test_the_failure_names_the_key_and_never_the_value(self, tmp_path, monkeypatch):
        """On the merge-in-place path the live `.env` is one of its own sources, so the expectation
        holds the operator's real credentials. This message is raised verbatim and printed to
        stderr - and into the transcript of the agent that ran `/pipeline-init` - so a value
        interpolated here is a credential leak on the one path this module documents."""
        secret = "sk-or-v1-THE-OPERATORS-REAL-KEY"
        live = tmp_path / ".env"
        live.write_text(f"OPENROUTER_API_KEY={secret}\n", encoding="utf-8")
        template = tmp_path / ".env.pipeline.example"
        template.write_text("GATE_MODEL=x", encoding="utf-8")
        monkeypatch.setattr("env_assemble.join_text", lambda texts: "".join(texts))

        with pytest.raises(AssemblyError) as exc:
            assemble([template, live], live, force=True)

        message = str(exc.value)
        assert "OPENROUTER_API_KEY" in message
        assert secret not in message
        for fragment in (secret[:12], secret[-12:], "THE-OPERATORS"):
            assert fragment not in message
        assert live.read_text(encoding="utf-8") == f"OPENROUTER_API_KEY={secret}\n"

    def test_a_missing_source_is_named_and_nothing_is_written(self, tmp_path):
        out = tmp_path / ".env"
        with pytest.raises(AssemblyError) as exc:
            assemble([tmp_path / "absent.example"], out)
        assert "absent.example" in str(exc.value)
        assert not out.exists()

    def test_an_unwritable_output_names_its_cause_rather_than_raising_oserror(
        self, tmp_path
    ):
        """Every other failure here is a named AssemblyError printed as `env-assemble: <cause>`.
        An unguarded write made the one step whose purpose is a legible config failure the one
        that answered with a Python stack trace."""
        a, b = write_sources(tmp_path)
        out = tmp_path / "absent-dir" / ".env"

        with pytest.raises(AssemblyError) as exc:
            assemble([a, b], out)

        assert str(out) in str(exc.value)
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


class TestMergingIntoALiveEnv:
    """The non-greenfield path: a worktree that already has a `.env`.

    `assemble` refuses an existing output without `--force`, so the only way the pipeline's keys
    reach a configured worktree is to name the live file as a source of its own. It goes LAST,
    because the last source to declare a key wins: that is what keeps the operator's real values
    and still adds every pipeline key they lack. It is safe because every source is read before
    anything is written, and the read-back then proves the live file's own keys survived - which
    is the property the whole module exists for.
    """

    def write_live(self, tmp_path, text):
        live = tmp_path / ".env"
        live.write_text(text, encoding="utf-8")
        return live

    def shipped_template(self, tmp_path):
        """The REAL `docs/templates/env.example`, not a hand-written minimal stand-in.

        The template is the artifact `/pipeline-init` actually copies, and it is what makes the
        ordering matter: it declares `OPENROUTER_API_KEY`, `GATE_MODEL`, `GATE_PROVIDER`,
        `AUTHOR_FAMILY` and `MUTATION_POLICY` UNCOMMENTED. A minimal fixture that overlaps the live
        `.env` in no key cannot observe which source won, so it can never catch the defect this
        pins - the same reason `tests/test_mutation_target.py` reads the shipped `cosmic-ray.toml`.
        """
        template = tmp_path / ".env.pipeline.example"
        template.write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        return template

    def test_the_live_credentials_survive_and_the_pipeline_keys_arrive(self, tmp_path):
        live = self.write_live(
            tmp_path,
            "OPENROUTER_API_KEY=sk-or-v1-real\n"
            "GATE_MODEL=deepseek/deepseek-chat\n"
            "MUTATION_POLICY=enforce\n"
            "DRY_RUN=true\n",
        )
        template = self.shipped_template(tmp_path)

        assemble([template, live], live, force=True)

        merged = parse(live.read_text())
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-real"
        assert merged["GATE_MODEL"] == "deepseek/deepseek-chat"
        assert merged["MUTATION_POLICY"] == "enforce"
        assert merged["DRY_RUN"] == "true"
        # ...and a pipeline key the live file never had is still added by the template.
        assert merged["AUTHOR_FAMILY"] == "anthropic"

    def test_the_wrong_order_is_what_loses_the_operators_values(self, tmp_path):
        """The defect this ordering exists to stop, kept reproducible rather than only asserted
        away: with the live file FIRST the template wins every key they share, so a real API key
        becomes the placeholder and a chosen model reverts to the template's default."""
        live = self.write_live(
            tmp_path,
            "OPENROUTER_API_KEY=sk-or-v1-real\nGATE_MODEL=deepseek/deepseek-chat\n",
        )
        template = self.shipped_template(tmp_path)

        assemble([live, template], live, force=True)

        merged = parse(live.read_text())
        assert PLACEHOLDER_MARKER in merged["OPENROUTER_API_KEY"]
        assert merged["GATE_MODEL"] == "google/gemini-3.1-pro-preview"

    def test_a_live_env_with_no_trailing_newline_still_merges_intact(self, tmp_path):
        """The measured defect, on the path that reads the live file as a source."""
        live = self.write_live(tmp_path, "API_KEY=abc\nDRY_RUN=true")
        template = self.shipped_template(tmp_path)

        assemble([template, live], live, force=True)

        merged = parse(live.read_text())
        assert merged["DRY_RUN"] == "true"
        assert merged["API_KEY"] == "abc"
        assert merged["GATE_PROVIDER"] == "openrouter"


class TestTheWriteIsAtomic:
    """On the merge path the destination IS the live `.env` - the operator's real credentials, a
    gitignored file with no backup - and it is also one of its own sources. A `write_text` there
    opens it in truncate mode, so a failure between the truncate and the completed write destroys
    a file that can no longer be re-merged, because one of the sources WAS it. The bytes therefore
    go to a temp file beside the destination and are moved over it with `os.replace`.
    """

    def live_env(self, tmp_path):
        live = tmp_path / ".env"
        live.write_text(
            "OPENROUTER_API_KEY=sk-or-v1-real\nGATE_MODEL=deepseek/deepseek-chat\n",
            encoding="utf-8",
        )
        return live

    def template(self, tmp_path):
        template = tmp_path / ".env.pipeline.example"
        template.write_text(SHIPPED_TEMPLATE, encoding="utf-8")
        return template

    def test_a_failure_during_the_write_leaves_the_live_env_exactly_as_it_was(
        self, tmp_path, monkeypatch
    ):
        """`write_text` would have truncated it first and left the operator with a partial file
        and no error, which is why this asserts BOTH the loud failure and the intact bytes."""
        live = self.live_env(tmp_path)
        template = self.template(tmp_path)
        before = live.read_bytes()

        def boom(_fd):
            raise OSError("no space left on device")

        monkeypatch.setattr(env_assemble.os, "fsync", boom)

        with pytest.raises(AssemblyError) as caught:
            assemble([template, live], live, force=True)

        assert "no space left on device" in str(caught.value)
        assert live.read_bytes() == before

    @pytest.mark.parametrize(
        "interruption",
        [
            pytest.param(OSError("no space left on device"), id="enospc"),
            pytest.param(KeyboardInterrupt(), id="ctrl-c"),
            pytest.param(SystemExit(1), id="sigterm-handler"),
        ],
    )
    def test_no_temp_file_is_left_behind_by_an_interrupted_write(
        self, tmp_path, monkeypatch, interruption
    ):
        """Ctrl-C and a hook-budget SIGTERM are the exact scenario the atomic write exists for, and
        both are `BaseException` - cleaned up only in `except OSError`, the temp file survived, and
        it holds the FULLY MERGED result including the live `.env`'s own credentials."""
        live = self.live_env(tmp_path)
        template = self.template(tmp_path)
        before = live.read_bytes()

        def boom(_fd):
            raise interruption

        monkeypatch.setattr(env_assemble.os, "fsync", boom)

        with pytest.raises((AssemblyError, KeyboardInterrupt, SystemExit)):
            assemble([template, live], live, force=True)

        assert sorted(q.name for q in tmp_path.iterdir()) == [
            ".env",
            ".env.pipeline.example",
        ]
        assert live.read_bytes() == before

    @pytest.mark.subprocess(
        "git is the only authority on what its own ignore rules match"
    )
    def test_the_temp_name_is_ignored_by_this_repositorys_own_gitignore(self, tmp_path):
        """Asked of git itself, not of the file's text: `.gitignore` is a machine-consumed artifact
        and git is its only authority. The temp file holds the merged credentials, an interruption
        nothing can catch leaves it behind, and `git add -A` is what the pipeline's own commit steps
        run - the name built from the destination (`..env.<rand>.tmp`) matched no pattern at all.
        """
        toplevel = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if toplevel.returncode != 0 or Path(toplevel.stdout.strip()) != REPO:
            pytest.skip(
                "not this repository's git work tree, so its ignore rules cannot be asked"
            )

        leftover = env_assemble.TEMP_GITIGNORE_PATTERN.replace("*", "probe")

        proc = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "-q", leftover],
            capture_output=True,
        )

        assert proc.returncode == 0, (
            f"{leftover} is not ignored - a leftover temp file holding live credentials would be "
            f"reported untracked and committed by `git add -A`"
        )

    def test_the_declared_pattern_matches_the_name_the_writer_actually_produces(
        self, tmp_path, monkeypatch
    ):
        """The other half: the gitignore test above asks git about the DECLARED pattern, so that
        pattern has to be the one the writer's real temp names fall under. Otherwise the two copies
        on disk could be held to a declaration the code had drifted away from."""
        seen = []
        real_mkstemp = env_assemble.tempfile.mkstemp

        def record(*args, **kwargs):
            handle, name = real_mkstemp(*args, **kwargs)
            seen.append(Path(name).name)
            return handle, name

        monkeypatch.setattr(env_assemble.tempfile, "mkstemp", record)

        assemble([self.template(tmp_path)], tmp_path / ".env")

        assert seen
        for name in seen:
            assert fnmatch(name, env_assemble.TEMP_GITIGNORE_PATTERN)

    @pytest.mark.subprocess(
        "git is the only authority on what its own ignore rules match"
    )
    def test_the_declared_pattern_alone_is_enough_for_a_project_that_adopts_it(
        self, tmp_path, monkeypatch
    ):
        """What every install path is told to add must be SUFFICIENT on its own.

        A consumer repository gets no `.gitignore` from `install.sh` and reaches no
        `/pipeline-init` step-2 list on the opencode route, so the one line the docs hand it is all
        it has. A leftover temp file holds the fully merged result - on the merge path, live
        credentials - and SIGKILL or power loss cannot be caught, so `git add -A` is what commits
        them if that line does not match.
        """
        project = tmp_path / "consumer"
        project.mkdir()
        assert (
            subprocess.run(
                ["git", "-C", str(project), "init", "-q"], capture_output=True
            ).returncode
            == 0
        )
        (project / ".gitignore").write_text(
            f".env\n{env_assemble.TEMP_GITIGNORE_PATTERN}\n", encoding="utf-8"
        )

        leftovers = []
        real_mkstemp = env_assemble.tempfile.mkstemp

        def record(*args, **kwargs):
            handle, name = real_mkstemp(*args, **kwargs)
            leftovers.append(name)
            return handle, name

        monkeypatch.setattr(env_assemble.tempfile, "mkstemp", record)
        assemble([self.template(project)], project / ".env")
        assert leftovers

        for name in leftovers:
            # The writer removed it; recreate it exactly where an uncatchable kill would have.
            Path(name).write_text(
                "OPENROUTER_API_KEY=sk-or-v1-real\n", encoding="utf-8"
            )

        untracked = subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
        )

        assert untracked.returncode == 0, untracked.stderr
        assert env_assemble.TEMP_PREFIX not in untracked.stdout, (
            "a leftover temp file holding live credentials is untracked, so `git add -A` "
            "would commit it"
        )

    def test_a_symlinked_destination_is_written_through_rather_than_replaced(
        self, tmp_path
    ):
        """A worktree whose `.env` links to a shared canonical file - the pattern this repository's
        own multi-worktree setup uses - must not be silently detached from it.

        `os.replace` replaces the LINK where the `write_text` this took over from followed it, so
        the documented merge reported success while turning the link into a private regular file:
        the shared file kept its old contents, later edits there stopped applying, and the merged
        credentials now lived in a second copy with nothing saying so.
        """
        shared = tmp_path / "shared"
        shared.mkdir()
        canonical = shared / "env"
        canonical.write_text("OPENROUTER_API_KEY=sk-or-v1-real\n", encoding="utf-8")

        worktree = tmp_path / "wt"
        worktree.mkdir()
        live = worktree / ".env"
        live.symlink_to(Path("..") / "shared" / "env")
        template = self.template(worktree)

        assemble([template, live], live, force=True)

        assert live.is_symlink()
        assert live.resolve() == canonical.resolve()
        merged = parse(canonical.read_text())
        assert merged["OPENROUTER_API_KEY"] == "sk-or-v1-real"
        assert merged["GATE_MODEL"] == parse(SHIPPED_TEMPLATE)["GATE_MODEL"]
        assert sorted(q.name for q in worktree.iterdir()) == [
            ".env",
            ".env.pipeline.example",
        ]

    def test_a_symlinked_destinations_mode_is_taken_from_the_file_it_points_at(
        self, tmp_path
    ):
        """`stat()` follows a link too, so the widening this guards against reappears one
        indirection out unless the mode is read from and applied to the resolved file."""
        shared = tmp_path / "shared"
        shared.mkdir()
        canonical = shared / "env"
        canonical.write_text("OPENROUTER_API_KEY=sk-or-v1-real\n", encoding="utf-8")
        os.chmod(canonical, 0o600)

        worktree = tmp_path / "wt"
        worktree.mkdir()
        live = worktree / ".env"
        live.symlink_to(canonical)

        assemble([self.template(worktree), live], live, force=True)

        assert canonical.stat().st_mode & 0o777 == 0o600

    def test_the_destination_is_replaced_rather_than_opened_for_truncation(
        self, tmp_path
    ):
        """The structural tell: `os.replace` installs a new inode, `write_text` keeps the old one
        and empties it first. There is no window in which the live file is short."""
        live = self.live_env(tmp_path)
        template = self.template(tmp_path)
        before = live.stat().st_ino

        assemble([template, live], live, force=True)

        assert live.stat().st_ino != before
        assert parse(live.read_text())["OPENROUTER_API_KEY"] == "sk-or-v1-real"

    def test_an_existing_files_mode_survives_the_replace(self, tmp_path):
        """A `.env` is routinely chmod 600; a fresh temp file is not, and a replace that carried
        the temp file's mode would silently widen the credentials it holds."""
        live = self.live_env(tmp_path)
        template = self.template(tmp_path)
        os.chmod(live, 0o600)

        assemble([template, live], live, force=True)

        assert live.stat().st_mode & 0o777 == 0o600


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

    def test_assemble_into_an_unwritable_path_exits_one_without_a_traceback(
        self, tmp_path
    ):
        write_sources(tmp_path)
        proc = self.run(
            "assemble",
            "--out",
            "absent-dir/.env",
            ".env.example",
            ".env.pipeline.example",
            cwd=tmp_path,
        )
        assert proc.returncode == 1
        assert proc.stderr.startswith("env-assemble:")
        assert "Traceback" not in proc.stderr

    def test_a_non_utf8_source_exits_one_without_a_traceback(self, tmp_path):
        """The merge-in-place form names the live `.env` as a source, and a live `.env` is
        hand-edited - one cp1252/latin-1 byte pasted into a comment or a password used to answer
        with a stack trace instead of a named cause."""
        write_sources(tmp_path)
        (tmp_path / ".env.pipeline.example").write_bytes(
            b"# caf\xe9 - a latin-1 byte\nGATE_MODEL=x\n"
        )

        proc = self.run(
            "assemble",
            "--out",
            ".env",
            ".env.example",
            ".env.pipeline.example",
            cwd=tmp_path,
        )

        assert proc.returncode == 1
        assert proc.stderr.startswith("env-assemble:")
        assert ".env.pipeline.example" in proc.stderr
        assert "Traceback" not in proc.stderr
        assert not (tmp_path / ".env").exists()

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

    def test_check_refuses_the_openrouter_key_from_the_environment(self, tmp_path):
        refused = self.run(
            "check",
            "--root",
            ".",
            cwd=tmp_path,
            OPENROUTER_API_KEY=f"sk-or-v1-{PLACEHOLDER_MARKER}",
        )
        assert refused.returncode == 1
        assert "OPENROUTER_API_KEY" in refused.stderr

    def test_check_ignores_a_bypass_reason_that_mentions_the_marker(self, tmp_path):
        proc = self.run(
            "check",
            "--root",
            ".",
            cwd=tmp_path,
            GATE_BYPASS=f"OPENROUTER_API_KEY still {PLACEHOLDER_MARKER} in CI, proceeding",
        )
        assert proc.returncode == 0, proc.stderr


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


class TestEveryPipelineVariableIsConsumedOnEveryProviderPath:
    """`OPENROUTER_API_KEY` is in scope whichever provider the run resolves to.

    It was briefly scoped to `openrouter` alone, on the premise that `gate_runner.call_openrouter`
    is its only reader. It is not: `proc_group.run_bounded` builds the `opencode` child with no
    `env=`, so that child inherits the variable verbatim, and exporting it is one of the two
    documented ways to authenticate opencode. A placeholder therefore reaches a run under the
    runner's own default provider, which is exactly what this refusal exists to stop.
    """

    def test_openrouters_key_is_refused_from_the_real_environment(self, tmp_path):
        env = {"OPENROUTER_API_KEY": f"sk-or-v1-{PLACEHOLDER_MARKER}"}
        assert run_placeholders(env, tmp_path) == ["OPENROUTER_API_KEY"]

    def test_the_projects_own_env_is_in_scope_too(self, tmp_path):
        """The operator wrote that file; every key in it is theirs to fill in."""
        (tmp_path / ".env").write_text(
            f"OPENROUTER_API_KEY={PLACEHOLDER_MARKER}\n", encoding="utf-8"
        )
        env = {"OPENROUTER_API_KEY": PLACEHOLDER_MARKER}
        assert run_placeholders(env, tmp_path) == ["OPENROUTER_API_KEY"]

    def test_the_real_environment_wins_over_the_file(self, tmp_path):
        (tmp_path / ".env").write_text(
            f"GATE_MODEL={PLACEHOLDER_MARKER}\n", encoding="utf-8"
        )
        assert (
            run_placeholders({"GATE_MODEL": "google/gemini-3.1-pro-preview"}, tmp_path)
            == []
        )


class TestProseValuedVariablesAreNotConfiguration:
    """`GATE_BYPASS` and `GATE_SAME_FAMILY_WAIVER` hold author-written prose, and both start with
    `GATE_`. A bypass reason legitimately NAMES the key it is bypassing, so a substring scan
    reported the reason itself as an unfilled config value - on the exact path an operator uses to
    break glass past this refusal."""

    def test_a_bypass_reason_mentioning_the_marker_is_not_a_placeholder(self, tmp_path):
        env = {
            "GATE_BYPASS": f"OPENROUTER_API_KEY still {PLACEHOLDER_MARKER} in CI, proceeding"
        }
        assert run_placeholders(env, tmp_path) == []

    def test_a_same_family_waiver_reason_is_not_a_placeholder(self, tmp_path):
        env = {
            "GATE_SAME_FAMILY_WAIVER": f"no other family reachable, {PLACEHOLDER_MARKER}"
        }
        assert run_placeholders(env, tmp_path) == []

    def test_a_real_gate_variable_beside_one_is_still_refused(self, tmp_path):
        env = {
            "GATE_BYPASS": f"proceeding despite {PLACEHOLDER_MARKER}",
            "GATE_MODEL": f"vendor/{PLACEHOLDER_MARKER}",
        }
        assert run_placeholders(env, tmp_path) == ["GATE_MODEL"]
