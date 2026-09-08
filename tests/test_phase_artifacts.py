"""Tests for the Stop-hook artifact sweep's key.

The sweep was keyed to the PRESENCE of `implementation-report.md` — a file no template, agent,
skill, command or script ever instructed anyone to write. So the list of phases it swept was the
list of phases where an implementer happened to invent an undocumented file, and a gate keyed to
that mostly does not run. That is why issue #29's outcome for that class is removal rather than a
`readers: none` declaration: the honest replacement is a signal the pipeline actually produces.

The replacement is `status: done` on every spec in the phase - the implementer's own completion
stamp, the one `hook_verifier.sh` fires its `spec-done` trigger on and `spec_done_guard.py` reverts
when it is not backed by evidence - and what it then asks for is the per-spec `test-mapping.md`,
which is owed at that same stamp.

It asks for NOTHING else, and `test_the_sweep_never_asks_for_a_handover` pins that: `handover.md`
belongs to `hook_verifier.sh`, which refuses the write on six checks beyond the verdict, so any
condition this sweep could ask is weaker than the one that permits the write. A Stop hook demanding
a document another gate refuses to let anyone write is a phase with no reachable end state.

The dangerous directions are a sweep that fires EARLY (on a phase still being built), one that never
fires at all (what the old key did), one that fires on phases the change is not responsible for, and
one that reports a crash as an artifact somebody forgot to write. All get a test.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from phase_artifacts import (  # noqa: E402
    finished_implementing,
    main,
    phase_dirs,
    problems,
)

REPO = Path(__file__).resolve().parents[1]

SPEC = """---
feature: demo
phase: 1-slice
spec: 1.1-thing
status: {status}
readers: spec gate @ on write; implementer @ once, its own spec
---

# Spec

## Requirements
- **R1.1.1** — a thing happens. `binding: integration`
"""

SPEC_ALL_NONE = """---
feature: demo
phase: 1-slice
spec: 1.1-thing
status: done
readers: spec gate @ on write; implementer @ once, its own spec
---

# Spec

## Requirements
- **R1.1.1** — a structural property. `binding: none`
"""

MAPPING = """---
readers: avenger-verifier @ per phase
---

| requirement | test | level |
|---|---|---|
| R1.1.1 | tests/demo/1-slice/test_thing.py::test_thing | integration |
"""


def phase(root: Path, *, status: str = "done", spec_body: str | None = None) -> Path:
    phase_dir = root / "docs" / "features" / "demo" / "phases" / "1-slice"
    spec_dir = phase_dir / "specs" / "1.1-thing"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(spec_body or SPEC.format(status=status))
    return phase_dir


def verdict(
    phase_dir: Path, *, result: str = "pass", findings: list | None = None
) -> Path:
    """The Verifier's record, written the way the read path requires it (`readers` key and all)."""
    target = phase_dir / "verdict.json"
    target.write_text(
        json.dumps(
            {
                "verdict": result,
                "attempt": 1,
                "findings": findings or [],
                "readers": ["hook_verifier.sh @ per phase"],
            }
        )
    )
    return target


def complete(phase_dir: Path) -> None:
    """Everything this sweep asks of a finished phase, which is the mapping row and nothing else."""
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)


def owed(root: Path) -> list[str]:
    """What the sweep would block on, with the diff scope taken out of the question."""
    return problems(root, enforce_all=True)


# --- the key ---------------------------------------------------------------------------------


def test_a_phase_still_being_built_is_not_swept(tmp_path: Path) -> None:
    """Red is the expected state during a build; the sweep must not fire on it."""
    phase_dir = phase(tmp_path, status="in-progress")
    assert not finished_implementing(phase_dir)
    assert owed(tmp_path) == []


def test_a_phase_with_every_spec_done_is_swept(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    assert finished_implementing(phase_dir)
    assert owed(tmp_path), "a done phase missing its mapping owes it"


def test_one_unfinished_spec_holds_the_whole_phase_back(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    second = phase_dir / "specs" / "1.2-other"
    second.mkdir()
    (second / "spec.md").write_text(SPEC.format(status="in-progress"))
    assert not finished_implementing(phase_dir)


def test_a_phase_with_no_specs_has_not_started(tmp_path: Path) -> None:
    phase_dir = tmp_path / "docs" / "features" / "demo" / "phases" / "1-slice"
    phase_dir.mkdir(parents=True)
    assert not finished_implementing(phase_dir)
    assert owed(tmp_path) == []


def test_an_unreadable_status_is_not_done(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "spec.md").write_bytes(b"\xff\xfe not utf-8")
    assert not finished_implementing(phase_dir)


# --- what a finished phase owes ---------------------------------------------------------------


def test_the_sweep_never_asks_for_a_handover(tmp_path: Path) -> None:
    """The regression: a Stop hook must not demand what `hook_verifier.sh` may refuse to permit.

    That hook gates the handover write on a passing verdict PLUS `verifier_precheck.py`,
    `required_skills.py audit`, `verifier_evidence.py check`, `breaker_gate.py due`, the
    carried-items gate and `emission_gate.py defects`. Any condition this sweep could ask is weaker
    than that set, so asking at all produces phases told to create a document nothing will let them
    write, with the prescribed remedies unavailable and only `GATE_BYPASS` left.
    """
    phase_dir = phase(tmp_path, status="done")
    complete(phase_dir)
    verdict(phase_dir, result="pass")
    assert not (phase_dir / "handover.md").exists()
    assert owed(tmp_path) == []


def test_no_verdict_state_makes_the_sweep_ask_for_anything_extra(
    tmp_path: Path,
) -> None:
    """Whatever the verdict says, the sweep's answer is the same: it does not read one."""
    for findings in (
        [],
        [{"id": "F1", "status": "open"}],
        [{"id": "F1", "status": "fixed"}],
    ):
        root = tmp_path / f"tree{len(findings)}{findings and findings[0]['status']}"
        phase_dir = phase(root, status="done")
        complete(phase_dir)
        verdict(phase_dir, result="pass", findings=findings)
        assert owed(root) == []


def test_the_mapping_is_looked_for_beside_the_SPEC_not_the_phase(
    tmp_path: Path,
) -> None:
    """The old sweep looked in the phase directory, which has not been its home since `<n>.<k>`."""
    phase_dir = phase(tmp_path)
    complete(phase_dir)
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").unlink()
    (phase_dir / "test-mapping.md").write_text(MAPPING)
    found = owed(tmp_path)
    assert any("specs/1.1-thing/test-mapping.md" in p for p in found), found


def test_the_mapping_is_owed_at_the_STAMP_not_at_the_verdict(tmp_path: Path) -> None:
    """The half that stays keyed on `status: done`: the row is owed the instant the spec is."""
    phase_dir = phase(tmp_path, status="done")
    assert not (phase_dir / "verdict.json").exists()
    found = owed(tmp_path)
    assert any("test-mapping.md" in p for p in found), found


def test_a_complete_phase_is_clean(tmp_path: Path) -> None:
    complete(phase(tmp_path))
    assert owed(tmp_path) == []


def test_a_spec_whose_every_requirement_is_unbound_owes_no_mapping(
    tmp_path: Path,
) -> None:
    phase(tmp_path, spec_body=SPEC_ALL_NONE)
    assert owed(tmp_path) == []


def test_a_done_spec_declaring_no_requirement_is_reported_not_exempted(
    tmp_path: Path,
) -> None:
    """Identical to the exemption when read off an empty binding list, and not the same thing."""
    phase_dir = phase(
        tmp_path,
        spec_body="---\nfeature: demo\nstatus: done\n---\n\n# Spec\n\n## Requirements\n",
    )
    assert phase_dir.is_dir()
    found = owed(tmp_path)
    assert any("declares no requirement at all" in p for p in found), found


# --- the applicability boundary -----------------------------------------------------------------


@pytest.mark.subprocess(
    "the subject IS the applicability boundary, which reads a real git index"
)
def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository holding one committed phase that finished and owes its mapping row."""
    git(tmp_path, "init", "-q")
    phase(tmp_path, status="done")
    git(tmp_path, "add", "-A")
    git(
        tmp_path,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "phase 1",
    )
    return tmp_path


@pytest.mark.subprocess(
    "the subject is what git says this change touched; a double proves nothing"
)
def test_a_phase_the_diff_did_not_touch_is_counted_not_blocked(
    repo: Path, capsys
) -> None:
    """A consumer repo's shipped phases must not fail every Stop from installation onwards."""
    assert problems(repo) == []
    assert "NOT enforced" in capsys.readouterr().err


@pytest.mark.subprocess(
    "the subject is what git says this change touched; a double proves nothing"
)
def test_all_enforces_the_phase_the_diff_did_not_touch(repo: Path) -> None:
    found = problems(repo, enforce_all=True)
    assert any("test-mapping.md" in p for p in found), found


@pytest.mark.subprocess(
    "the subject is what git says this change touched; a double proves nothing"
)
def test_a_phase_this_change_touched_is_enforced(repo: Path) -> None:
    """Untracked artifacts are in scope from the moment they exist, before any commit."""
    other = (
        repo / "docs" / "features" / "demo" / "phases" / "2-next" / "specs" / "2.1-x"
    )
    other.mkdir(parents=True)
    (other / "spec.md").write_text(SPEC.format(status="done"))
    found = problems(repo)
    assert any("2-next" in p for p in found), found
    assert not any("1-slice" in p for p in found), found


@pytest.mark.subprocess("the subject is git's own answer when it has none")
def test_an_unknowable_scope_enforces_nothing_and_says_so(
    tmp_path: Path, capsys
) -> None:
    phase_dir = phase(tmp_path)
    complete(phase_dir)
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").unlink()
    assert problems(tmp_path) == []
    assert "scope is unknowable" in capsys.readouterr().err


# --- scope and CLI ------------------------------------------------------------------------------


def test_an_absent_artifact_tree_scans_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLEAN, but never invisibly: the sibling check in the same hook says this out loud too.

    `doc_read_path.check_artifacts` prints its own absent-tree line, and two checks answering the
    same absence differently is how one of them stops meaning anything to whoever reads the session.
    """
    assert phase_dirs(tmp_path) == []
    assert owed(tmp_path) == []
    assert "nothing to check" in capsys.readouterr().err


def test_an_undecidable_spec_is_not_told_to_create_a_file(tmp_path: Path) -> None:
    """The regression: the prescribed remedy WORKED and left the defect in place.

    A `status: done` spec whose requirement layout cannot be read, with no `test-mapping.md`, is
    reported and blocks the Stop. Under one shared header, "create them before stopping", the
    available fix was to write a mapping file - and `_spec_problems` returns clean the moment one
    exists, so the report vanished and the spec stayed unreadable. Each report now carries its own
    remedy, and this one points at the spec.
    """
    phase(
        tmp_path,
        spec_body=(
            "---\nfeature: demo\nstatus: done\n---\n\n# Spec\n\n"
            "## Requirements\n\nSee the appendix, where R1.1.1 lives in prose.\n"
        ),
    )
    found = owed(tmp_path)
    problem = next((p for p in found if "cannot be read" in p), None)
    assert problem is not None, found
    assert "repair the requirement layout in the spec itself" in problem.lower(), (
        problem
    )
    assert "silences this report" in problem, problem


def test_the_owed_header_prescribes_nothing_of_its_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One header cannot be right for three shapes, only one of which is a missing file."""
    phase(
        tmp_path,
        spec_body="---\nfeature: demo\nstatus: done\n---\n\n# Spec\n\n## Requirements\n",
    )
    assert main(["check", str(tmp_path), "--all"]) == 1
    header = capsys.readouterr().err.splitlines()[0]
    assert "create them before stopping" not in header, header
    assert "own remedy" in header, header


def test_the_cli_returns_one_on_a_violation_and_zero_when_clean(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    assert main(["check", str(tmp_path), "--all"]) == 1
    complete(phase_dir)
    assert main(["check", str(tmp_path), "--all"]) == 0


def test_a_sweep_that_could_not_answer_exits_two_and_not_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """§6: every stop names which it is, and this one had only one code for two situations.

    Exit 1 reaches the operator as "create them before stopping" through
    `hook_artifact_check.sh`, which is a remedy that cannot repair a crash. No artifact shape
    reaches this branch today - every one of them is caught and reported as a finding - and that is
    exactly why the split has to be structural rather than a handler for the failures somebody has
    already thought of, so the collaborator is made to fail here.
    """
    complete(phase(tmp_path))

    def exploding(_root: Path) -> list[Path]:
        raise RuntimeError("the artifact tree could not be walked")

    monkeypatch.setattr("phase_artifacts.phase_dirs", exploding)
    assert main(["check", str(tmp_path), "--all"]) == 2
    said = capsys.readouterr().err
    assert "could not decide what" in said, said
    assert "RuntimeError" in said, said
    assert "create them before stopping" not in said, said


def test_list_prints_only_the_finished_phases(tmp_path: Path, capsys) -> None:
    complete(phase(tmp_path))
    other = (
        tmp_path
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / "2-next"
        / "specs"
        / "2.1-x"
    )
    other.mkdir(parents=True)
    (other / "spec.md").write_text(SPEC.format(status="in-progress"))
    assert main(["list", str(tmp_path)]) == 0
    assert capsys.readouterr().out.split() == ["docs/features/demo/phases/1-slice"]


# --- the hook, executed -------------------------------------------------------------------------


@pytest.mark.subprocess(
    "the subject IS the hook: what it keys on is only observable by running it"
)
def run_hook(root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "hook_artifact_check.sh")],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.subprocess(
    "the subject IS the hook: what it keys on is only observable by running it"
)
def test_the_hook_keys_on_the_stamp_not_on_a_stray_report(
    tmp_path: Path,
) -> None:
    """The regression that matters: the old key coming back into the hook.

    A phase that has NOT finished carries a stray `implementation-report.md`. Under the old key that
    file alone put the phase in the swept set, and the sweep then demanded artifacts it does not
    owe. Under the new key the phase is not swept at all, so the hook passes over it - and the same
    tree with the FINISHED phase's mapping row removed fails, which is what proves the sweep is
    running rather than silently doing nothing.

    Both phases are written after the initial commit, so both are inside the diff scope: a pass here
    is the key's answer, never the boundary's.
    """
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("demo\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")

    done = phase(tmp_path, status="done")
    complete(done)

    building = tmp_path / "docs" / "features" / "demo" / "phases" / "2-next"
    (building / "specs" / "2.1-x").mkdir(parents=True)
    (building / "specs" / "2.1-x" / "spec.md").write_text(
        SPEC.format(status="in-progress")
    )
    (building / "implementation-report.md").write_text("invented by an implementer\n")

    clean = run_hook(tmp_path)
    assert clean.returncode == 0, clean.stderr
    assert "NOT enforced" not in clean.stderr, clean.stderr

    (done / "specs" / "1.1-thing" / "test-mapping.md").unlink()
    blocked = run_hook(tmp_path)
    assert blocked.returncode == 2, blocked.stdout
    assert "test-mapping.md" in blocked.stderr
    assert "implementation-report.md" not in blocked.stderr


@pytest.mark.subprocess(
    "the subject IS the hook's exit mapping, which only a real run of it exposes"
)
def test_a_check_that_could_not_answer_lets_the_stop_through_and_names_itself(
    tmp_path: Path,
) -> None:
    """An obligation blocks; a crash does not, and the difference has to reach the session.

    Both codes used to land on the Stop hook's one blocking exit, so an unexpected failure inside
    the sweep told the agent "create them before stopping" - a remedy that cannot repair a crash -
    and this hook honours no `GATE_BYPASS`, so a defect in a checker wedged a session that owed
    nothing. Driven through a sweep made to fail on a real run of the hook, since the mapping lives
    in the hook and nowhere else.
    """
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("demo\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    complete(phase(tmp_path, status="done"))

    owed_nothing = run_hook(tmp_path)
    assert owed_nothing.returncode == 0, owed_nothing.stderr

    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *phase_artifacts.py*) echo 'boom' >&2; exit 2 ;;\n"
        f'  *) exec {sys.executable} "$@" ;;\n'
        "esac\n"
    )
    (shim / "python3").chmod(0o755)

    stepped_aside = subprocess.run(
        ["bash", str(REPO / "scripts" / "hook_artifact_check.sh")],
        env=dict(
            os.environ,
            CLAUDE_PROJECT_DIR=str(tmp_path),
            PATH=f"{shim}{os.pathsep}{os.environ['PATH']}",
        ),
        capture_output=True,
        text=True,
    )
    assert stepped_aside.returncode == 0, stepped_aside.stderr
    assert "phase_artifacts.py check" in stepped_aside.stderr, stepped_aside.stderr
    assert "could not reach a verdict" in stepped_aside.stderr, stepped_aside.stderr
    assert "NOT blocked" in stepped_aside.stderr, stepped_aside.stderr
    assert "before stopping" not in stepped_aside.stderr, stepped_aside.stderr

    (
        tmp_path
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / "1-slice"
        / "specs"
        / "1.1-thing"
        / "test-mapping.md"
    ).unlink()
    still_blocks = run_hook(tmp_path)
    assert still_blocks.returncode == 2, still_blocks.stderr
    assert "test-mapping.md" in still_blocks.stderr


# --- a carried spec brings its mapping with it (issue #123) -------------------------------------


def carry(root: Path) -> Path:
    """The same spec, carried into a later phase of the same feature, mapping left behind.

    That is what a carry is in this pipeline: `spec.md` and the tests the phase suite has to run
    travel, and `test-mapping.md` stays in the phase that implemented it.
    """
    later = (
        root
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / "2-later"
        / "specs"
        / "1.1-thing"
    )
    later.mkdir(parents=True)
    (later / "spec.md").write_text(SPEC.format(status="done"))
    return later.parents[1]


def test_a_carried_spec_does_not_owe_a_second_copy_of_its_mapping(
    tmp_path: Path,
) -> None:
    """The sweep looks beside the spec, so a carried spec was reported as owing a mapping - and its
    remedy, "write the row that traces its requirements to their tests", is to duplicate a row that
    already exists one phase back. A second copy of a trace is not extra safety (issue #123)."""
    phase_dir = phase(tmp_path)
    complete(phase_dir)
    carry(tmp_path)

    assert owed(tmp_path) == []


def test_a_spec_with_no_mapping_anywhere_in_the_feature_still_owes_one(
    tmp_path: Path,
) -> None:
    """The other direction: nothing is carried, so nothing changes. A `status: done` spec whose
    mapping exists in no phase of this feature owes one exactly as it did."""
    phase(tmp_path)
    carry(tmp_path)

    found = owed(tmp_path)
    assert found and all(
        "test-mapping.md" in line and "missing" in line for line in found
    )
