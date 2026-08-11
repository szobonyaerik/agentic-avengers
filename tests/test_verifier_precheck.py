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

    spec.write_text(spec_gate_cache.stamp(spec.read_text(), "gate", "APPROVED"), encoding="utf-8")


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


def test_a_requirement_with_no_binding_at_all_is_still_owed_a_trace(phase: Path) -> None:
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
    write_mapping(phase, "| R1.1.1, R1.1.2 | tests/demo/test_journey.py::test_j1 | e2e |\n")
    assert check_phase(phase) == []


def test_bound_requirements_splits_owed_from_exempt(phase: Path) -> None:
    spec = write_spec(
        phase,
        "- R1.1.1 — `binding: e2e` — a\n"
        "- R1.1.2 — `binding: none` — b\n"
        "- R1.1.3 — `binding: integration` — c\n",
    )
    assert bound_requirements(spec) == (["R1.1.1", "R1.1.3"], ["R1.1.2"])


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
