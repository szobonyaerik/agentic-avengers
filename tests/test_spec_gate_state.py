"""One place decides what a spec's gate stamp means, including for specs written before the collapse.

The pipeline ran two model gates over one document and a spec carried a stamp from each. They are one
gate now, with one stamp. The risk in that change is not the new stamp — it is a repository full of
specs carrying the old one, and a rule about how to read them copied into three callers.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import spec_gate_cache  # noqa: E402
from spec_gate_state import (  # noqa: E402
    APPROVED,
    BLOCKED,
    FRESH,
    PENDING,
    STALE,
    UNRECORDED,
    frontmatter,
    main,
    status,
    status_of,
)


def fm(**fields: str) -> dict[str, str]:
    return fields


# ── the current stamp ────────────────────────────────────────────────────────


def test_the_current_stamp_is_read_directly() -> None:
    assert status(fm(spec_gate="approved")) == APPROVED
    assert status(fm(spec_gate="blocked")) == BLOCKED
    assert status(fm(spec_gate="pending")) == PENDING


def test_a_stamp_nobody_can_read_is_pending_not_approved() -> None:
    """Under-report, as the resolver does everywhere: the cost of re-gating is one call, and the
    cost of skipping is an ungated spec reaching the implementer."""
    assert status(fm(spec_gate="ok?")) == PENDING


def test_a_spec_with_no_stamp_at_all_is_pending() -> None:
    assert status(fm()) == PENDING


# ── the legacy pair, derived rather than migrated ────────────────────────────


def test_a_legacy_no_go_reads_as_blocked() -> None:
    assert status(fm(fidelity_verdict="NO-GO", review_status="approved")) == BLOCKED


def test_a_legacy_go_reads_as_approved() -> None:
    assert status(fm(fidelity_verdict="GO", review_status="pending")) == APPROVED
    assert status(fm(fidelity_verdict="REVIEW")) == APPROVED


def test_a_legacy_pending_fidelity_reads_as_pending() -> None:
    assert status(fm(fidelity_verdict="pending", review_status="approved")) == PENDING


def test_the_new_stamp_wins_over_a_stale_legacy_one() -> None:
    """A spec re-gated by the one gate carries both for a while; the current stamp is the answer."""
    assert status(fm(spec_gate="approved", fidelity_verdict="NO-GO")) == APPROVED


# ── files ────────────────────────────────────────────────────────────────────


def test_status_of_reads_a_file(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\nspec_gate: blocked\n---\n\n# Spec\n")
    assert status_of(spec) == BLOCKED


def test_an_unreadable_spec_is_pending_never_approved(tmp_path: Path) -> None:
    assert status_of(tmp_path / "missing.md") == PENDING


def test_a_spec_with_no_frontmatter_is_pending(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec with no frontmatter\n")
    assert frontmatter(spec.read_text()) == {}
    assert status_of(spec) == PENDING


def test_cli_exit_code_is_approved_or_not(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nspec_gate: approved\n---\n\nbody\n")
    assert main(["status", str(spec)]) == 0
    spec.write_text("---\nspec_gate: pending\n---\n\nbody\n")
    assert main(["status", str(spec)]) == 1
    assert main(["status", str(tmp_path / "gone.md")]) == 2


def test_a_comment_after_the_value_is_not_part_of_it(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nspec_gate: approved   # set by the gate\n---\n\nbody\n")
    assert status_of(spec) == APPROVED


# ── writing the stamp — one writer, one spelling ─────────────────────────────


def test_set_replaces_an_existing_stamp(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\nspec_gate: pending\n---\n\n# Spec\n")
    main(["set", str(spec), "approved"])
    assert status_of(spec) == APPROVED
    assert spec.read_text().count("spec_gate:") == 1


def test_set_INSERTS_a_stamp_that_is_not_there(tmp_path: Path) -> None:
    """The defect this writer exists to remove: the hook had a `sed` for the key-present case and an
    inline heredoc for the key-absent one, and only the second could insert. A blocked spec whose
    frontmatter lacked the key was left reading `pending` — an ungated spec that looks un-judged."""
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\nstatus: draft\n---\n\n# Spec\n")
    main(["set", str(spec), "blocked"])
    assert status_of(spec) == BLOCKED
    assert spec.read_text().startswith("---\n")


def test_set_leaves_the_body_alone(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    body = "\n# Spec\n\n## Requirements\n- R1.1.1 — a thing\n"
    spec.write_text("---\nfeature: demo\n---\n" + body)
    main(["set", str(spec), "approved"])
    assert spec.read_text().endswith(body)


def test_set_refuses_a_state_that_is_not_one_of_the_three(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\n---\n\n# Spec\n")
    assert main(["set", str(spec), "probably-fine"]) == 2
    assert "probably-fine" not in spec.read_text()


def test_set_refuses_a_spec_with_no_frontmatter(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec with no frontmatter\n")
    assert main(["set", str(spec), "approved"]) == 2


# --- freshness: the runbook check that derives the gate name ------------------------------------
#
# Issue #56: the documented freshness command hard-coded ONE gate name (`fidelity`), which collapsed
# specs do not carry — so following the runbook on a healthy, freshly APPROVED spec returned
# NEEDS_GATING and read as STALE. The fix is not a corrected gate name in a document: it is a check
# that derives the name from the spec, so the runbook and the gate names cannot drift again.


def _stamped(tmp_path: Path, gate: str) -> Path:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\nspec_gate: approved\n---\n\n# Spec\n- a requirement\n")
    spec.write_text(spec_gate_cache.stamp(spec.read_text(), gate, "APPROVED"))
    return spec


def test_freshness_reads_a_collapsed_gate_stamp_as_fresh(tmp_path: Path, capsys) -> None:
    spec = _stamped(tmp_path, "gate")
    assert main(["freshness", str(spec)]) == 0
    assert capsys.readouterr().out.strip() == FRESH


def test_freshness_reads_a_legacy_fidelity_stamp_as_fresh(tmp_path: Path, capsys) -> None:
    """A repository mid-upgrade still carries the two gates the one gate replaced."""
    spec = _stamped(tmp_path, "fidelity")
    assert main(["freshness", str(spec)]) == 0
    assert capsys.readouterr().out.strip() == FRESH


def test_freshness_reads_an_edited_body_as_stale(tmp_path: Path, capsys) -> None:
    spec = _stamped(tmp_path, "gate")
    spec.write_text(spec.read_text() + "\n- a requirement nobody gated\n")
    assert main(["freshness", str(spec)]) == 1
    assert capsys.readouterr().out.strip() == STALE


def test_freshness_separates_a_never_hashed_spec_from_a_drifted_one(tmp_path: Path, capsys) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("---\nfeature: demo\nspec_gate: approved\n---\n\n# Spec\n")
    assert main(["freshness", str(spec)]) == 1
    assert capsys.readouterr().out.strip() == UNRECORDED


def test_freshness_fails_closed_on_a_spec_it_cannot_read(tmp_path: Path) -> None:
    assert main(["freshness", str(tmp_path / "nope.md")]) == 2
