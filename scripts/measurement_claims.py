#!/usr/bin/env python3
"""A claim carries its measurement - one owner of the rule, asked at three surfaces (issue #96).

## The class

Every stage in one measured feature made the same mistake in a different costume: **a partial
measurement written up as a complete result.** An implementer told to *sweep two adapters* swept by
READING which exception clauses had been narrowed, reported complete, and missed two instances - one
that could leave a live trading position with no stop order. An orchestrator probed a collaborator by
setting what it *returns*, never what it *raises*, and scoped a fix round from that half. An approved
spec justified narrowing a handler with "the method only calls X, Y and float()" - it did not, and the
claim passed the machine gate, both human reviews and the grill. In another feature (#117) three
consecutive amendment records claimed a scope wider than their author had measured - "nothing in the
catalogue can turn waypoints red" (six of ten do), "every other clip's report bit-for-bit identical"
(false of three) - each caught by a verifier, each costing a further round.

Every one produced work that LOOKED finished. Five of seven were caught by a human reading artifacts
against source, one by a gate, none by a test. The author's own diagnosis: *"I compare the thing I
changed against the fields I was thinking about, write the conclusion at document scope, and the
sentence outlives the measurement."*

## The rule, in its mechanical form

"Sweep both connectors for this class" is satisfied by reading, because reading is cheaper and its
output is indistinguishable from the real thing until someone runs the code. "Drive every method on
both axes and compare outcomes" cannot be satisfied by reading. So:

- **An acceptance criterion names what it DRIVES** (`drives:`) - the seam, command or planted input
  the test pushes through. Asked at spec-write time, before any paid gate call
  (`hook_spec_gate.sh`), because that is the moment the writer still owns the criterion.
- **A verifier finding names its METHOD** (`method`) - how the defect was established and how its
  fix is to be confirmed: what was driven, over which axes. Asked by `verifier_precheck.py`, the
  Verifier's mechanical bookkeeping, where every other absence on the verdict is already decided.
- **A claim that quantifies universally carries the set it was MEASURED OVER** (`measured_over`), as
  a field and not as prose. Asked at write time of an amendment (`amendments.py open`) and of a
  contract card's forward claims (`carried_items.py`), the two records #117's instances were written
  in. The verifier then tests the claim over exactly that set (`skills/verifier-triage`).

## What this does and does not decide

The vocabulary of universals is a CLOSED set, pinned by test. Every word in it appeared in a measured
false claim or is its plain synonym, and a detector that matched more would refuse ordinary prose and
be answered with a bypass. A false positive costs one field; a universal the set does not know passes
- that is stated here rather than implied, and widening the set is a deliberate edit to `UNIVERSALS`.

Presence is what is checked, never content. A `drives:` that says `undriven (visual)` passes this
check: whether a criterion may be undriven, and what such a declaration must carry, is issue #116's
decision and this module deliberately leaves that door open rather than deciding it by refusal. A
`method` of "read the source" passes too - the field makes the method VISIBLE to the reader of the
verdict, which is what turns "sweep complete" into something a reviewer can disagree with. Nothing
here compares a claim's quantifier against its set; that comparison needs the set's members and the
world, and it belongs to the Verifier.

Usage:
    measurement_claims.py spec <spec.md>        every acceptance criterion carries `drives:`
                                                exit 0 clean (or counted: the spec has shipped),
                                                1 findings, 2 error
    measurement_claims.py verdict <phase-dir>   every finding in verdict.json carries `method`
                                                exit 0 clean (no verdict is nothing to check),
                                                1 findings, 2 error
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402
from applicability import report_unenforced  # noqa: E402
from md_section import slice_section  # noqa: E402
from spec_gate_state import APPROVED, frontmatter, status  # noqa: E402

CLEAN = 0
FINDINGS = 1
ERROR = 2

#: The field names, so the three surfaces and the documents that instruct writers spell them alike.
DRIVES = "drives"
METHOD = "method"
MEASURED_OVER = "measured_over"

#: The closed vocabulary of universal quantification. Multi-word entries first so `no other` is one
#: match, not `no` and `other`. `tests/test_measurement_claims.py` pins the set.
UNIVERSALS: tuple[str, ...] = (
    "no other",
    "all",
    "every",
    "everything",
    "nothing",
    "none",
    "never",
    "always",
    "only",
    "entire",
    "identical",
    "unchanged",
    "byte-for-byte",
    "bit-for-bit",
    "subsumed",
)

_UNIVERSAL = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(word) for word in UNIVERSALS) + r")(?![\w-])",
    re.IGNORECASE,
)
_INLINE_CODE = re.compile(r"`[^`\n]*`")

#: A criterion item under `## Acceptance criteria`: a list item (or table row) opening with a
#: requirement id `R<n>.<k>.<m>` or a journey id `J<n>`. Continuation lines belong to the item above
#: until the next item, so the field may sit on its own indented line.
_CRITERION = re.compile(
    r"^[ \t]*(?:\|[ \t]*)?(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:\*\*)?(?P<id>R\d+\.\d+\.\d+|J\d+)\b"
)
_DRIVES_FIELD = re.compile(rf"\b{DRIVES}:", re.IGNORECASE)
#: The prose spelling of the set on surfaces that are text rather than JSON (a contract card row).
MEASURED_OVER_FIELD = re.compile(rf"\b{MEASURED_OVER}:", re.IGNORECASE)


class ClaimError(Exception):
    """An artifact this module could not read. Always exit 2, never a clean result."""


def universals(text: str) -> list[str]:
    """The universal quantifiers `text` uses, lowercased, each once, in order of first use.

    Inline code is stripped first: `None`, `all(rows)` and `only_once` are identifiers, not claims
    about the world. Matching is on word boundaries that treat `-` as part of a word, so `call`,
    `small`, `nonetheless` and `everyday` never match and `bit-for-bit` matches whole.
    """
    prose = _INLINE_CODE.sub(" ", text or "")
    seen: list[str] = []
    for match in _UNIVERSAL.finditer(prose):
        word = match.group(1).lower()
        if word not in seen:
            seen.append(word)
    return seen


def _has_set(measured_over) -> bool:
    if isinstance(measured_over, str):
        return bool(measured_over.strip())
    if isinstance(measured_over, (list, tuple)):
        return any(isinstance(item, str) and item.strip() for item in measured_over)
    return False


def claim_problem(text: str, measured_over) -> str | None:
    """Why a claim is refused, or None. A universal quantifier with no measured set is refused.

    The set may be a string or a list of strings; blank is no set. A claim with no universal in it
    owes nothing here - the rule is about the quantifier, not about the sentence.
    """
    words = universals(text)
    if not words or _has_set(measured_over):
        return None
    quoted = ", ".join(f"'{w}'" for w in words)
    return (
        f"the claim quantifies universally ({quoted}) and names no `{MEASURED_OVER}` set. A scope "
        f"claim carries the set it was measured over AS A FIELD, not as prose: 'nothing in the "
        f"catalogue' was written against a four-field diff and cost two amendment rounds to falsify. "
        f"Name the set - the files, ids, clips or cases actually checked - or narrow the claim to "
        f"what was measured."
    )


def _criteria_items(section: str) -> list[tuple[str, str]]:
    """(id, the item's full text including continuation lines) for each criterion in the section."""
    items: list[tuple[str, list[str]]] = []
    for line in section.splitlines():
        match = _CRITERION.match(line)
        if match:
            items.append((match.group("id"), [line]))
        elif items and line.strip() and not line.lstrip().startswith("#"):
            items[-1][1].append(line)
    return [(identifier, "\n".join(lines)) for identifier, lines in items]


def criteria_problems(spec_text: str) -> list[str]:
    """Every acceptance criterion that does not name what it drives, as finding lines.

    A spec with no `## Acceptance criteria` section is not this check's finding - `verifier_precheck`
    already refuses a spec whose heading is gone - and prose under the heading that is not an item is
    read past, so a sentence introducing the table costs nothing.
    """
    found = slice_section(
        spec_text, "Acceptance criteria", min_level=2, allow_trailing=True
    )
    if found is None:
        return []
    out: list[str] = []
    for identifier, text in _criteria_items(found[1]):
        if not _DRIVES_FIELD.search(text):
            out.append(
                f"criterion {identifier} names nothing it drives - no `{DRIVES}:` field. "
                f"'Sweep both adapters for this class' is satisfied by reading; name the seam, "
                f"command or planted input the test pushes through (or declare it `{DRIVES}: "
                f"undriven (<why>)` where nothing can be driven)."
            )
    return out


def finding_problems(verdict: dict) -> list[str]:
    """Every finding in a verdict that carries no `method`, as finding lines.

    Held on every finding, open or fixed: the rule is about the record, and a fixed finding still
    tells its reader how the defect was established and how the fix was confirmed.
    """
    out: list[str] = []
    findings = verdict.get("findings") if isinstance(verdict, dict) else None
    for entry in findings or []:
        if not isinstance(entry, dict):
            continue
        method = entry.get(METHOD)
        if not (isinstance(method, str) and method.strip()):
            out.append(
                f"finding {entry.get('id') or '<no id>'} states no `{METHOD}` - how it was "
                f"established and how its fix is to be confirmed: what was DRIVEN, over which axes. "
                f"A probe that set what a collaborator returns and never what it raises scoped a fix "
                f"round that closed half the defect; the unstated axis is the unmeasured one."
            )
    return out


def _spec_past_the_gate(fields: dict[str, str]) -> str | None:
    """Why this spec is outside what the criterion check may bind, or None when it binds.

    The applicability boundary (CLAUDE.md 3a): a spec the machine gate has APPROVED, or that an
    implementer has taken up (`status: in-progress` or `done`), has shipped its criteria. Rewriting
    them is a remedy unavailable to a stage that has ended, and a rule whose remedy is unavailable is
    a wedge. A `blocked` or `pending` spec is still going through the gate and is held.
    """
    if status(fields) == APPROVED:
        return "the gate already approved this spec"
    if fields.get("status", "").strip().lower() in ("in-progress", "done"):
        return (
            f"the spec is `status: {fields['status'].strip()}` - an implementer has it"
        )
    return None


def check_spec(spec: Path) -> int:
    try:
        text = spec.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaimError(f"cannot read {spec}: {exc}") from exc
    problems = criteria_problems(text)
    if not problems:
        return CLEAN
    why = _spec_past_the_gate(frontmatter(text))
    if why is not None:
        report_unenforced(
            "measurement_claims",
            len(problems),
            f"{why}; its criteria are counted, not held: " + " | ".join(problems),
        )
        return CLEAN
    print(
        f"measurement_claims: {spec} - {len(problems)} acceptance criterion/criteria name nothing "
        f"they drive:",
        file=sys.stderr,
    )
    for line in problems:
        print(f"  ✗ {line}", file=sys.stderr)
    return FINDINGS


def check_verdict(phase_dir: Path) -> int:
    target = Path(phase_dir) / "verdict.json"
    if not target.is_file():
        return CLEAN
    try:
        verdict = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClaimError(f"cannot read {target}: {exc}") from exc
    problems = finding_problems(verdict)
    if not problems:
        return CLEAN
    print(
        f"measurement_claims: {target} - {len(problems)} finding(s) state no method:",
        file=sys.stderr,
    )
    for line in problems:
        print(f"  ✗ {line}", file=sys.stderr)
    return FINDINGS


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("spec", "verdict"):
        print(__doc__, file=sys.stderr)
        return ERROR
    try:
        if args[0] == "spec":
            return check_spec(Path(args[1]))
        return check_verdict(Path(args[1]))
    except ClaimError as exc:
        print(f"[measurement_claims] {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
