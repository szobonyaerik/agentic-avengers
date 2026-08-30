#!/usr/bin/env python3
"""How a spec's `criticality` is resolved - the one place an absent field is turned into a value.

## The defect (issue #101)

`criticality: critical` is what routes the Breaker (`commands/avenger-run.md` §4), the pipeline's one
criticality-gated stage. It was read in two places - `pipeline_state._phase_criticality` and
`breaker_gate.owed` - and both read it the same way: `fields.get("criticality", "standard")`. An
absent field was not an error, it was the WEAKER pipeline. So a spec that simply never wrote the line
lost its adversarial stage, the phase completed normally, the verdict passed, and nothing anywhere
said a stage had not run. It was caught by hand in grid-bot-platform phase 4 - the OKX connector, that
feature's highest-risk phase - before dispatch, and nothing in the pipeline would have flagged it.

That is this repository's recurring shape one more time: **a safety-relevant absence resolving to the
less safe option, silently.**

## The decision: an absent or unreadable field resolves to `critical`

Two directions were available (issue #101 names both): fail at spec-gate time on an absent
`criticality`, or default it to `critical`. This defaults it, for the reason §3a states as a rule -
**a rule whose remedy is unavailable is not a gate, it is a wedge.** A hard spec-gate failure binds
only a spec still going THROUGH the gate; a spec already stamped `status: done` has shipped, cannot
be re-gated, and would go on resolving to `standard` forever - so the gate direction leaves the actual
hole open on exactly the specs nobody is looking at any more, which is where it did its damage. The
default binds every reader immediately, including shipped specs, and its cost - a Breaker run on a
phase that never asked for one - has a remedy that already exists and is audited: the disclosed
exception ledger (`applicability.py record --rule breaker`). A wedge with no remedy versus a run with
a recorded one is not a close call.

**Nothing here is a new criticality level.** The resolved VALUE is still exactly `standard` or
`critical` (`KNOWN`). What is new is that the resolution also carries WHY it has that value -
`declared`, `absent`, `malformed`, `unreadable` - so a defaulted `critical` can be reported as the
default it is rather than passing for something a spec author wrote.

## Reporting is the half that has to hold even if the default is later changed

`skipped stage` reporting lives with the gate that owns the stage (`breaker_gate.skipped`), and the
resolver carries it on the state it returns (`pipeline_state.State.skipped_stages`). This module owns
only the resolution and the announcement that a default was applied.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import spec_gate_state  # noqa: E402

FIELD = "criticality"
CRITICAL = "critical"
STANDARD = "standard"
#: The closed set of criticality values. Issue #101 is explicit that the taxonomy does not grow.
KNOWN: tuple[str, ...] = (STANDARD, CRITICAL)

#: Why a resolution has the value it has. `declared` is the only one an author wrote.
DECLARED = "declared"
ABSENT = "absent"
MALFORMED = "malformed"
UNREADABLE = "unreadable"

#: What an absence resolves to. Read by the announcement, so a later change to the decision above
#: changes the message with it rather than leaving a stale sentence behind.
DEFAULT = CRITICAL

#: Every stage gated on criticality. One today; named as a set because the reporting below is about
#: "which criticality-gated stages did not run", not about the Breaker specifically.
GATED_STAGES: tuple[str, ...] = ("breaker",)


@dataclass(frozen=True)
class Resolution:
    """One spec's resolved criticality, and why it has that value."""

    value: str
    source: str
    raw: str | None = None

    @property
    def defaulted(self) -> bool:
        """Whether this value came from the default rather than from the spec."""
        return self.source != DECLARED

    def describe(self) -> str:
        if self.source == DECLARED:
            return f"declares `{FIELD}: {self.value}`"
        if self.source == ABSENT:
            return f"declares no `{FIELD}` - defaulted to `{DEFAULT}`"
        if self.source == UNREADABLE:
            return f"could not be read - `{FIELD}` defaulted to `{DEFAULT}`"
        return (
            f"declares `{FIELD}: {self.raw!r}`, which is not one of {KNOWN} - defaulted to "
            f"`{DEFAULT}`"
        )


def resolve(fields: dict[str, str]) -> Resolution:
    """Resolve one spec's criticality from its parsed frontmatter.

    An absent field and a value outside `KNOWN` both resolve to `DEFAULT`, carrying the source that
    says so. Comparison is case-insensitive on a stripped value: `Critical` is what an author meant,
    and reading it as malformed would default to the same answer while reporting a defect that is
    not there.
    """
    raw = fields.get(FIELD)
    if raw is None or not raw.strip():
        return Resolution(value=DEFAULT, source=ABSENT, raw=raw)
    normalised = raw.strip().lower()
    if normalised in KNOWN:
        return Resolution(value=normalised, source=DECLARED, raw=raw)
    return Resolution(value=DEFAULT, source=MALFORMED, raw=raw)


def resolve_spec_file(spec_file: Path) -> Resolution:
    """Resolve one spec.md on disk.

    A spec this cannot read resolves to `DEFAULT`, not to `standard`. The previous readers skipped an
    unreadable spec, which is the same silent weakening as an absent field one layer down: a phase
    whose specs cannot be parsed is the last phase that should quietly lose its adversarial stage.

    Unreadable has two shapes and both answer here: the file cannot be OPENED (`OSError` - a
    permission error is the one `phase()` can produce, since a missing file is never globbed), and
    its bytes cannot be DECODED (`UnicodeDecodeError`, which is a `ValueError` and escaped an
    `OSError`-only handler). Nothing wider is caught: an unreadable spec is a known state with a
    defined answer, and any other failure is a defect that must not be swallowed into one.
    """
    try:
        text = Path(spec_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Resolution(value=DEFAULT, source=UNREADABLE)
    return resolve(spec_gate_state.frontmatter(text))


@dataclass(frozen=True)
class PhaseCriticality:
    """A phase's criticality, and the per-spec resolutions it was derived from."""

    value: str
    resolutions: tuple[tuple[str, Resolution], ...]

    @property
    def critical(self) -> bool:
        return self.value == CRITICAL

    @property
    def defaulted(self) -> tuple[tuple[str, Resolution], ...]:
        """The specs whose value came from the default rather than from the spec."""
        return tuple((name, r) for name, r in self.resolutions if r.defaulted)

    def reason(self) -> str:
        """One line saying how this phase reached its value - what the reporting quotes."""
        if not self.resolutions:
            return "the phase has no specs, so no spec declares criticality"
        if self.critical:
            names = [n for n, r in self.resolutions if r.value == CRITICAL]
            return f"{len(names)} spec(s) resolve to `{CRITICAL}`: {', '.join(names)}"
        return (
            f"no spec in this phase resolves to `{CRITICAL}` "
            f"({len(self.resolutions)} spec(s) declare `{STANDARD}`)"
        )


def phase(phase_dir: Path) -> PhaseCriticality:
    """A phase's criticality: `critical` when ANY spec in it resolves to critical.

    The same OR the Breaker has always keyed on; what changed underneath it is what an absent field
    contributes to that OR.
    """
    resolutions = tuple(
        (spec_file.parent.name, resolve_spec_file(spec_file))
        for spec_file in sorted(Path(phase_dir).glob("specs/*/spec.md"))
    )
    value = CRITICAL if any(r.value == CRITICAL for _, r in resolutions) else STANDARD
    return PhaseCriticality(value=value, resolutions=resolutions)


#: Phases already announced, so a resolver that asks twice in one process does not say it twice.
_ANNOUNCED: set[str] = set()


def announce(phase_dir: Path, resolved: PhaseCriticality) -> None:
    """Say on stderr which specs got their criticality from the default rather than from an author.

    A default that fires silently is the defect this module exists to remove, one step milder: the
    phase would then run the stronger pipeline for a reason nobody could see. stdout is the JSON the
    orchestrator parses, so this is stderr.
    """
    defaulted = resolved.defaulted
    if not defaulted:
        return
    key = str(Path(phase_dir).resolve())
    if key in _ANNOUNCED:
        return
    _ANNOUNCED.add(key)
    print(
        f"[criticality] {Path(phase_dir).name}: {len(defaulted)} spec(s) did not declare a usable "
        f"`{FIELD}` and were defaulted to `{DEFAULT}` (issue #101 - an absent safety-relevant field "
        f"must not resolve to the weaker pipeline):",
        file=sys.stderr,
    )
    for name, resolution in defaulted:
        print(f"  - {name} {resolution.describe()}", file=sys.stderr)
