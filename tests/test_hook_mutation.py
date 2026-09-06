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


# --- a killed exec must not leave a mutant in the working tree (issue #95) ------------------------

REAL_SOURCE = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def capacity_ok(open_orders, limit):\n"
    "    if open_orders < limit:\n"
    "        return True\n"
    "    return False\n"
)


@pytest.fixture
def live_project(project):
    """A real repository, a real cosmic-ray, and a test command slow enough to be killed mid-mutant.

    `pkg/app.py` is UNTRACKED, so every line of it is in scope. The test command only sleeps: what
    is under test is not whether a mutant is killed, but what the working tree holds when the hook
    itself is killed while a mutant is applied - the harness's timeout path, issue #95's mechanism.
    """
    root, phase, store, writer, tmp = project
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (root / "cosmic-ray.toml").write_text(
        "[cosmic-ray]\n"
        'module-path = "pkg"\n'
        "timeout = 60\n"
        "excluded-modules = []\n"
        f"test-command = \"{sys.executable} -c 'import time; time.sleep(3)'\"\n"
        "\n"
        "[cosmic-ray.distributor]\n"
        'name = "local"\n',
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
    target = pkg / "app.py"
    target.write_text(REAL_SOURCE, encoding="utf-8")
    return project, target


def start_hook(project, **extra_env) -> subprocess.Popen:
    root, phase, store, writer, tmp = project
    payload = json.dumps({"tool_input": {"file_path": str(phase / "handover.md")}})
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp),
        "CLAUDE_PROJECT_DIR": str(root),
        "MUTATION_POLICY": "advisory",
        "AVENGER_METRICS_CMD": str(writer),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp / "diagnostics.log"),
        "DOUBLE_LOG": str(tmp / "calls.log"),
        "DOUBLE_STORE": str(store),
        **extra_env,
    }
    payload_file = tmp / "payload.json"
    payload_file.write_text(payload, encoding="utf-8")
    with payload_file.open("r", encoding="utf-8") as stdin:
        return subprocess.Popen(
            ["bash", str(HOOK)],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )


def wait_until(predicate, timeout_s: float) -> bool:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_hook_killed_mid_exec_leaves_the_tree_exactly_as_it_found_it(live_project):
    """Issue #95's reproduction, kept as the regression test.

    cosmic-ray applies each mutant IN PLACE and reverts it in a `finally`. The hook's kill path
    SIGTERMs then SIGKILLs the child's process group, and neither signal runs a `finally`, so the
    mutant applied at that moment stayed on disk - and was committed as authored code, four times
    in one measured phase. The harness's timeout IS the expected path on a real suite (the hook's
    own comments cite 569s, 3818s and 4276s against 300s), so this is the path that must restore.
    """
    project, target = live_project
    import signal

    proc = start_hook(project, MUTATION_HOOK_BUDGET_S="600")
    try:
        mutated = wait_until(
            lambda: target.read_text(encoding="utf-8") != REAL_SOURCE, timeout_s=120
        )
        assert mutated, (
            "cosmic-ray never applied a mutant; the fixture is not exercising exec"
        )
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert target.read_text(encoding="utf-8") == REAL_SOURCE, (
        "a mutant is still applied in the working tree after the kill:\n"
        + target.read_text(encoding="utf-8")
    )
    assert proc.returncode == 2, (
        "a corrupted tree is a hook failure regardless of policy - advisory does not soften it"
    )
    assert "RESTORED" in err, "the restoration is announced, never silent"
    assert "NOT a gate verdict" in err


def test_a_tree_that_differs_after_a_clean_exit_fails_the_hook_under_advisory(project):
    """The comparison is not only for the kill path. An exec that returns 0 and leaves a file changed
    is the same corruption, and `advisory` says nothing about it: the policy selects how much
    authority the SCORE has. The file is put back, the hook fails, and the run's result is void.
    """
    root, phase, store, writer, tmp = project
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    target = root / "pkg" / "app.py"
    original = "def a():\n    return 1\n"
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
    target.write_text(original, encoding="utf-8")  # untracked: every line in scope

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
        # exec "reverts" nothing: the mutant stays, and the command still reports success.
        f"  exec) printf 'def a():\\n    return 2\\n' > '{target}'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result = run_hook(project, policy="advisory", path_prefix=f"{binaries}:")

    assert target.read_text(encoding="utf-8") == original, "the file is put back"
    assert result.returncode == 2, "a corrupted tree is never advisory"
    assert "TREE INTEGRITY FAILED" in result.stderr
    assert "RESTORED" in result.stderr
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert "altered by cosmic-ray exec" in row["note"]


def test_an_exec_that_cannot_finish_inside_the_budget_is_refused_up_front(live_project):
    """Where the kill can be predicted, exec is not started. The baseline's measured wall clock
    times the pending mutants is the estimate available on every run; here one test run takes 3s
    and the budget left after the headroom is nothing, so the hook refuses rather than begin a run
    it would have to kill mid-mutant. Nothing was mutated, and the refusal is recorded as the gate
    not having run - never as a score.
    """
    project, target = live_project
    root, phase, store, writer, tmp = project

    proc = start_hook(project, MUTATION_HOOK_BUDGET_S="40")
    _, err = proc.communicate(timeout=120)

    assert target.read_text(encoding="utf-8") == REAL_SOURCE, "exec never started"
    assert "OVER BUDGET" in err
    assert "refused to start" in err
    assert proc.returncode == 0, (
        "advisory: the gate did not run, and that does not block"
    )
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert "refused to start" in row["note"]


def test_policy_off_still_exits_before_any_cosmic_ray_call(live_project):
    """The workaround the issue names must keep working exactly as it did: `off` runs no mutation
    tool anywhere, so neither the budget check nor the snapshot has anything to bracket.
    """
    project, target = live_project
    root, phase, store, writer, tmp = project
    calls = tmp / "cr-calls.log"
    binaries = tmp / "bin"
    binaries.mkdir()
    stub = binaries / "cosmic-ray"
    stub.write_text(
        f'#!/usr/bin/env bash\necho "$1" >> "{calls}"\nexit 0\n', encoding="utf-8"
    )
    stub.chmod(0o755)

    result = run_hook(project, policy="off", path_prefix=f"{binaries}:")

    assert result.returncode == 0
    assert not calls.exists(), "no cosmic-ray call of any kind under off"
    assert recorded(store) == []
