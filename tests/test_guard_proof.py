"""The guard-proof harness, proved the way it proves everything else.

Issue #69's rule applies to this file first: a harness that reports every guard `proven` because it
cannot tell a red run from a green one would be the exact defect it exists to find, one level up. So
each case here builds a small tree with a real guard in it, hands it to `guard_proof`, and checks the
verdict against what that tree actually deserves - including a guard deliberately built to be
worthless, which must come back `unproven` and must fail the run.

The trees are tiny on purpose. `guard_proof` runs pytest in a copy of whatever it is pointed at, so a
toy tree exercises the same code path as the repository does at a fraction of the wall clock.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import guard_proof  # noqa: E402
from guard_proof import (  # noqa: E402
    BASELINE_RED,
    ERRORED,
    FINDINGS,
    GuardProofError,
    OK,
    PROVEN,
    UNANCHORED,
    UNPROVEN,
    load,
    main,
)

pytestmark = pytest.mark.subprocess(
    "the subject IS the running of a test suite against a mutated copy of a tree; a stubbed runner "
    "would only ever prove the stub"
)

#: A guard: it refuses a latency below a floor. Small enough to read, real enough to break.
GUARD = """\
FLOOR = 250


def implausible(latency):
    if latency < FLOOR:
        return f"{latency} ms is below the {FLOOR} ms floor"
    return None
"""

#: Its proof: the test asserts the refusal, so neutering the guard must turn this red.
GUARDED_TEST = """\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from {module} import implausible


def test_a_value_below_the_floor_is_refused():
    assert implausible(4) is not None
"""

#: A test that exercises the guard's HAPPY path only. It passes with the guard and passes without
#: it, which is precisely the shape a guard-proof sweep exists to name.
UNGUARDED_TEST = """\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from {module} import implausible


def test_a_value_above_the_floor_is_allowed():
    assert implausible(9000) is None
"""

GATE_CI = """\
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/{modules}.py"
"""


def tree(root: Path, *modules: str) -> Path:
    """A toy project whose gate floor invokes each named module."""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    for module in modules:
        (root / "scripts" / f"{module}.py").write_text(GUARD, encoding="utf-8")
    (root / "scripts" / "gate_ci.sh").write_text(
        "#!/usr/bin/env bash\n"
        + "".join(f'python3 "$SCRIPT_DIR/{module}.py"\n' for module in modules),
        encoding="utf-8",
    )
    return root


def with_test(root: Path, module: str, body: str = GUARDED_TEST) -> None:
    (root / "tests" / f"test_{module}.py").write_text(
        body.format(module=module), encoding="utf-8"
    )


def inventory(root: Path, body: str, *, exempt_surface: bool = True) -> Path:
    """Write a toy inventory.

    `exempt_surface` covers the toy tree's own `gate_ci.sh`, which is an enforcement surface with
    nothing behind it. Exempting it by default keeps the undeclared count out of the cases that are
    about a VERDICT, so each of those asserts one thing; the cases that ARE about the undeclared
    count turn it off.
    """
    path = root / "guards.toml"
    path.write_text(body + (SURFACE_EXEMPT if exempt_surface else ""), encoding="utf-8")
    return path


def entry(
    identifier: str,
    module: str,
    *,
    find: str = "    if latency < FLOOR:",
    replace: str = "    if False:",
    tests: str | None = None,
) -> str:
    return f"""
[[guard]]
id = "{identifier}"
implements = "scripts/{module}.py"
defect = "A latency no real call can produce was consumed as a pass."
tests = ["{tests or f"tests/test_{module}.py"}"]
[[guard.mutation]]
file = "scripts/{module}.py"
find = '''
{find}'''
replace = '''
{replace}'''
"""


SURFACE_EXEMPT = (
    '\n[[exempt]]\nfile = "scripts/gate_ci.sh"\n'
    'reason = "the enforcement surface itself, in a toy tree with no gate of its own"\n'
)


@pytest.fixture(autouse=True)
def _bounded(monkeypatch):
    """A toy suite that has not answered in a minute is not going to."""
    monkeypatch.setenv("GUARD_PROOF_BUDGET_S", "60")


def status_of(root: Path, path: Path) -> dict[str, str]:
    report = guard_proof.sweep(
        root, load(path), scope=guard_proof.Scope("all", frozenset()), parallel=2
    )
    return {proof.guard_id: proof.status for proof in report.proofs}


def digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── the verdicts ────────────────────────────────────────────────────────────


def test_a_guard_whose_test_notices_the_defect_is_proven(tmp_path: Path) -> None:
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))

    assert status_of(root, path) == {"floor.latency": PROVEN}
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == OK


def test_a_guard_NOTHING_notices_is_reported_unproven_and_fails_the_run(
    tmp_path: Path,
) -> None:
    """The harness proving itself.

    This tree holds a real guard and a test that never exercises it. Everything is green, the guard
    is decoration, and a harness that could not tell the difference would report the same `proven`
    it reports for a guard that works - which is the silent yes issue #69 is about, wearing the
    costume of the thing that was supposed to find it.
    """
    root = tree(tmp_path, "unwatched")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(root, entry("unwatched.latency", "unwatched"))

    assert status_of(root, path) == {"unwatched.latency": UNPROVEN}
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_one_unproven_guard_fails_a_sweep_that_is_otherwise_green(
    tmp_path: Path,
) -> None:
    root = tree(tmp_path, "watched", "unwatched")
    with_test(root, "watched")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(
        root,
        entry("watched.latency", "watched") + entry("unwatched.latency", "unwatched"),
    )

    assert status_of(root, path) == {
        "watched.latency": PROVEN,
        "unwatched.latency": UNPROVEN,
    }
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_a_mutation_whose_anchor_moved_is_unanchored_and_never_a_skip(
    tmp_path: Path,
) -> None:
    """A mutation that cannot be applied proved nothing, and is indistinguishable from a guard
    nobody ever tested. Reading it as fine is how an inventory rots into a list of claims."""
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(
        root,
        entry("floor.latency", "floor", find="    if latency <= FLOOR:  # renamed"),
    )

    assert status_of(root, path) == {"floor.latency": UNANCHORED}
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_a_mutation_that_matches_more_often_than_declared_is_unanchored(
    tmp_path: Path,
) -> None:
    """Two matches where one was declared means the edit lands somewhere nobody reviewed."""
    root = tree(tmp_path, "floor")
    (root / "scripts" / "floor.py").write_text(GUARD + "\n" + GUARD, encoding="utf-8")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))

    assert status_of(root, path) == {"floor.latency": UNANCHORED}


def test_tests_that_already_fail_are_baseline_red_not_proven(tmp_path: Path) -> None:
    """Red before the mutation and red after it says nothing about the guard - and reading the
    second red as a proof is how a broken test set certifies every guard it names."""
    root = tree(tmp_path, "floor")
    (root / "tests" / "test_floor.py").write_text(
        "def test_already_broken():\n    assert False\n", encoding="utf-8"
    )
    path = inventory(root, entry("floor.latency", "floor"))

    assert status_of(root, path) == {"floor.latency": BASELINE_RED}
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_a_runner_that_cannot_start_is_errored_never_a_pass(
    tmp_path: Path, monkeypatch
) -> None:
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))
    monkeypatch.setenv("GUARD_PROOF_PYTEST", str(tmp_path / "no-such-runner"))

    assert status_of(root, path) == {"floor.latency": ERRORED}
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_a_runner_that_cannot_be_EXECUTED_is_errored_not_a_crash(
    tmp_path: Path, monkeypatch
) -> None:
    """A runner that exists and cannot be started is the same answer as one that is not there.

    `FileNotFoundError` is one way a `Popen` fails; a path that is not executable raises
    `PermissionError` instead. Left uncaught it escapes the thread pool as a traceback and exits
    `FINDINGS`, so a harness that could not run at all arrives looking exactly like a guard that
    failed its proof.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))
    monkeypatch.setenv("GUARD_PROOF_PYTEST", str(tmp_path))

    assert status_of(root, path) == {"floor.latency": ERRORED}
    assert main(["prove", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_an_id_the_inventory_does_not_declare_is_refused_not_a_clean_pass(
    tmp_path: Path,
) -> None:
    """`prove --guard <id>` over an id nobody declares proved nothing, and must say so.

    Selecting a guard by id is how CI, a bisect and a developer address one guard. A typo, or an id
    renamed in the inventory while a caller still names the old one, would otherwise report a green
    sweep of zero guards - which is the exact silent yes this harness exists to remove.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))

    assert (
        main(
            [
                "prove",
                "--root",
                str(root),
                "--inventory",
                str(path),
                "--guard",
                "floor.latency",
            ]
        )
        == OK
    )

    with pytest.raises(GuardProofError) as exc:
        guard_proof.sweep(
            root,
            load(path),
            scope=guard_proof.Scope("all", frozenset()),
            only=("floor.latenc",),
            parallel=2,
        )
    assert "floor.latenc" in str(exc.value)
    assert (
        main(
            [
                "prove",
                "--root",
                str(root),
                "--inventory",
                str(path),
                "--guard",
                "floor.latenc",
            ]
        )
        == 2
    ), (
        "a usage error is ERROR, never FINDINGS - the two codes already mean different things here"
    )


# ── the working tree ────────────────────────────────────────────────────────


def test_the_working_tree_is_never_mutated(tmp_path: Path) -> None:
    """Every mutation lands in a throwaway copy. A crashed sweep must not leave a neutered guard
    behind - the tree this ran against has to be byte-identical afterwards."""
    root = tree(tmp_path, "floor", "unwatched")
    with_test(root, "floor")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(
        root, entry("floor.latency", "floor") + entry("unwatched.latency", "unwatched")
    )
    before = digest(root)

    main(["prove", "--root", str(root), "--inventory", str(path)])

    assert digest(root) == before


def test_the_working_tree_survives_a_run_that_could_not_finish(
    tmp_path: Path, monkeypatch
) -> None:
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))
    monkeypatch.setenv("GUARD_PROOF_PYTEST", str(tmp_path / "no-such-runner"))
    before = digest(root)

    main(["prove", "--root", str(root), "--inventory", str(path)])

    assert digest(root) == before


# ── the undeclared count ────────────────────────────────────────────────────


def test_a_guard_the_gate_floor_invokes_with_no_entry_is_undeclared(
    tmp_path: Path,
) -> None:
    root = tree(tmp_path, "floor", "nobody_declared_me")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"), exempt_surface=False)

    report = guard_proof.sweep(
        root, load(path), scope=guard_proof.Scope("all", frozenset()), parallel=2
    )

    assert [name for name, _ in report.undeclared] == [
        "scripts/gate_ci.sh",
        "scripts/nobody_declared_me.py",
    ]
    assert main(["report", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_prove_prints_the_undeclared_count_without_failing_on_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`prove` answers one question - do the declared guards guard? An undeclared guard is a rule
    added after the tree it runs on, so it binds what a change TOUCHES (`check`) and is audited
    deliberately (`report`). It is still printed by all three: a count that stops being printed is
    a count nobody acts on."""
    root = tree(tmp_path, "floor", "nobody_declared_me")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"), exempt_surface=False)

    code = main(["prove", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == OK
    assert "nobody_declared_me" in said.out
    assert main(["report", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_an_import_carrying_a_noqa_comment_is_still_followed(tmp_path: Path) -> None:
    """`import x  # noqa: E402` is this repository's prevailing style, not an edge case.

    Every script here opens with a `sys.path.insert` preamble, so every sibling import after it
    carries that suffix. A closure that drops them leaves real deciders - `evidence_redaction.py`
    among them - outside the universe entirely: not declared, not exempt, not undeclared, with the
    report claiming full coverage over a file nobody has ever broken on purpose.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    (root / "scripts" / "reached_by_import.py").write_text(GUARD, encoding="utf-8")
    (root / "scripts" / "floor.py").write_text(
        "import reached_by_import  # noqa: E402\n" + GUARD, encoding="utf-8"
    )
    path = inventory(root, entry("floor.latency", "floor"))

    universe = guard_proof.guard_universe(root)

    assert "scripts/reached_by_import.py" in universe
    report = guard_proof.sweep(
        root, load(path), scope=guard_proof.Scope("all", frozenset()), parallel=2
    )
    assert "scripts/reached_by_import.py" in [name for name, _ in report.undeclared]


def test_an_exempt_file_is_not_undeclared(tmp_path: Path) -> None:
    root = tree(tmp_path, "floor", "helper")
    with_test(root, "floor")
    path = inventory(
        root,
        entry("floor.latency", "floor")
        + '\n[[exempt]]\nfile = "scripts/helper.py"\nreason = "reads, never decides"\n',
        exempt_surface=True,
    )

    report = guard_proof.sweep(
        root, load(path), scope=guard_proof.Scope("all", frozenset()), parallel=2
    )

    assert report.undeclared == ()


def test_an_exemption_nothing_invokes_any_more_is_a_finding(tmp_path: Path) -> None:
    """An exemption outlives its reason the moment the file leaves the enforcement path, and a
    stale exemption is how a guard re-enters the tree already excused."""
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(
        root,
        entry("floor.latency", "floor")
        + '\n[[exempt]]\nfile = "scripts/long_gone.py"\nreason = "deleted two releases ago"\n',
    )

    report = guard_proof.sweep(
        root, load(path), scope=guard_proof.Scope("all", frozenset()), parallel=2
    )

    assert report.stale_exemptions == ("scripts/long_gone.py",)
    assert guard_proof.verdict(report) == FINDINGS


def test_an_exemption_reachable_only_from_a_NON_HOOK_surface_is_not_stale(
    tmp_path: Path,
) -> None:
    """A stale-exemption finding is not diff-scoped, so it must be judged against the whole set.

    `guard_universe` walks the gate floor and the hook scripts; the config and workflow members of
    the closed surface set reach a script through no shell line it reads. Judged against that
    narrower universe, an exemption for a file only they invoke reads as stale on every unrelated
    diff, forever, with no way for any of those diffs to clear it - the hostage failure again.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    (root / "scripts" / "helper.py").write_text(GUARD, encoding="utf-8")
    (root / ".pre-commit-config.yaml").write_text(
        "        entry: python3 scripts/helper.py\n", encoding="utf-8"
    )
    path = inventory(
        root,
        entry("floor.latency", "floor")
        + '\n[[exempt]]\nfile = "scripts/helper.py"\nreason = "reads, never decides"\n',
    )
    git_repo(root)
    (root / "tests" / "test_floor.py").write_text(
        GUARDED_TEST.format(module="floor") + "\n\n# touched\n", encoding="utf-8"
    )

    report = guard_proof.sweep(
        root,
        load(path),
        scope=guard_proof.resolve_scope(root, changed_only=True, base=None),
        parallel=2,
    )

    assert report.stale_exemptions == ()
    assert guard_proof.verdict(report) == OK


def test_an_inventory_naming_a_file_that_is_not_there_is_a_finding(
    tmp_path: Path,
) -> None:
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(root, entry("ghost.latency", "ghost"))

    report = guard_proof.sweep(
        root, load(path), scope=guard_proof.Scope("all", frozenset()), parallel=2
    )

    assert "scripts/ghost.py" in report.missing


# ── the applicability boundary ──────────────────────────────────────────────


def git_repo(root: Path) -> Path:
    for args in (
        ("init", "-q"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("add", "-A"),
        ("commit", "-qm", "root"),
    ):
        subprocess.run(
            ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
        )
    return root


def test_a_guard_the_change_does_not_touch_is_counted_not_proved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A guard declared today may not retroactively fail a tree written before it (CLAUDE.md 3a).
    Out of scope has to look different from clean, so what was not enforced is named."""
    root = tree(tmp_path, "floor", "unwatched")
    with_test(root, "floor")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(
        root, entry("floor.latency", "floor") + entry("unwatched.latency", "unwatched")
    )
    git_repo(root)
    (root / "scripts" / "floor.py").write_text(GUARD + "# touched\n", encoding="utf-8")

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == OK, (
        "the unproven guard is outside what this change is responsible for"
    )
    assert "floor.latency" in said.out
    assert "unwatched.latency" in said.err, (
        "what was not enforced is named, never silent"
    )


def test_a_guard_the_change_DOES_touch_is_enforced(tmp_path: Path) -> None:
    root = tree(tmp_path, "unwatched")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(root, entry("unwatched.latency", "unwatched"))
    git_repo(root)
    (root / "scripts" / "unwatched.py").write_text(
        GUARD + "# touched\n", encoding="utf-8"
    )

    assert main(["check", "--root", str(root), "--inventory", str(path)]) == FINDINGS


def test_a_script_this_change_newly_WIRES_IN_is_enforced_though_it_was_not_edited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second way into scope: the diff edits the SURFACE, not the guard.

    `helper.py` was already sitting in the tree and answered to nothing, because no enforcement
    surface invoked it. This change adds that invocation to `gate_ci.sh` and touches `helper.py` not
    at all - so a scope decided on the guard's own file lets the one diff that put a wholly unproven
    check into the pipeline pass clean.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    (root / "scripts" / "helper.py").write_text(GUARD, encoding="utf-8")
    path = inventory(root, entry("floor.latency", "floor"))
    git_repo(root)
    surface = root / "scripts" / "gate_ci.sh"
    surface.write_text(
        surface.read_text(encoding="utf-8") + 'python3 "$SCRIPT_DIR/helper.py"\n',
        encoding="utf-8",
    )

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == FINDINGS
    assert "scripts/helper.py" in said.out
    assert "scripts/helper.py" not in said.err, (
        "a newly wired guard is enforced, never counted-and-named"
    )


def test_a_COMMENT_naming_an_undeclared_script_does_not_put_it_in_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Naming a path is not wiring it in, and this half BLOCKS.

    Read as an invocation, one documentation line added to a surface the diff already touches fails
    CI over a script nothing runs - a rule binding what the change is not responsible for, which is
    the hostage failure removed twice already in this same mechanism.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    (root / "scripts" / "helper.py").write_text(GUARD, encoding="utf-8")
    path = inventory(root, entry("floor.latency", "floor"))
    git_repo(root)
    surface = root / "scripts" / "gate_ci.sh"
    surface.write_text(
        surface.read_text(encoding="utf-8") + "# see scripts/helper.py for the floor\n",
        encoding="utf-8",
    )

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == OK
    assert "scripts/helper.py" not in said.out


def test_the_spellings_this_repository_ACTUALLY_writes_invocations_in_are_seen(
    tmp_path: Path,
) -> None:
    """Measured against the real surfaces, not against the one spelling a toy tree happens to use.

    An allow-list of interpreter prefixes read ZERO invocations out of `hooks/hooks.json` - all
    sixteen hook scripts, whose JSON-escaped quotes made the preceding token a lone backslash - and
    lost `OUT=$(python3 …)` in `gate_ci.sh` besides. A shrunken universe is the defect this whole
    module exists to remove, so the question is asked the only way that stays decidable: what is
    NOT a comment.
    """
    surface = "\n".join(
        [
            '{"command": "bash \\"${CLAUDE_PLUGIN_ROOT}/scripts/hook_x.sh\\""}',
            'OUT=$(python3 "$SD/queued.py" run)',
            'GATE_STATE="$(python3 "$SCRIPT_DIR/state.py" status)"',
            '. "$SCRIPT_DIR/load_env.sh"',
            'if ! "$SD/probe.sh"; then exit 1; fi',
            "        entry: python3 scripts/entrypoint.py",
            "          run: bash scripts/workflow.sh",
            "# see scripts/only_mentioned.py for the floor",
        ]
    )

    seen = set(guard_proof._uncommented_tokens(surface))

    assert seen == {
        "scripts/hook_x.sh",
        "scripts/queued.py",
        "scripts/state.py",
        "scripts/load_env.sh",
        "scripts/probe.sh",
        "scripts/entrypoint.py",
        "scripts/workflow.sh",
    }
    assert "scripts/only_mentioned.py" not in seen


def test_a_script_reached_only_through_a_NON_HOOK_surface_brings_its_imports(
    tmp_path: Path,
) -> None:
    """The import closure has to see everything the universe holds, not only what it seeded itself.

    `guard_universe` walks the gate floor and the hook scripts; a file reached through the config or
    workflow members of the closed set arrives from elsewhere. Merged in after the closure ran, it
    contributed itself and none of its imports - so a module it alone imports was absent from the
    report entirely: not declared, not exempt, not undeclared, with the count claiming coverage.
    """
    root = tree(tmp_path, "floor")
    (root / "scripts" / "wired_only.py").write_text(
        "import reached_by_import\n" + GUARD, encoding="utf-8"
    )
    (root / "scripts" / "reached_by_import.py").write_text(GUARD, encoding="utf-8")

    universe = guard_proof.guard_universe(
        root, {"scripts/wired_only.py": ".github/workflows/guard-proof.yml"}
    )

    assert "scripts/wired_only.py" in universe
    assert "scripts/reached_by_import.py" in universe


def test_an_invocation_that_was_ALREADY_THERE_is_counted_though_the_surface_is_touched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mirror of the case above, and the whole difference between ADDED and INVOKED.

    `gate_ci.sh` has always invoked `helper.py`; this change edits an unrelated line of it. Read as
    "a touched surface invokes it", every pre-existing undeclared script the surface calls becomes
    this diff's problem - which is a rule added later binding what the change is not responsible
    for, the hostage failure the applicability boundary exists to remove.
    """
    root = tree(tmp_path, "floor", "helper")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))
    git_repo(root)
    surface = root / "scripts" / "gate_ci.sh"
    surface.write_text(
        surface.read_text(encoding="utf-8") + "# an unrelated line\n", encoding="utf-8"
    )

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == OK, "the pre-existing invocation is not this change's responsibility"
    assert "scripts/helper.py" in said.err, (
        "what was not enforced is named, never silent"
    )


def test_newness_git_cannot_answer_enforces_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base git cannot resolve makes newness unknowable, and unknowable enforces nothing.

    Falling back to "every invocation the surface makes" is the wide predicate being removed here;
    falling back QUIETLY is the silent pass this harness refuses everywhere else.
    """
    root = tree(tmp_path, "floor", "helper")
    with_test(root, "floor")
    git_repo(root)
    surface = root / "scripts" / "gate_ci.sh"
    surface.write_text(
        surface.read_text(encoding="utf-8") + "# an unrelated line\n", encoding="utf-8"
    )

    scope = guard_proof.Scope(
        "changed", frozenset([root / "scripts" / "gate_ci.sh"]), "no-such-ref"
    )
    wired = guard_proof.newly_wired(root, scope)
    said = capsys.readouterr()

    assert wired == {}
    assert "no-such-ref" in said.err


def test_a_script_an_UNTOUCHED_surface_invokes_is_still_only_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The boundary is unchanged: this adds one more way to be IN scope, it does not widen it.

    Here the surface already invoked `helper.py` before the change and the change touches neither,
    so the undeclared guard is pre-existing and may not hold this diff hostage (CLAUDE.md 3a).
    """
    root = tree(tmp_path, "floor", "helper")
    with_test(root, "floor")
    path = inventory(root, entry("floor.latency", "floor"))
    git_repo(root)
    (root / "tests" / "test_floor.py").write_text(
        GUARDED_TEST.format(module="floor") + "\n\n# touched\n", encoding="utf-8"
    )

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == OK
    assert "scripts/helper.py" in said.err, (
        "what was not enforced is named, never silent"
    )


def test_when_git_cannot_say_what_changed_nothing_is_enforced_and_it_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Falling back to enforcing everything is the hostage failure the scoping exists to remove;
    falling back to enforcing nothing QUIETLY is the silent pass it exists to refuse."""
    root = tree(tmp_path, "unwatched")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(root, entry("unwatched.latency", "unwatched"))

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == OK
    assert "UNKNOWABLE" in said.out


def test_an_unknowable_scope_that_exits_nonzero_names_what_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The banner and the exit code have to describe the same run.

    A stale exemption is an INVENTORY defect, not a diff-scoped one, so it survives an unknowable
    scope and should - but a run that printed "nothing was enforced" and exited 1 left an operator
    with no way to see what actually failed.
    """
    root = tree(tmp_path, "floor")
    with_test(root, "floor")
    path = inventory(
        root,
        entry("floor.latency", "floor")
        + '\n[[exempt]]\nfile = "scripts/long_gone.py"\nreason = "deleted two releases ago"\n',
    )

    code = main(["check", "--root", str(root), "--inventory", str(path)])
    said = capsys.readouterr()

    assert code == FINDINGS
    assert "INVENTORY defect" in said.out
    assert "scripts/long_gone.py" in said.out


def test_prove_with_a_base_scopes_instead_of_silently_sweeping_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An accepted-and-ignored argument is this module's own thesis, one layer up.

    `--base` without `--changed` used to resolve the full-tree scope and report a whole sweep as
    though the requested branch scope had been applied.
    """
    root = tree(tmp_path, "floor", "unwatched")
    with_test(root, "floor")
    with_test(root, "unwatched", UNGUARDED_TEST)
    path = inventory(
        root, entry("floor.latency", "floor") + entry("unwatched.latency", "unwatched")
    )
    git_repo(root)
    (root / "scripts" / "floor.py").write_text(GUARD + "# touched\n", encoding="utf-8")

    code = main(
        ["prove", "--root", str(root), "--inventory", str(path), "--base", "HEAD"]
    )
    said = capsys.readouterr()

    assert code == OK
    assert "unwatched.latency" in said.err, (
        "the unproven guard is outside the requested base scope, counted and named"
    )
    assert "unwatched.latency" not in said.out


# ── the inventory is validated, never read loosely ──────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('[[guard]]\nimplements = "scripts/floor.py"\n', "non-empty `id`"),
        (
            '[[guard]]\nid = "a"\nimplements = "scripts/floor.py"\ndefect = "d"\n'
            'tests = ["tests/test_floor.py"]\n',
            "at least one mutation",
        ),
        (
            '[[guard]]\nid = "a"\nimplements = "scripts/floor.py"\ndefect = "d"\ntests = []\n'
            '[[guard.mutation]]\nfile = "scripts/floor.py"\nfind = "x"\nreplace = "y"\n',
            "at least one test",
        ),
        (
            '[[guard]]\nid = "a"\nimplements = "scripts/floor.py"\ndefect = "d"\n'
            'tests = ["t"]\ntypo = 1\n[[guard.mutation]]\nfile = "f"\nfind = "x"\nreplace = "y"\n',
            "unknown key",
        ),
        (
            '[[guard]]\nid = "a"\nimplements = "scripts/floor.py"\ndefect = "d"\n'
            'tests = ["t"]\n[[guard.mutation]]\nfile = "f"\nfind = "x"\nreplace = "x"\n',
            "changes nothing",
        ),
        ('[[exempt]]\nfile = "scripts/x.py"\n', "non-empty 'reason'"),
    ],
)
def test_a_malformed_inventory_is_an_error_never_an_empty_one(
    tmp_path: Path, body: str, expected: str
) -> None:
    path = inventory(tmp_path, body, exempt_surface=False)

    with pytest.raises(GuardProofError) as exc:
        load(path)

    assert expected in str(exc.value)


def test_two_guards_may_not_share_an_id(tmp_path: Path) -> None:
    root = tree(tmp_path, "floor")
    path = inventory(
        root, entry("same", "floor") + entry("same", "floor"), exempt_surface=False
    )

    with pytest.raises(GuardProofError) as exc:
        load(path)

    assert "same" in str(exc.value)


def test_an_unreadable_inventory_stops_the_run(tmp_path: Path) -> None:
    assert (
        main(
            [
                "prove",
                "--root",
                str(tmp_path),
                "--inventory",
                str(tmp_path / "gone.toml"),
            ]
        )
        == 2
    )


# ── this repository's own inventory ─────────────────────────────────────────

REPO = Path(__file__).resolve().parents[1]


def test_this_repositorys_inventory_loads_and_points_at_real_files() -> None:
    """The declaration is checked for free on every commit; PROVING it is the sweep's job."""
    declared = load(REPO / guard_proof.INVENTORY)

    assert declared.guards, "the inventory is not a placeholder"
    assert guard_proof.missing_paths(REPO, declared) == []


def test_every_guard_the_issue_named_is_declared() -> None:
    """Issue #69's instances are the floor of this inventory, not a sample of it."""
    owed = {
        "scripts/carried_items.py",
        "scripts/spec_done_guard.py",
        "scripts/emission_gate.py",
        "scripts/gate_plausibility.py",
        "scripts/hook_mutation.sh",
        "scripts/requirement_cap.py",
        "scripts/suite_outcome.py",
        "scripts/verifier_evidence.py",
        "scripts/doc_read_path.py",
    }
    declared = load(REPO / guard_proof.INVENTORY).declared_files()

    assert owed <= declared, f"undeclared: {sorted(owed - declared)}"
