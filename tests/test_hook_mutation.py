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
import os
import signal
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
    project, policy: str = "advisory", path_prefix: str = "", **extra_env: str
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
            **extra_env,
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


def start_hook(
    project, path_prefix: str = "", new_session: bool = False, **extra_env
) -> subprocess.Popen:
    root, phase, store, writer, tmp = project
    payload = json.dumps({"tool_input": {"file_path": str(phase / "handover.md")}})
    env = {
        "PATH": f"{path_prefix}{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
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
            start_new_session=new_session,
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


LEAVES_THE_MUTANT = "printf 'def a():\\n    return 2\\n' > '{target}'; exit 0"
LEAVES_THE_MUTANT_UNWRITABLE = (
    "printf 'def a():\\n    return 2\\n' > '{target}'; chmod 0444 '{target}'; exit 0"
)
# A worker that outlives the SIGTERM, writes a mutant, and only then exits - the shape
# `cosmic-ray exec` really has, where the CLI leader dies at once and the test-command subprocess it
# spawned is still running. The leader blocks so the hook has to be killed to get past it.
WORKER_OUTLIVES_THE_LEADER = (
    "( trap '' TERM; sleep 0.6; printf 'def a():\\n    return 2\\n' > '{target}' )"
    " >/dev/null 2>&1 &\n"
    "touch '{target}.exec-started'\n"
    "sleep 120"
)


def corrupting_project(project, exec_body: str):
    """A real repo whose `cosmic-ray exec` stub leaves its mutant applied and still reports success.

    Returns the in-scope file, the bytes it held before exec, and the PATH prefix carrying the stub.
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
    binaries.mkdir(exist_ok=True)
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
        f"  exec) {exec_body.format(target=target)} ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return target, original, f"{binaries}:"


def test_a_tree_that_differs_after_a_clean_exit_fails_the_hook_under_advisory(project):
    """The comparison is not only for the kill path. An exec that returns 0 and leaves a file changed
    is the same corruption, and `advisory` says nothing about it: the policy selects how much
    authority the SCORE has. The file is put back, the hook fails, and the run's result is void.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, LEAVES_THE_MUTANT)

    result = run_hook(project, policy="advisory", path_prefix=prefix)

    assert target.read_text(encoding="utf-8") == original, "the file is put back"
    assert result.returncode == 2, "a corrupted tree is never advisory"
    assert "TREE INTEGRITY FAILED" in result.stderr
    assert "RESTORED" in result.stderr
    assert "every in-scope file has been put back" in result.stderr
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert "altered by cosmic-ray exec" in row["note"]


def _live_member_source() -> str:
    """Slice `group_has_live_member` out of the hook and run it, the same seam the gate_ci tests use.

    The subject is one bash function over real OS state; it is executed, not read.
    """
    lines = HOOK.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, ln in enumerate(lines) if ln.startswith("group_has_live_member() {")
    )
    close_at = next(i for i, ln in enumerate(lines[start:], start) if ln == "}")
    return "\n".join(lines[start : close_at + 1])


def _ask_live_member(pgid: int) -> int:
    body = f'{_live_member_source()}\ngroup_has_live_member "{pgid}"\n'
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True
    ).returncode


# A: a new session and process group. It forks B; B forks C, which exits at once and becomes a
# ZOMBIE whose parent B is alive. B then leaves the group for one of its own, and A exits - so the
# group holds exactly one entry, a corpse, held there for as long as B lives. This is the shape the
# hook's kill path really produces: `wait` collects the leader, and the test-command process it
# spawned lingers unreaped wherever PID 1 does not reap.
ZOMBIE_ONLY_GROUP = """
import os, sys, time
b = os.fork()
if b == 0:
    if os.fork() == 0:
        os._exit(0)
    time.sleep(0.2)
    os.setpgid(0, 0)
    open(sys.argv[1], "w").write(str(os.getpid()))
    time.sleep(120)
    os._exit(0)
time.sleep(0.5)
os._exit(0)
"""


def test_a_zombie_is_not_a_worker_that_could_still_be_writing(tmp_path):
    """A corpse holds a process-table entry and runs no code, so it holds no working tree.

    `kill -0` answers yes for a zombie, and after the SIGKILL the test-command process cosmic-ray
    spawned is reparented to PID 1 - which clears at once under launchd or systemd and never clears
    in a PID namespace whose PID 1 does not reap, which is what a plain `docker run` CI container
    is. Counted as a live worker it produces the UNKNOWN tree state over a tree the restore proved
    intact - and UNKNOWN is the state this pipeline defines as "inspect by hand" and refuses to let
    GATE_BYPASS waive, so the wrong answer here costs an operator a hard block with no override.
    """
    marker = tmp_path / "b.pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", ZOMBIE_ONLY_GROUP, str(marker)], start_new_session=True
    )
    try:
        assert wait_until(marker.exists, timeout_s=30), (
            "the fixture never built its group"
        )
        proc.wait(timeout=30)
        table = subprocess.run(
            ["ps", "-eo", "pgid=,stat="], capture_output=True, text=True, check=False
        ).stdout
        states = [
            row.split()[1]
            for row in table.splitlines()
            if row.split()[:1] == [str(proc.pid)]
        ]
        assert states and all(s.startswith("Z") for s in states), (
            f"the fixture must leave a zombie-only group; ps says {states}"
        )

        assert _ask_live_member(proc.pid) == 1, (
            "a group holding nothing but a corpse has no member that could still write"
        )
    finally:
        if marker.exists():
            os.kill(int(marker.read_text()), signal.SIGKILL)
        if proc.poll() is None:
            proc.kill()


def test_a_running_member_is_reported_as_one(tmp_path):
    """The companion: the check must still see a member that really can write."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
    )
    try:
        assert _ask_live_member(proc.pid) == 0
    finally:
        proc.kill()


def test_a_worker_that_outlives_the_leader_is_reaped_before_the_tree_is_judged(project):
    """The tree may only be judged once nothing in the group can still write to it.

    `cosmic-ray exec` is a CLI leader plus the test-command subprocesses it spawns, and this shell
    reaps the backgrounded leader asynchronously - so asking `kill -0 <leader>` reports the work
    stopped while a worker is still running, and the restore then hashes and copies a tree something
    else is writing to. Here the worker ignores the SIGTERM and applies its mutant 0.6s later: the
    reap has to wait for the GROUP, or that write is never seen at all and the hook reports a tree
    it never established, over a file cosmic-ray was still holding.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, WORKER_OUTLIVES_THE_LEADER)
    import signal

    started = target.with_suffix(".py.exec-started")
    proc = start_hook(project, path_prefix=prefix, MUTATION_HOOK_BUDGET_S="600")
    try:
        assert wait_until(started.exists, timeout_s=120), (
            "exec never started; the fixture is not exercising the kill path"
        )
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert target.read_text(encoding="utf-8") == original, (
        "the worker's mutant is still applied in the working tree after the kill:\n"
        + target.read_text(encoding="utf-8")
    )
    assert "TREE INTEGRITY" in err, (
        "the worker wrote before it exited, so the tree check must have seen that write - a clean "
        "report here means the restore ran while the group was still alive"
    )
    assert "RESTORED" in err
    assert proc.returncode == 2, "a corrupted tree is never advisory"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes through a read-only file, so the copy back cannot fail",
)
def test_a_restore_that_could_not_put_everything_back_never_says_it_did(project):
    """Only exit 1 out of `restore` means "the tree differed and every file is back".

    Exit 2 means at least one file could NOT be put back - a read-only in-scope file, a copy that
    still differs afterwards - and the operator has to be told to look at the tree. Reported as the
    successful restoration it is not, a mutant stays on disk under a line saying it does not.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, LEAVES_THE_MUTANT_UNWRITABLE)

    try:
        result = run_hook(project, policy="advisory", path_prefix=prefix)
    finally:
        target.chmod(0o644)

    assert target.read_text(encoding="utf-8") != original, (
        "the fixture must leave the mutant on disk; that is the state being reported on"
    )
    assert result.returncode == 2, "a corrupted tree is never advisory"
    assert "NOT RESTORED" in result.stderr
    assert "Inspect the working tree by hand" in result.stderr
    assert "every in-scope file has been put back" not in result.stderr, (
        "nothing may claim a restoration that did not happen"
    )
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert "NOT every file could be put back" in row["note"]


# --- GATE_BYPASS waives a verdict, never a tree nobody established as clean (issue #95) -----------


def overrides_log(root: Path) -> str:
    log = root / "gate-overrides.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def failing_restore_python(binaries: Path, code: int) -> None:
    """A `python3` on PATH whose `mutation_exec_guard.py restore` exits `code`, delegating the rest.

    The three tree states are the restore's own exit codes, and only rc 2 has a fixture that
    produces it naturally (a read-only in-scope file). A restore killed by a signal or crashing
    part-way is the third, and it has no natural fixture at all, so the code is delivered directly
    while every other call runs on the real interpreter.
    """
    shim = binaries / "python3"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "argv = sys.argv[1:]\n"
        "if len(argv) >= 2 and argv[0].endswith('mutation_exec_guard.py') "
        "and argv[1] == 'restore':\n"
        f"    sys.exit({code})\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *argv])\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)


def test_a_bypass_still_waives_a_tree_that_was_put_back(project):
    """The break-glass is not being removed, and this is the state it still covers.

    After a restore that put every in-scope file back, the mutant is provably no longer on disk:
    what is left is a run whose RESULT is void, and a void result is exactly what an audited
    override exists to waive.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, LEAVES_THE_MUTANT)

    result = run_hook(
        project,
        policy="advisory",
        path_prefix=prefix,
        GATE_BYPASS="mutation gate is flaky on this runner",
    )

    assert target.read_text(encoding="utf-8") == original, "the file was put back"
    assert result.returncode == 0, (
        "a restored tree leaves only a verdict, which is waivable"
    )
    assert "gate:mutation:tree-corrupted" in overrides_log(root), (
        "an override that is honoured is an override that is logged"
    )


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes through a read-only file, so the copy back cannot fail",
)
def test_a_bypass_cannot_waive_a_mutant_that_is_still_applied(project):
    """`GATE_BYPASS` used to carry issue #95's damage model straight through the audited path.

    An unscoped break-glass is documented and `GATE_BYPASS_GATES` is opt-in, so an operator waiving
    a flaky mutation gate also waived the tree check underneath it: the restore could not write back
    over a read-only in-scope file, the mutant stayed on disk, and the hook exited 0 with cosmic-ray
    source in the working tree, ready to be committed as authored code.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, LEAVES_THE_MUTANT_UNWRITABLE)

    try:
        result = run_hook(
            project,
            policy="advisory",
            path_prefix=prefix,
            GATE_BYPASS="skipping flaky mutation gate",
        )
    finally:
        target.chmod(0o644)

    assert target.read_text(encoding="utf-8") != original, (
        "the fixture must leave the mutant on disk; that is the state being reported on"
    )
    assert result.returncode == 2, (
        "a tree holding a mutant is not a verdict an override converts"
    )
    assert "NOT BYPASSED" in result.stderr
    assert "STILL APPLIED" in result.stderr
    assert overrides_log(root) == "", (
        "a refused override leaves no record: a line in that log asserts a gate WAS waived"
    )
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run", (
        "the phase record still says the gate reached no verdict"
    )


def test_a_bypass_cannot_waive_a_restore_that_did_not_complete(project):
    """The third state: nothing established what exec left behind, so there is nothing to waive."""
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, LEAVES_THE_MUTANT)
    failing_restore_python(tmp / "bin", 9)

    result = run_hook(
        project,
        policy="advisory",
        path_prefix=prefix,
        GATE_BYPASS="skipping flaky mutation gate",
    )

    assert result.returncode == 2
    assert "NOT BYPASSED" in result.stderr
    assert "UNKNOWN" in result.stderr
    assert "STILL APPLIED" not in result.stderr, (
        "a restore that did not complete establishes neither direction"
    )
    assert overrides_log(root) == ""
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"


def test_a_clean_restore_states_what_it_does_not_establish(project):
    """CLAUDE.md §11: a later stage reads OUTPUT, not source, and it is the CLEAN result that gets
    over-read. The restore's own scope statement was written to a file the hook cat'd only in the
    failure branch, so the one run it is written for - the clean one - never showed it, and the
    operator saw only `hook_mutation.sh`'s statement, which says nothing about tree integrity.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, "exit 0")

    result = run_hook(project, policy="advisory", path_prefix=prefix)

    assert target.read_text(encoding="utf-8") == original, (
        "the fixture leaves the tree intact"
    )
    assert "tree intact" in result.stderr
    assert "SCOPE OF A CLEAN RESULT" in result.stderr


def snapshot_stalling_python(binaries: Path, marker: Path) -> None:
    """A `python3` on PATH that stalls inside `mutation_exec_guard.py snapshot`, delegating the rest.

    It reproduces the real command's first observable action - `snapshot()` mkdirs `<out>/files`
    before it hashes or copies anything - and then holds there, which is the window the hook is
    being asked about. Everything else runs on the real interpreter.
    """
    shim = binaries / "python3"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys, time\n"
        "argv = sys.argv[1:]\n"
        "if len(argv) >= 2 and argv[0].endswith('mutation_exec_guard.py') "
        "and argv[1] == 'snapshot':\n"
        "    (pathlib.Path(argv[-1]) / 'files').mkdir(parents=True, exist_ok=True)\n"
        f"    pathlib.Path({str(marker)!r}).write_text('x')\n"
        "    time.sleep(120)\n"
        "    sys.exit(0)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *argv])\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)


def test_a_kill_while_the_snapshot_is_being_written_never_claims_the_tree_was_altered(
    project,
):
    """A snapshot that has not finished cannot answer anything, so nothing may be claimed from it.

    `SNAP` used to be armed BEFORE the snapshot command ran, while `restore_tree` asked only that
    the directory existed. `snapshot()` mkdirs `<out>/files` as its first action, so the whole write
    was a window where `SNAP` named a manifest-less directory: a signal arriving there ran a restore
    against nothing, which is ERROR, and the hook reported `NOT every file could be put back` and
    exited 2 under advisory - while exec had provably never started and nothing had been altered.

    The signal goes to the process GROUP, which is how the harness delivers it: the snapshot is a
    foreground child, so bash defers its trap until that child returns, and the child returns
    because the same signal reached it.
    """
    root, phase, store, writer, tmp = project
    target, original, prefix = corrupting_project(project, LEAVES_THE_MUTANT)
    import signal

    marker = tmp / "snapshot-started"
    snapshot_stalling_python(tmp / "bin", marker)

    proc = start_hook(project, path_prefix=prefix, new_session=True)
    try:
        assert wait_until(marker.exists, timeout_s=120), (
            "the snapshot was never reached; the fixture is not exercising the window"
        )
        os.killpg(proc.pid, signal.SIGTERM)
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert target.read_text(encoding="utf-8") == original, (
        "exec never ran, so nothing was altered"
    )
    assert "TREE INTEGRITY" not in err, (
        "an unfinished snapshot establishes nothing about the tree, in either direction"
    )
    assert "NOT a gate verdict" in err, "a kill before exec is a kill, and says so"
    assert proc.returncode == 0, (
        "advisory does not block on a kill the gate never answered"
    )
    assert not [c for c in recorded(store) if c["stage"] == "mutation"], (
        "nothing was measured and nothing was corrupted, so there is no mutation row to write"
    )


# --- a budget the hook cannot compute with is a configuration error, not a skipped gate -----------


def test_a_budget_that_is_not_a_positive_integer_stops_the_hook_and_is_recorded(
    scoped_project,
):
    """Unvalidated, MUTATION_HOOK_BUDGET_S went straight into bash arithmetic: a non-numeric value
    left REMAINING_S unset and `set -u` exited 1 before the budget check, the snapshot and exec - the
    whole gate and its tree guard skipped, nothing recorded, and exit 1 is not the blocking code, so
    `enforce` did not stop either. A configuration error stops, named, and leaves its record.
    """
    proj, prefix = scoped_project
    root, phase, store, writer, tmp = proj
    calls = tmp / "cr-calls.log"
    stub = tmp / "bin" / "cosmic-ray"
    stub.write_text(
        f'#!/usr/bin/env bash\necho "$1" >> "{calls}"\nexit 0\n', encoding="utf-8"
    )
    stub.chmod(0o755)

    result = run_hook(proj, path_prefix=prefix, MUTATION_HOOK_BUDGET_S="soon")

    assert result.returncode == 2, "a configuration error stops under every policy"
    assert "MUTATION_HOOK_BUDGET_S='soon'" in result.stderr, "the stop names the value"
    assert "positive integer number of seconds" in result.stderr, (
        "the stop names the shape it accepts"
    )
    assert not calls.exists(), "no cosmic-ray call before the configuration is usable"
    (row,) = [c for c in recorded(store) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert "soon" in row["note"]


def test_policy_off_outranks_a_budget_nobody_can_parse(scoped_project):
    """`off` runs no mutation tool anywhere, so it is asked before the budget is: an operator who
    switched the gate off must not be stopped by the configuration of the gate that is not running.
    """
    proj, prefix = scoped_project
    root, phase, store, writer, tmp = proj
    calls = tmp / "cr-calls.log"
    stub = tmp / "bin" / "cosmic-ray"
    stub.write_text(
        f'#!/usr/bin/env bash\necho "$1" >> "{calls}"\nexit 0\n', encoding="utf-8"
    )
    stub.chmod(0o755)

    result = run_hook(
        proj, policy="off", path_prefix=prefix, MUTATION_HOOK_BUDGET_S="soon"
    )

    assert result.returncode == 0
    assert not calls.exists()
    assert recorded(store) == []


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
