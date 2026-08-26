"""Tests that install.sh vendors every file the shipped surface points at.

SRC_SETS in `scripts/install.sh` is the whole opencode distribution: whatever is not in it does not
exist in a vendored repo. It has rotted three times in a row, always the same class of defect — a
shipped file invoking a file nobody added to the list (pipeline_observations.py, mutation_run.sh,
verifier_evidence.py) — and every one of them was caught by a human reading a diff.

So this derives the expectation instead of restating it: it enumerates every
`${CLAUDE_PLUGIN_ROOT}/scripts/…` and `$SD/…` reference in the repo and asserts each one is covered
by SRC_SETS. A second hand-maintained list would rot exactly like the first; the point is that
adding a script and calling it is enough to move this test, with no list to remember.

`NOT_VENDORED` is the escape hatch, and it is deliberately a decision on the record: an entry needs
a reason, and the test also fails if an entry stops being referenced at all, so it cannot silently
outlive its justification.

A template a shipped skill names by path is the same defect wearing different clothes: the skill
still renders, so nothing fails loudly — the writer simply follows a pointer to a file that is not
there. `docs/templates/…` is therefore derived exactly like `scripts/…`, so pointing at a template is
enough to move this test.

The fourth rot was a shape this file could not see at all, and a human reviewer found it: a SHIPPED
file EXECUTING a script that is deliberately not shipped. `.pre-commit-config.yaml` and
`.github/workflows/pipeline-gates.yml` are both SRC_SETS entries, and both acquired a bare
`python3 scripts/guard_proof.py …` line while `guard_proof.py` stayed out of SRC_SETS on purpose —
so a vendored repo could not commit at all and its CI failed on every build. Two independent blind
spots let it through: `SCANNED` never opens a repo-root or `.github/` file, and `REFERENCE` knows
only the `${CLAUDE_PLUGIN_ROOT}/`, `$SD/` and `$SCRIPT_DIR/` spellings, never the bare `scripts/x.py`
a pre-commit `entry:` and a workflow `run:` line actually use.

So `EXECUTION` asks the question from the other end: it walks the SHIPPED files themselves — the
SRC_SETS entries, which is exactly what reaches a consumer — and every script they RUN must ship
too. Scanning the shipped set rather than the whole repository is what keeps a repo-local file
calling a repo-local script (`.github/workflows/guard-proof.yml`, which does not ship) from being
flagged.

That check shipped skipping markdown, on the claim that a shipped `.md` naming a script is the
pointer case `REFERENCE` already covers. It is not: `SCANNED` never opens repo-root `AGENTS.md`, and
`REFERENCE` does not know the bare `scripts/x.py` spelling either — so the exemption was a third
blind spot, and the very next round shipped `python3 scripts/guard_proof.py check` into `AGENTS.md`
with the suite green. A vendored agent reading that runs a command that is not there. Markdown is
therefore scanned like everything else, and the line between naming and running is drawn where a
machine can see it: an interpreter in front of the path. `scripts/x.py` in a sentence is a name;
`python3 scripts/x.py` is an instruction to run it, wherever it appears.

`NOT_VENDORED` is the escape hatch here too, with its existing property that an exclusion nothing
references any more is itself a finding — an entry is a decision on the record, which is what the
guard-proof wiring never had.
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
_SCRIPT_PREFIXES = (
    r"\$\{CLAUDE_PLUGIN_ROOT[^}]*\}/scripts/",
    r"\$SD/",
    r"\$SCRIPT_DIR/",
)
_SCRIPT_NAME = r"([A-Za-z0-9_.-]+\.(?:py|sh))"

REFERENCE = re.compile("|".join(prefix + _SCRIPT_NAME for prefix in _SCRIPT_PREFIXES))

# A python script's dependency on a SIBLING script, which the shell forms above cannot see:
#   from proc_group import run_bounded      /      import model_vendors
# A vendored install that ships the importer without the imported module fails at import time — on
# the gate path, where a missing module is a hook that dies with no verdict. Same class of defect as
# the shell references above, one layer down, and previously invisible to this test.
PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([A-Za-z0-9_]+)\s+import\s"
    r"|import\s+([A-Za-z0-9_]+)\s*(?:as\s+[A-Za-z0-9_]+\s*)?$)",
    re.M,
)

# A shipped file RUNNING a sibling script — every spelling this repository writes one in: the bare
# `scripts/x.py` a pre-commit `entry:`, a workflow `run:` line and a documented command use, and the
# three above, which shipped skills, agents and hook scripts use:
#   entry: python3 scripts/x.py   /   bash "$SD/x.sh"   /   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/x.py"
# The prefixes come from the same list `REFERENCE` is built from: a check that knows one spelling of
# the thing it looks for reports clean about the spellings it cannot see, and a second copy of the
# list is what drifts into being that check.
#
# The interpreter is the whole distinction: it is what separates naming a path from telling somebody
# to run it, and it is a distinction a machine can draw without guessing at intent.
EXECUTION = re.compile(
    r"(?:python3?|bash|sh)\s+\"?(?:"
    + "|".join((*_SCRIPT_PREFIXES, "scripts/"))
    + ")"
    + _SCRIPT_NAME
)

# A template a shipped file sends its writer to, by path:
#   docs/templates/test-mapping.template.md   /   docs/templates/verdict.template.json
TEMPLATE_REFERENCE = re.compile(
    r"docs/templates/([A-Za-z0-9_.-]+\.template\.[A-Za-z0-9]+)"
)

# Referenced but deliberately outside the vendored surface. Reason per entry, on the record.
NOT_VENDORED = {
    "scripts/sync_opencode.py": (
        "Regenerates .opencode/ from the canonical agents/ and skills/ in THIS repo. A vendored "
        "repo receives the generated .opencode/ as a build artifact and never regenerates it, so "
        "shipping the generator would ship a command that rewrites files from sources it lacks."
    ),
}


def src_sets(root: Path = REPO) -> list[str]:
    """The vendored surface, read out of install.sh rather than copied."""
    install = root / "scripts" / "install.sh"
    line = re.search(r'^SRC_SETS="([^"]*)"', install.read_text(encoding="utf-8"), re.M)
    assert line, (
        "SRC_SETS must stay a single double-quoted assignment install.sh's test can read"
    )
    return line.group(1).split()


def shipped_files(root: Path = REPO) -> list[Path]:
    """Every file install.sh actually copies, expanded from SRC_SETS the way install.sh expands it."""
    found: list[Path] = []
    for entry in src_sets(root):
        target = root / entry
        if target.is_dir():
            found.extend(
                p
                for p in sorted(target.rglob("*"))
                if p.is_file() and not SKIP_DIR_PARTS & set(p.parts)
            )
        elif target.is_file():
            found.append(target)
    return found


def unvendored_executions(root: Path = REPO) -> dict[str, set[str]]:
    """Scripts a shipped file RUNS that the vendored surface does not carry."""
    sets = src_sets(root)
    found: dict[str, set[str]] = {}
    for path in shipped_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name in EXECUTION.findall(text):
            target = f"scripts/{name}"
            if target not in NOT_VENDORED and not covered_by(sets, target):
                found.setdefault(target, set()).add(str(path.relative_to(root)))
    return found


def references() -> dict[str, set[str]]:
    """Every vendored-surface file the repo points at, mapped to the files that point at it."""
    found: dict[str, set[str]] = {}
    for path in scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for plugin_ref, sd_ref, script_dir_ref in REFERENCE.findall(text):
            target = f"scripts/{plugin_ref or sd_ref or script_dir_ref}"
            found.setdefault(target, set()).add(str(path.relative_to(REPO)))
        for template in TEMPLATE_REFERENCE.findall(text):
            found.setdefault(f"docs/templates/{template}", set()).add(
                str(path.relative_to(REPO))
            )
        if path.suffix == ".py" and path.parent.name == "scripts":
            for from_mod, plain_mod in PY_IMPORT.findall(text):
                module = from_mod or plain_mod
                if (REPO / "scripts" / f"{module}.py").is_file():
                    found.setdefault(f"scripts/{module}.py", set()).add(
                        str(path.relative_to(REPO))
                    )
    return found


def scanned_files() -> list[Path]:
    """The files whose pointers are checked: the runtime directories AND every shipped file.

    `SCANNED` alone never opens a repo-root or `.github/` SRC_SETS member - `AGENTS.md`,
    `.pre-commit-config.yaml`, `pipeline-gates.yml`, `cosmic-ray.toml` - which is half of why a
    shipped file pointing at an unvendored script stayed invisible here for three rounds. Whatever
    reaches a consumer is read, whichever directory it happens to sit in.
    """
    seen: dict[Path, None] = {}
    for top in SCANNED:
        for path in sorted((REPO / top).rglob("*")):
            if path.is_file() and not SKIP_DIR_PARTS & set(path.parts):
                seen.setdefault(path, None)
    for path in shipped_files():
        seen.setdefault(path, None)
    return list(seen)


def covered_by(sets: list[str], path: str) -> bool:
    return any(path == s or path.startswith(f"{s}/") for s in sets)


def test_every_referenced_file_is_vendored_or_explicitly_excluded() -> None:
    sets = src_sets()
    missing = {
        target: sorted(callers)
        for target, callers in references().items()
        if target not in NOT_VENDORED and not covered_by(sets, target)
    }

    assert not missing, (
        "these files are pointed at by the shipped surface but are not in install.sh's SRC_SETS, "
        f"so a vendored repo cannot reach them: {missing}"
    )


def toy_surface(root: Path, shipped_entry: str) -> Path:
    """A miniature vendored surface: an install.sh whose SRC_SETS ships one config and one script."""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "gate_ci.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    (root / "scripts" / "guard_proof.py").write_text(
        "# kept out of the surface\n", encoding="utf-8"
    )
    (root / "scripts" / "install.sh").write_text(
        'SRC_SETS="scripts/gate_ci.sh .pre-commit-config.yaml"\n', encoding="utf-8"
    )
    (root / ".pre-commit-config.yaml").write_text(shipped_entry, encoding="utf-8")
    return root


def test_a_shipped_file_running_an_unvendored_script_is_reported(
    tmp_path: Path,
) -> None:
    """The exact wiring a human reviewer had to catch: shipped config, unshipped script.

    In a vendored repo this is `python3: can't open file 'scripts/guard_proof.py'` on every commit.
    """
    root = toy_surface(
        tmp_path, "        entry: python3 scripts/guard_proof.py check\n"
    )

    assert unvendored_executions(root) == {
        "scripts/guard_proof.py": {".pre-commit-config.yaml"}
    }


def test_shipped_DOCUMENTATION_telling_an_agent_to_run_an_unvendored_script_is_reported(
    tmp_path: Path,
) -> None:
    """The instance that got past this check while it skipped markdown.

    `AGENTS.md` is an SRC_SETS entry, and it acquired a `python3 scripts/guard_proof.py check` line
    for a script deliberately kept out of the surface. Nothing fails loudly over there - the agent
    reading it simply runs a command that is not there, and is handed a rule about an inventory file
    it does not have.
    """
    root = toy_surface(tmp_path, "        entry: bash scripts/gate_ci.sh\n")
    Path(root / "scripts" / "install.sh").write_text(
        'SRC_SETS="scripts/gate_ci.sh .pre-commit-config.yaml AGENTS.md"\n',
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "## Every guard is proven by going RED\n\n"
        "    python3 scripts/guard_proof.py check   # diff-scoped\n",
        encoding="utf-8",
    )

    assert unvendored_executions(root) == {"scripts/guard_proof.py": {"AGENTS.md"}}


def test_the_plugin_root_and_SD_spellings_are_seen_too(tmp_path: Path) -> None:
    """The bare `scripts/x.py` form is one of three, and it was the only one this could see.

    A shipped skill, agent or hook script names a sibling script through `${CLAUDE_PLUGIN_ROOT}/` or
    `$SD/`, so a check that knew only the bare form reported clean about the spellings the rest of
    the shipped surface actually writes - the same blind spot one layer in.
    """
    root = toy_surface(tmp_path, "        entry: bash scripts/gate_ci.sh\n")
    Path(root / "scripts" / "install.sh").write_text(
        'SRC_SETS="scripts/gate_ci.sh .pre-commit-config.yaml AGENTS.md hooks"\n',
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        'Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/guard_proof.py" check` to prove the guards.\n',
        encoding="utf-8",
    )
    (root / "hooks").mkdir()
    (root / "hooks" / "run.sh").write_text(
        'bash "$SD/gate_runner_guard.sh"\n', encoding="utf-8"
    )

    assert unvendored_executions(root) == {
        "scripts/guard_proof.py": {"AGENTS.md"},
        "scripts/gate_runner_guard.sh": {"hooks/run.sh"},
    }


def test_shipped_documentation_may_NAME_an_unvendored_path_without_running_it(
    tmp_path: Path,
) -> None:
    """The line is the interpreter, and it is where the canonical skill now stands.

    A sentence saying the harness lives in the pipeline repository and is not part of the vendored
    surface is accurate and costs a consumer nothing. Reading every path a document mentions as an
    invocation would flag it, and the remedy would be to stop saying a true thing.
    """
    root = toy_surface(tmp_path, "        entry: bash scripts/gate_ci.sh\n")
    Path(root / "scripts" / "install.sh").write_text(
        'SRC_SETS="scripts/gate_ci.sh .pre-commit-config.yaml AGENTS.md"\n',
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "`scripts/guard_proof.py` proves that repository's own guards and is deliberately not "
        "part of the vendored surface.\n",
        encoding="utf-8",
    )

    assert unvendored_executions(root) == {}


def test_a_repo_local_file_running_a_repo_local_script_is_not_reported(
    tmp_path: Path,
) -> None:
    """The question is what SHIPS, not what the repository contains.

    A workflow outside SRC_SETS may run whatever this repository keeps to itself - that is where the
    harness's own CI wiring lives - and reading it as a vendoring defect would push the wiring back
    into the shipped files it was moved out of.
    """
    root = toy_surface(tmp_path, "        entry: bash scripts/gate_ci.sh\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "guard-proof.yml").write_text(
        "        run: python3 scripts/guard_proof.py prove --jobs 4\n", encoding="utf-8"
    )

    assert unvendored_executions(root) == {}


def test_no_shipped_file_runs_a_script_the_vendored_surface_lacks() -> None:
    executions = unvendored_executions()

    assert not executions, (
        "these shipped files run a script that install.sh does not vendor, so a consumer repo "
        f"cannot commit or build: {executions}"
    )


def test_referenced_files_exist_in_the_repo() -> None:
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
