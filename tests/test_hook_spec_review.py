"""Tests for the spec-review hook's two halves.

The hook is where the cost gate and the diff-scoped re-gate actually fire, and both are easy to get
subtly wrong in ways nothing else notices: a mechanical check that quietly stops running in the mode
everyone uses, a break-glass that ends the hook before the paid gate ever runs, or a re-gate bundle
that hands the reviewer a diff full of noise. Each of those is pinned here.

No test calls a model. The model half is exercised by copying `scripts/` and replacing
`gate_runner.py` with a stub that records the `--target` it was handed — the real hook runs, only the
paid call is swapped out.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]

SPEC = """---
feature: demo
phase: 1-demo
status: {status}
review_status: {review_status}
---

## Requirements
- R1.1.1 — `binding: e2e` — {requirement}
"""

# A stub gate_runner: records what it was asked to judge, then approves. The hook's own logic is the
# subject; whether a real model says GO is not.
STUB_RUNNER = """import sys
target = sys.argv[sys.argv.index("--target") + 1]
open(sys.argv[0] + ".target", "w").write(open(target).read())
print("GO")
"""

CLEAN_TEST = "def test_nothing():\n    assert True\n"
SPAWNING_TEST = "import subprocess\n\n\ndef test_spawns():\n    subprocess.run(['true'])\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project dir with a spec, a tests/ tree, and a private copy of scripts/."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(CLEAN_TEST)
    (tmp_path / "docs").mkdir()
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "scripts" / "gate_runner.py").write_text(STUB_RUNNER)
    return tmp_path


def write_spec(project: Path, *, status: str = "pending", review_status: str = "pending",
               requirement: str = "the user sees a greeting") -> Path:
    spec = project / "docs" / "spec.md"
    spec.write_text(SPEC.format(status=status, review_status=review_status, requirement=requirement))
    return spec


def run_hook(project: Path, spec: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the hook against `spec` as a PostToolUse payload."""
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_spec_review.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % spec,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            **(env or {}),
        },
        check=False,
    )


def judged(project: Path) -> str:
    """What the stub gate_runner was handed to judge."""
    return (project / "scripts" / "gate_runner.py.target").read_text()


def stamp(project: Path, spec: Path) -> None:
    """Record the current body as the one the review gate approved."""
    subprocess.run(
        ["python3", str(project / "scripts" / "spec_gate_cache.py"), "stamp", str(spec), "review"],
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
        check=True,
        capture_output=True,
    )


# --- The mechanical half: runs in BOTH modes, calls no model ------------------------------------


def test_a_write_that_is_not_a_spec_is_ignored(project: Path) -> None:
    (project / "tests" / "test_spawn.py").write_text(SPAWNING_TEST)
    other = project / "docs" / "notes.md"
    other.write_text("not a spec")
    assert run_hook(project, other).returncode == 0


def test_clean_tests_pass_the_hook_in_hitl_mode(project: Path) -> None:
    result = run_hook(project, write_spec(project))
    assert result.returncode == 0, result.stderr


def test_an_undeclared_spawner_blocks_in_hitl_mode(project: Path) -> None:
    """HITL is the default, so a check that only ran under --auto would be a check that never ran."""
    (project / "tests" / "test_spawn.py").write_text(SPAWNING_TEST)
    result = run_hook(project, write_spec(project))
    assert result.returncode == 2
    assert "tests/test_spawn.py" in result.stderr
    assert "@pytest.mark.subprocess" in result.stderr


def test_an_undeclared_spawner_blocks_in_auto_mode_before_any_model_call(project: Path) -> None:
    (project / "tests" / "test_spawn.py").write_text(SPAWNING_TEST)
    result = run_hook(project, write_spec(project), {"SPEC_REVIEW_MODE": "auto"})
    assert result.returncode == 2
    assert not (project / "scripts" / "gate_runner.py.target").exists()


def test_an_unreadable_test_file_blocks_without_the_marker_guidance(project: Path) -> None:
    """Exit 2 is a file the checker could not parse — it has no spawner to declare a marker for."""
    (project / "tests" / "test_broken.py").write_text("def test_a(:\n")
    result = run_hook(project, write_spec(project))
    assert result.returncode == 2
    assert "could not read a file" in result.stderr
    assert "mark each one" not in result.stderr


def test_the_tests_root_is_overridable(project: Path) -> None:
    """A project whose tests are not at tests/ must still be gated, not silently passed."""
    (project / "packages" / "api" / "tests").mkdir(parents=True)
    (project / "packages" / "api" / "tests" / "test_spawn.py").write_text(SPAWNING_TEST)
    result = run_hook(
        project, write_spec(project), {"SUBPROC_CHECK_PATHS": "packages/api/tests"}
    )
    assert result.returncode == 2
    assert "packages/api/tests/test_spawn.py" in result.stderr


def test_an_absent_tests_root_is_reported_but_does_not_block(project: Path) -> None:
    """A fresh repo must still be able to write a spec — the defect is invisibility, not leniency."""
    shutil.rmtree(project / "tests")
    result = run_hook(project, write_spec(project))
    assert result.returncode == 0, result.stderr
    assert "no such test root" in result.stderr


def test_a_declared_and_justified_spawner_passes(project: Path) -> None:
    (project / "tests" / "test_spawn.py").write_text(
        "import subprocess\n\nimport pytest\n\n\n"
        '@pytest.mark.subprocess("drives the real CLI binary")\n'
        "def test_spawns():\n    subprocess.run(['true'])\n"
    )
    assert run_hook(project, write_spec(project)).returncode == 0


def test_break_glass_on_the_subprocess_half_falls_through_to_the_model_half(project: Path) -> None:
    """`exec`ing the bypass logger would end the hook here at exit 0, so the paid gate never ran.

    It pins reachability, not scope: GATE_BYPASS is one unscoped variable, so the same value also
    waives a cross-family NO-GO later in this run. That is audited, not silent — both overrides get
    their own record in gate-overrides.log — and scoping break-glass per gate is separate work.
    """
    (project / "tests" / "test_spawn.py").write_text(SPAWNING_TEST)
    result = run_hook(
        project,
        write_spec(project),
        {"SPEC_REVIEW_MODE": "auto", "GATE_BYPASS": "captain-authorised: harness tests drive real binaries"},
    )
    assert result.returncode == 0, result.stderr
    log = (project / "gate-overrides.log").read_text()
    assert "gate:spec-review-subprocess" in log
    assert "harness tests drive real binaries" in log
    assert (project / "scripts" / "gate_runner.py.target").exists(), "the model half must still run"


# --- The model half: diff-scoped re-gate of an implemented spec ---------------------------------


def test_a_first_gate_judges_the_whole_spec(project: Path) -> None:
    run_hook(project, write_spec(project), {"SPEC_REVIEW_MODE": "auto"})
    assert "## CHANGES SINCE APPROVAL" not in judged(project)


def test_a_spec_approved_but_not_yet_implemented_is_judged_whole(project: Path) -> None:
    """Draft text has never been settled by this gate, so there is nothing to hold out of bounds."""
    spec = write_spec(project, status="pending", review_status="approved")
    stamp(project, spec)
    spec.write_text(spec.read_text().replace("a greeting", "a farewell"))
    run_hook(project, spec, {"SPEC_REVIEW_MODE": "auto"})
    assert "## CHANGES SINCE APPROVAL" not in judged(project)


def test_an_implemented_spec_is_re_gated_on_its_diff(project: Path) -> None:
    spec = write_spec(project, status="done", review_status="approved")
    stamp(project, spec)
    spec.write_text(spec.read_text().replace("a greeting", "a farewell"))

    run_hook(project, spec, {"SPEC_REVIEW_MODE": "auto"})
    bundle = judged(project)

    assert "## PREVIOUSLY APPROVED (reference only)" in bundle
    assert "## SPEC UNDER REVIEW" in bundle
    assert "## CHANGES SINCE APPROVAL" in bundle
    changes = bundle.split("## CHANGES SINCE APPROVAL", 1)[1]
    assert "-- R1.1.1 — `binding: e2e` — the user sees a greeting" in changes
    assert "+- R1.1.1 — `binding: e2e` — the user sees a farewell" in changes


def test_the_re_gate_diff_carries_no_change_but_the_edit(project: Path) -> None:
    """A phantom hunk from a trailing newline is exactly the noise the bundle exists to remove."""
    spec = write_spec(project, status="in-progress", review_status="approved")
    stamp(project, spec)
    spec.write_text(spec.read_text().replace("a greeting", "a farewell") + "\n\n")

    run_hook(project, spec, {"SPEC_REVIEW_MODE": "auto"})
    changes = judged(project).split("## CHANGES SINCE APPROVAL", 1)[1]

    edits = [ln for ln in changes.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    assert edits == [
        "-- R1.1.1 — `binding: e2e` — the user sees a greeting",
        "+- R1.1.1 — `binding: e2e` — the user sees a farewell",
    ], changes


def test_with_no_kept_body_the_whole_spec_is_gated(project: Path) -> None:
    """The safe direction: never let a missing cache entry turn into an unreviewed diff."""
    spec = write_spec(project, status="done", review_status="approved")
    run_hook(project, spec, {"SPEC_REVIEW_MODE": "auto"})
    assert "## CHANGES SINCE APPROVAL" not in judged(project)


def test_an_unchanged_body_is_not_re_judged_at_all(project: Path) -> None:
    """A frontmatter-only edit — `status: done` — must not re-roll a verdict on approved text."""
    spec = write_spec(project, status="pending", review_status="approved")
    stamp(project, spec)
    spec.write_text(spec.read_text().replace("status: pending", "status: done"))
    result = run_hook(project, spec, {"SPEC_REVIEW_MODE": "auto"})
    assert result.returncode == 0
    assert not (project / "scripts" / "gate_runner.py.target").exists()


def test_hitl_mode_never_reaches_the_model_half(project: Path) -> None:
    run_hook(project, write_spec(project))
    assert not (project / "scripts" / "gate_runner.py.target").exists()
