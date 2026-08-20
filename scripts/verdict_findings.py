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

Stdlib only, no imports of its own - it is a predicate over data the caller already loaded.
"""

from __future__ import annotations


def open_findings(findings: list[dict]) -> list[dict]:
    """Findings that are still unresolved: `status: open` and not waived by break-glass.

    A missing `status` reads as `open`, deliberately: a finding that never says it was fixed has not
    been, and defaulting the other way would resolve a finding by omitting a field.
    """
    return [
        f for f in findings
        if isinstance(f, dict)
        and str(f.get("status") or "open").lower() == "open"
        and not f.get("break_glass")
    ]
