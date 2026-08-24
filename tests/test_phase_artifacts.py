"""Tests for the Stop-hook artifact sweep's key.

The sweep was keyed to the PRESENCE of `implementation-report.md` — a file no template, agent,
skill, command or script ever instructed anyone to write. So the list of phases it swept was the
list of phases where an implementer happened to invent an undocumented file, and a gate keyed to
that mostly does not run. That is why issue #29's outcome for that class is removal rather than a
`readers: none` declaration: the honest replacement is a signal the pipeline actually produces.

The replacement is `status: done` on every spec in the phase — the implementer's own completion
stamp, the one `hook_verifier.sh` fires its `spec-done` trigger on and `spec_done_guard.py` reverts
when it is not backed by evidence.

The dangerous direction is a sweep that fires EARLY (on a phase still being built, which is what
the key exists to prevent) or one that never fires at all (what the old key did). Both get a test.
"""

import sys
from pathlib import Path

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


def complete(phase_dir: Path) -> None:
    (phase_dir / "handover.md").write_text("---\nreaders: x\n---\n")
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").write_text(MAPPING)


# --- the key ---------------------------------------------------------------------------------


def test_a_phase_still_being_built_is_not_swept(tmp_path: Path) -> None:
    """Red is the expected state during a build; the sweep must not fire on it."""
    phase_dir = phase(tmp_path, status="in-progress")
    assert not finished_implementing(phase_dir)
    assert problems(tmp_path) == []


def test_a_phase_with_every_spec_done_is_swept(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    assert finished_implementing(phase_dir)
    assert problems(tmp_path), (
        "a done phase missing handover.md and its mapping owes both"
    )


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
    assert problems(tmp_path) == []


def test_an_unreadable_status_is_not_done(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path, status="done")
    (phase_dir / "specs" / "1.1-thing" / "spec.md").write_bytes(b"\xff\xfe not utf-8")
    assert not finished_implementing(phase_dir)


# --- what a finished phase owes ---------------------------------------------------------------


def test_a_missing_handover_is_a_violation(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    complete(phase_dir)
    (phase_dir / "handover.md").unlink()
    assert any("handover.md" in p for p in problems(tmp_path))


def test_the_mapping_is_looked_for_beside_the_SPEC_not_the_phase(
    tmp_path: Path,
) -> None:
    """The old sweep looked in the phase directory, which has not been its home since `<n>.<k>`."""
    phase_dir = phase(tmp_path)
    complete(phase_dir)
    (phase_dir / "specs" / "1.1-thing" / "test-mapping.md").unlink()
    (phase_dir / "test-mapping.md").write_text(MAPPING)
    found = problems(tmp_path)
    assert any("specs/1.1-thing/test-mapping.md" in p for p in found), found


def test_a_complete_phase_is_clean(tmp_path: Path) -> None:
    complete(phase(tmp_path))
    assert problems(tmp_path) == []


def test_a_spec_whose_every_requirement_is_unbound_owes_no_mapping(
    tmp_path: Path,
) -> None:
    phase_dir = phase(tmp_path, spec_body=SPEC_ALL_NONE)
    (phase_dir / "handover.md").write_text("---\nreaders: x\n---\n")
    assert problems(tmp_path) == []


def test_a_done_spec_declaring_no_requirement_is_reported_not_exempted(
    tmp_path: Path,
) -> None:
    """Identical to the exemption when read off an empty binding list, and not the same thing."""
    phase_dir = phase(
        tmp_path,
        spec_body="---\nfeature: demo\nstatus: done\n---\n\n# Spec\n\n## Requirements\n",
    )
    (phase_dir / "handover.md").write_text("---\nreaders: x\n---\n")
    found = problems(tmp_path)
    assert any("declares no requirement at all" in p for p in found), found


# --- scope and CLI ------------------------------------------------------------------------------


def test_an_absent_artifact_tree_scans_nothing(tmp_path: Path) -> None:
    assert phase_dirs(tmp_path) == []
    assert problems(tmp_path) == []


def test_the_cli_returns_one_on_a_violation_and_zero_when_clean(tmp_path: Path) -> None:
    phase_dir = phase(tmp_path)
    assert main(["check", str(tmp_path)]) == 1
    complete(phase_dir)
    assert main(["check", str(tmp_path)]) == 0


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


def test_the_hook_no_longer_keys_on_an_uninstructed_artifact() -> None:
    """The regression that matters: the old key coming back into the hook."""
    hook = (REPO / "scripts" / "hook_artifact_check.sh").read_text(encoding="utf-8")
    assert "phase_artifacts.py" in hook
    assert "-name implementation-report.md" not in hook
