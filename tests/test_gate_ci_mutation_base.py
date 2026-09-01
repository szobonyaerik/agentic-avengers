"""Where the mutation gate's `did-not-run` BINDS, and where it may only be counted and named.

`did-not-run` is reported in every state - that honesty is the whole of issue #97 and nothing here
softens it. The question this file pins is the separate one CLAUDE.md §3a asks of every mechanical
rule: whether the rule may BLOCK. It may, when a base exists and the scope is merely empty, because
an author has a remedy. It may not on a push to the default branch, where the PR base sha is empty
and the merge-base with the default branch is HEAD itself: nothing exists to compare against, in a
checkout where nothing is uncommitted either, so the failure has no remedy at all and a rule whose
remedy is unavailable is a wedge rather than a gate.

The two states must stay distinguishable in the OUTPUT, not just in the exit code, so a reader can
tell "there was nothing to compare against" from "there was, and it was empty".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_CI = ROOT / "scripts" / "gate_ci.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is bash's own evaluation of gate_ci.sh's routing, which only a shell performs"
)


def _routing_source() -> str:
    """Slice the mutation routing helpers out of `gate_ci.sh`.

    `gate_ci.sh` runs its whole floor on execution - the suite, mutation, every artifact check - so
    it cannot be sourced to reach one function. This is the same slice-and-run seam
    `tests/test_gate_ci_cross_family.py` uses, over the three contiguous definitions the routing is
    made of.
    """
    lines = GATE_CI.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("mutation_fail() {"))
    open_at = next(
        i
        for i, ln in enumerate(lines)
        if ln.startswith("mutation_nothing_in_scope() {")
    )
    close_at = next(i for i, ln in enumerate(lines[open_at:], open_at) if ln == "}")
    return "\n".join(lines[start : close_at + 1])


def _run(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    body = (
        "set -uo pipefail\n"
        'record_fail() { echo "RECORD_FAIL:$1"; }\n'
        f"{_routing_source()}\n"
        f"{script}\n"
    )
    return subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
    ):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "first"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    (root / "a.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "second"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    return root


def _sha(repo: Path, rev: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", rev],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class TestBaseState:
    def test_a_base_that_is_head_itself_is_unknowable(self, repo):
        """The default-branch push. `git merge-base HEAD origin/HEAD` resolves to HEAD."""
        proc = _run(f'mutation_base_state "{_sha(repo, "HEAD")}"', repo)

        assert proc.stdout.strip() == "unknowable"

    def test_an_empty_base_is_unknowable(self, repo):
        proc = _run('mutation_base_state ""', repo)

        assert proc.stdout.strip() == "unknowable"

    def test_a_real_ancestor_is_a_known_base(self, repo):
        proc = _run(f'mutation_base_state "{_sha(repo, "HEAD~1")}"', repo)

        assert proc.stdout.strip() == "known"


class TestRouting:
    def test_an_unknowable_base_does_not_block_under_enforce(self, repo):
        proc = _run(
            'MUTATION_POLICY=enforce; mutation_nothing_in_scope "unknowable"; echo "EXIT:$?"',
            repo,
        )

        assert "RECORD_FAIL" not in proc.stdout
        assert "EXIT:0" in proc.stdout

    def test_a_known_base_with_an_empty_scope_still_blocks_under_enforce(self, repo):
        proc = _run('MUTATION_POLICY=enforce; mutation_nothing_in_scope "known"', repo)

        assert "RECORD_FAIL:mutation:did-not-run" in proc.stdout

    def test_both_states_report_that_the_gate_did_not_run(self, repo):
        """The honesty is not softened - only where the rule binds changes."""
        for state in ("unknowable", "known"):
            proc = _run(
                f'MUTATION_POLICY=advisory; mutation_nothing_in_scope "{state}"', repo
            )

            assert "did not run" in proc.stderr, state
            assert "NOT a score" in proc.stderr, state

    def test_the_two_states_are_distinguishable_in_the_output(self, repo):
        """A reader must tell 'nothing to compare against' from 'there was, and it was empty'."""
        unknowable = _run(
            'MUTATION_POLICY=advisory; mutation_nothing_in_scope "unknowable"', repo
        ).stderr
        known = _run(
            'MUTATION_POLICY=advisory; mutation_nothing_in_scope "known"', repo
        ).stderr

        assert "NO COMPARISON BASE EXISTS" in unknowable
        assert "NO COMPARISON BASE EXISTS" not in known
        assert "a base EXISTS" in known
