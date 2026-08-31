"""Every shipped `.example` file ends with a newline.

Issue #108: a project `.env.example` with no trailing newline fused its last key onto the first line
of the file concatenated after it, and `DRY_RUN=true` was read as `DRY_RUN=false`. The assembly step
now joins with an explicit separator, so the fusion cannot be reintroduced from the operator's side -
this closes the other side, the artifacts this repository itself ships.

Enforced here rather than by inspection: a template is edited by hand, and an editor that strips the
final newline leaves nothing else behind to notice.

"Ships" is the TRACKED set, `git ls-files`, and not a walk of the working tree. The contract is
about what this repository publishes, and a walk let anything untracked decide the result: a
contributor's `.venv/` whose site-packages carry an `*.example*` without a final newline, or a local
`.env.example` copied while trying the flow, would fail a test whose remedy does not exist here.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Both tests here read the tracked listing, and only `git` can state it - the question is what this
#: repository SHIPS, which no in-process reading of the working tree can answer.
pytestmark = pytest.mark.subprocess(
    "git is the only authority on which files this repository tracks"
)


def shipped_examples() -> list[Path]:
    """Every tracked file whose name marks it an example artifact.

    A listing that cannot be obtained is fatal, never an empty scan: a silently empty set passes the
    byte contract below forever.
    """
    try:
        listed = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
    ) as exc:  # pragma: no cover - environment
        raise AssertionError(
            f"cannot list this repository's tracked files, so what it ships is unknown: {exc}"
        ) from exc

    return sorted(
        REPO / name
        for name in listed.stdout.decode("utf-8").split("\0")
        if name and ".example" in Path(name).name
    )


def test_the_repository_ships_example_files_at_all():
    # A listing that silently matches nothing would pass the test below forever.
    assert shipped_examples()


def test_every_shipped_example_ends_with_a_newline():
    offenders = [
        str(path.relative_to(REPO))
        for path in shipped_examples()
        if not path.read_bytes().endswith(b"\n")
    ]
    assert offenders == []
