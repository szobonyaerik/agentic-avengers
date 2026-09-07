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
import criticality as criticality_mod  # noqa: E402
import spec_gate_state  # noqa: E402
import verdict_currency  # noqa: E402

FEATURE_ORDER: tuple[tuple[str, str], ...] = (
    ("task-analysis.md", "task-analyst"),
    ("overview.md", "solution-architect"),
    ("plan.md", "implementation-planner"),
)

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
LEADING_NUMBERS = re.compile(r"\d+")
# The planner's contractual heading (docs/templates/plan.template.md): `### Phase <n> — <slug>`.
# Tolerate the dash variants people type; nothing else in a plan looks like this.
PLAN_PHASE_HEADING = re.compile(
    r"^###\s*Phase\s+(\d+)\s*[—–-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)


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
    criticality: str = criticality_mod.STANDARD
    #: Criticality-gated stages that do NOT run on this phase, each with its reason (issue #101).
    #: A list of sentences rather than a boolean because a reader has to be able to tell a skipped
    #: Breaker from a clean one, and the verdict reads identically in both cases.
    #:
    #: **Populated only on a phase-level state** - one whose specs are all written, gated and
    #: implemented, which is the first moment the phase's criticality is settled. On a state returned
    #: while the phase is still writing specs, empty means NOT YET DETERMINED, never "nothing is
    #: skipped": a phase whose specs are still being written has no final criticality, so a skip
    #: claim there would assert something the pipeline cannot know.
    skipped_stages: tuple[str, ...] = ()

    def as_json(self) -> str:
        """Serialise for the orchestrator, which reads this over Bash."""
        payload = asdict(self)
        payload["spec_path"] = str(self.spec_path) if self.spec_path else None
        payload["skipped_stages"] = list(self.skipped_stages)
        return json.dumps(payload, indent=2)


def _frontmatter(path: Path) -> dict[str, str]:
    """Parse a markdown artifact's YAML frontmatter as flat key/value strings.

    Unreadable is empty frontmatter, and it has the same two shapes here as in
    `criticality.resolve_spec_file`: the file cannot be OPENED (`OSError`), and its bytes cannot be
    DECODED (`UnicodeDecodeError`). The two readers must agree, because this one runs FIRST — every
    spec goes through `_spec_state` before any phase reaches the criticality resolver — so an
    `OSError`-only handler here propagated a decode failure out of `next_stage`, which routes every
    stage rather than only the Breaker. A spec that crashes the resolver is strictly worse than one
    that resolves to `critical`. Nothing wider is caught: any other failure is a defect, not a
    document this cannot read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
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
        "criticality": criticality_mod.resolve(fields).value,
    }

    # An approval is a claim about a BODY, and this module used to read only the stamp's value
    # (issue #42). `verifier_precheck` compared the body against the hash the gate recorded and
    # called a drifted spec UNGATED, so one spec was gated and ungated at once depending on who
    # asked — and this is the reader that decides what work happens next, so it was the one letting
    # work proceed on a spec no gate had judged in its current form. `spec_gate_state.status_of`
    # now folds the hash in and answers `stale` for that shape (issue #97), so there is no second
    # freshness question here for a reader to forget - the one token IS bound to the bytes.
    #
    # STALE only, never UNRECORDED: a spec the gate never hashed is UNKNOWABLE drift rather than
    # proven drift, and every spec stamped before the gate cache existed is in that state. Routing
    # those back would park the resolver on shipped work whose remedy nobody asked for, which is the
    # wedge §3a exists to prevent. The precheck still holds them, because it is diff-scoped and only
    # ever enforces what the change is responsible for.
    gate = spec_gate_state.status_of(spec_file)
    if not _excepted(phase, "spec-gate", spec.name):
        if gate == spec_gate_state.PENDING:
            return State(
                stage="spec-gate",
                reason=f"{spec.name} has not been through the spec gate",
                **common,
            )
        if gate == spec_gate_state.STALE:
            return State(
                stage="spec-gate",
                reason=(
                    f"{spec.name} has changed since the gate judged it — the approval describes a "
                    f"body this spec no longer has"
                ),
                **common,
            )
        if gate != spec_gate_state.APPROVED:
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


def _phase_criticality(phase: Path) -> criticality_mod.PhaseCriticality:
    """`critical` when any spec in the phase resolves to it — that is what the Breaker keys on.

    The resolution lives in `scripts/criticality.py`, which `breaker_gate` reads too: the stage this
    routes and the gate that enforces it must not disagree about whether a phase is critical. An
    absent, malformed or unreadable `criticality` resolves to `critical` there (issue #101) — it used
    to resolve to `standard` here, which deleted the Breaker from a phase with no author involved and
    nothing said so.

    Returns the whole resolution rather than its value so the one caller can hand it to
    `_skipped_stages` too; both readers otherwise parsed every spec.md in the phase separately.
    """
    resolved = criticality_mod.phase(phase)
    criticality_mod.announce(phase, resolved)
    return resolved


def _skipped_stages(
    phase: Path, resolved: criticality_mod.PhaseCriticality
) -> tuple[str, ...]:
    """The criticality-gated stages that will not run on this phase, each with its reason.

    Issue #101's second half, and the half that has to hold whichever way the default is decided: a
    phase whose Breaker never ran and a phase whose Breaker ran clean produce the same passing
    verdict, so the difference is stated rather than left to be inferred from an absence. Each gated
    stage answers for itself — `breaker_gate.skipped` knows about the exception ledger, which this
    resolver has no business re-deriving.

    The phase is resolved once by the caller and handed to both readers: every spec.md in it was
    otherwise read and parsed twice per state resolution, on every phase `next_stage` walks.
    """
    reason = breaker_gate.skipped(phase, resolved)
    return (reason,) if reason is not None else ()


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

    resolved = _phase_criticality(phase)
    common = {
        "feature": feature,
        "phase": phase.name,
        "criticality": resolved.value,
        "skipped_stages": _skipped_stages(phase, resolved),
    }

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

    if not (phase / "handover.md").is_file():
        # A phase that RESOLVES TO `criticality: critical` routes the Breaker (commands/avenger-run.md
        # §4), and "the resolver reports criticality: critical" used to be the only signal anyone
        # acted on — nothing enforced it, and it was owed twice on one feature and ran neither time
        # (issue #45). A stage that emits nothing is indistinguishable from a stage that never ran,
        # so this is asked mechanically, here, rather than by trusting the orchestrator to remember.
        # `breaker_gate.satisfied` refuses a missing record AND a vacuous one (a "clean" verdict
        # naming nothing attacked), because the agent's own instruction — "a clean report with no
        # attempts described is not acceptable" — is exactly what this makes checkable.
        #
        # It is asked INSIDE this branch, and only here, because a written handover.md is the
        # `shipped` evidence of §3a's applicability boundary: a mechanical rule binds what is still
        # OPEN, and what is CLOSED it may count and name, never block. Asked before this check it
        # re-opened every already-handed-over critical phase — no phase anywhere carries a
        # breaker.json yet — parking the resolver on shipped code so `/avenger-run --auto` could not
        # reach the phase in flight at all, which is the first wedge `applicability.py`'s own
        # docstring names. Enforcement loses nothing: `hook_verifier.sh` fires on the handover WRITE,
        # which is this same open moment, and `gate_ci.sh` backs it up diff-scoped.
        if breaker_gate.owed(phase, resolved):
            reason = breaker_gate.satisfied(phase)
            if reason is not None and not _excepted(phase, "breaker", phase.name):
                return State(stage="breaker", reason=reason, **common)

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

    # A passing verdict must not stand over a tree that has since changed (issue #51). The feature
    # close ship gate owns both findings and fixes while it runs, so it changes verified production
    # code and touches no phase artifact — and the verdict goes on asserting a file is byte-identical
    # after the fix commit changed it. Asked HERE because this is the last point at which the remedy
    # still exists: `done` is terminal, and a phase that has already closed cannot be re-opened for
    # it. Never a rewritten verdict — that would restate a verification nobody performed.
    stale = verdict_currency.check(Path(root), feature_dir)
    if stale is not None:
        return State(feature=feature, stage="verifier", reason=stale)

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
        state = next_stage(Path(args.root), args.feature, args.from_phase)
        print(state.as_json())
        # stdout is the JSON the orchestrator parses; a human reading the run sees the skip here.
        for line in state.skipped_stages:
            print(f"pipeline-state: {state.phase} — {line}", file=sys.stderr)
    except FeatureNotFoundError as exc:
        print(f"pipeline-state: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
