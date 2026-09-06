"""A phase that says it changes no behaviour has to prove it (issue #107).

grid-bot-platform's okx-migration phase 1 was mandated zero-behaviour-change and shipped three
altered trading guards - `-` became `+`, and a `0` sentinel became `1` at three sites - through
verification and a 283-test suite, because nothing compared behaviour against the contract the
phase had declared. These tests plant exactly those shapes in a fixture tree under a
`work_kind: refactor` declaration and hold `scripts/behaviour_drift.py` to blocking them; then they
hold it to LETTING THROUGH what a refactor is allowed to do - rename, move, reformat, extract - with
no citation and no allowlist entry, because a guard that flags every rename is answered with a
bypass rather than with attention.

Every test drives a real git repository. The comparison is a property of the diff, and a stubbed
diff would test a reimplementation of the thing under test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Every test here drives a real git repository and the guard's real CLI. The comparison IS a
# property of what git reports about a diff - which lines moved, which file it paired with which -
# and of an exit code a hook reads; a stubbed diff or an in-process call would test a
# reimplementation of the thing under test. See scripts/subprocess_check.py, the only cost gate.
pytestmark = pytest.mark.subprocess(
    "the comparison is a property of what git reports about a real diff; a stubbed diff tests the stub"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import behaviour_atoms  # noqa: E402
import behaviour_drift  # noqa: E402

GUARD = """\
MAX = 10


def remaining(open_orders, cap):
    rem = cap - open_orders
    if rem > 10:
        return rem
    return 0


def balance(free, asset):
    return free.get(asset, 0)
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
    ).stdout


def repo(tmp_path: Path, source: str = GUARD, *, work_kind: str = "refactor") -> Path:
    """A committed base with one production file and one spec declaring `work_kind`."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "guard.py").write_text(source, encoding="utf-8")
    write_spec(tmp_path, work_kind=work_kind)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def phase_dir(root: Path) -> Path:
    return root / "docs" / "features" / "demo" / "phases" / "1-split"


def write_spec(
    root: Path, *, work_kind: str, spec: str = "1.1-a", ids: str = "R1.1.1"
) -> Path:
    spec_dir = phase_dir(root) / "specs" / spec
    spec_dir.mkdir(parents=True, exist_ok=True)
    kind = f"work_kind: {work_kind}\n" if work_kind else ""
    requirements = "".join(f"- {rid} - `binding: none` - ...\n" for rid in ids.split())
    path = spec_dir / "spec.md"
    path.write_text(
        f"---\nfeature: demo\nphase: 1-split\nspec: {spec}\n{kind}status: in-progress\n---\n\n"
        f"# Spec\n\n## Requirements\n{requirements}\n## Acceptance criteria\n\nDone.\n",
        encoding="utf-8",
    )
    return path


def edit(root: Path, source: str, rel: str = "app/guard.py") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def check(root: Path, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "behaviour_drift.py"),
            "check",
            str(phase_dir(root)),
            "--root",
            str(root),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )
    return proc.returncode, proc.stderr


# --- the measured defect, planted ---------------------------------------------------------------


def test_a_minus_to_plus_flip_and_a_literal_change_block_a_refactor_phase(
    tmp_path: Path,
) -> None:
    """Issue #107's own fixture: `-` to `+` and `10` to `9`, under a zero-behaviour declaration."""
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace("cap - open_orders", "cap + open_orders").replace(
            "rem > 10", "rem > 9"
        ),
    )

    code, err = check(root)

    assert code == behaviour_drift.DRIFT, err
    assert "-op:Sub" in err and "+op:Add" in err
    assert "-literal:int:10" in err and "+literal:int:9" in err
    assert "BLOCKING" in err


def test_the_same_change_citing_a_declared_requirement_passes(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace(
            "cap - open_orders", "cap + open_orders  # behaviour: R1.1.1"
        ).replace("rem > 10:", "rem > 9:  # behaviour: R1.1.1"),
    )

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err
    assert "clean" in err and "cited" in err


def test_the_zero_sentinel_becoming_one_is_its_own_finding(tmp_path: Path) -> None:
    """The third measured guard: `.get(asset, 0)` becoming `1`, which a whole-tree multiset hid
    against zeros the same phase added elsewhere. Per hunk, it stands on its own line."""
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace("free.get(asset, 0)", "free.get(asset, 1)") + "\nNEW_ZERO = 0\n",
    )

    code, err = check(root)

    assert code == behaviour_drift.DRIFT
    assert "-literal:int:0  +literal:int:1" in err


def test_an_addition_in_the_SAME_definition_masks_one_half_and_still_blocks(
    tmp_path: Path,
) -> None:
    """The stated limit, pinned rather than implied: cancellation is per definition, so a `0` added
    inside the same function as a `0` -> `1` flip cancels the removed half. Only equal keys cancel,
    so the flip still surfaces on its other half and the phase is still blocked - which is the
    property that matters and the one a later reader must be able to rely on."""
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace(
            "    return free.get(asset, 0)\n",
            "    pad = 0\n    return free.get(asset, 1)\n",
        ),
    )

    code, err = check(root)

    assert code == behaviour_drift.DRIFT, err
    assert "+literal:int:1" in err
    assert "-literal:int:0" not in err  # the limit itself, measured


def test_a_citation_of_an_id_no_spec_declares_authorises_nothing(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace("cap - open_orders", "cap + open_orders  # behaviour: R9.9.9"),
    )

    code, err = check(root)

    assert code == behaviour_drift.DRIFT
    assert "R9.9.9" in err and "declared by no spec" in err


def test_a_citation_inside_a_string_is_not_a_citation(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace("cap - open_orders", 'cap + open_orders or "behaviour: R1.1.1"'),
    )

    code, _ = check(root)

    assert code == behaviour_drift.DRIFT


# --- what a refactor is allowed to do passes with nothing to cite --------------------------------


def test_a_rename_changes_no_atom(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace("open_orders", "orders_open").replace("remaining", "orders_left"),
    )

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_reformat_changes_no_atom(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace(
            "    rem = cap - open_orders\n",
            "    rem = (\n        cap\n        - open_orders\n    )\n",
        ),
    )

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_move_between_files_is_a_move(tmp_path: Path) -> None:
    root = repo(tmp_path)
    head, tail = GUARD.split("\n\ndef balance")
    edit(root, head + "\n")
    edit(root, "def balance" + tail, "app/wallet.py")

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_renamed_file_git_cannot_pair_is_paired_here_and_a_flip_inside_it_stands(
    tmp_path: Path,
) -> None:
    """In session the new path is untracked, so git sees a deletion and an addition. Read that way
    a flipped literal is one atom among a whole file's; paired, it is a hunk of its own."""
    root = repo(tmp_path)
    (root / "app" / "guard.py").unlink()
    edit(
        root,
        GUARD.replace("free.get(asset, 0)", "free.get(asset, 1)"),
        "app/guards/orders.py",
    )

    code, err = check(root)

    assert code == behaviour_drift.DRIFT
    assert "app/guard.py -> app/guards/orders.py" in err
    assert "-literal:int:0  +literal:int:1" in err
    assert "+op:Sub" not in err and "+literal:int:10" not in err


def test_extracting_a_helper_is_free(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace(
            "    rem = cap - open_orders\n    if rem > 10:\n        return rem\n    return 0\n",
            "    return _clamp(cap - open_orders)\n",
        )
        + "\n\ndef _clamp(rem):\n    if rem > 10:\n        return rem\n    return 0\n",
    )

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_docstring_or_annotation_change_is_not_behaviour(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace(
            "def remaining(open_orders, cap):\n",
            'def remaining(open_orders: "int", cap: int = 10) -> int:\n    """Capacity left, 10 max."""\n',
        ),
    )

    code, err = check(root)

    # The default `= 10` IS an atom - a new default changes what callers get - and is reported;
    # the docstring's 10 and the annotation are not.
    assert code == behaviour_drift.DRIFT
    assert "+literal:int:10" in err and "+literal:str:'int'" not in err


def test_a_deleted_guard_is_cited_on_the_hunk_that_removed_it(tmp_path: Path) -> None:
    root = repo(tmp_path)
    removed = GUARD.replace(
        "    if rem > 10:\n        return rem\n    return 0\n", "    return rem\n"
    )
    edit(root, removed)
    code, err = check(root)
    assert code == behaviour_drift.DRIFT
    assert "-flow:if" in err and "-flow:early-return" in err and "-op:Gt" in err

    edit(
        root,
        removed.replace(
            "    return rem\n",
            "    # behaviour: R1.1.1 the clamp is gone\n    return rem\n",
        ),
    )
    code, err = check(root)
    assert code == behaviour_drift.CLEAN, err


def test_a_new_file_cites_once_in_its_header(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(
        root,
        "# behaviour: R1.1.1\nRETRIES = 3\n\n\ndef backoff(n):\n    return 2**n\n",
        "app/retry.py",
    )

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_header_citation_on_an_EXISTING_file_authorises_nothing_below_it(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    edit(
        root,
        "# behaviour: R1.1.1\n"
        + GUARD.replace("cap - open_orders", "cap + open_orders"),
    )

    code, _ = check(root)

    assert code == behaviour_drift.DRIFT


def test_a_comment_inside_a_branch_does_not_authorise_the_condition_above_it(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    edit(
        root,
        GUARD.replace(
            "    if rem > 10:\n        return rem\n",
            "    if rem > 9:\n        return rem  # behaviour: R1.1.1\n",
        ),
    )

    code, err = check(root)

    assert code == behaviour_drift.DRIFT
    assert "+literal:int:9" in err


def test_tests_are_never_compared(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(root, "def test_it():\n    assert 10 - 9 == 1\n", "tests/test_guard.py")

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_python_file_that_does_not_parse_fails_closed(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(root, "def (:\n")

    code, err = check(root)

    assert code == behaviour_drift.ERROR
    assert "does not parse" in err


# --- the contract itself --------------------------------------------------------------------------


def test_a_greenfield_phase_is_untouched(tmp_path: Path) -> None:
    root = repo(tmp_path, work_kind="greenfield")
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))

    code, err = check(root)

    assert code == behaviour_drift.CLEAN
    assert "no behaviour contract binds" in err


def test_a_spec_with_no_work_kind_declares_no_contract(tmp_path: Path) -> None:
    root = repo(tmp_path, work_kind="")
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))

    code, err = check(root)

    assert code == behaviour_drift.CLEAN
    assert "absent" in err


def test_a_migration_phase_carries_the_contract_too(tmp_path: Path) -> None:
    """The measured phase declared `migration`, not `refactor`. Both are behaviour-preserving in
    `skills/tdd`, and a contract that bound only one would have missed the case it was built for."""
    root = repo(tmp_path, work_kind="migration")
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))

    code, err = check(root)

    assert code == behaviour_drift.DRIFT, err


def test_a_mixed_phase_is_bound_and_a_greenfield_requirement_is_a_valid_citation(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    write_spec(root, work_kind="greenfield", spec="1.2-b", ids="R1.2.1")
    edit(
        root,
        GUARD.replace("cap - open_orders", "cap + open_orders  # behaviour: R1.2.1"),
    )

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_declared_names_the_specs_that_bind(tmp_path: Path) -> None:
    root = repo(tmp_path)
    write_spec(root, work_kind="greenfield", spec="1.2-b", ids="R1.2.1")

    out = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "behaviour_drift.py"),
            "declared",
            str(phase_dir(root)),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert (
        out.startswith("under contract:")
        and "1.1-a (refactor)" in out
        and "1.2-b" not in out
    )


# --- the applicability boundary -------------------------------------------------------------------


def _record_exception(root: Path, subject: str) -> None:
    reason = root / "why.txt"
    reason.write_text("captain-authorised\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "applicability.py"),
            "record",
            str(phase_dir(root)),
            "--rule",
            behaviour_drift.RULE,
            f"--subject={subject}",
            "--reason-file",
            str(reason),
            "--recorded-by",
            "captain",
        ],
        check=True,
        capture_output=True,
        cwd=str(root),
        env={
            "PATH": __import__("os").environ["PATH"],
            "CLAUDE_PROJECT_DIR": str(root),
            "HOME": str(root),
        },
    )


def test_an_atom_excepted_on_the_ledger_is_the_allowlist(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))
    _record_exception(root, "+op:Add")
    _record_exception(root, "-op:Sub")

    code, err = check(root)

    assert code == behaviour_drift.CLEAN, err


def test_a_phase_excepted_whole_is_closed_and_says_so(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))
    _record_exception(root, "1-split")

    code, err = check(root)

    assert code == behaviour_drift.CLEAN
    assert "EXCEPTED whole" in err


def test_a_corrupt_ledger_is_an_error_never_an_empty_one(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))
    (phase_dir(root) / "exceptions.json").write_text("{not json", encoding="utf-8")

    code, err = check(root)

    assert code == behaviour_drift.ERROR


def test_the_rule_is_in_the_closed_set_and_this_module_is_its_declared_reader() -> None:
    import applicability

    assert behaviour_drift.RULE in applicability.RULES
    assert any("behaviour_drift.py" in reader for reader in applicability.READERS)


def test_diff_scoped_ci_form_does_not_hold_a_greenfield_phase_to_a_contract_it_never_declared(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    other = root / "docs" / "features" / "demo" / "phases" / "2-new" / "specs" / "2.1-a"
    other.mkdir(parents=True)
    (other / "spec.md").write_text(
        "---\nfeature: demo\nphase: 2-new\nspec: 2.1-a\nwork_kind: greenfield\n---\n\n"
        "## Requirements\n- R2.1.1 - `binding: none` - ...\n",
        encoding="utf-8",
    )
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))
    (phase_dir(root) / "handover.md").write_text("card\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "behaviour_drift.py"),
            "check",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )

    assert proc.returncode == behaviour_drift.CLEAN
    assert "NOT CHECKED" in proc.stderr and "2-new" in proc.stderr


def test_diff_scoped_ci_form_binds_when_every_touched_phase_is_under_the_contract(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))
    (phase_dir(root) / "handover.md").write_text("card\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "behaviour_drift.py"),
            "check",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )

    assert proc.returncode == behaviour_drift.DRIFT, proc.stderr


def test_a_phase_the_change_does_not_touch_is_not_in_scope(tmp_path: Path) -> None:
    root = repo(tmp_path)
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "behaviour_drift.py"),
            "check",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )

    assert proc.returncode == behaviour_drift.CLEAN
    assert "0 under the contract" in proc.stderr or "no phase in scope" in proc.stderr


def test_a_landed_change_is_measured_between_two_refs(tmp_path: Path) -> None:
    """`compare` is how the guard was held to the real okx-migration phase 1 before shipping."""
    root = repo(tmp_path)
    edit(root, GUARD.replace("cap - open_orders", "cap + open_orders"))
    git(root, "commit", "-qam", "flip")

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "behaviour_drift.py"),
            "compare",
            "--old",
            "HEAD~1",
            "--new",
            "HEAD",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )

    assert proc.returncode == behaviour_drift.DRIFT
    assert "-op:Sub  +op:Add" in proc.stderr


# --- the extraction layer, pinned decision by decision --------------------------------------------


def keys(source: str) -> list[str]:
    return [a.key for a in behaviour_atoms.atoms(source)]


def test_every_kind_of_atom_the_issue_names_is_extracted() -> None:
    found = keys(
        "def f(x, items):\n"
        "    if x > 10 and x not in items or x is None:\n"
        "        return 'early'\n"
        "    for i in items:\n"
        "        x -= i\n"
        "    try:\n"
        "        y = 1 / x\n"
        "    except ZeroDivisionError:\n"
        "        raise ValueError('zero')\n"
        "    finally:\n"
        "        pass\n"
        "    return [i for i in items if i]\n"
    )
    for key in (
        "literal:int:10",
        "op:Gt",
        "op:NotIn",
        "op:Is",
        "op:And",
        "op:Or",
        "literal:NoneType:None",
        "flow:if",
        "flow:early-return",
        "literal:str:'early'",
        "flow:loop",
        "op:aug:Sub",
        "flow:try",
        "op:Div",
        "flow:except:ZeroDivisionError",
        "flow:raise:ValueError",
        "flow:finally",
        "flow:comp-if",
    ):
        assert key in found, key


def test_a_tail_return_is_not_an_atom_but_an_early_one_is() -> None:
    assert "flow:early-return" not in keys("def f():\n    return 1\n")
    assert (
        keys("def f(x):\n    if x:\n        return 1\n    return 2\n").count(
            "flow:early-return"
        )
        == 1
    )


def test_docstrings_bare_strings_ellipses_and_annotations_are_not_atoms() -> None:
    found = keys(
        '"""Module doc with 10."""\n'
        "def f(x: int = 3) -> 'str':\n"
        '    """Doc 20."""\n'
        "    ...\n"
        "    y: list[int] = []\n"
        "    'bare 30'\n"
    )
    assert found == ["literal:int:3"]


def test_a_custom_exception_is_not_named_but_a_builtin_is() -> None:
    found = keys(
        "try:\n    pass\nexcept OrderError:\n    pass\nexcept (ValueError, KeyError):\n    pass\nexcept:\n    pass\n"
    )
    assert "flow:except:custom" in found
    assert "flow:except:KeyError|ValueError" in found
    assert "flow:except:bare" in found


def test_a_citation_range_covers_the_header_of_a_compound_statement_only() -> None:
    found = behaviour_atoms.atoms("if a > 1:\n    b = 2\n    c = 3\n")
    by_key = {a.key: a for a in found}
    assert by_key["flow:if"].cite == (1, 1)
    assert by_key["literal:int:1"].cite == (1, 1)
    assert by_key["literal:int:2"].cite == (2, 2)


def test_an_else_edge_is_cited_on_the_else_line() -> None:
    found = {
        a.key: a for a in behaviour_atoms.atoms("if a:\n    b = 1\nelse:\n    b = 2\n")
    }
    assert found["flow:else"].cite == (3, 3)


def test_citations_are_read_from_comments_only_and_the_header_is_before_the_first_statement() -> (
    None
):
    by_line, header = behaviour_atoms.citations(
        '"""doc"""\n# behaviour: R1.1.1, R1.1.2\nimport os\nx = "behaviour: R9.9.9"  # behavior: R2.2.2\n'
    )
    assert header == ["R1.1.1", "R1.1.2"]
    assert by_line == {2: ["R1.1.1", "R1.1.2"], 4: ["R2.2.2"]}


# --- wiring -----------------------------------------------------------------------------------------


def test_the_spec_done_and_handover_triggers_run_it() -> None:
    text = (ROOT / "scripts" / "hook_verifier.sh").read_text(encoding="utf-8")
    assert text.count("behaviour_drift.py") >= 2
    assert "verifier:behaviour-drift" in text


def test_ci_sweeps_it_too() -> None:
    assert "behaviour_drift.py" in (ROOT / "scripts" / "gate_ci.sh").read_text(
        encoding="utf-8"
    )


def test_the_exception_ledger_template_carries_the_reader() -> None:
    template = json.loads(
        (ROOT / "docs/templates/exceptions.template.json").read_text(encoding="utf-8")
    )
    assert any("behaviour_drift.py" in r for r in template["readers"])
