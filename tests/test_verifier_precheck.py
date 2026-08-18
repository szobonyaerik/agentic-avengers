"""The Verifier's bookkeeping, decided by a script instead of by a model.

Twelve of 46 Verifier findings across one measured feature (26%, and 45% on the worst phase) were
about the pipeline's own stamps, traceability rows and spec headings. Two whole attempts produced
nothing else. Every one of them was mechanically decidable, and one names its own detection method
as a grep.

What is pinned here is the arithmetic AND the exemption that stops it re-introducing the multiplier
the tiered-binding rule removed: a `binding: none` requirement is never owed a test, so it is never
an untraced id.
"""

import sys
from pathlib import Path

import pytest

# The check itself runs in-process below. The only real processes are `git`, and they are the
# subject: diff scoping is a claim about what git actually reports, so a faked diff would test the
# fake. Declared per scripts/subprocess_check.py, this pipeline's only cost gate — the helpers sit at
# module scope, which is the shape that needs a file-wide declaration.
pytestmark = pytest.mark.subprocess(
    "the diff scoping is a claim about what git actually reports; a faked diff tests the fake"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verifier_precheck import bound_requirements, check_phase, main, traced_ids  # noqa: E402

SPEC = """---
feature: demo
phase: 1-core
spec: 1.1-a
spec_gate: approved
---

# Spec

## Requirements
{requirements}

## Acceptance criteria
- R1.1.1 — passes when: …; fails when: …
"""


@pytest.fixture
def phase(tmp_path: Path) -> Path:
    directory = tmp_path / "docs" / "features" / "demo" / "phases" / "1-core"
    (directory / "specs" / "1.1-a").mkdir(parents=True)
    return directory


def write_spec(phase: Path, requirements: str, *, name: str = "1.1-a") -> Path:
    path = phase / "specs" / name / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SPEC.format(requirements=requirements), encoding="utf-8")
    return path


def write_mapping(phase: Path, rows: str, *, name: str = "1.1-a") -> None:
    (phase / "specs" / name / "test-mapping.md").write_text(
        f"| requirement | test | level |\n|---|---|---|\n{rows}", encoding="utf-8"
    )


def stamp(spec: Path) -> None:
    """Record the current body as judged, so freshness is not the finding under test."""
    import spec_gate_cache

    spec.write_text(
        spec_gate_cache.stamp(spec.read_text(), "gate", "APPROVED"), encoding="utf-8"
    )


# ── traceability, per binding ────────────────────────────────────────────────


def test_a_bound_requirement_with_no_mapping_row_is_a_finding(phase: Path) -> None:
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a thing\n")
    stamp(spec)
    write_mapping(phase, "")
    (finding,) = [f for f in check_phase(phase) if "test-mapping" in f]
    assert "R1.1.1" in finding


def test_a_traced_requirement_is_clean(phase: Path) -> None:
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a thing\n")
    stamp(spec)
    write_mapping(phase, "| R1.1.1 | tests/demo/test_a.py::test_a | integration |\n")
    assert check_phase(phase) == []


def test_a_binding_none_requirement_is_never_a_gap(phase: Path) -> None:
    """The tiered-binding rule says it gets no test. Demanding a row for one hands the suite back
    the per-id multiplier that rule removed."""
    spec = write_spec(phase, "- R1.1.1 — `binding: none` — a build-time property\n")
    stamp(spec)
    write_mapping(phase, "")
    assert check_phase(phase) == []


def test_a_requirement_with_no_binding_at_all_is_still_owed_a_trace(
    phase: Path,
) -> None:
    """A missing binding is a spec defect; treating it as exempt would let the ABSENCE of a
    declaration buy the absence of a test."""
    spec = write_spec(phase, "- R1.1.1 — a thing with no binding\n")
    stamp(spec)
    write_mapping(phase, "")
    assert any("R1.1.1" in f for f in check_phase(phase))


def test_a_journey_row_listing_several_ids_traces_all_of_them(phase: Path) -> None:
    spec = write_spec(
        phase,
        "- R1.1.1 — `binding: e2e` — one\n- R1.1.2 — `binding: e2e` — two\n",
    )
    stamp(spec)
    write_mapping(
        phase, "| R1.1.1, R1.1.2 | tests/demo/test_journey.py::test_j1 | e2e |\n"
    )
    assert check_phase(phase) == []


def test_bound_requirements_splits_owed_from_exempt(phase: Path) -> None:
    spec = write_spec(
        phase,
        "- R1.1.1 — `binding: e2e` — a\n"
        "- R1.1.2 — `binding: none` — b\n"
        "- R1.1.3 — `binding: integration` — c\n",
    )
    assert bound_requirements(spec) == (["R1.1.1", "R1.1.3"], ["R1.1.2"])


def test_a_table_formatted_bound_requirement_is_still_owed_a_trace(phase: Path) -> None:
    """The same blindness as the cap's, in the same regex: a table-formatted spec reported ZERO ids
    owed a trace, so this check passed vacuously on exactly the spec it exists to hold."""
    spec = write_spec(
        phase,
        "| id | binding | behaviour |\n|---|---|---|\n"
        "| R1.1.7 | binding: integration | a thing |\n",
    )
    stamp(spec)
    write_mapping(phase, "")
    assert any("R1.1.7" in f and "test-mapping" in f for f in check_phase(phase))


def test_a_table_formatted_binding_none_row_is_still_exempt(phase: Path) -> None:
    """The binding sits in a LATER CELL of the row, not after the id on the same list item."""
    spec = write_spec(phase, "| R1.1.7 | binding: none | a build-time property |\n")
    stamp(spec)
    write_mapping(phase, "| R1.1.1 | t | integration |\n")
    assert check_phase(phase) == []
    assert bound_requirements(spec) == (["R1.1.1"], ["R1.1.7"])


def test_a_table_row_whose_binding_cannot_be_read_is_owed_a_trace(phase: Path) -> None:
    """An unreadable binding buys no more than a missing one: a bare `none` in a binding COLUMN is
    not a declaration this check can read, so the row is owed a trace rather than made exempt."""
    spec = write_spec(phase, "| R1.1.7 | none | a thing |\n")
    owed, exempt = bound_requirements(spec)
    assert "R1.1.7" in owed and exempt == []


# ── one layout rule, one owner ───────────────────────────────────────────────
#
# `requirement_cap` owns both what a declaration looks like AND where its `binding:` sits, because
# they are the same fact about a spec's layout. They used to be two statements: the cap was widened
# to accept headings and ordered lists, this module still read the binding from the declaration line
# alone, and a `### R1.1.1` / `binding: none` spec — which the cap now counts correctly — was
# reported at handover as an untraced coverage gap for a requirement exempt by construction.


LAYOUTS = {
    "unordered": "- R1.1.1 — `binding: {b}` — a thing\n",
    "ordered": "1. R1.1.1 — `binding: {b}` — a thing\n",
    "bold": "**R1.1.1** — `binding: {b}` — a thing\n",
    "table": "| id | binding |\n|---|---|\n| R1.1.1 | binding: {b} | a thing |\n",
    "heading": "### R1.1.1\n\n`binding: {b}` — a thing\n",
}


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_every_layout_the_cap_accepts_reads_its_binding(
    phase: Path, layout: str
) -> None:
    """Both parsers move together or a red test says so."""
    bound = write_spec(phase, LAYOUTS[layout].format(b="e2e"))
    assert bound_requirements(bound) == (["R1.1.1"], []), (
        f"{layout}: bound must be owed a trace"
    )

    unbound = write_spec(phase, LAYOUTS[layout].format(b="none"))
    assert bound_requirements(unbound) == ([], ["R1.1.1"]), (
        f"{layout}: none must be exempt"
    )


def test_a_heading_declaration_does_not_inherit_the_next_ones_binding(
    phase: Path,
) -> None:
    """The lookahead is bounded by the next declaration, so an unbound requirement cannot be
    exempted by its neighbour — the absence of a declaration must never buy the absence of a test."""
    spec = write_spec(
        phase,
        "### R1.1.1\n\na thing with no binding\n\n### R1.1.2\n\n`binding: none` — structural\n",
    )
    assert bound_requirements(spec) == (["R1.1.1"], ["R1.1.2"])


def test_a_heading_declaration_does_not_reach_past_its_own_section(phase: Path) -> None:
    spec = write_spec(
        phase, "### R1.1.1\n\na thing with no binding\n\n## Notes\n\nbinding: none\n"
    )
    assert bound_requirements(spec) == (["R1.1.1"], [])


def test_traced_ids_reads_every_mapping_in_the_phase(phase: Path) -> None:
    write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    write_spec(phase, "- R1.2.1 — `binding: integration` — b\n", name="1.2-b")
    write_mapping(phase, "| R1.1.1 | t | integration |\n")
    write_mapping(phase, "| R1.2.1 | t | integration |\n", name="1.2-b")
    assert traced_ids(phase) == {"R1.1.1", "R1.2.1"}


# ── structure and stamp freshness ────────────────────────────────────────────


def test_a_deleted_acceptance_criteria_heading_is_a_finding(phase: Path) -> None:
    """An amendment deleted one, and the finding that caught it observed that no pipeline script
    parses the heading so nothing broke — which is the argument for a script that does."""
    spec = write_spec(phase, "- R1.1.1 — `binding: none` — a\n")
    spec.write_text(spec.read_text().replace("## Acceptance criteria", "## Notes"))
    stamp(spec)
    assert any("Acceptance criteria" in f for f in check_phase(phase))


def test_a_body_changed_since_it_was_judged_is_a_finding(phase: Path) -> None:
    """The defect that recurred twice, six attempts apart, in one phase — because nothing checked
    it continuously."""
    spec = write_spec(phase, "- R1.1.1 — `binding: none` — a\n")
    stamp(spec)
    spec.write_text(spec.read_text() + "\n\nan edit made after the gate judged it\n")
    assert any("UNGATED" in f for f in check_phase(phase))


def test_the_stale_stamp_message_never_names_amendments_as_a_remedy(
    phase: Path,
) -> None:
    """Issue 67: an amendment does not touch the spec-gate hash this check reads, so naming
    `amendments.py` as a remedy sends a worker in a circle — it can never clear this finding."""
    spec = write_spec(phase, "- R1.1.1 — `binding: none` — a\n")
    stamp(spec)
    spec.write_text(spec.read_text() + "\n\nan edit made after the gate judged it\n")
    (finding,) = [f for f in check_phase(phase) if "UNGATED" in f]
    assert "amendments.py" not in finding
    assert "applicability.py" in finding


def test_a_recorded_spec_gate_exception_clears_a_stale_stamp(
    phase: Path, capsys
) -> None:
    """Issue 67's other documented remedy actually clears the check: recording a disclosed
    exception needs no live gate provider, unlike re-gating, and unlike an amendment it names the
    rule this check reads (`spec-gate`)."""
    import json

    spec = write_spec(phase, "- R1.1.1 — `binding: none` — a\n")
    stamp(spec)
    spec.write_text(spec.read_text() + "\n\nan edit made after the gate judged it\n")
    (phase / "exceptions.json").write_text(
        json.dumps(
            {
                "exceptions": [
                    {
                        "id": "X1",
                        "rule": "spec-gate",
                        "subject": "1.1-a",
                        "reason": "gate provider was down; re-gating was not reachable",
                        "recorded_by": "captain",
                        "recorded_at": "2026-08-14T00:00:00Z",
                    }
                ]
            }
        )
    )
    assert not any("UNGATED" in f for f in check_phase(phase))
    err = capsys.readouterr().err
    assert "X1" in err and "NOT enforced" in err


def test_an_exception_for_a_different_spec_does_not_clear_this_one(phase: Path) -> None:
    import json

    spec = write_spec(phase, "- R1.1.1 — `binding: none` — a\n")
    stamp(spec)
    spec.write_text(spec.read_text() + "\n\nan edit made after the gate judged it\n")
    (phase / "exceptions.json").write_text(
        json.dumps(
            {
                "exceptions": [
                    {
                        "id": "X1",
                        "rule": "spec-gate",
                        "subject": "1.2-other",
                        "reason": "r",
                        "recorded_by": "captain",
                        "recorded_at": "2026-08-14T00:00:00Z",
                    }
                ]
            }
        )
    )
    assert any("UNGATED" in f for f in check_phase(phase))


def test_a_phase_with_no_specs_is_not_a_finding(phase: Path) -> None:
    assert check_phase(phase) == []


# ── the CLI contract the hook and CI branch on ───────────────────────────────


def test_cli_exit_codes(phase: Path) -> None:
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    stamp(spec)
    write_mapping(phase, "")
    assert main([str(phase)]) == 1
    write_mapping(phase, "| R1.1.1 | t | integration |\n")
    assert main([str(phase)]) == 0


def test_a_missing_phase_directory_is_an_error(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope")]) == 2


def test_all_walks_every_phase(phase: Path, tmp_path: Path) -> None:
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    stamp(spec)
    write_mapping(phase, "")
    assert main(["--all", "--root", str(tmp_path)]) == 1


# ── scope: you are responsible for what you change ───────────────────────────


def git_repo(root: Path) -> None:
    """An initialised repo with one commit — `git diff HEAD` has no answer before the first one."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".keep").write_text("")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=root,
        check=True,
    )


def commit_all(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "history",
        ],
        cwd=root,
        check=True,
    )


def test_no_target_is_diff_scoped_and_finds_the_phase_the_diff_touches(
    phase: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diff-scoping is what lets this run on EVERY commit instead of only under --full, and what
    stops a consumer repo's CI hard-failing over locked phases nobody touched."""
    git_repo(tmp_path)
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    stamp(spec)
    write_mapping(phase, "")
    assert main(["--root", str(tmp_path)]) == 1
    assert "diff-scoped" in capsys.readouterr().err


def test_a_phase_the_diff_does_not_touch_is_not_enforced_but_full_still_audits_it(
    phase: Path, tmp_path: Path
) -> None:
    git_repo(tmp_path)
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    stamp(spec)
    write_mapping(phase, "")
    commit_all(tmp_path)
    assert main(["--root", str(tmp_path)]) == 0, (
        "committed history is not this commit's business"
    )
    assert main(["--all", "--root", str(tmp_path)]) == 1, (
        "--full still audits everything"
    )


def test_an_unknowable_scope_enforces_nothing_and_says_so(
    phase: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Falling back to enforcing everything is the hostage failure the scoping removes."""
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    stamp(spec)
    write_mapping(phase, "")
    assert main(["--root", str(tmp_path)]) == 0  # tmp_path is not a git repository
    assert "unknowable" in capsys.readouterr().err


def test_named_phases_still_check_the_whole_phase(phase: Path) -> None:
    """The handover hook names its phase, and that must not be narrowed by what the diff touched."""
    spec = write_spec(phase, "- R1.1.1 — `binding: integration` — a\n")
    stamp(spec)
    write_mapping(phase, "")
    assert main([str(phase)]) == 1
