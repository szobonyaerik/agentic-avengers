"""Tests that install.sh vendors every script the shipped surface calls.

SRC_SETS in `scripts/install.sh` is the whole opencode distribution: whatever is not in it does not
exist in a vendored repo. It has rotted three times in a row, always the same class of defect — a
shipped file invoking a file nobody added to the list (pipeline_observations.py, mutation_run.sh,
verifier_review_check.py) — and every one of them was caught by a human reading a diff.

So this derives the expectation instead of restating it: it enumerates every
`${CLAUDE_PLUGIN_ROOT}/scripts/…` and `$SD/…` reference in the repo and asserts each one is covered
by SRC_SETS. A second hand-maintained list would rot exactly like the first; the point is that
adding a script and calling it is enough to move this test, with no list to remember.

`NOT_VENDORED` is the escape hatch, and it is deliberately a decision on the record: an entry needs
a reason, and the test also fails if an entry stops being referenced at all, so it cannot silently
outlive its justification.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "scripts" / "install.sh"

# Directories whose files ship to a runtime and may therefore call a script at run time.
SCANNED = ("agents", "commands", "hooks", "prompts", "scripts", "skills", ".opencode")

SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__"}

# A reference to a script, in any of the forms the runtimes use:
#   ${CLAUDE_PLUGIN_ROOT}/scripts/x.py   (plugin root, from skills/commands/agents/hooks)
#   $SD/x.sh                             (the running script's own dir, from scripts/*.sh)
#   $SCRIPT_DIR/x.py                     (same idea, gate_ci.sh's spelling)
REFERENCE = re.compile(
    r"\$\{CLAUDE_PLUGIN_ROOT[^}]*\}/scripts/([A-Za-z0-9_.-]+\.(?:py|sh))"
    r"|\$SD/([A-Za-z0-9_.-]+\.(?:py|sh))"
    r"|\$SCRIPT_DIR/([A-Za-z0-9_.-]+\.(?:py|sh))"
)

# A python script's dependency on a SIBLING script, which the shell forms above cannot see:
#   from proc_group import run_bounded      /      import model_vendors
# A vendored install that ships the importer without the imported module fails at import time — on
# the gate path, where a missing module is a hook that dies with no verdict. Same class of defect as
# the shell references above, one layer down, and previously invisible to this test.
PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([A-Za-z0-9_]+)\s+import\s|import\s+([A-Za-z0-9_]+)\s*$)", re.M
)

# Referenced but deliberately outside the vendored surface. Reason per entry, on the record.
NOT_VENDORED = {
    "scripts/sync_opencode.py": (
        "Regenerates .opencode/ from the canonical agents/ and skills/ in THIS repo. A vendored "
        "repo receives the generated .opencode/ as a build artifact and never regenerates it, so "
        "shipping the generator would ship a command that rewrites files from sources it lacks."
    ),
}


def src_sets() -> list[str]:
    """The vendored surface, read out of install.sh rather than copied."""
    line = re.search(r'^SRC_SETS="([^"]*)"', INSTALL.read_text(encoding="utf-8"), re.M)
    assert line, "SRC_SETS must stay a single double-quoted assignment install.sh's test can read"
    return line.group(1).split()


def references() -> dict[str, set[str]]:
    """Every `scripts/<file>` the repo invokes, mapped to the files that invoke it."""
    found: dict[str, set[str]] = {}
    for top in SCANNED:
        for path in sorted((REPO / top).rglob("*")):
            if not path.is_file() or SKIP_DIR_PARTS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for plugin_ref, sd_ref, script_dir_ref in REFERENCE.findall(text):
                target = f"scripts/{plugin_ref or sd_ref or script_dir_ref}"
                found.setdefault(target, set()).add(str(path.relative_to(REPO)))
            if path.suffix == ".py" and path.parent.name == "scripts":
                for from_mod, plain_mod in PY_IMPORT.findall(text):
                    module = from_mod or plain_mod
                    if (REPO / "scripts" / f"{module}.py").is_file():
                        found.setdefault(f"scripts/{module}.py", set()).add(
                            str(path.relative_to(REPO)))
    return found


def covered_by(sets: list[str], path: str) -> bool:
    return any(path == s or path.startswith(f"{s}/") for s in sets)


def test_every_referenced_script_is_vendored_or_explicitly_excluded() -> None:
    sets = src_sets()
    missing = {
        target: sorted(callers)
        for target, callers in references().items()
        if target not in NOT_VENDORED and not covered_by(sets, target)
    }

    assert not missing, (
        "these scripts are called by the shipped surface but are not in install.sh's SRC_SETS, "
        f"so a vendored repo cannot run them: {missing}"
    )


def test_referenced_scripts_exist_in_the_repo() -> None:
    """The mutation_run.sh case: a file referenced by a shipped skill that was never written."""
    absent = {
        target: sorted(callers)
        for target, callers in references().items()
        if not (REPO / target).exists()
    }

    assert not absent, f"referenced but missing from the repo: {absent}"


def test_vendored_surface_lists_only_paths_that_exist() -> None:
    absent = [s for s in src_sets() if not (REPO / s).exists()]

    assert not absent, f"SRC_SETS lists paths that no longer exist: {absent}"


@pytest.mark.parametrize("path", sorted(NOT_VENDORED))
def test_exclusions_stay_justified(path: str) -> None:
    """An exclusion outlives its reason the moment nothing calls it any more."""
    assert NOT_VENDORED[path].strip(), f"{path} needs a reason for being excluded"
    assert path in references(), (
        f"{path} is excluded from the vendored surface but nothing references it any more — "
        "drop the exclusion instead of carrying a stale one"
    )
    assert not covered_by(src_sets(), path), (
        f"{path} is now vendored by SRC_SETS; remove it from NOT_VENDORED"
    )
