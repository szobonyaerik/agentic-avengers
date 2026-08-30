"""Every shipped `.example` file ends with a newline.

Issue #108: a project `.env.example` with no trailing newline fused its last key onto the first line
of the file concatenated after it, and `DRY_RUN=true` was read as `DRY_RUN=false`. The assembly step
now joins with an explicit separator, so the fusion cannot be reintroduced from the operator's side -
this closes the other side, the artifacts this repository itself ships.

Enforced here rather than by inspection: a template is edited by hand, and an editor that strips the
final newline leaves nothing else behind to notice.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__", ".pytest_cache"}


def shipped_examples() -> list[Path]:
    return sorted(
        path
        for path in REPO.rglob("*")
        if path.is_file()
        and not SKIP_DIR_PARTS & set(path.parts)
        and (".example" in path.name)
    )


def test_the_repository_ships_example_files_at_all():
    # A glob that silently matches nothing would pass the test below forever.
    assert shipped_examples()


def test_every_shipped_example_ends_with_a_newline():
    offenders = [
        str(path.relative_to(REPO))
        for path in shipped_examples()
        if not path.read_bytes().endswith(b"\n")
    ]
    assert offenders == []
