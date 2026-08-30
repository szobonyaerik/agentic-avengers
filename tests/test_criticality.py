"""Criticality resolution - an absent safety-relevant field must not resolve to the weaker pipeline.

Issue #101: `criticality: critical` routes the Breaker, the pipeline's one criticality-gated stage,
and both readers of the field turned an ABSENT one into `standard`. So a spec that simply never wrote
the line lost its adversarial stage; the phase completed normally and the verdict passed, so the
absence was indistinguishable from a Breaker that ran and found nothing. It was caught by hand in
grid-bot-platform phase 4 - that feature's highest-risk phase - and nothing in the pipeline would
have flagged it.

These tests pin the four states the field can be in (absent, explicit `critical`, explicit
non-critical, malformed) at the resolution, at the gate that keys on it and at the resolver that
routes from it, plus the reporting half: a skipped stage is named with its reason.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import criticality  # noqa: E402

SPEC = """---
feature: demo
phase: {phase}
spec: {spec}
{criticality_line}---

# Spec
"""


def write_spec(
    root: Path, phase: str, spec: str, *, criticality_value: str | None
) -> Path:
    """One spec on disk. `criticality_value=None` omits the line entirely - the defect's own shape."""
    spec_dir = root / "docs" / "features" / "demo" / "phases" / phase / "specs" / spec
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / "spec.md"
    line = "" if criticality_value is None else f"criticality: {criticality_value}\n"
    path.write_text(SPEC.format(phase=phase, spec=spec, criticality_line=line))
    return path


def phase_dir(root: Path, phase: str = "1-core") -> Path:
    return root / "docs" / "features" / "demo" / "phases" / phase


@pytest.fixture(autouse=True)
def _fresh_announcements():
    """`announce` dedupes per phase path for the life of the process; tests must not inherit that."""
    criticality._ANNOUNCED.clear()
    yield
    criticality._ANNOUNCED.clear()


# --- the four states of the field -------------------------------------------------------------


def test_absent_field_resolves_to_critical() -> None:
    resolved = criticality.resolve({})
    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.ABSENT
    assert resolved.defaulted


def test_blank_field_resolves_to_critical() -> None:
    """`criticality:` with nothing after it is an absent value, not a declared one."""
    resolved = criticality.resolve({"criticality": "   "})
    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.ABSENT


def test_explicit_critical_is_declared() -> None:
    resolved = criticality.resolve({"criticality": "critical"})
    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.DECLARED
    assert not resolved.defaulted


def test_explicit_standard_is_declared_and_stays_standard() -> None:
    """The whole cost of this change: an author who wrote `standard` still gets `standard`."""
    resolved = criticality.resolve({"criticality": "standard"})
    assert resolved.value == criticality.STANDARD
    assert resolved.source == criticality.DECLARED
    assert not resolved.defaulted


def test_malformed_value_resolves_to_critical_and_says_so() -> None:
    resolved = criticality.resolve({"criticality": "high"})
    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.MALFORMED
    assert "high" in resolved.describe()


def test_case_and_whitespace_are_a_declaration_not_a_defect() -> None:
    """`Critical` is what an author meant; reporting it as malformed would name a defect that is
    not there while resolving to the same answer."""
    resolved = criticality.resolve({"criticality": " Critical "})
    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.DECLARED


def test_taxonomy_is_not_extended() -> None:
    """Issue #101 is explicit that the levels do not grow: every resolution lands in KNOWN."""
    assert criticality.KNOWN == (criticality.STANDARD, criticality.CRITICAL)
    for raw in (
        {},
        {"criticality": "critical"},
        {"criticality": "standard"},
        {"criticality": "x"},
    ):
        assert criticality.resolve(raw).value in criticality.KNOWN


def test_unreadable_spec_file_resolves_to_critical(tmp_path: Path) -> None:
    """A spec nobody can parse is the last one that should quietly lose its adversarial stage."""
    resolved = criticality.resolve_spec_file(tmp_path / "nope" / "spec.md")
    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.UNREADABLE


def test_undecodable_spec_file_resolves_to_critical(tmp_path: Path) -> None:
    """Bytes that are not UTF-8 are the OTHER unreadable shape, and it used to escape the handler.

    `Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError` here - a `ValueError`, not an
    `OSError` - so a latin-1 byte pasted into a spec.md propagated out of `criticality.phase()` and
    killed `pipeline_state.next_stage` with a traceback instead of routing the phase. The source is
    asserted as well as the value: `critical` alone would also be produced by an absent field, so a
    test reading only the value would pass for the wrong reason.
    """
    spec = tmp_path / "spec.md"
    spec.write_bytes(b"---\ncriticality: standard\nauthor: caf\xe9\n---\n\n# Spec\n")

    resolved = criticality.resolve_spec_file(spec)

    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.UNREADABLE


def test_a_phase_holding_an_undecodable_spec_still_resolves(tmp_path: Path) -> None:
    """The reachable path: the resolver must ANSWER over such a phase, not raise through it."""
    spec_dir = tmp_path / "specs" / "1.1-a"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_bytes(b"---\ncriticality: standard\nx: caf\xe9\n---\n")

    resolved = criticality.phase(tmp_path)

    assert resolved.critical is True
    assert [r.source for _, r in resolved.resolutions] == [criticality.UNREADABLE]


# --- the phase-level OR -------------------------------------------------------------------------


def test_phase_with_a_spec_missing_the_field_is_critical(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality_value="standard")
    write_spec(tmp_path, "1-core", "1.2-b", criticality_value=None)
    resolved = criticality.phase(phase_dir(tmp_path))
    assert resolved.critical
    assert [name for name, _ in resolved.defaulted] == ["1.2-b"]


def test_phase_where_every_spec_declares_standard_stays_standard(
    tmp_path: Path,
) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality_value="standard")
    write_spec(tmp_path, "1-core", "1.2-b", criticality_value="standard")
    resolved = criticality.phase(phase_dir(tmp_path))
    assert not resolved.critical
    assert resolved.defaulted == ()
    assert "no spec in this phase resolves to `critical`" in resolved.reason()


def test_phase_with_an_explicit_critical_spec_is_critical(tmp_path: Path) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality_value="standard")
    write_spec(tmp_path, "1-core", "1.2-b", criticality_value="critical")
    resolved = criticality.phase(phase_dir(tmp_path))
    assert resolved.critical
    assert "1.2-b" in resolved.reason()


def test_announce_names_every_defaulted_spec(tmp_path: Path, capsys) -> None:
    """A default that fires silently is the same defect one step milder."""
    write_spec(tmp_path, "1-core", "1.1-a", criticality_value=None)
    resolved = criticality.phase(phase_dir(tmp_path))
    criticality.announce(phase_dir(tmp_path), resolved)
    err = capsys.readouterr().err
    assert "1.1-a" in err and "defaulted to `critical`" in err


def test_announce_is_silent_when_every_spec_declared(tmp_path: Path, capsys) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality_value="standard")
    resolved = criticality.phase(phase_dir(tmp_path))
    criticality.announce(phase_dir(tmp_path), resolved)
    assert capsys.readouterr().err == ""


def test_announce_does_not_repeat_for_one_phase(tmp_path: Path, capsys) -> None:
    write_spec(tmp_path, "1-core", "1.1-a", criticality_value=None)
    resolved = criticality.phase(phase_dir(tmp_path))
    criticality.announce(phase_dir(tmp_path), resolved)
    capsys.readouterr()
    criticality.announce(phase_dir(tmp_path), resolved)
    assert capsys.readouterr().err == ""


def test_the_shipped_spec_template_resolves_to_critical() -> None:
    """A spec authored from the template and never edited gets the STRONGER pipeline.

    The template is this pipeline's own emitted interface for `spec.md` (`doc_read_path.READ_PATH`
    names it as spec.md's `emitted_by`), so its frontmatter is parsed with the same reader a real
    spec goes through and handed to the same resolver - never matched as text. Pre-filled
    `standard`, the opt-out was the template's decision rather than an author's, which is the
    practical shape of issue #101 even though no field is literally absent.
    """
    import spec_gate_state

    template = Path(__file__).resolve().parents[1] / "docs/templates/spec.template.md"
    fields = spec_gate_state.frontmatter(template.read_text(encoding="utf-8"))

    resolved = criticality.resolve(fields)

    assert resolved.value == criticality.CRITICAL
    assert resolved.source == criticality.ABSENT
