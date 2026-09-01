"""Tests for the runtime scope statement - what a CLEAN result does NOT establish.

Issue #97: four guards reported CLEAN while missing something real, and three of the four had their
limitation written down in a module docstring. The rule that came out of it is one sentence, and its
second half is the whole point:

    A guard must either cover what it claims, or state at the point of use what it does not cover -
    in its RUNTIME OUTPUT, not only in its source.

So the dangerous direction here is a guard that goes quiet: an emission that stops happening leaves a
clean result reading exactly like total coverage, which is the state the issue found. The other
direction matters as much and is pinned too - an emission may NEVER move an exit code, because a
guard weakened by its own documentation is the one remedy the issue puts out of scope.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import guard_proof  # noqa: E402
import guard_scope  # noqa: E402

MODULE = ROOT / "scripts" / "guard_scope.py"


def check(root: Path):
    """`guard_proof scope`'s decision, as (code, findings).

    The inventory half lives in `guard_proof` because `guard_scope.py` is VENDORED and may not
    reach for `guards.toml`, which deliberately is not. Same rule, asked from the side that owns
    the inventory.
    """
    inventory = guard_proof.load(root / "scripts" / "guards.toml")
    findings = guard_proof.scope_findings(root, inventory)
    return (guard_scope.FINDINGS if findings else guard_scope.OK), findings


def write_inventory(root: Path, body: str) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    target = scripts / "guard_scope.toml"
    target.write_text(body, encoding="utf-8")
    return target


#: A guard body that genuinely reaches `guard_scope`, spelled the way every wired guard here does.
EMITTING = (
    "import guard_scope\n"
    'if __name__ == "__main__":\n'
    "    raise SystemExit(guard_scope.run(__file__, main))\n"
)

ONE_GUARD = """
[guard."thing.py"]
proves = "that the thing is fine."
limits = ["that the other thing is fine", "anything about a file it never read"]
"""


class TestStatement:
    def test_a_statement_names_what_it_proves_and_what_it_does_not(self, tmp_path):
        inventory = write_inventory(tmp_path, ONE_GUARD)

        found = guard_scope.load(inventory).statements["thing.py"]

        rendered = found.render()
        assert guard_scope.SCOPE_MARKER in rendered
        assert "that the thing is fine." in rendered
        assert "Does NOT establish" in rendered
        assert "anything about a file it never read" in rendered

    def test_it_renders_on_ONE_line_so_a_tail_and_a_grep_still_carry_it(self, tmp_path):
        inventory = write_inventory(tmp_path, ONE_GUARD)

        assert "\n" not in guard_scope.load(inventory).statements["thing.py"].render()

    def test_a_statement_with_no_limits_is_refused(self, tmp_path):
        """An empty `limits` claims total coverage - the assertion this file exists to stop."""
        inventory = write_inventory(
            tmp_path, '[guard."thing.py"]\nproves = "everything."\nlimits = []\n'
        )

        with pytest.raises(guard_scope.GuardScopeError, match="does NOT establish"):
            guard_scope.load(inventory)

    def test_an_entry_with_no_limits_key_at_all_is_refused(self, tmp_path):
        inventory = write_inventory(
            tmp_path, '[guard."thing.py"]\nproves = "everything."\n'
        )

        with pytest.raises(guard_scope.GuardScopeError):
            guard_scope.load(inventory)

    def test_an_unknown_key_is_refused_rather_than_ignored(self, tmp_path):
        """A misspelled key would be a statement that silently says less than its author wrote."""
        inventory = write_inventory(
            tmp_path,
            '[guard."thing.py"]\nproves = "x."\nlimits = ["y"]\nlimit = ["typo"]\n',
        )

        with pytest.raises(guard_scope.GuardScopeError, match="closed"):
            guard_scope.load(inventory)


class TestEmission:
    def test_a_clean_result_emits_the_statement(self, capsys):
        code = guard_scope.clean("interface_drift.py", 0)

        captured = capsys.readouterr()
        assert code == 0
        assert guard_scope.SCOPE_MARKER in captured.err
        assert "order of operations" in captured.err

    def test_a_failing_result_does_not(self):
        """A failing guard already tells the reader what it found; it is the clean one that is over-read."""

        class Stream:
            def __init__(self):
                self.text = ""

            def write(self, chunk):
                self.text += chunk

        stream = Stream()
        guard_scope.clean("interface_drift.py", 1, stream=stream)

        assert stream.text == ""

    def test_the_exit_code_is_returned_unchanged_on_every_path(self, capsys):
        for code in (0, 1, 2, 3):
            assert guard_scope.clean("interface_drift.py", code) == code
        capsys.readouterr()

    def test_an_unknown_guard_says_the_declaration_is_MISSING(self, capsys):
        """Silence would read as 'no limits', which is the defect one layer down."""
        guard_scope.clean("no_such_guard.py", 0)

        err = capsys.readouterr().err
        assert guard_scope.UNAVAILABLE in err
        assert "MISSING declaration" in err
        assert "not a claim of full coverage" in err

    def test_an_emission_that_blows_up_never_becomes_a_verdict(
        self, monkeypatch, capsys
    ):
        """Patched on the function `clean()` ACTUALLY calls, or the handler is never reached."""

        def explode(_name, root=None):
            raise RuntimeError("inventory on fire")

        monkeypatch.setattr(guard_scope, "resolve", explode)

        assert guard_scope.clean("interface_drift.py", 0) == 0
        assert guard_scope.SCOPE_MARKER not in capsys.readouterr().err


class TestRun:
    def test_a_main_that_RETURNS_its_code_emits(self, capsys):
        assert guard_scope.run("interface_drift.py", lambda: 0) == 0
        assert guard_scope.SCOPE_MARKER in capsys.readouterr().err

    def test_a_main_that_calls_sys_exit_emits_too(self, capsys):
        """gate_runner.py exits inline at every one of its outcomes and would otherwise look wired."""

        def entry():
            raise SystemExit(0)

        assert guard_scope.run("gate_runner.py", entry) == 0
        assert guard_scope.SCOPE_MARKER in capsys.readouterr().err

    def test_a_main_that_returns_None_is_a_clean_result(self, capsys):
        assert guard_scope.run("gate_runner.py", lambda: None) == 0
        assert guard_scope.SCOPE_MARKER in capsys.readouterr().err

    def test_a_failing_main_keeps_its_code_and_stays_quiet(self, capsys):
        def entry():
            raise SystemExit(2)

        assert guard_scope.run("gate_runner.py", entry) == 2
        assert guard_scope.SCOPE_MARKER not in capsys.readouterr().err


class TestCheck:
    def test_every_declared_guard_with_runtime_output_declares_and_emits(self):
        """The two ends held together in this repository, which is the only place they both exist."""
        code, findings = check(ROOT)

        assert code == guard_scope.OK, findings

    def synthetic(self, root: Path, *, body: str, statement: bool) -> Path:
        """A tree with one declared guard, so `check`'s findings can be driven directly."""
        scripts = root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "sync_opencode.py").write_text("", encoding="utf-8")
        (scripts / "thing.py").write_text(body, encoding="utf-8")
        (scripts / "guards.toml").write_text(
            "[[guard]]\n"
            'id = "thing.one"\n'
            'implements = "scripts/thing.py"\n'
            'defect = "it stopped guarding"\n'
            'tests = ["tests/test_thing.py"]\n'
            "[[guard.mutation]]\n"
            'file = "scripts/thing.py"\n'
            'find = "a"\n'
            'replace = "b"\n',
            encoding="utf-8",
        )
        declaration = (
            '[guard."thing.py"]\nproves = "that the thing is fine."\nlimits = ["not much else"]\n'
            if statement
            else ""
        )
        (scripts / "guard_scope.toml").write_text(declaration, encoding="utf-8")
        return root

    def test_a_guard_with_runtime_output_and_no_statement_is_a_finding(self, tmp_path):
        root = self.synthetic(
            tmp_path, body='if __name__ == "__main__":\n    pass\n', statement=False
        )

        code, findings = check(root)

        assert code == guard_scope.FINDINGS
        assert any("declares no scope statement" in line for line in findings)

    def test_a_guard_that_declares_a_statement_and_never_emits_it_is_a_finding(
        self, tmp_path
    ):
        """A limitation only the inventory carries is invisible to a later stage, which reads output."""
        root = self.synthetic(
            tmp_path, body='if __name__ == "__main__":\n    pass\n', statement=True
        )

        code, findings = check(root)

        assert code == guard_scope.FINDINGS
        assert any("never emits it" in line for line in findings)

    def test_a_guard_that_declares_and_emits_is_clean(self, tmp_path):
        root = self.synthetic(tmp_path, body=EMITTING, statement=True)

        assert check(root) == (guard_scope.OK, [])

    def test_a_guard_that_only_MENTIONS_the_module_is_not_emitting(self, tmp_path):
        """A docstring naming this module reaches no reader. `mutation_scope.py` was exactly this.

        The check that was supposed to catch it asked `"guard_scope" in text`, so the mention
        satisfied it and the pipeline's own flagship guard false-cleaned - issue #97's class inside
        issue #97's fix. The extraction layer has to follow what it claims to check.
        """
        root = self.synthetic(
            tmp_path,
            body=(
                '"""A guard whose limits are stated in scripts/guard_scope.py and nowhere a\n'
                'later stage can read them."""\n'
                "# see guard_scope for the inventory\n"
                'if __name__ == "__main__":\n    raise SystemExit(main())\n'
            ),
            statement=True,
        )

        code, findings = check(root)

        assert code == guard_scope.FINDINGS
        assert any("never emits it" in line for line in findings)

    def test_an_unused_import_is_not_emitting_either(self, tmp_path):
        root = self.synthetic(
            tmp_path,
            body=(
                "import guard_scope  # noqa: F401\n"
                'if __name__ == "__main__":\n    raise SystemExit(main())\n'
            ),
            statement=True,
        )

        assert check(root)[0] == guard_scope.FINDINGS

    def test_a_real_call_counts_however_the_import_is_spelled(self):
        """Renaming an import must not defeat the check, the way an alias would defeat a substring."""
        for body in (
            "import guard_scope\nguard_scope.run(__file__, main)\n",
            "import guard_scope as gs\ngs.clean(__file__, 0)\n",
            "from guard_scope import run\nrun(__file__, main)\n",
            "from guard_scope import clean as done\ndone(__file__, 0)\n",
        ):
            assert guard_scope.emits(body), body

    def test_a_call_that_only_RETURNS_the_text_is_not_emitting(self):
        """`statement()` and `notice()` hand back a string; a caller may print none of it.

        Accepting them was the same mention-is-not-an-emission defect one notch narrower - a guard
        that never writes a line reading as one that does.
        """
        for call in ("guard_scope.statement(", "guard_scope.notice("):
            body = f'import guard_scope\nx = {call}"thing.py")\n'
            assert not guard_scope.emits(body), call

    def test_the_call_must_be_a_CALL_and_not_a_mention(self):
        """The defect that false-cleaned `guard_scope.py` itself, on its own list of spellings.

        Its only matching lines were a docstring and the tuple the search was made of, so the module
        defining "a mention is not an emission" satisfied itself by mentioning.
        """
        for body in (
            '"""Wired with guard_scope.run(__file__, main) one day."""\nimport guard_scope\n',
            "import guard_scope\n# guard_scope.run(__file__, main)\n",
            'import guard_scope\nSPELLINGS = ("guard_scope.run(", "guard_scope.clean(")\n',
        ):
            assert not guard_scope.emits(body), body

    def test_python_source_that_will_not_parse_is_UNDECIDABLE_not_clean(self):
        """Read as 'does not emit' it prescribes wiring; read as emitting it is a false clean."""
        with pytest.raises(guard_scope.GuardScopeError, match="UNDECIDABLE"):
            guard_scope.emits("def broken(:\n")

    def test_a_shell_guard_is_decided_on_its_UNCOMMENTED_lines(self):
        """bash has no AST here, so the one thing a scanner can say is that a comment runs nothing."""
        assert guard_scope.emits(
            'python3 "$SD/guard_scope.py" emit hook_thing.sh >/dev/null || true\n',
            shell=True,
        )
        assert not guard_scope.emits(
            '# python3 "$SD/guard_scope.py" emit hook_thing.sh\n', shell=True
        )

    def test_a_shell_guard_is_not_decided_as_python(self):
        """A `.sh` body is not parseable source; asking for one as Python would raise, not decide."""
        body = 'python3 "$SD/guard_scope.py" emit hook_thing.sh\nif [ -z "$x" ]; then :; fi\n'

        assert guard_scope.emits(body, shell=True)
        with pytest.raises(guard_scope.GuardScopeError):
            guard_scope.emits(body)

    def test_a_guard_that_cannot_be_read_is_a_finding_not_a_silent_pass(self, tmp_path):
        root = self.synthetic(
            tmp_path, body='if __name__ == "__main__"\n    pass\n', statement=True
        )

        code, findings = check(root)

        assert code == guard_scope.FINDINGS
        assert any("cannot be read to decide" in line for line in findings)

    def test_a_declared_exception_is_not_a_finding_but_is_NAMED(self, tmp_path, capsys):
        """An exception recorded only in a data file is the invisibility this issue is about."""
        root = self.synthetic(
            tmp_path,
            body='if __name__ == "__main__":\n    raise SystemExit(main())\n',
            statement=True,
        )
        (root / "scripts" / "sync_opencode.py").write_text("", encoding="utf-8")
        inventory = guard_proof.load(root / "scripts" / "guards.toml")

        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(
                guard_proof.EMISSION_EXCEPTIONS,
                "scripts/thing.py",
                "it answers on the deny only",
            )
            assert guard_proof.scope_findings(root, inventory) == []
            assert guard_proof._do_scope(root, inventory) == guard_scope.OK

        err = capsys.readouterr().err
        assert "DECLARED EXCEPTION" in err
        assert "scripts/thing.py" in err
        assert "it answers on the deny only" in err
        assert "does not mean every guard emits" in err

    def test_an_exception_whose_guard_now_emits_is_a_finding(self, tmp_path):
        """A recorded exception nothing needs is one nobody has re-read."""
        root = self.synthetic(tmp_path, body=EMITTING, statement=True)

        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(
                guard_proof.EMISSION_EXCEPTIONS, "scripts/thing.py", "stale reason"
            )
            code, findings = check(root)

        assert code == guard_scope.FINDINGS
        assert any("emits after all" in line for line in findings)

    def test_a_statement_no_guard_emits_is_a_finding(self, tmp_path):
        """A statement nothing emits is not a statement; it is the same invisibility one level up."""
        root = self.synthetic(tmp_path, body=EMITTING, statement=True)
        (root / "scripts" / "guard_scope.toml").write_text(
            '[guard."thing.py"]\nproves = "x."\nlimits = ["y"]\n'
            '[guard."ghost.py"]\nproves = "x."\nlimits = ["y"]\n',
            encoding="utf-8",
        )

        code, findings = check(root)

        assert code == guard_scope.FINDINGS
        assert any("ghost.py" in line for line in findings)

    def test_the_obligated_set_is_the_guards_that_actually_print(self):
        duty = guard_proof.obligated(
            ROOT, guard_proof.load(ROOT / "scripts" / "guards.toml")
        )

        assert "scripts/interface_drift.py" in duty
        assert "scripts/hook_mutation.sh" in duty
        assert "scripts/proc_group.py" not in duty, (
            "a library module prints no result of its own"
        )
        assert not any(name.startswith("tests/") for name in duty)


@pytest.mark.subprocess(
    "the subject is a guard's real process output, which only a process has"
)
class TestRealGuards:
    def guard_run(self, tmp_path: Path) -> subprocess.CompletedProcess:
        """A real guard, run to a genuinely clean verdict over a tree of its own."""
        (tmp_path / "app.py").write_text(
            "def poll_once():\n    return 1\n", encoding="utf-8"
        )
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "interface_drift.py"),
                "--root",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )

    def test_a_real_guard_prints_its_scope_statement_on_a_clean_run(self, tmp_path):
        proc = self.guard_run(tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert guard_scope.SCOPE_MARKER in proc.stderr

    def test_the_statement_goes_to_stderr_so_machine_readable_stdout_is_untouched(
        self, tmp_path
    ):
        assert guard_scope.SCOPE_MARKER not in self.guard_run(tmp_path).stdout

    def test_emit_always_exits_zero_so_a_shell_guard_cannot_be_failed_by_it(self):
        for name in ("hook_mutation.sh", "not-a-guard-at-all.sh"):
            proc = subprocess.run(
                [sys.executable, str(MODULE), "emit", name],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0
            assert guard_scope.SCOPE_MARKER in proc.stderr


class TestAllowlists:
    def test_every_declared_allowlist_matches_something(self):
        inventory = guard_scope.load(guard_scope.inventory_path(ROOT / "scripts"))

        for entry in inventory.allowlists:
            assert entry.paths(ROOT), f"{entry.id} matches no file"

    def test_growth_is_reported_as_a_signal(self, tmp_path):
        inventory = guard_scope.load(
            write_inventory(
                tmp_path,
                ONE_GUARD
                + '\n[[allowlist]]\nid = "x"\nfile = "a.txt"\nmarker = "ALLOW"\n'
                'guard = "thing.py"\nwhat = "things waved through"\n',
            )
        )

        lines = guard_scope._render_growth(inventory, {"x": (2, 11)}, "main")

        assert "ALLOWLIST GREW" in lines[0]
        assert "+9" in lines[0]
        assert "extraction layer" in lines[0]

    def test_no_growth_is_reported_too_rather_than_going_silent(self, tmp_path):
        inventory = guard_scope.load(
            write_inventory(
                tmp_path,
                ONE_GUARD
                + '\n[[allowlist]]\nid = "x"\nfile = "a.txt"\nmarker = "ALLOW"\n'
                'guard = "thing.py"\nwhat = "things waved through"\n',
            )
        )

        lines = guard_scope._render_growth(inventory, {"x": (4, 4)}, "main")

        assert "no growth" in lines[0]

    def test_an_unknowable_comparison_says_so_and_never_reports_no_growth(
        self, tmp_path
    ):
        inventory = guard_scope.load(write_inventory(tmp_path, ONE_GUARD))

        lines = guard_scope._render_growth(inventory, None, "main")

        assert "UNKNOWABLE" in lines[0]
        assert "no growth" not in lines[0]

    def test_an_excluded_path_is_not_counted(self, tmp_path):
        """The reported number has to mean what the entry's `what` says, or it buys no attention."""
        (tmp_path / "real.py").write_text("MARK\nMARK\n", encoding="utf-8")
        (tmp_path / "fixtures.py").write_text("MARK\nMARK\nMARK\n", encoding="utf-8")
        inventory = guard_scope.load(
            write_inventory(
                tmp_path,
                ONE_GUARD
                + '\n[[allowlist]]\nid = "x"\nglob = "*.py"\nmarker = "MARK"\n'
                'exclude = ["fixtures.py"]\n'
                'guard = "thing.py"\nwhat = "things waved through"\n',
            )
        )

        assert guard_scope.sizes(tmp_path, inventory) == {"x": 2}

    def test_an_exclusion_that_is_not_a_list_of_globs_is_refused(self, tmp_path):
        """A silently narrowed count is worse than a wide one: it under-reports the thing watched."""
        body = (
            ONE_GUARD + '\n[[allowlist]]\nid = "x"\nglob = "*.py"\nmarker = "MARK"\n'
            'exclude = "fixtures.py"\nguard = "thing.py"\nwhat = "waved through"\n'
        )

        with pytest.raises(guard_scope.GuardScopeError, match="exclude"):
            guard_scope.load(write_inventory(tmp_path, body))

    def test_the_subprocess_allowlist_skips_the_guards_own_fixture_corpus(self):
        """Those markers are source under test, not declarations, and editing them is not growth."""
        inventory = guard_scope.load(guard_scope.inventory_path(ROOT / "scripts"))
        entry = next(
            item
            for item in inventory.allowlists
            if item.id == "subprocess-declarations"
        )

        counted = {path.name for path in entry.paths(ROOT)}
        assert "test_subprocess_check.py" not in counted
        assert "test_phase_artifacts.py" in counted

    def test_ci_supplies_a_comparison_base_so_the_delta_is_actually_computed(self):
        """A signal that never fires is the same defect class as a guard that never runs.

        `gate_ci.sh` calls `allowlists` with `${GUARD_SCOPE_BASE:+--base ...}`, so with nothing
        supplying that variable every CI run printed absolute sizes and no growth. The workflow is
        the machine-consumed artifact that supplies it; this parses it and asserts the meaning.
        """
        yaml = pytest.importorskip("yaml")
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "pipeline-gates.yml").read_text(
                encoding="utf-8"
            )
        )
        step = next(
            step
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if "gate_ci.sh" in str(step.get("run", ""))
        )

        assert step["env"]["GUARD_SCOPE_BASE"] == step["env"]["MUTATION_BASE"], (
            "the growth base follows the mutation base, from the same source"
        )

    @pytest.mark.subprocess(
        "git is the authority for what a file list looked like at a ref; a stubbed git would "
        "only test the stub"
    )
    def test_a_rename_is_not_reported_as_growth(self, tmp_path):
        """`before` built from TODAY's file list counted a renamed file as N brand-new entries.

        A false GREW line is noise in exactly the signal this exists to buy attention for, so the
        base side is enumerated from the merge-base tree instead.
        """
        root = tmp_path / "repo"
        (root / "tests").mkdir(parents=True)
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=str(root), check=True, capture_output=True
        )
        run("init", "-q", "-b", "main")
        run("config", "user.email", "p@example.com")
        run("config", "user.name", "p")
        (root / "tests" / "test_old.py").write_text("MARK\nMARK\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        (root / "tests" / "test_old.py").unlink()
        (root / "tests" / "test_new.py").write_text("MARK\nMARK\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", "rename")
        inventory = guard_scope.load(
            write_inventory(
                root,
                ONE_GUARD
                + '\n[[allowlist]]\nid = "x"\nglob = "tests/**/*.py"\nmarker = "MARK"\n'
                'guard = "thing.py"\nwhat = "things waved through"\n',
            )
        )
        run("add", "-A")
        run("commit", "-q", "-m", "inventory")

        measured = guard_scope.growth(root, inventory, "HEAD~2")

        assert measured is not None
        assert measured["x"] == (2, 2)

    @pytest.mark.subprocess(
        "git is the authority for what a file list looked like at a ref; a stubbed git would "
        "only test the stub"
    )
    def test_a_deletion_reads_as_a_shrink_rather_than_no_growth(self, tmp_path):
        root = tmp_path / "repo"
        (root / "tests").mkdir(parents=True)
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=str(root), check=True, capture_output=True
        )
        run("init", "-q", "-b", "main")
        run("config", "user.email", "p@example.com")
        run("config", "user.name", "p")
        (root / "tests" / "test_gone.py").write_text("MARK\nMARK\n", encoding="utf-8")
        write_inventory(
            root,
            ONE_GUARD
            + '\n[[allowlist]]\nid = "x"\nglob = "tests/**/*.py"\nmarker = "MARK"\n'
            'guard = "thing.py"\nwhat = "things waved through"\n',
        )
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        inventory = guard_scope.load(guard_scope.inventory_path(root / "scripts"))
        (root / "tests" / "test_gone.py").unlink()
        run("add", "-A")
        run("commit", "-q", "-m", "delete")

        measured = guard_scope.growth(root, inventory, "HEAD~1")

        assert measured is not None
        assert measured["x"] == (2, 0)

    def test_it_never_gates(self):
        """Growth is a REPORT. A blocking check would be answered with a bypass, not attention."""
        assert guard_scope.main(["allowlists", "--root", str(ROOT)]) == guard_scope.OK
