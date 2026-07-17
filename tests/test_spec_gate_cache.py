"""Tests for the spec gate cache.

This decides whether a paid, nondeterministic gate re-runs. The dangerous direction is skipping a
gate that should have run, so most of these pin "must still gate" cases.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from spec_gate_cache import (  # noqa: E402
    body_hash,
    needs_gating,
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


class TestParsing:
    def test_no_frontmatter_raises(self):
        with pytest.raises(ValueError):
            needs_gating("# Just a heading\n", "fidelity")

    def test_trailing_whitespace_is_not_a_content_change(self):
        assert body_hash("body\n") == body_hash("body\n\n  ")
