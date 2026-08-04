"""Tests for the pipeline-observations log.

The retrospective's whole value is that an observation survives to a human. The dangerous direction
is losing one: an `--auto` run skips the lavish triage, so its observations sit with
`triage: pending` until the *next interactive* run's preflight sweep finds them. If the sweep misses
a file, the observation is silently lost and the pipeline stops learning — so the sweep cases below
are pinned hard, including the cross-feature hand-back that `--auto` depends on.

The opposite failure matters too: a set the human already dismissed must never be re-presented, or
the triage becomes noise people learn to skip.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pipeline_observations import (  # noqa: E402
    OBSERVATIONS_FILENAME,
    append_observation,
    pending_features,
    resolve_triage,
    read_triage,
)


def feature_dir(root: Path, name: str) -> Path:
    d = root / "docs" / "features" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── append: creates a well-formed file, then appends without clobbering ──────


def test_append_creates_the_file_with_pending_triage(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="gate-friction", note="fidelity NO-GO x3")

    path = tmp_path / "docs" / "features" / "demo" / OBSERVATIONS_FILENAME
    assert path.exists()
    body = path.read_text()
    assert "triage: pending" in body
    assert "stage: pipeline-retrospective" in body
    assert "feature: demo" in body
    assert "fidelity NO-GO x3" in body


def test_append_twice_keeps_both_observations(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="gate-friction", note="first")
    append_observation(tmp_path, "demo", kind="success", note="second")

    body = (tmp_path / "docs" / "features" / "demo" / OBSERVATIONS_FILENAME).read_text()
    assert "first" in body
    assert "second" in body
    # exactly one frontmatter block - the second append must not re-emit a header
    assert body.count("stage: pipeline-retrospective") == 1


def test_append_records_evidence_when_given(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(
        tmp_path, "demo", kind="gate-friction", note="rubric too strict",
        evidence=["docs/features/demo/phases/1-x/specs/1.1-y/spec.md"],
    )
    body = (tmp_path / "docs" / "features" / "demo" / OBSERVATIONS_FILENAME).read_text()
    assert "docs/features/demo/phases/1-x/specs/1.1-y/spec.md" in body


def test_append_reopens_triage_on_a_resolved_file(tmp_path: Path) -> None:
    """A new observation after a triage must be seen, not buried under `triage: done`."""
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="success", note="old")
    resolve_triage(tmp_path, "demo")
    assert read_triage(tmp_path, "demo") == "done"

    append_observation(tmp_path, "demo", kind="gate-friction", note="new")
    assert read_triage(tmp_path, "demo") == "pending"


# ── the preflight sweep ──────────────────────────────────────────────────────


def test_pending_is_empty_when_nothing_was_observed(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    assert pending_features(tmp_path) == []


def test_pending_finds_an_observed_feature(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="success", note="gate earned its keep")
    assert pending_features(tmp_path) == ["demo"]


def test_pending_ignores_a_resolved_feature(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="success", note="x")
    resolve_triage(tmp_path, "demo")
    assert pending_features(tmp_path) == []


def test_auto_run_handback_surfaces_across_features(tmp_path: Path) -> None:
    """The case `--auto` depends on.

    An unattended run finishes feature A and leaves it pending. The human then starts an interactive
    run on a *different* feature B. B's preflight must surface A, or A is lost forever: `done` is a
    terminal state, so nothing will ever re-trigger A's own close.
    """
    feature_dir(tmp_path, "alpha")
    feature_dir(tmp_path, "beta")
    append_observation(tmp_path, "alpha", kind="gate-friction", note="from an --auto run")

    assert "alpha" in pending_features(tmp_path)


def test_pending_is_sorted_for_stable_output(tmp_path: Path) -> None:
    for name in ("zulu", "alpha", "mike"):
        feature_dir(tmp_path, name)
        append_observation(tmp_path, name, kind="success", note="x")
    assert pending_features(tmp_path) == ["alpha", "mike", "zulu"]


def test_pending_survives_a_feature_dir_with_no_observations(tmp_path: Path) -> None:
    feature_dir(tmp_path, "empty")
    feature_dir(tmp_path, "observed")
    append_observation(tmp_path, "observed", kind="success", note="x")
    assert pending_features(tmp_path) == ["observed"]


def test_pending_returns_empty_when_docs_features_is_absent(tmp_path: Path) -> None:
    """A repo that has never run the pipeline must not error the preflight."""
    assert pending_features(tmp_path) == []


def test_unreadable_frontmatter_is_reported_pending(tmp_path: Path) -> None:
    """Bias to surfacing. A malformed file is a human's problem, not something to silently drop."""
    d = feature_dir(tmp_path, "broken")
    (d / OBSERVATIONS_FILENAME).write_text("no frontmatter at all\n")
    assert pending_features(tmp_path) == ["broken"]


# ── resolve: dismissing is a decision and must stick ─────────────────────────


def test_resolve_flips_pending_to_done(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="success", note="x")
    resolve_triage(tmp_path, "demo")
    assert read_triage(tmp_path, "demo") == "done"


def test_resolve_preserves_the_observations(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="gate-friction", note="keep me")
    resolve_triage(tmp_path, "demo")
    body = (tmp_path / "docs" / "features" / "demo" / OBSERVATIONS_FILENAME).read_text()
    assert "keep me" in body


def test_resolve_is_idempotent(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    append_observation(tmp_path, "demo", kind="success", note="x")
    resolve_triage(tmp_path, "demo")
    resolve_triage(tmp_path, "demo")
    assert read_triage(tmp_path, "demo") == "done"
    assert pending_features(tmp_path) == []


def test_resolve_on_a_feature_with_no_observations_raises(tmp_path: Path) -> None:
    feature_dir(tmp_path, "demo")
    with pytest.raises(FileNotFoundError):
        resolve_triage(tmp_path, "demo")
