"""Tests for the Stop-hook artifact sweep's key.

The sweep was keyed to the PRESENCE of `implementation-report.md` — a file no template, agent,
skill, command or script ever instructed anyone to write. So the list of phases it swept was the
list of phases where an implementer happened to invent an undocumented file, and a gate keyed to
that mostly does not run. That is why issue #29's outcome for that class is removal rather than a
`readers: none` declaration: the honest replacement is a signal the pipeline actually produces.

The replacement is `status: done` on every spec in the phase — the implementer's own completion
stamp, the one `hook_verifier.sh` fires its `spec-done` trigger on and `spec_done_guard.py` reverts
when it is not backed by evidence - for the per-spec `test-mapping.md`, and a PASSING `verdict.json`
for the phase's `handover.md`, which is the first moment `hook_verifier.sh` will let one be written.

The dangerous directions are a sweep that fires EARLY (on a phase still being built, or on one still
inside the verification stage, where the handover it demands is forbidden), one that never fires at
all (what the old key did), and one that fires on phases the change is not responsible for. All get
a test.
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
    verified,
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

HANDOVER = "---\nreaders: the next phase's spec writer @ per phase\n---\n\nDone.\n"


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
    verdict(phase_dir)
    (phase_dir / "handover.md").write_text(HANDOVER)
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


# --- the handover is owed at the VERDICT, not at the stamp -------------------------------------


def test_a_done_phase_still_in_verification_is_not_asked_for_a_handover(
    tmp_path: Path,
) -> None:
    """The regression: `hook_verifier.sh` REFUSES the handover write until a verdict passes.

    Every spec stamped `done` with no verdict yet is the whole verification stage - 3 attempts plus
    implementer route-backs - and `/avenger-run` is resumable, so a session ending here is ordinary
    usage. Demanding the handover here wedges it: the hook that would write it refuses to.
    """
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    assert not (phase_dir / "verdict.json").exists()
    assert owed(tmp_path) == []


def test_a_passing_verdict_is_what_makes_the_handover_owed(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    verdict(phase_dir)
    assert verified(phase_dir)
    found = owed(tmp_path)
    assert any("handover.md" in problem for problem in found), found


def test_a_failing_verdict_does_not_ask_for_a_handover(tmp_path: Path) -> None:
    """A phase routed back is still in the loop, and the handover is still refused."""
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    verdict(phase_dir, result="fail", findings=[{"id": "F1", "status": "open"}])
    assert not verified(phase_dir)
    assert owed(tmp_path) == []


def test_a_pass_still_carrying_an_open_finding_is_not_a_pass(tmp_path: Path) -> None:
    """`verdict_findings` owns this, and `hook_verifier.sh` fails closed on it too."""
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    verdict(phase_dir, result="pass", findings=[{"id": "F1", "status": "open"}])
    assert not verified(phase_dir)
    assert owed(tmp_path) == []


def test_a_waived_but_open_finding_does_not_make_a_handover_owed(
    tmp_path: Path,
) -> None:
    """The regression: this sweep and `hook_verifier.sh` must not disagree about one verdict.

    Break-glass is one of the three remedies the attempt cap prescribes, so a `pass` carrying a
    waived-but-still-open finding is reachable through the documented route. `hook_verifier.sh`
    counts `status: open` literally and fails closed on exactly that shape, so demanding a
    `handover.md` here would demand a document the hook refuses to let anyone write - a phase with
    no reachable end state. The sweep asks for less.
    """
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    verdict(
        phase_dir,
        result="pass",
        findings=[{"id": "F1", "status": "open", "break_glass": "waived by captain"}],
    )
    assert not verified(phase_dir)
    assert owed(tmp_path) == []


def test_a_waived_finding_that_is_no_longer_open_is_a_clean_pass(
    tmp_path: Path,
) -> None:
    """The other side of it: once the finding is closed, the handover is owed as usual."""
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    verdict(
        phase_dir,
        result="pass",
        findings=[{"id": "F1", "status": "fixed", "break_glass": "waived by captain"}],
    )
    assert verified(phase_dir)
    assert any("handover.md" in problem for problem in owed(tmp_path))


def test_an_unreadable_verdict_is_not_a_pass(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)
    (phase_dir / "verdict.json").write_text("{not json")
    assert not verified(phase_dir)
    assert owed(tmp_path) == []


# --- what a finished phase owes ---------------------------------------------------------------


def test_a_missing_handover_is_a_violation(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    complete(phase_dir)
    (phase_dir / "handover.md").unlink()
    assert any("handover.md" in p for p in owed(tmp_path))


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
    phase_dir = phase(tmp_path, spec_body=SPEC_ALL_NONE)
    verdict(phase_dir)
    (phase_dir / "handover.md").write_text(HANDOVER)
    assert owed(tmp_path) == []


def test_a_done_spec_declaring_no_requirement_is_reported_not_exempted(
    tmp_path: Path,
) -> None:
    """Identical to the exemption when read off an empty binding list, and not the same thing."""
    phase_dir = phase(
        tmp_path,
        spec_body="---\nfeature: demo\nstatus: done\n---\n\n# Spec\n\n## Requirements\n",
    )
    verdict(phase_dir)
    (phase_dir / "handover.md").write_text(HANDOVER)
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
    """A repository holding one phase that finished, passed, and owes both artifacts."""
    git(tmp_path, "init", "-q")
    phase(tmp_path, status="done")
    verdict(tmp_path / "docs" / "features" / "demo" / "phases" / "1-slice")
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
    assert any("handover.md" in p for p in found), found
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
    verdict(other.parent.parent)
    found = problems(repo)
    assert any("2-next" in p for p in found), found
    assert not any("1-slice" in p for p in found), found


@pytest.mark.subprocess("the subject is git's own answer when it has none")
def test_an_unknowable_scope_enforces_nothing_and_says_so(
    tmp_path: Path, capsys
) -> None:
    complete(phase(tmp_path))
    (
        tmp_path / "docs" / "features" / "demo" / "phases" / "1-slice" / "handover.md"
    ).unlink()
    assert problems(tmp_path) == []
    assert "scope is unknowable" in capsys.readouterr().err


# --- scope and CLI ------------------------------------------------------------------------------


def test_an_absent_artifact_tree_scans_nothing(tmp_path: Path) -> None:
    assert phase_dirs(tmp_path) == []
    assert owed(tmp_path) == []


def test_the_cli_returns_one_on_a_violation_and_zero_when_clean(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    assert main(["check", str(tmp_path), "--all"]) == 1
    complete(phase_dir)
    assert main(["check", str(tmp_path), "--all"]) == 0


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
def test_the_hook_keys_on_the_stamp_and_the_verdict_not_on_a_stray_report(
    tmp_path: Path,
) -> None:
    """The regression that matters: the old key coming back into the hook.

    A phase that has NOT finished carries a stray `implementation-report.md`. Under the old key that
    file alone put the phase in the swept set, and the sweep then demanded artifacts it does not
    owe. Under the new key the phase is not swept at all, so the hook passes over it - and the same
    tree with the FINISHED phase's handover removed fails, which is what proves the sweep is
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

    (done / "handover.md").unlink()
    blocked = run_hook(tmp_path)
    assert blocked.returncode == 2, blocked.stdout
    assert "handover.md" in blocked.stderr
    assert "implementation-report.md" not in blocked.stderr
