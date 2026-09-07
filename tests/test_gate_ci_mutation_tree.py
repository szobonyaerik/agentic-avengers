"""CI's `cosmic-ray exec` is bracketed the same way the hook's is (issue #95).

A tree that differs after exec is a FAILED RUN whatever `MUTATION_POLICY` says, and that holds on
the KILL PATH as well as after a clean exit: `gate_ci.sh` is not CI-only, because
`agents/avenger-verifier.md` has the Verifier run `gate_ci.sh --full` in the LIVE working copy, and
an agent Bash call interrupted mid-exec leaves the mutant in an uncommitted tree nobody discards.
`mutation_exec_guarded` is sliced out and run against a `cosmic-ray` stub, the same seam
`tests/test_gate_ci_mutation_base.py` uses.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_CI = ROOT / "scripts" / "gate_ci.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash function of gate_ci.sh wrapping a cosmic-ray process"
)


def _guarded_source() -> str:
    lines = GATE_CI.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, ln in enumerate(lines) if ln.startswith("mutation_exec_guarded() {")
    )
    close_at = next(i for i, ln in enumerate(lines[start:], start) if ln == "}")
    return "\n".join(lines[start : close_at + 1])


@pytest.fixture
def repo(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    target = root / "pkg" / "app.py"
    target.write_text("def a():\n    return 1\n", encoding="utf-8")
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("add", "-A"),
        ("commit", "-q", "-m", "root"),
    ):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)

    from cosmic_ray.work_db import WorkDB, use_db
    from cosmic_ray.work_item import MutationSpec, WorkItem

    session = tmp_path / "session.sqlite"
    with use_db(str(session), WorkDB.Mode.create) as db:
        db.add_work_item(
            WorkItem.single(
                "m0",
                MutationSpec(
                    module_path="pkg/app.py",
                    operator_name="core/NumberReplacer",
                    occurrence=0,
                    start_pos=(2, 4),
                    end_pos=(2, 12),
                ),
            )
        )
    return root, session, target


def _start(repo, exec_body: str, tmp_path: Path) -> subprocess.Popen[str]:
    root, session, target = repo
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    stub = binaries / "cosmic-ray"
    stub.write_text(
        f'#!/usr/bin/env bash\ncase "$1" in\n  exec) {exec_body} ;;\n  *) exit 0 ;;\nesac\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    body = (
        "set -uo pipefail\n"
        f'SCRIPT_DIR="{ROOT / "scripts"}"; ROOT="{root}"; TMP="{work / "report.txt"}"\n'
        f"{_guarded_source()}\n"
        f'mutation_exec_guarded "{root / "cosmic-ray.toml"}" "{session}" "{work / "tree"}"\n'
        'echo "RC:$?"\n'
    )
    return subprocess.Popen(
        ["bash", "-c", body],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(root),
        env={
            "PATH": f"{binaries}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
    )


def _run(repo, exec_body: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    proc = _start(repo, exec_body, tmp_path)
    out, err = proc.communicate()
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _wait_until(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_clean_exec_over_an_intact_tree_is_0(repo, tmp_path):
    proc = _run(repo, "exit 0", tmp_path)

    assert "RC:0" in proc.stdout, proc.stderr


def test_an_exec_that_leaves_a_mutant_is_3_and_the_file_is_put_back(repo, tmp_path):
    root, session, target = repo
    original = target.read_text(encoding="utf-8")

    proc = _run(
        repo, f"printf 'def a():\\n    return 2\\n' > '{target}'; exit 0", tmp_path
    )

    assert "RC:3" in proc.stdout, proc.stderr
    assert target.read_text(encoding="utf-8") == original
    assert "RESTORED" in proc.stderr, "the restoration is on stderr where CI shows it"


def test_an_errored_exec_over_an_intact_tree_is_1(repo, tmp_path):
    proc = _run(repo, "exit 7", tmp_path)

    assert "RC:1" in proc.stdout, proc.stderr


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes through a read-only file, so the copy back cannot fail",
)
def test_a_restore_that_cannot_put_the_file_back_is_5_and_never_claims_the_checkout_is_clean(
    repo, tmp_path
):
    """Exit 1 and exit 2 out of `restore` do not mean the same thing, and CI must not conflate them.

    `restore` returns 2 whenever the copy back raises - a read-only in-scope file, a restrictive
    checkout mode - or the content still differs after it. Collapsed into the same code as "the tree
    differed and every file is back", CI printed the clean-checkout claim over a checkout that still
    held the mutant.
    """
    root, session, target = repo
    mutant = "def a():\n    return 2\n"

    try:
        proc = _run(
            repo,
            f"printf 'def a():\\n    return 2\\n' > '{target}'; chmod 0444 '{target}'; exit 0",
            tmp_path,
        )
    finally:
        target.chmod(0o644)

    assert "RC:5" in proc.stdout, proc.stderr
    assert mutant == target.read_text(encoding="utf-8"), (
        "the fixture must leave the mutant on disk; that is the state being reported on"
    )
    assert "NOT RESTORED" in proc.stderr
    assert "NOT clean" in proc.stderr
    assert "was put back from the pre-exec snapshot" not in proc.stderr, (
        "the headline must not claim a restoration that did not happen"
    )


def test_a_kill_mid_exec_puts_the_working_tree_back_before_exiting(repo, tmp_path):
    """The kill path is where issue #95's damage happens, and CI is not exempt from it.

    `agents/avenger-verifier.md` has the Verifier run `gate_ci.sh --full` in the LIVE working copy,
    so an interrupted or timed-out agent Bash call is a real signal arriving mid-exec. With exec run
    in the foreground and no trap, the restore happened only after a normal return and the mutant
    stayed applied in the implementer's uncommitted tree. The property is the file's bytes after the
    signal, not the presence of a trap.
    """
    root, session, target = repo
    original = target.read_text(encoding="utf-8")

    proc = _start(
        repo, f"printf 'def a():\\n    return 2\\n' > '{target}'; sleep 60", tmp_path
    )
    try:
        applied = _wait_until(
            lambda: target.read_text(encoding="utf-8") != original, timeout_s=30
        )
        assert applied, (
            "the stub never applied its mutant; the fixture is not exercising exec"
        )
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert target.read_text(encoding="utf-8") == original, (
        "a mutant is still applied in the working tree after the signal:\n"
        + target.read_text(encoding="utf-8")
    )
    assert "RESTORED" in err, "the restoration is announced, never silent"
    assert proc.returncode != 0, "an interrupted exec is not a passing gate"


def test_a_signal_during_the_restore_does_not_kill_the_restore(repo, tmp_path):
    """The restore is the one thing answering the snapshot, so it must not run unprotected.

    A signal arriving mid-exec was already covered; this is the window after exec returns, where the
    traps used to be cleared before the restore started. The Verifier runs `gate_ci.sh --full` in
    the live working copy, so a timed-out agent Bash call lands here just as easily - and with the
    default disposition bash died at 143 while the restore was in flight: no classification, no
    `inspect it by hand`, and a mutant left on disk through the very code added to remove it. With
    the traps installed bash defers the signal until the foreground restore returns, so the files go
    back before anything exits.

    bash delivers a deferred trap the moment that foreground command returns, ahead of the lines
    that would classify its exit code - so the run reports that the restore's verdict was never
    read, rather than claiming an outcome this shell did not observe.
    """
    root, session, target = repo
    original = target.read_text(encoding="utf-8")
    started = tmp_path / "restore-started"
    slow = tmp_path / "bin"
    slow.mkdir(parents=True, exist_ok=True)
    (slow / "python3").write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "argv = sys.argv[1:]\n"
        "if len(argv) >= 2 and argv[0].endswith('mutation_exec_guard.py') "
        "and argv[1] == 'restore':\n"
        f"    open({str(started)!r}, 'w').write('x')\n"
        "    time.sleep(1.5)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *argv])\n",
        encoding="utf-8",
    )
    (slow / "python3").chmod(0o755)

    proc = _start(
        repo, f"printf 'def a():\\n    return 2\\n' > '{target}'; exit 0", tmp_path
    )
    try:
        assert _wait_until(started.exists, timeout_s=60), (
            "the restore never started; the fixture is not exercising this window"
        )
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert target.read_text(encoding="utf-8") == original, (
        "the restore was cut short and left the mutant on disk:\n"
        + target.read_text(encoding="utf-8")
    )
    assert "TREE INTEGRITY" in err, (
        "a run interrupted during its restore must still say what it established - dying at the "
        "default disposition reports nothing at all"
    )
    assert "inspect it by hand" in err
    assert proc.returncode == 2, (
        f"the gate's own exit code, not the shell's death by signal (got {proc.returncode})"
    )


def test_a_restore_that_did_not_complete_is_not_reported_as_a_changed_tree(
    repo, tmp_path
):
    """`restore` exit 2 and any other non-zero code do not support the same claim.

    Exit 2 establishes that the tree differed and something could not be put back. A restore killed
    by a signal or crashing establishes neither, so reporting it as "exec left the working tree
    changed" asserts a fact nobody measured. The operator action and the return code are the same;
    only the claim differs.
    """
    root, session, target = repo
    fake = tmp_path / "bin" / "python3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "argv = sys.argv[1:]\n"
        "if len(argv) >= 2 and argv[0].endswith('mutation_exec_guard.py') "
        "and argv[1] == 'restore':\n"
        "    sys.exit(9)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *argv])\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    proc = _run(repo, "exit 0", tmp_path)

    assert "RC:5" in proc.stdout, proc.stderr
    assert "TREE INTEGRITY UNKNOWN" in proc.stderr
    assert "left the working tree changed" not in proc.stderr, (
        "a restore that did not complete measured nothing about what exec left behind"
    )
    assert "inspect it by hand" in proc.stderr


def test_a_clean_restore_states_what_it_does_not_establish(repo, tmp_path):
    """CLAUDE.md §11: a later stage reads OUTPUT, not source, and it is the CLEAN result that gets
    over-read. Held in a file and shown only in the failure branch, the tree guard's scope statement
    reached the reader on every run except the ones it is written for.
    """
    proc = _run(repo, "exit 0", tmp_path)

    assert "RC:0" in proc.stdout, proc.stderr
    assert "SCOPE OF A CLEAN RESULT" in proc.stderr
    assert "tree intact" in proc.stderr


def test_a_tree_that_cannot_be_snapshotted_never_starts_exec(repo, tmp_path):
    root, session, target = repo
    target.unlink()  # the session names a file the tree lacks
    marker = tmp_path / "exec-ran"

    proc = _run(repo, f"touch '{marker}'; exit 0", tmp_path)

    assert "RC:4" in proc.stdout, proc.stderr
    assert not marker.exists(), "no snapshot, no exec"
