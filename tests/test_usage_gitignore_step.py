"""The `.gitignore` step `docs/USAGE.md` hands an operator is EXECUTED here, not read.

Section C - `install.sh` + opencode - is the one documented install route that reaches neither of
the other two ignore lists: `install.sh` vendors no `.gitignore` and `.opencode/` ships no
`pipeline-init` command. So the shell block in that section is the only thing standing between a
freshly assembled `.env` - which step 5 creates holding a live `OPENROUTER_API_KEY` - and the
`git add -A` the pipeline's own commit steps run.

That block appends to a file, and appending is the defect this whole change exists to close. A bare
`>>` onto a `.gitignore` whose last line has no trailing newline joins BYTES, exactly as the `cat`
`env_assemble.join_text` replaced does: the previous last pattern and the appended one fuse into one
line that matches neither, so the operator loses the rule they already had AND the rule the step was
added for, and `grep -qxF` then fails to match on the next run and appends again rather than
self-correcting.

The block is an executable procedure this repository publishes for an operator to run verbatim -
that is the contract under test - so it is run verbatim, in a real git repository, and **git is
asked what its own rules match**. Nothing here asserts about the text of the block or of the
`.gitignore` it produces.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import env_assemble  # noqa: E402

USAGE = REPO / "docs" / "USAGE.md"

#: The step, as the document publishes it. Located by its loop head so an edit that renames a
#: variable still finds it; an edit that removes or duplicates the step fails here rather than
#: silently testing nothing.
STEP = re.compile(r"^for pattern in .*?^done$", re.DOTALL | re.MULTILINE)

pytestmark = pytest.mark.subprocess(
    "the step is a shell block, and git is the only authority on what its own ignore rules match"
)


def documented_step() -> str:
    found = STEP.findall(USAGE.read_text(encoding="utf-8"))
    assert len(found) == 1, (
        f"expected exactly one ignore step in {USAGE.name}, found {len(found)} - this test runs "
        f"the documented procedure, so it must be unambiguous about which one that is"
    )
    return found[0]


def a_repo_whose_gitignore_has_no_final_newline(tmp_path: Path) -> Path:
    """The ordinary state that makes an append fuse: git treats a final unterminated line as a
    valid pattern, and many editors leave one."""
    project = tmp_path / "consumer"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    (project / ".gitignore").write_bytes(b".venv/\nnode_modules/\n.env.local")
    return project


def run_step(project: Path) -> None:
    done = subprocess.run(
        ["sh", "-c", documented_step()], cwd=str(project), capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


def ignored(project: Path, name: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(project), "check-ignore", "-q", name], capture_output=True
        ).returncode
        == 0
    )


def test_the_step_ignores_the_env_it_creates_and_the_temp_file_it_may_leave(tmp_path):
    """Both rules, because this route carries no other list. `.env` is the primary one - step 5
    creates it every single time - and the temp pattern is the derived one, for the leftover a
    SIGKILL or a power loss cannot be cleaned up."""
    project = a_repo_whose_gitignore_has_no_final_newline(tmp_path)
    run_step(project)

    leftover = env_assemble.TEMP_GITIGNORE_PATTERN.replace("*", "probe")
    assert ignored(project, ".env"), (
        "the assembled .env is not ignored - `git add -A` commits a live OPENROUTER_API_KEY"
    )
    assert ignored(project, leftover), (
        f"{leftover} is not ignored - a leftover holding the fully merged live credentials would "
        f"be committed by `git add -A`"
    )


def test_the_rule_the_project_already_had_survives_the_append(tmp_path):
    """The fusion regression. Under a bare `>>` the unterminated `.env.local` became
    `.env.local.env`, so the project lost a rule it already had and gained neither new one."""
    project = a_repo_whose_gitignore_has_no_final_newline(tmp_path)
    run_step(project)

    assert ignored(project, ".env.local"), (
        "the pattern that was last in .gitignore stopped matching - the append fused onto it"
    )


def test_running_the_step_twice_changes_nothing(tmp_path):
    """A fused append does not self-correct: `grep -qxF` cannot match the fused line, so every
    later run appends again. Re-running an install step has to be a no-op."""
    project = a_repo_whose_gitignore_has_no_final_newline(tmp_path)
    run_step(project)
    once = (project / ".gitignore").read_bytes()
    run_step(project)

    assert (project / ".gitignore").read_bytes() == once
    assert ignored(project, ".env") and ignored(project, ".env.local")
