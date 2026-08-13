#!/usr/bin/env python3
"""Which skills each stage REQUIRES - and the evidence that it actually got them.

The pipeline delegates its core behaviour to thirteen skills, and until now it delegated by *asking*:
every implementer prompt says "Load `skills/tdd` before you start". That is an instruction, not a
mechanism. Nothing checked, nothing recorded, and a stage that skipped a required skill fell back
silently to whatever the model already believed - which is the failure mode the whole pipeline exists
to remove from everything else. `skills/ponytail` is the only one genuinely injected, and
`docs/lessons/` shipped with a complete written procedure and **zero invocations** for the same
reason: a directive in a skill reaches only the agents that load that skill.

Three decisions make this a mechanism rather than a second instruction, and each replaced a shape
this file used to have:

- **The requirement is DERIVED, never restated.** `scripts/skill_contract.py` reads `skills/<name>`
  out of the stage's own `agents/<stage>.md`. A hand-maintained table here was a second statement of
  a fact the agent definitions already carry, and a second statement of a fact is precisely the
  promise-versus-enforcement gap this work exists to close. Adding `skills/<name>` to an agent is
  now enough to make it required; there is no list to remember.
- **The load is OBSERVED, never self-reported.** The evidence is the per-phase metrics record's
  `skill_loads[]`, written by `scripts/hook_skill_load.sh` on a real `Read`/`Skill` of
  `skills/<name>/SKILL.md`, and by `scripts/hook_skills.sh` at the moment it injects one. A path
  that needed the agent to run a `record` command to prove a load *is* instruction-not-mechanism -
  a guard that reports success while proving nothing - so that command is gone.
- **Detection without a gate is a log.** `audit` BLOCKS. That is the one thing observation alone
  never gave, and it is the whole reason this module still exists.

## Delivery: POINTER PLUS EVIDENCED LOAD, not blanket injection

Injecting every required skill's whole body is one way to **guarantee** the load, and it was the
first shape of this. It is also expensive in exactly the way the read path taught: every stage
requires `pipeline-conventions`, the largest file in `skills/`, and inlining it on every `avenger-*`
spawn is the same order of cost the read-path work had just removed from `handover.md`.

The `skill_loads` record is a cheaper way to **detect** a failure to load, and a required skill with
no observed load is a loud blocker that stops the phase anyway. **Detection at roughly a million
tokens saved per feature beats prevention at roughly a million spent, when both end the same way.**
So delivery is decided by size:

- **At or under `SKILL_INJECT_MAX_BYTES` (default 8192): injected in full**, and recorded as an
  OBSERVED load at the moment of injection, with the evidence naming injection. An injected skill is
  never read, so without that record it would seed as required-and-unobserved and never flip - the
  audit would report a false gap on precisely the skills whose load is *guaranteed*.
- **Over it: a POINTER, and a pointer is not a suggestion.** The stage is handed the skill's path,
  size and one-line description and told the load is REQUIRED. It does not have to report anything:
  reading the file IS the record. A pointer with no observed load is a **blocking** audit failure at
  handover and in CI.

The saving is a **PREDICTION, not an achievement**: declared as **H9** in the metrics ledger before
this landed - roughly 1M tokens saved per 8-phase feature with zero unrecorded required loads -
and settled in phase 9, not here.

**Scope needs no session id.** The evidence is per-phase by construction: `hook_skill_load.sh`
records nothing when no phase is in flight, so a pointer delivered in phase 1 cannot block phase 8
without any run-scoping machinery at all. `--all` sweeps every phase under `--full`.

**A required skill that is missing or unreadable is a LOUD BLOCKER**, not a silent fallback. The
hook says so in the injected context and records `loaded: false`. The one thing it must never do is
let the stage proceed believing it has rules it does not have.

Usage:
    required_skills.py for <agent-type>          print the skill dirs that stage requires
    required_skills.py table                     print every stage's contract, with each delivery
    required_skills.py verify [--root .]         every required skill exists and is readable
                                                 (exit 1 when one does not)
    required_skills.py audit                     exit 1 when a stage in the phase in flight required
                                                 a skill and no load was observed
    required_skills.py audit --stage <stage>     the same, for ONE stage — asked at the moment that
                                                 stage finishes (scripts/hook_skill_audit.sh)
    required_skills.py audit --all               the same over every phase with a record

## WHEN the audit is asked, and why it is asked twice

It used to be asked in two places, both of them at the end: the handover hook and CI. Measured on
clickup-agents phase 10, `avenger-spec-writer` had never loaded `skills/spec-review-checklist`, and
the phase learned it at the contract card — with every spec written, gated, reviewed and
implemented. That is the most expensive moment available, and it is also the moment the remedy stops
existing: "open the file in that stage" cannot be done by a stage that finished days ago, so a
genuine requirement arrived as a blocker on an artifact that did not cause it.

The requirement itself is not the defect. `agents/avenger-spec-writer.md` makes that checklist the
standard its Phase/Spec summaries are held to, so the load is genuinely owed; and phase 9 measured
13 of 13 required loads evidenced by observation rather than self-report, which is the property this
audit protects. What was wrong was the placement: the omission should stop the STAGE that owes it,
while it can still answer, not the card that closes over it.

So `--stage` asks the same question at `SubagentStop`, and the close-time audit stays exactly as it
was. It is a backstop, not a duplicate: opencode has no such event and the main thread reaches no
`SubagentStop` either, so a phase driven those ways is still covered. Nothing about what counts as a
gap changes in either place.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_sink as sink  # noqa: E402
import pipeline_metrics as metrics  # noqa: E402
import skill_contract  # noqa: E402

OK = 0
MISSING = 1
ERROR = 2

#: At or under this, a required skill is injected whole; over it, it is a pointer the stage must read
#: and be observed reading. 8192 bytes is the line at which injecting costs less than the risk of a
#: missed load.
DEFAULT_INJECT_MAX_BYTES = 8192

INJECT = "inject"
POINTER = "pointer"

#: Skills another `SubagentStart` hook already delivers, mapped to the hook that owns them. Delivered
#: twice they cost twice, and — worse — that hook's off switch would stop meaning what it says:
#: `PONYTAIL_OFF=1` turns off the minimalism persona, and this hook injecting the same body anyway
#: would put it back. Ownership, not exemption: the owning hook records its own load, so the audit
#: below still sees one. No agent declares `skills/ponytail` on its `Required skills` line today —
#: both implementers name it only in prose, to say the hook injects it — so this maps an ownership
#: that would matter the moment one did, rather than an exemption anything currently relies on.
DELIVERED_ELSEWHERE = {"ponytail": "scripts/hook_ponytail.sh"}

#: The stages `scripts/hook_ponytail.sh` injects the minimalism ladder into. **That hook owns this
#: default**; it is restated here only so the NOTE below can tell "expected and absent" from "never
#: expected", and the note is advisory, so a drift between the two costs a wrong note and never a
#: wrong verdict. The delivery path deliberately does not import this: an injection that fails
#: because a Python module could not be imported would be a far worse failure than a stale default.
PONYTAIL_AGENTS_DEFAULT = "avenger-backend-architect|avenger-frontend-developer"


def ponytail_expected(stage: str) -> bool:
    """Whether `hook_ponytail.sh` would have injected the ladder into this stage.

    `PONYTAIL_OFF=1` is a deliberate, supported choice, so nothing is expected under it — a note that
    fired every time the documented off switch was used is noise that teaches people to ignore notes.
    """
    if os.environ.get("PONYTAIL_OFF", "").strip() == "1":
        return False
    try:
        pattern = re.compile(os.environ.get("PONYTAIL_AGENTS") or PONYTAIL_AGENTS_DEFAULT, re.I)
    except re.error:
        return False
    return bool(stage) and bool(pattern.search(stage))


def ponytail_notes(record: dict | None, stage: str | None = None) -> list[str]:
    """One NOTE per stage that was expected to receive ponytail and has no record of it.

    **A note, never a gap.** `skills/ponytail` is deliberately outside every declared contract:
    requiring it would make the required set depend on an environment variable, and a documented off
    switch that can fail an audit is not an off switch — that exact wedge was removed from this file
    and is not coming back in a milder form.

    But an absence nobody can see is how a pipeline discovers three phases late that every
    implementation ran without the minimalism ladder. The injection already records itself
    (`hook_ponytail.sh`), so the absence is observable; this makes it *visible*. It never touches the
    exit code and nothing branches on it.
    """
    loads = (record or {}).get("skill_loads") or []
    seen = {
        entry.get("stage")
        for entry in loads
        if isinstance(entry, dict) and entry.get("skill") == "ponytail" and entry.get("loaded")
    }
    stages_in_record = {
        entry.get("stage") for entry in loads if isinstance(entry, dict) and entry.get("stage")
    }
    if stage:
        # A stage-scoped audit answers about one stage; another stage's note there is noise, and a
        # note nobody asked for is how notes stop being read.
        stages_in_record &= {metrics.observing_stage(stage)}
    return [
        f"{stage} was expected to be injected with skills/ponytail and no injection was recorded. "
        f"This is a NOTE, not a gap: ponytail is never required and never blocks. It means this "
        f"stage may have implemented without the minimalism ladder — check hook_ponytail.sh reached "
        f"it, or set PONYTAIL_OFF=1 if that is deliberate."
        for stage in sorted(stages_in_record - seen)
        if ponytail_expected(str(stage))
    ]


def required_for(agent_type: str, root: Path | None = None) -> tuple[str, ...]:
    """The skills that stage must have, read out of its own definition. Empty for a foreign agent.

    Deliberately a thin pass-through to `skill_contract`: one statement of the requirement, in the
    document that already carries it.
    """
    return tuple(sorted(skill_contract.required_skills(agent_type, root)))


def to_deliver(agent_type: str, root: Path | None = None) -> tuple[str, ...]:
    """What THIS hook delivers: the contract, minus what another hook already owns."""
    return tuple(s for s in required_for(agent_type, root) if s not in DELIVERED_ELSEWHERE)


def skill_path(root: Path, skill: str) -> Path:
    return Path(root) / "skills" / skill / "SKILL.md"


def inject_max_bytes() -> int:
    """The injection ceiling in force. A value that does not parse is a hard error, never a guess.

    Same rule as the requirement cap and `GATE_CALL_TIMEOUT`: a budget believed rather than checked
    is the defect. Guessing the default here would silently change every stage's delivery mode.
    """
    raw = os.environ.get("SKILL_INJECT_MAX_BYTES", "").strip()
    if not raw:
        return DEFAULT_INJECT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"SKILL_INJECT_MAX_BYTES={raw!r} is not a whole number of bytes. It decides whether a "
            f"required skill is injected or pointed at, so it is not guessed."
        ) from None
    if value < 0:
        raise ValueError(f"SKILL_INJECT_MAX_BYTES={raw!r} cannot be negative.")
    return value


def delivery_for(size: int, limit: int | None = None) -> str:
    """`inject` for a body at or under the ceiling, `pointer` above it."""
    return INJECT if size <= (inject_max_bytes() if limit is None else limit) else POINTER


def stages(root: Path | None = None) -> list[str]:
    """Every stage this pipeline owns, which is every canonical agent definition."""
    base = (root or skill_contract.repo_root()) / "agents"
    if not base.is_dir():
        return []
    return sorted(path.stem for path in base.glob("*.md"))


def phases_with_records(root: Path | None = None) -> list[str]:
    """Every phase number the artifact tree knows about, for the `--all` sweep.

    Derived from `docs/features/*/phases/*` rather than from firstmate's store, because the store is
    firstmate's and this repo reads it one phase at a time by design.
    """
    base = Path(root or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    found = set()
    for phase_dir in base.glob("docs/features/*/phases/*"):
        if not phase_dir.is_dir():
            continue
        phase = metrics.resolve_phase(str(phase_dir))
        if phase:
            found.add(phase)
    return sorted(found)


def audit_gaps(record: dict | None, stage: str | None = None) -> list[str]:
    """One line per required skill in `record` with no observed load, optionally for ONE stage.

    `stage` narrows the scope and nothing else — same rows, same test, same wording. It exists
    because the question "did this stage get what it was owed?" is answerable at the moment that
    stage runs, and answering it there is the difference between opening one file and re-opening a
    closed phase. The stage name is normalised through `observing_stage`, the same function that
    wrote the rows: `SubagentStop` reports `plan-build-verify:avenger-spec-writer` where the record
    holds `avenger-spec-writer`, and a scoped audit that matched neither would answer about nobody
    while reporting itself clean.

    Two things are a gap, and they are the same gap wearing different clothes:

      * a stage's contract seeded `required: true` that no `Read`/`Skill` and no injection ever
        answered - the stage was owed a skill and there is no evidence it got it;
      * a delivery recorded `loaded: false` because the file was missing or unreadable - the stage
        ran with no rules rather than lighter ones.

    Both read the same way out of `skill_loads[]`, because `record_skill_load` keys every entry
    `<stage>:<skill>` and upserts: the requirement and its answer are ONE row, and a seed never
    overwrites an observed load whichever order the two hooks run in. There is no cross-stage
    looseness to guard against here - the Verifier loading `pipeline-conventions` writes
    `avenger-verifier:pipeline-conventions` and says nothing about the implementer's row.
    """
    wanted = metrics.observing_stage(stage) if stage else None
    gaps: list[str] = []
    for entry in (record or {}).get("skill_loads") or []:
        if not entry.get("required") or entry.get("loaded"):
            continue
        if wanted is not None and entry.get("stage") != wanted:
            continue
        entry_stage = entry.get("stage") or "?"
        skill = entry.get("skill") or "?"
        gaps.append(
            f"{entry_stage} required skills/{skill} and no load was ever observed. A required "
            f"skill with no evidence of a load is a stage running on whatever the model already "
            f"believed — "
            f"read skills/{skill}/SKILL.md in that stage, or re-run it: a stage that never loaded "
            f"its rules did not run under them."
        )
    return gaps


def undeclared(root: Path) -> list[str]:
    """Canonical agents that declare no `Required skills` line at all.

    The contract is read from that one line and nowhere else, so a stage missing it has an EMPTY
    contract — which is indistinguishable, at the audit, from a stage that legitimately needs
    nothing. That silence is exactly the shape the derivation replaced, so it is said out loud here
    rather than guessed at by scanning the agent's prose.
    """
    return [stage for stage in stages(root) if skill_contract.contract_line(stage, root) is None]


def missing(root: Path) -> list[tuple[str, str]]:
    """(stage, skill) for every required skill that is absent or unreadable."""
    out: list[tuple[str, str]] = []
    for stage in stages(root):
        for skill in required_for(stage, root):
            path = skill_path(root, skill)
            try:
                if not path.read_text(encoding="utf-8").strip():
                    out.append((stage, skill))
            except OSError:
                out.append((stage, skill))
    return out


def _audit(all_phases: bool, stage: str | None = None) -> int:
    """Fail closed on a required skill with no observed load, over the scope asked for."""
    if os.environ.get("SKILLS_OFF", "").strip() == "1":
        # Delivery is off, so nothing was ever handed to a stage to load. Auditing the residue of
        # earlier runs would block a phase for a mechanism the operator switched off.
        print("  (skill delivery is off, SKILLS_OFF=1 — nothing to audit)", file=sys.stderr)
        return OK
    if not sink.enabled():
        # No writer means no observations were ever taken, so there is nothing to have missed. Said
        # out loud rather than passing invisibly — the same rule as an absent subprocess-check root.
        print(
            "  (no metrics writer configured — skill loads were never observed, nothing to audit)",
            file=sys.stderr,
        )
        return OK

    if all_phases:
        scope = phases_with_records()
        mode = f"--all: every phase with a record ({len(scope) or 'none'})"
    else:
        current = metrics.current_phase()
        scope = [current] if current else []
        mode = f"the phase in flight ({current})" if current else "no phase in flight"
    if stage:
        mode = f"{mode}, {stage} only"
    if not scope:
        # Per-phase by construction: with no phase there is no record and no scope. Nothing is
        # enforced and the check says so, rather than falling back to enforcing everything.
        print(f"  (skill audit: {mode} — nothing to audit)", file=sys.stderr)
        return OK

    gaps: list[str] = []
    notes: list[str] = []
    for phase in scope:
        record = sink.show(phase)
        gaps += [f"phase {phase}: {gap}" for gap in audit_gaps(record, stage)]
        notes += [f"phase {phase}: {note}" for note in ponytail_notes(record, stage)]

    # Printed before the verdict and outside it. Notes never reach the exit code, in any mode.
    for note in notes:
        print(f"  ⓘ note: {note}", file=sys.stderr)

    if not gaps:
        print(
            f"  required skills: every required skill has an observed load — {mode}",
            file=sys.stderr,
        )
        return OK
    print(
        f"required_skills: a required skill was never observed being loaded — {mode}:",
        file=sys.stderr,
    )
    for gap in gaps:
        print(f"  ✗ {gap}", file=sys.stderr)
    return MISSING


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_for = sub.add_parser("for")
    p_for.add_argument("agent_type")
    p_table = sub.add_parser("table")
    p_table.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--all", action="store_true", help="every phase with a record")
    p_audit.add_argument(
        "--stage",
        help="only this stage's contract, for the audit at the moment that stage finishes",
    )
    args = parser.parse_args(argv)

    if args.action == "for":
        print("\n".join(required_for(args.agent_type)))
        return OK
    if args.action == "table":
        try:
            limit = inject_max_bytes()
        except ValueError as exc:
            print(f"[required_skills] {exc}", file=sys.stderr)
            return ERROR
        for stage in stages(args.root):
            skills = required_for(stage, args.root)
            if not skills:
                continue
            annotated = []
            for skill in skills:
                path = skill_path(args.root, skill)
                size = path.stat().st_size if path.is_file() else 0
                annotated.append(f"{skill}:{delivery_for(size, limit)}")
            print(f"{stage}\t{','.join(annotated)}")
        return OK
    if args.action == "audit":
        return _audit(args.all, args.stage)

    silent = undeclared(args.root)
    gaps = missing(args.root)
    if not silent and not gaps:
        return OK
    if silent:
        print(
            "required_skills: a canonical agent declares no `Required skills` line, so its contract "
            "reads as empty. An omission there shrinks a stage's contract silently:",
            file=sys.stderr,
        )
        for stage in silent:
            print(f"  ✗ agents/{stage}.md has no `Required skills` line", file=sys.stderr)
    if gaps:
        print(
            "required_skills: a stage requires a skill that is missing or empty. A required skill "
            "that is not there is a stage running on whatever the model already believed:",
            file=sys.stderr,
        )
        for stage, skill in gaps:
            print(f"  ✗ {stage} requires skills/{skill}/SKILL.md", file=sys.stderr)
    return MISSING


if __name__ == "__main__":
    raise SystemExit(main())
