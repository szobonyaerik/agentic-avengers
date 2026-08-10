#!/usr/bin/env python3
"""Scope the Verifier's review bundle to the specs that actually changed.

`verifier_review.sh` globbed `<phase-dir>/specs/*/spec.md` and every `test-mapping.md`
unconditionally, on every attempt. A phase's second attempt re-sent every requirement of every spec
in it — one measured bundle reached ~832k tokens, and one phase had to be split into four chunks to
fit a context at all. PR #27's diff-only rule covers spec RE-GATES; it never reached this bundle.

So a spec whose `spec.md` and `test-mapping.md` are byte-identical to what the last completed review
saw AND whose findings are all resolved or waived is CARRIED: its verdict and its findings come
forward from that review, it is named in the bundle as carried, and its text is not re-sent.
Everything else is reviewed — including a spec that still holds an OPEN finding, however unchanged
its text is, because a finding fixed in a TEST file changes neither of those two documents and a
spec that is never re-bundled is a finding that is never regenerated.

Three properties this keeps, because each one is a way the scoping could quietly weaken the gate:

  * **Carried is not forgotten.** A carried spec's findings are merged back into this run's verdict,
    and an open one there forces NO-GO. Dropping a finding by not re-sending its spec would be a
    fail-open dressed as an optimisation.
  * **No state, no scoping.** A first run, a lost state file, or `VERIFIER_SCOPE=full` sends
    everything — the same bundle as before. The safe direction costs tokens, not correctness.
  * **What was carried is stated**, in the bundle and on stderr and in the verdict artifact. A
    review whose scope is invisible cannot be audited.

The state file is a rebuildable cache (`.verifier-bundle-state.json`, gitignored): losing it costs
one full bundle.

    verifier_bundle_scope.py plan     <phase-dir>                 what to review, what to carry
    verifier_bundle_scope.py finalize <phase-dir> <verdict.json>  merge carried in, record state
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

STATE_NAME = ".verifier-bundle-state.json"
STATE_VERSION = 1

#: The files whose content decides whether a spec needs re-reviewing. Both, because the mapping is
#: how a requirement reaches a test — a mapping edit changes the review even when the spec did not.
SPEC_FILES = ("spec.md", "test-mapping.md")

#: `8.1-persist-verdict` -> `8.1`; findings say `R8.1.2`, which resolves to the same spec.
SPEC_DIR_ID = re.compile(r"^(\d+\.\d+)")
FINDING_SPEC_ID = re.compile(r"^R?(\d+\.\d+)")

PASS_VERDICTS = {"GO", "PASS"}


def state_path(phase_dir: Path) -> Path:
    return phase_dir / STATE_NAME


def spec_dirs(phase_dir: Path) -> list[Path]:
    """Every spec directory of the phase, in id order."""
    specs = phase_dir / "specs"
    if not specs.is_dir():
        return []
    return sorted((d for d in specs.iterdir() if d.is_dir() and (d / "spec.md").is_file()),
                  key=lambda d: d.name)


def spec_fingerprint(spec_dir: Path) -> str:
    """Digest over the spec's reviewable text. A missing mapping is part of the fingerprint."""
    digest = hashlib.sha256()
    for name in SPEC_FILES:
        path = spec_dir / name
        digest.update(name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


def spec_id_of(name: str) -> str | None:
    match = SPEC_DIR_ID.match(name)
    return match.group(1) if match else None


def finding_spec_id(finding: dict) -> str | None:
    match = FINDING_SPEC_ID.match(str(finding.get("spec_id") or "").strip())
    return match.group(1) if match else None


def load_state(phase_dir: Path) -> dict:
    """The previous review's record, or an empty one. Any problem reads as 'no state' — full bundle."""
    try:
        data = json.loads(state_path(phase_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "specs": {}}
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "specs": {}}
    specs = data.get("specs")
    return {"version": STATE_VERSION, "specs": specs if isinstance(specs, dict) else {}}


def scoping_disabled() -> bool:
    """`VERIFIER_SCOPE=full` sends the whole phase, for when the scoping itself is under suspicion."""
    return os.environ.get("VERIFIER_SCOPE", "").strip().lower() == "full"


def open_findings(findings: list[dict]) -> list[dict]:
    """Findings that are still unresolved — these are what keep a spec in the review set."""
    return [
        f for f in findings
        if isinstance(f, dict)
        and str(f.get("status") or "open").lower() == "open"
        and not f.get("break_glass")
    ]


def plan(phase_dir: Path) -> dict:
    """Which spec dirs to review and which to carry, with the carried verdicts."""
    state = load_state(phase_dir)
    review: list[str] = []
    carry: list[dict] = []
    for spec_dir in spec_dirs(phase_dir):
        # Keyed by directory name: stable across checkouts, unique within a phase.
        key = spec_dir.name
        record = state["specs"].get(key)
        stored = (record.get("findings") or []) if isinstance(record, dict) else []
        unchanged = (
            isinstance(record, dict)
            and record.get("fingerprint") == spec_fingerprint(spec_dir)
            and record.get("verdict")
        )
        # A spec with an OPEN finding is never carried, however unchanged its text is. Carrying one
        # wedged the phase in permanent NO-GO: a `gamed test` finding is fixed in a TEST file, so
        # `spec.md` and `test-mapping.md` never change, so the spec is never re-bundled, so the
        # finding is never regenerated, so nothing ever marks it fixed — and the merge below keeps
        # forcing NO-GO on every later attempt, with no way out but deleting the state file. Sending
        # it back to the cross-family reader is what lets the finding either clear or reappear on its
        # own. It keeps the bar, removes the wedge, and gives up the token saving only on the specs
        # actually under repair, which is exactly where economising is wrong.
        if unchanged and not open_findings(stored) and not scoping_disabled():
            carry.append({
                "spec": key,
                "path": str(spec_dir),
                "verdict": record["verdict"],
                "findings": record.get("findings") or [],
            })
        else:
            review.append(str(spec_dir))
    if carry and not review:
        # Nothing changed, so there is nothing to scope TO. Sending the review set with no
        # requirements attached is not a cheaper review, it is a review the model cannot do: it
        # judges coverage against the requirements, and a bundle carrying none has nothing to judge
        # against. Fall back to the whole phase — the safe direction costs tokens, not coverage.
        return {"review": [str(d) for d in spec_dirs(phase_dir)], "carry": [], "full": True}
    return {"review": review, "carry": carry, "full": scoping_disabled() or not carry}


def finalize(phase_dir: Path, verdict_path: Path) -> int:
    """Merge carried findings into this run's verdict and record the new state.

    Returns 0 when the merged verdict is a pass, 2 when a carried finding still holds it back.
    """
    current = plan(phase_dir)
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[verifier_bundle_scope] cannot read {verdict_path}: {exc} — fail closed",
              file=sys.stderr)
        return 2

    findings = [f for f in (verdict.get("findings") or []) if isinstance(f, dict)]
    seen = {f.get("id") for f in findings if f.get("id")}

    carried_open = 0
    for entry in current["carry"]:
        for finding in entry["findings"]:
            if not isinstance(finding, dict):
                continue
            if finding.get("id") in seen:
                continue
            carried = dict(finding)
            carried["carried_from_previous_review"] = True
            findings.append(carried)
            seen.add(carried.get("id"))
        carried_open += len(open_findings(entry["findings"]))

    verdict["findings"] = findings
    verdict["reviewed_specs"] = [Path(p).name for p in current["review"]]
    verdict["carried_specs"] = [
        {"spec": e["spec"], "verdict": e["verdict"], "open_findings": len(open_findings(e["findings"]))}
        for e in current["carry"]
    ]

    value = str(verdict.get("verdict") or "").strip().upper()
    if carried_open and value in PASS_VERDICTS:
        # A carried spec still holding an open finding is not a passed phase, whatever this run's
        # narrower review concluded. Scoping may shrink the bundle; it may not shrink the bar.
        # `plan()` no longer carries a spec that has one, so this is an invariant guard rather than
        # a live path — NOT dead code to delete. If it is ever reached again (a hand-edited or
        # half-written state file, a later change to the carry condition), forcing NO-GO is the safe
        # direction, and the only alternative is a silent fail-open.
        verdict["verdict"] = "NO-GO"
        verdict["report"] = (
            f"{verdict.get('report') or ''}\n\n[scope] {carried_open} finding(s) carried forward "
            "from specs unchanged since the previous review are still open; the phase cannot pass "
            "while they are."
        ).strip()
        value = "NO-GO"

    try:
        verdict_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[verifier_bundle_scope] cannot write {verdict_path}: {exc} — fail closed",
              file=sys.stderr)
        return 2

    # Record what this run establishes: reviewed specs get this run's verdict and their own
    # findings; carried specs keep the record they already had.
    by_spec: dict[str, list[dict]] = {}
    for finding in verdict["findings"]:
        sid = finding_spec_id(finding)
        if sid:
            by_spec.setdefault(sid, []).append(finding)

    specs = {}
    for entry in current["carry"]:
        specs[entry["spec"]] = {
            "fingerprint": spec_fingerprint(phase_dir / "specs" / entry["spec"]),
            "verdict": entry["verdict"],
            "findings": entry["findings"],
        }
    # The verdict recorded against a reviewed spec is that SPEC's, not the phase's. Stamping the
    # merged phase verdict on all of them stored a spec reviewed clean in a run that failed on a
    # sibling as NO-GO, and the next bundle then announced "previous verdict NO-GO, 0 finding(s)
    # carried" for a spec nothing was ever said against — misinforming the reviewer about the work
    # it was told not to look at. The findings do the blocking; this is the label.
    #
    # A failure is only localised when every open finding names a spec. A rejection carrying an open
    # finding with no resolvable `spec_id` — or none at all — belongs to the phase, so no spec in it
    # may be labelled clean: those runs keep the phase verdict against every reviewed spec rather
    # than localise a failure that was never localised.
    still_open = open_findings(verdict["findings"])
    localised = value in PASS_VERDICTS or (
        bool(still_open) and all(finding_spec_id(f) for f in still_open)
    )
    for path in current["review"]:
        spec_dir = Path(path)
        sid = spec_id_of(spec_dir.name)
        own = by_spec.get(sid or "", [])
        specs[spec_dir.name] = {
            "fingerprint": spec_fingerprint(spec_dir),
            "verdict": ("NO-GO" if open_findings(own) else "GO") if localised
            else (value or "NO-GO"),
            "findings": own,
        }
    try:
        state_path(phase_dir).write_text(
            json.dumps({"version": STATE_VERSION, "specs": specs}, indent=2), encoding="utf-8")
    except OSError as exc:
        # A state file that cannot be written only costs the next run a full bundle, so this is a
        # warning rather than a stop — the safe direction is the expensive one.
        print(f"[verifier_bundle_scope] could not record scope state: {exc}", file=sys.stderr)

    if current["carry"]:
        names = ", ".join(e["spec"] for e in current["carry"])
        print(f"verifier-review: carried forward (unchanged since last review): {names}",
              file=sys.stderr)
    return 0 if value in PASS_VERDICTS else 2


def as_tsv(result: dict) -> str:
    """The plan in the one shape a shell can read without an inline parser.

        REVIEW\t<spec-dir>
        CARRY\t<spec>\t<verdict>\t<finding count>

    The shell used to unpack the JSON with an inline `python3 -c`, and a quoting mistake in it made
    the carried list come back EMPTY without failing anything: the bundle simply lost its carried
    notice and looked fine. A gate that degrades quietly is the defect this whole change is about,
    so the formatting lives here, where it is tested.
    """
    lines = [f"REVIEW\t{path}" for path in result["review"]]
    lines += [f"CARRY\t{e['spec']}\t{e['verdict']}\t{len(e['findings'])}" for e in result["carry"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "plan" and args[2] == "--tsv":
        print(as_tsv(plan(Path(args[1]))))
        return 0
    if len(args) == 2 and args[0] == "plan":
        print(json.dumps(plan(Path(args[1])), indent=2))
        return 0
    if len(args) == 3 and args[0] == "finalize":
        return finalize(Path(args[1]), Path(args[2]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
