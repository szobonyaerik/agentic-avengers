#!/usr/bin/env python3
"""What "still open" means for a finding in `verdict.json` - one rule, one owner.

It lived in `verifier_bundle_scope.py`, which owned it because it is what kept a spec in the
removed cross-family reading pass's bundle. That pass is gone (it returned GO with zero findings on a phase
containing real defects, and the hypothesis testing whether it earned its cost returned unmeasured),
and the bundle went with it - but the rule did not. `verifier_attempts.py` reads it to decide whether
a phase at the attempt cap has actually ENDED its loop, and restating it there as "the findings array
is empty" once made the cap unclearable by the very remedy its own message prescribes: waive the
remainder, and the Verifier writes `pass` with `bypassed: true` and the waived findings still in the
array. A check its prescribed remedy cannot satisfy is a wedge, not a gate.

So the rule moves house rather than being copied. One owner, imported by everything that asks.

**A fourth disposition, `deferred`, resolves a finding only when a ledger backs it** (issue #115). A
finding that is real, does not block this phase's *Done when* and belongs to a later phase is
`status: deferred`, and it is resolved here only when its id is in the `deferred` set the caller
passes - the deferrals `scripts/carried_items.py` recorded for the phase, each one gated on the
*Done when* and carrying its measurement. A `deferred` stamp with nothing behind it is OPEN: the stamp
is written by the Verifier, and a status that resolved a finding by being written would be the
waiver-by-omission this predicate already refuses one line down. The default is the empty set, so a
caller that does not know about deferrals fails closed rather than open.

Stdlib only, no imports of its own - it is a predicate over data the caller already loaded.
"""

from __future__ import annotations

from collections.abc import Collection

#: The disposition a finding takes when it is real, does not block this phase's *Done when*, and a
#: later phase owns it. `carried_items.py` records the deferral; the Verifier stamps the finding.
DEFERRED = "deferred"


def open_findings(findings: list[dict], deferred: Collection[str] = ()) -> list[dict]:
    """Findings that are still unresolved: `status: open` and not waived by break-glass, plus any
    `status: deferred` finding whose id is not in `deferred` - a deferral nothing recorded.

    A missing `status` reads as `open`, deliberately: a finding that never says it was fixed has not
    been, and defaulting the other way would resolve a finding by omitting a field.
    """
    backed = set(deferred)
    out: list[dict] = []
    for f in findings:
        if not isinstance(f, dict) or f.get("break_glass"):
            continue
        status = str(f.get("status") or "open").lower()
        if status == "open":
            out.append(f)
        elif status == DEFERRED and str(f.get("id") or "") not in backed:
            out.append(f)
    return out
