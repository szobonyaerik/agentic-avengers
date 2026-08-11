"""Tests for the spec gate cache.

This decides whether a paid, nondeterministic gate re-runs. The dangerous direction is skipping a
gate that should have run, so most of these pin "must still gate" cases.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from spec_gate_cache import (  # noqa: E402
    NEEDS_GATING,
    UNCHANGED,
    body_hash,
    main,
    needs_gating,
    previous,
    split_spec,
    stamp,
)

SPEC = """---
feature: f
spec: 1.1
status: draft
review_status: pending
fidelity_verdict: pending
---
# Spec 1.1

## Requirements
- R1.1.1 — a signed delivery is persisted exactly once.
"""


class TestNeedsGating:
    def test_ungated_spec_is_gated(self):
        assert needs_gating(SPEC, "fidelity") is True

    def test_stamped_spec_is_skipped(self):
        assert needs_gating(stamp(SPEC, "fidelity"), "fidelity") is False

    def test_each_gate_has_its_own_key(self):
        """A shared key would let the first gate to stamp disable every later gate."""
        stamped = stamp(SPEC, "fidelity")
        assert needs_gating(stamped, "fidelity") is False
        assert needs_gating(stamped, "review") is True

    def test_frontmatter_only_change_is_skipped(self):
        """`status: done` is the implementer's bookkeeping, not a change to what was judged."""
        stamped = stamp(SPEC, "fidelity")
        edited = stamped.replace("status: draft", "status: done")
        assert edited != stamped
        assert needs_gating(edited, "fidelity") is False

    def test_body_change_forces_regating(self):
        stamped = stamp(SPEC, "fidelity")
        edited = stamped.replace("persisted exactly once", "persisted at least once")
        assert needs_gating(edited, "fidelity") is True

    def test_added_requirement_forces_regating(self):
        stamped = stamp(SPEC, "fidelity") + "- R1.1.2 — something new.\n"
        assert needs_gating(stamped, "fidelity") is True

    def test_tampered_hash_forces_regating(self):
        """A hand-edited hash must not be able to wave a spec through."""
        stamped = stamp(SPEC, "fidelity").replace(
            "fidelity_gated_hash: ", "fidelity_gated_hash: 0"
        )
        assert needs_gating(stamped, "fidelity") is True


class TestStamp:
    def test_stamp_is_idempotent(self):
        once = stamp(SPEC, "fidelity")
        assert stamp(once, "fidelity") == once

    def test_stamp_preserves_body_exactly(self):
        _, before = split_spec(SPEC)
        _, after = split_spec(stamp(SPEC, "fidelity"))
        assert before == after

    def test_two_gates_coexist(self):
        both = stamp(stamp(SPEC, "fidelity"), "review")
        assert needs_gating(both, "fidelity") is False
        assert needs_gating(both, "review") is False

    def test_restamp_updates_after_body_change(self):
        stamped = stamp(SPEC, "fidelity")
        edited = stamped.replace("exactly once", "twice")
        assert needs_gating(edited, "fidelity") is True
        assert needs_gating(stamp(edited, "fidelity"), "fidelity") is False


class TestKeptBody:
    """`stamp` keeps the body it judged, so a re-gate can be scoped to what changed.

    A verdict is a sample from a distribution: re-judging already-approved text is how one spec drew
    three different verdicts from the same model. The kept body is what bounds the second look.
    """

    @pytest.fixture
    def spec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        path = tmp_path / "spec.md"
        path.write_text(SPEC, encoding="utf-8")
        return path

    def test_nothing_is_kept_before_the_first_gate(self, spec):
        assert previous(spec, "review") is None

    def test_stamp_keeps_the_body_it_judged(self, spec):
        assert main(["stamp", str(spec), "review"]) == NEEDS_GATING
        assert previous(spec, "review") == split_spec(SPEC)[1]

    def test_the_kept_body_survives_a_later_edit(self, spec):
        """The point of keeping it: it is the predecessor the current text is diffed against."""
        main(["stamp", str(spec), "review"])
        spec.write_text(
            spec.read_text(encoding="utf-8").replace("exactly once", "at least once"),
            encoding="utf-8",
        )
        assert "exactly once" in (previous(spec, "review") or "")

    def test_each_gate_keeps_its_own_body(self, spec):
        main(["stamp", str(spec), "fidelity"])
        assert previous(spec, "review") is None

    def test_sibling_specs_do_not_collide(self, spec, tmp_path):
        other = tmp_path / "other.md"
        other.write_text(SPEC.replace("R1.1.1", "R1.2.1"), encoding="utf-8")
        main(["stamp", str(spec), "review"])
        main(["stamp", str(other), "review"])
        assert "R1.1.1" in (previous(spec, "review") or "")
        assert "R1.2.1" in (previous(other, "review") or "")

    def test_previous_reports_a_miss_without_failing(self, spec, capsys):
        """A lost cache costs a full re-gate — the safe direction — not an error."""
        assert main(["previous", str(spec), "review"]) == UNCHANGED
        assert capsys.readouterr().out == ""

    def test_previous_prints_the_kept_body(self, spec, capsys):
        main(["stamp", str(spec), "review"])
        assert main(["previous", str(spec), "review"]) == NEEDS_GATING
        assert "R1.1.1" in capsys.readouterr().out


class TestParsing:
    def test_no_frontmatter_raises(self):
        with pytest.raises(ValueError):
            needs_gating("# Just a heading\n", "fidelity")

    def test_trailing_whitespace_is_not_a_content_change(self):
        assert body_hash("body\n") == body_hash("body\n\n  ")


class TestVerdictIsRecordedWithTheHash:
    """A hash stamped only on passes is a rejection with no record of what was rejected.

    Only GO and REVIEW used to stamp, so a NO-GO left the spec exactly as it found it. Two things
    followed: the run kept no trace of which text had been refused, and the NEXT frontmatter-only
    edit found an unchanged body with no verdict attached — which the caller read as "nothing to do".
    """

    def test_the_verdict_is_stamped_next_to_the_hash(self):
        text = stamp(SPEC, "fidelity", "NO-GO")
        assert "fidelity_gated_verdict: NO-GO" in text
        assert "fidelity_gated_hash:" in text

    def test_check_hands_back_the_verdict_it_recorded(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(stamp(SPEC, "fidelity", "NO-GO"))
        assert main(["check", str(spec), "fidelity"]) == UNCHANGED
        assert capsys.readouterr().out.strip() == "NO-GO"

    def test_a_stamp_from_before_verdicts_were_recorded_reads_as_a_pass(
        self, tmp_path, capsys, monkeypatch
    ):
        """Historically only GO and REVIEW stamped at all, so a bare hash IS a pass. Inferring
        anything else would re-gate every spec approved before this change."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        legacy = stamp(SPEC, "fidelity", "GO").replace("fidelity_gated_verdict: GO\n", "")
        spec.write_text(legacy)
        assert main(["check", str(spec), "fidelity"]) == UNCHANGED
        assert capsys.readouterr().out.strip() == "GO"

    def test_each_gate_records_its_own_verdict(self, tmp_path, capsys, monkeypatch):
        """A shared key would let the first gate's verdict answer for the second's."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(stamp(stamp(SPEC, "fidelity", "GO"), "review", "NO-GO"))
        assert main(["check", str(spec), "fidelity"]) == UNCHANGED
        assert capsys.readouterr().out.strip() == "GO"
        assert main(["check", str(spec), "review"]) == UNCHANGED
        assert capsys.readouterr().out.strip() == "NO-GO"

    def test_a_changed_body_is_still_gated_whatever_was_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(stamp(SPEC, "fidelity", "NO-GO") + "\n- R1.1.2 — another requirement.\n")
        assert main(["check", str(spec), "fidelity"]) == NEEDS_GATING

    def test_the_report_is_kept_and_handed_back(self, tmp_path, monkeypatch, capsys):
        """So a replayed rejection carries reasoning rather than just a token."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(SPEC)
        report = tmp_path / "report.txt"
        report.write_text("R1.1.1 has no observable acceptance criterion.")

        main(["stamp", str(spec), "fidelity", "NO-GO", str(report)])
        capsys.readouterr()

        assert main(["report", str(spec), "fidelity"]) == NEEDS_GATING
        assert "no observable acceptance criterion" in capsys.readouterr().out

    def test_a_missing_report_is_reported_as_missing_not_as_empty(self, tmp_path, monkeypatch):
        """The kept report is rebuildable scratch; its absence must be distinguishable from a
        rejection that genuinely said nothing."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(SPEC)
        main(["stamp", str(spec), "fidelity", "NO-GO"])
        assert main(["report", str(spec), "fidelity"]) == UNCHANGED

    def test_a_rejection_does_not_replace_the_body_the_gate_approved(self, tmp_path, monkeypatch):
        """The kept body is what the next re-gate diffs against under 'PREVIOUSLY APPROVED'. A
        rejection that overwrote it would label rejected text as approved, and show the author
        changes-since-rejection while telling them they were changes-since-approval."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(SPEC)
        main(["stamp", str(spec), "review", "GO"])
        approved = previous(spec, "review")

        spec.write_text(spec.read_text() + "\n- R1.1.2 — a requirement the gate refused.\n")
        main(["stamp", str(spec), "review", "NO-GO"])

        assert previous(spec, "review") == approved
        assert "the gate refused" not in previous(spec, "review")

    def test_a_rejection_still_records_its_own_hash_and_verdict(self, tmp_path, monkeypatch, capsys):
        """Leaving the approved body alone must not cost the rejection its record."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        spec = tmp_path / "spec.md"
        spec.write_text(SPEC)
        main(["stamp", str(spec), "review", "GO"])
        spec.write_text(spec.read_text() + "\n- R1.1.2 — a requirement the gate refused.\n")
        main(["stamp", str(spec), "review", "NO-GO"])
        capsys.readouterr()

        assert main(["check", str(spec), "review"]) == UNCHANGED
        assert capsys.readouterr().out.strip() == "NO-GO"
