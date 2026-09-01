"""The mutation gate leaves a record when it could NOT run (agents#16, the class of issue #69).

`MUTATION_POLICY=advisory` was set for two whole phases while the gate never once ran: no
`cosmic-ray.toml` at the repo root and the tool not installed. The hook said so on stderr and exited
0 - correctly, since advisory never blocks - but nothing durable distinguished "the gate did not
run" from "the gate ran and found nothing". A hypothesis then settled on the premise that
`MUTATION_POLICY=advisory` was its one changed variable, while the gate under test had never
executed, and what actually caught defects was hand-built drills the implementer substituted after
the gate produced nothing.

Advisory must keep NOT blocking. That is a deliberate decision, not the defect. What is asserted
here is that the absence is now written where a later reader looks.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import DOUBLE  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hook_mutation.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash hook and the writer process it shells out to"
)


@pytest.fixture
def project(tmp_path: Path):
    """A project with a phase and NO cosmic-ray.toml - the measured shape exactly."""
    root = tmp_path / "proj"
    phase = root / "docs" / "features" / "demo" / "phases" / "8-auth"
    phase.mkdir(parents=True)
    store = tmp_path / "store"
    store.mkdir()
    writer = tmp_path / "fm-pipeline-metrics.sh"
    writer.write_text(DOUBLE, encoding="utf-8")
    writer.chmod(0o755)
    return root, phase, store, writer, tmp_path


def run_hook(
    project, policy: str = "advisory", path_prefix: str = ""
) -> subprocess.CompletedProcess:
    root, phase, store, writer, tmp = project
    payload = json.dumps({"tool_input": {"file_path": str(phase / "handover.md")}})
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{path_prefix}{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp),
            "CLAUDE_PROJECT_DIR": str(root),
            "MUTATION_POLICY": policy,
            "AVENGER_METRICS_CMD": str(writer),
            "AVENGER_METRICS_PROJECT": "unit-test",
            "AVENGER_METRICS_LOG": str(tmp / "diagnostics.log"),
            "DOUBLE_LOG": str(tmp / "calls.log"),
            "DOUBLE_STORE": str(store),
        },
    )


def recorded(store: Path) -> list[dict]:
    record = store / "phase-08.json"
    if not record.exists():
        return []
    return json.loads(record.read_text(encoding="utf-8"))["gate_calls"]


def test_a_missing_config_is_recorded_and_still_does_not_block(project) -> None:
    result = run_hook(project)

    assert result.returncode == 0, "advisory never blocks — that is not the defect"
    assert "could not run" in result.stderr
    (row,) = [c for c in recorded(project[2]) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert row["verdict"] == "REVIEW", "a gate that did not run reached no verdict"
    assert "cosmic-ray.toml missing" in row["note"]


def test_the_record_is_written_under_enforce_too(project) -> None:
    """Under `enforce` the hook blocks, and the reason it blocked is still worth recording."""
    result = run_hook(project, policy="enforce")

    assert result.returncode == 2
    assert [c for c in recorded(project[2]) if c["stage"] == "mutation"]


def test_a_run_that_records_nothing_leaves_no_row(project) -> None:
    """`MUTATION_POLICY=off` runs no mutation tool anywhere, so there is no absence to report."""
    result = run_hook(project, policy="off")

    assert result.returncode == 0
    assert recorded(project[2]) == []


def test_a_policy_nobody_recognises_fails_closed(project) -> None:
    """A MUTATION_POLICY the hook does not know must STOP it, never select a default.

    The three values mean different things and one of them switches the gate off entirely, so a
    typo that resolves to `off` is a mutation gate nobody turned off and nobody notices is gone -
    the same silent yes as a gate that never ran, chosen by a slip of the keyboard rather than a
    missing config.
    """
    result = run_hook(project, policy="advisroy")

    assert result.returncode == 2, "an unrecognised policy is a stop, not a default"
    assert "enforce|advisory|off" in result.stderr, (
        "the stop names what the valid values are"
    )


# --- nothing in scope is NOT a clean gate (issue #97) ---------------------------------------------


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    )


@pytest.fixture
def scoped_project(project):
    """A real repository with a committed file, a cosmic-ray config, and a `cosmic-ray` stub.

    The stub's `init` writes a session holding one mutant in the committed file. Nothing in the
    working tree has changed, so the gate has no line to measure - the state that used to arrive as
    GO, "all N mutants fall outside the diff".
    """
    root, phase, store, writer, tmp = project
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    target = root / "pkg" / "app.py"
    target.write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "cosmic-ray.toml").write_text(
        '[cosmic-ray]\nmodule-path = "pkg"\ntimeout = 10\ntest-command = "true"\n',
        encoding="utf-8",
    )
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("add", "-A"),
        ("commit", "-q", "-m", "root"),
    ):
        git(root, *args)

    binaries = tmp / "bin"
    binaries.mkdir()
    maker = binaries / "make_session.py"
    maker.write_text(
        "import sys\n"
        "from cosmic_ray.work_db import WorkDB, use_db\n"
        "from cosmic_ray.work_item import MutationSpec, WorkItem\n"
        "with use_db(sys.argv[1], WorkDB.Mode.create) as db:\n"
        "    db.add_work_item(WorkItem.single('m0', MutationSpec(\n"
        f"        module_path=r'{target}', operator_name='core/NumberReplacer', occurrence=0,\n"
        "        start_pos=(2, 4), end_pos=(2, 12))))\n",
        encoding="utf-8",
    )
    stub = binaries / "cosmic-ray"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        f'  init) exec "{sys.executable}" "{maker}" "$3" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return project, f"{binaries}:"


def test_nothing_in_scope_is_recorded_as_did_not_run_and_still_does_not_block(
    scoped_project,
):
    """Issue #97's second instance at the hook.

    The old path ran `cr-filter-git`, whose `git diff` cannot see an untracked file at all, and the
    scorer turned an all-skipped session into GO. Now the absence of a measurement is recorded
    exactly like a gate that could not run, and advisory still never blocks.
    """
    proj, prefix = scoped_project

    result = run_hook(proj, path_prefix=prefix)

    assert result.returncode == 0, "advisory never blocks"
    (row,) = [c for c in recorded(proj[2]) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert row["verdict"] == "REVIEW", "a gate that measured nothing reached no verdict"
    assert "did not run" in row["note"]


def test_nothing_in_scope_stops_the_phase_under_enforce(scoped_project):
    proj, prefix = scoped_project

    result = run_hook(proj, policy="enforce", path_prefix=prefix)

    assert result.returncode == 2
    assert [c for c in recorded(proj[2]) if c["stage"] == "mutation"]
