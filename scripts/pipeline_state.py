#!/usr/bin/env python3
"""Resolve which pipeline stage a feature owes next, from the artifacts on disk.

`/avenger-run` is resumable across a `/clear`, a compaction, or a closed laptop. Resume must not be a
model guess, so the artifact tree is the state: spec frontmatter stamps (`spec_gate`, `review_status`,
`status`) and the Verifier's `verdict.json` say exactly how far a feature got.

`spec_gate` is read through `scripts/spec_gate_state.py`, never parsed here — that module is the one
place the machine gate's status is decided, including how a legacy `fidelity_verdict` stamp reads.

The bias here is to under-report progress. When a stamp is missing or unreadable the stage that owns
it is reported as still owing work, because the cost of re-running a stage is a wasted call, while
the cost of skipping one is an ungated spec reaching the implementer.

**That bias has one honest limit, and it is the applicability boundary** (`scripts/applicability.py`).
A phase can close with a **disclosed exception**: a captain-ordered cap, a gate that could not be
reached, a stage deliberately not run — recorded on the phase's `exceptions.json` ledger, naming the
rule, the subject, who decided it and why. Read as an absent stamp, that is indistinguishable from
unfinished work: one measured feature closed phase 8 that way and this resolver parked on spec 8.1
forever, so `/avenger-run --auto` could not start a phase again from the moment phase 8 closed.
**A phase closed with a recorded exception is CLOSED**, and it is read here as closed. The two
remedies that do not need this — stamping `review_status: approved` for a human sign-off nobody gave,
or obtaining a machine verdict that was never obtained — are the "looks fine" class this pipeline
exists to remove, and the ledger is what makes neither necessary.

`--from-phase` is the blunt companion to that: it enters at a named phase, skipping everything
below it. It records nothing and judges nothing, so it says loudly which phases it skipped **and it
never answers a feature-wide question over them** — with any phase stepped over, `done`, `e2e-author`
and "the plan promises a phase nobody specced" are all claims about work this walk did not look at,
so the stage is `unknown` and the reason says why. Prefer the ledger — it fixes the cause; this only
steps over it for one invocation.

    python3 scripts/pipeline_state.py <feature-id> [--root .] [--from-phase N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import amendments  # noqa: E402
import applicability  # noqa: E402
import breaker_gate  # noqa: E402
import spec_gate_state  # noqa: E402

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


def _excepted(phase: Path, rule: str, subject: str) -> bool:
    """Whether this phase records a disclosed exception to `rule` for `subject`.

    An exception that applied silently would be the bypass this ledger exists to replace, so every
    one it applies is named on stderr — stdout carries the JSON the orchestrator parses.

    A ledger this cannot read grants NOTHING and says so. That is the same under-report bias the rest
    of the resolver runs on: the cost of re-running a stage is a wasted call, and the cost of
    skipping one on a file nobody could parse is a stage silently deleted.
    """
    try:
        record = applicability.excepted(phase, rule, subject)
    except applicability.ApplicabilityError as exc:
        print(
            f"pipeline-state: {phase.name} has an exception ledger this cannot read ({exc}). "
            f"No exception is granted — the stage below is still owed.",
            file=sys.stderr,
        )
        return False
    if record is None:
        return False
    print(
        f"pipeline-state: {phase.name} — `{rule}` is not owed for {subject}: {record.describe()}",
        file=sys.stderr,
    )
    return True


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

    gate = spec_gate_state.status(fields)
    if gate != spec_gate_state.APPROVED and not _excepted(phase, "spec-gate", spec.name):
        if gate == spec_gate_state.PENDING:
            return State(
                stage="spec-gate",
                reason=f"{spec.name} has not been through the spec gate",
                **common,
            )
        return State(
            stage="spec-writer",
            reason=f"{spec.name} was blocked by the spec gate",
            **common,
        )

    # The human sign-off is a separate question from the machine gate, and it is the only thing
    # `review_status` still means. Under SPEC_REVIEW_MODE=auto the gate stamps it itself.
    if fields.get("review_status") != "approved" and not _excepted(
        phase, "spec-review", spec.name
    ):
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
    if verdict != "pass" and not _excepted(phase, "verdict", phase.name):
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

    # A verdict is a claim about code. An amendment says that code has since changed and names the
    # requirement ids it touched, so a passing verdict standing over one is the pipeline asserting
    # something it has not checked. Only the amended requirements re-verify — that is the whole
    # point of the record — but the phase does owe the Verifier a pass over them.
    try:
        owed = amendments.due(phase)
    except amendments.AmendmentError:
        owed = [{"id": "?"}]  # an unreadable ledger is owed work, never silently none
    if owed:
        return State(
            stage="verifier",
            reason=(
                f"phase {phase.name} has {len(owed)} amendment(s) owed re-verification "
                f"({', '.join(str(a.get('id')) for a in owed)}); only their requirement ids re-verify"
            ),
            **common,
        )

    # A phase that declares `criticality: critical` routes the Breaker (commands/avenger-run.md §4),
    # and "the resolver reports criticality: critical" used to be the only signal anyone acted on —
    # nothing enforced it, and it was owed twice on one feature and ran neither time (issue #45). A
    # stage that emits nothing is indistinguishable from a stage that never ran, so this is checked
    # the same way a missing handover.md is: mechanically, here, not by trusting the orchestrator to
    # remember. `breaker_gate.satisfied` refuses a missing record AND a vacuous one (a "clean" verdict
    # naming nothing attacked), because the agent's own instruction — "a clean report with no
    # attempts described is not acceptable" — is exactly what this makes checkable.
    if breaker_gate.owed(phase):
        reason = breaker_gate.satisfied(phase)
        if reason is not None and not _excepted(phase, "breaker", phase.name):
            return State(stage="breaker", reason=reason, **common)

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


def _entered_at(
    phases: list[Path], from_phase: int | None
) -> tuple[list[Path], list[str]]:
    """The phases to walk and the ones stepped over, honouring `--from-phase`.

    Never silent about what it stepped over, and the skipped list is returned as well as printed:
    a stage the caller acts on must be derivable from what was actually examined.
    """
    if from_phase is None:
        return phases, []
    kept = [p for p in phases if _numeric_key(p.name)[0] >= from_phase]
    skipped = [p.name for p in phases if p not in kept]
    print(
        f"pipeline-state: entering at phase {from_phase} — {len(skipped)} earlier phase(s) were "
        f"NOT examined and nothing about them is claimed"
        + (f": {', '.join(skipped)}" if skipped else ""),
        file=sys.stderr,
    )
    return kept, skipped


def next_stage(root: Path, feature: str, from_phase: int | None = None) -> State:
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
    walked, skipped = _entered_at(phases, from_phase)

    for phase in walked:
        pending = _phase_state(feature, phase)
        if pending is not None:
            return pending

    # Everything below here is a claim about the FEATURE — a planned phase nobody specced, the e2e
    # suite, `done` — and every one of them is false about a phase this invocation never opened.
    # `--from-phase` judges nothing, so it may not answer them: an orchestrator reading `done` from a
    # walk that skipped an unfinished phase 1 would drive the feature straight to close.
    if skipped:
        return State(
            feature=feature,
            stage="unknown",
            reason=(
                f"every phase examined from {from_phase} on is green, but "
                f"{len(skipped)} earlier phase(s) were not examined ({', '.join(skipped)}), so "
                f"nothing feature-wide is claimed. Re-run without --from-phase, or record the "
                f"exception that closes those phases (scripts/applicability.py record)"
            ),
        )

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
    parser.add_argument(
        "--from-phase",
        type=int,
        default=None,
        help="enter at this phase number, skipping every earlier phase. Records nothing and judges "
        "nothing; the phases it steps over are named on stderr. Prefer an exception on the phase's "
        "ledger (scripts/applicability.py), which fixes the cause instead of stepping over it.",
    )
    args = parser.parse_args(argv)

    try:
        print(next_stage(Path(args.root), args.feature, args.from_phase).as_json())
    except FeatureNotFoundError as exc:
        print(f"pipeline-state: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
