#!/usr/bin/env python3
"""Resolve which pipeline stage a feature owes next, from the artifacts on disk.

`/avenger-run` is resumable across a `/clear`, a compaction, or a closed laptop. Resume must not be a
model guess, so the artifact tree is the state: spec frontmatter stamps (`fidelity_verdict`,
`review_status`, `status`) and the Verifier's `verdict.json` say exactly how far a feature got.

The bias here is to under-report progress. When a stamp is missing or unreadable the stage that owns
it is reported as still owing work, because the cost of re-running a stage is a wasted call, while
the cost of skipping one is an ungated spec reaching the implementer.

    python3 scripts/pipeline_state.py <feature-id> [--root .]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

FEATURE_ORDER: tuple[tuple[str, str], ...] = (
    ("task-analysis.md", "task-analyst"),
    ("overview.md", "solution-architect"),
    ("plan.md", "implementation-planner"),
)

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
LEADING_NUMBERS = re.compile(r"\d+")
# The planner's contractual heading (docs/templates/plan.template.md): `### Phase <n> — <slug>`.
# Tolerate the dash variants people type; nothing else in a plan looks like this.
PLAN_PHASE_HEADING = re.compile(r"^###\s*Phase\s+(\d+)\s*[—–-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


class PipelineStateError(Exception):
    """Base class for state-resolution failures."""


class FeatureNotFoundError(PipelineStateError):
    """Raised when the feature has no docs/features/<feature> directory."""


@dataclass(frozen=True)
class State:
    """The single next stage a feature owes, and why."""

    feature: str
    stage: str
    reason: str
    phase: str | None = None
    spec: str | None = None
    spec_path: Path | None = None
    criticality: str = "standard"

    def as_json(self) -> str:
        """Serialise for the orchestrator, which reads this over Bash."""
        payload = asdict(self)
        payload["spec_path"] = str(self.spec_path) if self.spec_path else None
        return json.dumps(payload, indent=2)


def _frontmatter(path: Path) -> dict[str, str]:
    """Parse a markdown artifact's YAML frontmatter as flat key/value strings."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    match = FRONTMATTER.match(text)
    if not match:
        return {}

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip()] = value.split("#")[0].strip()
    return fields


def _numeric_key(name: str) -> tuple[int, ...]:
    """Sort `1.10-c` after `1.2-b`; a lexical sort would walk them out of order."""
    return tuple(int(n) for n in LEADING_NUMBERS.findall(name)) or (0,)


def _ordered_dirs(parent: Path) -> list[Path]:
    """Child directories in pipeline (numeric) order; empty when the parent is absent."""
    if not parent.is_dir():
        return []
    return sorted(
        (p for p in parent.iterdir() if p.is_dir()), key=lambda p: _numeric_key(p.name)
    )


def _spec_state(feature: str, phase: Path, spec: Path) -> State | None:
    """The stage this spec still owes, or None when it is implemented and green."""
    spec_file = spec / "spec.md"
    if not spec_file.is_file():
        return State(
            feature=feature,
            stage="spec-writer",
            reason=f"{spec.name} has no spec.md",
            phase=phase.name,
            spec=spec.name,
        )

    fields = _frontmatter(spec_file)
    common = {
        "feature": feature,
        "phase": phase.name,
        "spec": spec.name,
        "spec_path": spec_file,
        "criticality": fields.get("criticality", "standard"),
    }

    fidelity = fields.get("fidelity_verdict", "")
    if not fidelity:
        return State(
            stage="fidelity-gate",
            reason=f"{spec.name} carries no fidelity_verdict",
            **common,
        )
    if fidelity.upper() == "NO-GO":
        return State(
            stage="spec-writer",
            reason=f"{spec.name} fidelity_verdict is NO-GO",
            **common,
        )

    if fields.get("review_status") != "approved":
        return State(
            stage="spec-review", reason=f"{spec.name} is not approved", **common
        )

    if fields.get("status") != "done":
        return State(
            stage="implementer", reason=f"{spec.name} is not implemented", **common
        )

    return None


def _phase_criticality(phase: Path) -> str:
    """`critical` when any spec in the phase declares it — that is what the Breaker keys on."""
    for spec in _ordered_dirs(phase / "specs"):
        if _frontmatter(spec / "spec.md").get("criticality") == "critical":
            return "critical"
    return "standard"


def _verdict(phase: Path) -> str | None:
    """The Verifier's persisted verdict, or None when it is absent or unreadable."""
    path = phase / "verdict.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("verdict", ""))
    except (OSError, ValueError, AttributeError):
        return None


def _phase_state(feature: str, phase: Path) -> State | None:
    """The stage this phase still owes, or None when it is verified and handed over."""
    specs = _ordered_dirs(phase / "specs")
    if not specs:
        return State(
            feature=feature,
            stage="spec-writer",
            reason=f"phase {phase.name} has no specs",
            phase=phase.name,
        )

    for spec in specs:
        pending = _spec_state(feature, phase, spec)
        if pending is not None:
            return pending

    criticality = _phase_criticality(phase)
    common = {"feature": feature, "phase": phase.name, "criticality": criticality}

    verdict = _verdict(phase)
    if verdict != "pass":
        if verdict == "fail":
            return State(
                stage="implementer",
                reason=f"phase {phase.name} verdict is fail",
                **common,
            )
        return State(
            stage="verifier",
            reason=f"phase {phase.name} has no passing verdict",
            **common,
        )

    if not (phase / "handover.md").is_file():
        return State(
            stage="handover", reason=f"phase {phase.name} has no handover.md", **common
        )

    return None


def _planned_phases(feature_dir: Path) -> list[tuple[int, str]]:
    """Phase numbers and titles the plan commits to, in order; empty when plan.md is unreadable.

    This is what stops the resolver from calling a feature finished just because every phase
    *folder that exists* is green: the plan, not the filesystem, says how many phases there are.
    A plan without recognisable headings yields [] and the folder-walk behaviour stands — a
    malformed plan must not wedge a feature, only an explicit one may extend it.
    """
    try:
        text = (feature_dir / "plan.md").read_text(encoding="utf-8")
    except OSError:
        return []
    return [(int(num), title) for num, title in PLAN_PHASE_HEADING.findall(text)]


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "phase"


def _missing_planned_phase(feature_dir: Path, phases: list[Path]) -> State | None:
    """The first planned phase with no folder on disk, as a spec-writer obligation."""
    planned = _planned_phases(feature_dir)
    if not planned:
        return None
    on_disk = {_numeric_key(p.name)[0] for p in phases}
    for number, title in planned:
        if number not in on_disk:
            name = f"{number}-{_slugify(title)}"
            return State(
                feature=feature_dir.name,
                stage="spec-writer",
                reason=(
                    f"plan.md lists {len(planned)} phases but only {len(phases)} exist on disk; "
                    f"phase {number} ({title}) has not been specced"
                ),
                phase=name,
            )
    return None


def next_stage(root: Path, feature: str) -> State:
    """Resolve the one stage `feature` owes next, walking phases in dependency order.

    Raises:
        FeatureNotFoundError: the feature has never been scaffolded.
    """
    feature_dir = Path(root) / "docs" / "features" / feature
    if not feature_dir.is_dir():
        raise FeatureNotFoundError(f"no such feature: {feature_dir}")

    for artifact, stage in FEATURE_ORDER:
        if not (feature_dir / artifact).is_file():
            return State(feature=feature, stage=stage, reason=f"{artifact} is missing")

    phases = _ordered_dirs(feature_dir / "phases")
    if not phases:
        return State(
            feature=feature,
            stage="spec-writer",
            reason="plan.md has no phases on disk yet",
        )

    for phase in phases:
        pending = _phase_state(feature, phase)
        if pending is not None:
            return pending

    # Every phase folder on disk is green — but the plan may promise more. Without this check the
    # resolver skipped straight to e2e/done after the last *existing* phase, silently dropping every
    # phase nobody had specced yet.
    missing = _missing_planned_phase(feature_dir, phases)
    if missing is not None:
        return missing

    if not (feature_dir / "e2e-mapping.md").is_file():
        return State(
            feature=feature,
            stage="e2e-author",
            reason="every phase is verified; e2e is missing",
        )

    return State(
        feature=feature,
        stage="done",
        reason="every phase is verified and e2e is mapped",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print the next stage as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feature", help="feature id under docs/features/")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    args = parser.parse_args(argv)

    try:
        print(next_stage(Path(args.root), args.feature).as_json())
    except FeatureNotFoundError as exc:
        print(f"pipeline-state: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
