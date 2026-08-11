"""Tests for the read path's diff scope: you answer for the artifacts you changed.

The check shipped fail-closed over the whole of `docs/features`, which fails any repository already
running the pipeline the moment it upgrades — every historical handover is over the cap and none
carries `readers:`, and none of that is something the current session did. Scoping enforcement to
the diff is the rule `verifier_bundle_scope.py`, `spec_gate_cache.py` and the mutation gate already
run on, and it needs no grandfathering list to maintain.

The dangerous direction runs BOTH ways, so both are pinned: an unchanged artifact must not block
(the hostage failure the scoping removes), and a changed one must still block (the reason the check
exists at all). git is the authority for that question, so these run against real repositories
rather than a stub whose answers would be the test's own.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from doc_read_path import (  # noqa: E402
    HANDOVER_MAX_BYTES,
    changed_paths,
    check_artifacts,
    main,
)

pytestmark = pytest.mark.subprocess(
    "the scope is whatever git reports; a stubbed git would only ever test the stub"
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


def repo(root: Path) -> Path:
    git(root, "init", "-q")
    git(root, "config", "user.email", "pipeline@example.com")
    git(root, "config", "user.name", "pipeline")
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    return root


def write_card(root: Path, body: str) -> Path:
    d = root / "docs" / "features" / "demo" / "phases" / "1-webhook"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "handover.md"
    path.write_text(f"---\nfeature: demo\nreaders: spec-writer @ per spec\n---\n{body}\n")
    return path


def project_under(toplevel: Path) -> Path:
    """A pipeline root that is NOT the git root — a monorepo package, or a vendored install."""
    project = toplevel / "packages" / "api"
    project.mkdir(parents=True, exist_ok=True)
    return project


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "artifacts")


def over_cap() -> str:
    return "x" * (HANDOVER_MAX_BYTES + 1)


# --- history is visible, never a hostage ----------------------------------------------------------

def test_an_unchanged_non_conforming_artifact_does_not_block(tmp_path: Path) -> None:
    write_card(repo(tmp_path), over_cap())
    commit_all(tmp_path)
    assert check_artifacts(tmp_path) == []


def test_it_is_counted_on_stderr_and_the_exit_code_is_still_zero(tmp_path: Path, capsys) -> None:
    write_card(repo(tmp_path), over_cap())
    commit_all(tmp_path)
    assert main(["check", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert "1 pre-existing artifact(s)" in err
    assert "over the handover cap" in err


def test_nothing_is_said_when_all_the_history_conforms(tmp_path: Path, capsys) -> None:
    write_card(repo(tmp_path), "short")
    commit_all(tmp_path)
    assert check_artifacts(tmp_path) == []
    assert "pre-existing" not in capsys.readouterr().err


# --- but what this session touched still blocks ---------------------------------------------------

def test_a_changed_artifact_still_blocks(tmp_path: Path) -> None:
    write_card(repo(tmp_path), "short")
    commit_all(tmp_path)
    write_card(tmp_path, over_cap())
    problems = check_artifacts(tmp_path)
    assert len(problems) == 1 and "cap" in problems[0]


def test_an_untracked_artifact_is_in_scope_before_it_is_ever_committed(tmp_path: Path) -> None:
    """A card written this session has no commit yet; that is exactly when the cap must bite."""
    repo(tmp_path)
    (write_card(tmp_path, over_cap()))
    assert check_artifacts(tmp_path)


def test_a_staged_artifact_is_in_scope(tmp_path: Path) -> None:
    repo(tmp_path)
    write_card(tmp_path, over_cap())
    git(tmp_path, "add", "-A")
    assert check_artifacts(tmp_path)


# --- a root below the git root --------------------------------------------------------------------
# The three commands do not share a path convention: `ls-files` prints cwd-relative names and `diff`
# does too under `diff.relative`. Read as if they agreed, every artifact under a non-toplevel root
# resolves to a path that matches nothing — and the check goes on reporting itself clean while
# enforcing nothing. These pin the convention at the boundary, not at one call site.

def test_an_untracked_artifact_under_a_subdirectory_root_is_still_enforced(tmp_path: Path) -> None:
    repo(tmp_path)
    project = project_under(tmp_path)
    write_card(project, over_cap())
    assert any(str(project) in problem for problem in check_artifacts(project))


def test_a_modified_artifact_under_a_subdirectory_root_survives_diff_relative(tmp_path: Path) -> None:
    """`diff.relative=true` is a user's own git config, and it must not disarm the check."""
    repo(tmp_path)
    project = project_under(tmp_path)
    write_card(project, "short")
    commit_all(tmp_path)
    git(tmp_path, "config", "diff.relative", "true")
    write_card(project, over_cap())
    assert check_artifacts(project)


def test_the_scope_is_absolute_whatever_the_root(tmp_path: Path) -> None:
    repo(tmp_path)
    project = project_under(tmp_path)
    card = write_card(project, "short")
    assert card.resolve() in (changed_paths(project) or set())


# --- the full audit -------------------------------------------------------------------------------

def test_all_enforces_what_the_diff_scope_carries(tmp_path: Path) -> None:
    write_card(repo(tmp_path), over_cap())
    commit_all(tmp_path)
    assert check_artifacts(tmp_path, enforce_all=True)
    assert main(["check", "--all", str(tmp_path)]) == 1
    assert main(["check", str(tmp_path)]) == 0


# --- git cannot answer ----------------------------------------------------------------------------

def test_a_non_git_directory_enforces_nothing_and_says_so(tmp_path: Path, capsys) -> None:
    """Unknowable scope enforces nothing. Falling back to everything is the hostage failure again."""
    write_card(tmp_path, over_cap())
    assert changed_paths(tmp_path) is None
    assert check_artifacts(tmp_path) == []
    err = capsys.readouterr().err
    assert "unknowable" in err and "--all" in err


def test_a_non_git_directory_is_still_auditable(tmp_path: Path) -> None:
    write_card(tmp_path, over_cap())
    assert check_artifacts(tmp_path, enforce_all=True)
