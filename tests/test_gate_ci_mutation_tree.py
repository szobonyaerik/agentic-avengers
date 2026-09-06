"""CI's `cosmic-ray exec` is bracketed the same way the hook's is (issue #95).

`gate_ci.sh` runs exec on a CI checkout it will throw away, so it has no kill path of its own to
guard - but the comparison after a clean exit is the same question, and a tree that differs after
exec is a FAILED RUN whatever `MUTATION_POLICY` says. `mutation_exec_guarded` is sliced out and run
against a `cosmic-ray` stub, the same seam `tests/test_gate_ci_mutation_base.py` uses.
"""

from __future__ import annotations

import subprocess
import sys
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


def _run(repo, exec_body: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
        env={
            "PATH": f"{binaries}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
    )


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


def test_a_tree_that_cannot_be_snapshotted_never_starts_exec(repo, tmp_path):
    root, session, target = repo
    target.unlink()  # the session names a file the tree lacks
    marker = tmp_path / "exec-ran"

    proc = _run(repo, f"touch '{marker}'; exit 0", tmp_path)

    assert "RC:4" in proc.stdout, proc.stderr
    assert not marker.exists(), "no snapshot, no exec"
