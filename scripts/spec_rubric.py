#!/usr/bin/env python3
"""The spec gate's rubric, rendered for the writer BEFORE it writes - from the gate's own sources.

## Why this file exists

Phase 9 of one measured feature ran **fourteen** gate rounds on its first spec. The next three specs
in the same phase took one, three and one. The distribution is the finding: the decide pass returned
NO-GO ten times before its first GO on 9.1, and then the writer - having learned by rejection what
the collapsed gate actually blocks - was almost immediately right about everything after it. Nothing
carried that learning anywhere, so **the next phase pays the fourteen again**.

The gate's criteria are already mechanical. The closed blocking set is a table
(`spec_gate_triage.BLOCKING`); the size rule is a counter (`requirement_cap`); what the observe pass
reads for is a section of `prompts/spec-gate-observe.md`; the tie-break is a section of
`prompts/spec-gate-triage.md`. Handing those to the writer is wiring, not new judgement.

## The one thing this module must never do is RESTATE them

A second copy of a rubric drifts, and a drifted copy is **worse than none**: the writer is then
primed against a standard it is not judged by, and every disagreement between the two costs a round
of exactly the kind this exists to remove. So nothing here is authored. Every line of the rendered
rubric is either

- **data** read out of the module that decides with it (`BLOCKING`, `NOTE`, the requirement cap), or
- **a section lifted verbatim** out of the prompt the gate's own model is given.

Change the gate and this text changes with it, because there is nothing here to keep in step.

## It fails closed, and that is the point

A rubric that renders *most* of itself is the drifted copy wearing a different hat - the writer would
be primed against three of four blocking categories and never know which one was missing. So a
missing prompt, a renamed section or an unreadable file is an **error with a named cause**, never a
partial render. `tests/test_spec_rubric.py` pins the section headings this depends on, so renaming
one goes red in the suite rather than silently emptying the brief.

Usage:
    spec_rubric.py                 the rubric on stdout
    spec_rubric.py --sources       one line per source it was rendered from
                                   exit 0 = rendered, 2 = could not render (cause on stderr)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The authorities. Imported, never copied: `BLOCKING` is the table the verdict is derived from and
# `cap()` is the number counted before any model runs. A literal restatement of either here is the
# second copy this module exists to avoid.
from md_section import slice_section  # noqa: E402
from requirement_cap import cap  # noqa: E402
from spec_gate_triage import BLOCKING, NOTE  # noqa: E402

OK = 0
ERROR = 2

#: The prompt sections lifted verbatim. Each is (file, heading, why the writer needs it). The heading
#: text is the contract with the prompt; `tests/test_spec_rubric.py` asserts each one still resolves.
LIFTED: tuple[tuple[str, str, str], ...] = (
    (
        "prompts/spec-gate-observe.md",
        "What to look at",
        "What the gate's reading pass reports on. These are places it looks, not a checklist to "
        "answer - most of them produce nothing on most specs.",
    ),
    (
        "prompts/spec-gate-triage.md",
        "The tie-break, stated once",
        "How an uncertain observation is resolved. Read it before you assume a note will escalate.",
    ),
)


class RubricError(Exception):
    """A source this module could not read. Always renders nothing rather than part of a rubric."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause

    def render(self) -> str:
        return f"[spec_rubric] cause={self.cause} {self}"


def prompts_dir() -> Path:
    """Where the gate's rubrics live, in-repo and in the vendored flat layout alike.

    `scripts/hook_spec_gate.sh` reaches them as `$SD/../prompts`, so this resolves the same way and
    the two cannot disagree about which prompt the writer is primed from.
    """
    return HERE.parent / "prompts"


def section(text: str, heading: str) -> str | None:
    """The body under a markdown heading, up to the next heading of the same or shallower level.

    The slicing is `md_section.slice_section`'s, shared with `requirement_cap.requirements_section`
    and `spec_gate_context.contracts_section`: a subsection under the heading belongs to it, so
    ending at any deeper heading would silently truncate the section to nothing - which is the
    partial render this module refuses to produce. The policy that stays here is that an EMPTY
    section is treated exactly like a missing one, because an empty brief claiming to be the gate's
    rubric is the drifted copy.
    """
    found = slice_section(text, heading)
    return None if found is None else (found[1].strip() or None)


def lifted(root: Path | None = None) -> list[tuple[str, str, str]]:
    """(source path, why, verbatim body) for every prompt section the rubric carries.

    `root` redirects `prompts/` for the tests that prove this fails closed; production callers pass
    nothing and get the shipped prompts.

    Every failure is a `RubricError`. A section that renders empty is treated exactly like a missing
    one: an empty brief that claims to be the gate's rubric is the drifted copy.
    """
    base = prompts_dir() if root is None else Path(root) / "prompts"
    out: list[tuple[str, str, str]] = []
    for relative, heading, why in LIFTED:
        path = base / Path(relative).name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RubricError(
                "unreadable-prompt",
                f"cannot read {path}, which is one of the gate's own rubrics. The writer would be "
                f"primed against a standard it is not judged by, so nothing is rendered.",
            ) from exc
        body = section(text, heading)
        if body is None:
            raise RubricError(
                "missing-section",
                f"{relative} has no `{heading}` section any more. This module lifts that section "
                f"verbatim rather than restating it; a renamed heading is a change to the rubric "
                f"and is answered here, not worked around.",
            )
        out.append((relative, why, body))
    return out


def blocking_block() -> str:
    """The closed set, rendered from the table the verdict is derived from."""
    rows = [f"- `{name}` - {meaning}" for name, meaning in BLOCKING.items()]
    return "\n".join(rows)


def render(root: Path | None = None) -> str:
    """The whole brief. Raises `RubricError` rather than returning a partial one."""
    limit = cap()
    parts = [
        "# THE SPEC GATE'S RUBRIC - what will judge this spec, read it before you write",
        "",
        "You are being handed the gate's criteria up front because the alternative is learning them "
        "by rejection. One measured phase spent **fourteen rounds** on its first spec and one round "
        "on its next; the writer learned what the gate blocks, and nothing carried that learning "
        "into the next phase.",
        "",
        "Nothing below is a second statement of the rules. Every line is rendered from the gate's "
        "own sources - `scripts/spec_gate_triage.py` (the table the verdict is derived from), "
        "`scripts/requirement_cap.py` (the count), and the two prompts the gate's models are given. "
        "If it changes there, it changes here.",
        "",
        "## Size is settled mechanically, before any model sees your spec",
        "",
        f"At most **{limit} requirements** per spec, counted by `scripts/requirement_cap.py` before "
        "any paid call. Over the cap the spec **SPLITS** into siblings `<n>.<k>` under the same "
        "phase, each independently gated - it is not rejected, and it is not too long.",
        "",
        "**No gate will ever block your spec for being large, thin, vague or under-detailed, and "
        "none will ask you to expand a section.** So never answer anything with more prose. A spec "
        "that grows to satisfy a gate is the failure this design removed: one measured spec went "
        "25k -> 51k characters across four rejected rounds.",
        "",
        "## Exactly these things BLOCK. The set is closed.",
        "",
        blocking_block(),
        "",
        "That is the whole list, and it is closed mechanically: a category invented at run time is a "
        "hard failure of the gate, not a stricter verdict. If a block does not name one of these, "
        f"say so rather than padding the spec. Everything else the gate notices is a `{NOTE}` - "
        "recorded in `spec-notes.md` beside your spec, read once by the implementer, **blocking "
        "nothing**, and never escalating in a later round.",
    ]
    for relative, why, body in lifted(root):
        parts += ["", f"## Lifted verbatim from `{relative}`", "", why, "", body]
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources", action="store_true", help="list the sources instead of rendering"
    )
    args = parser.parse_args(argv)

    if args.sources:
        print("scripts/spec_gate_triage.py: the closed blocking set and the note category")
        print("scripts/requirement_cap.py: the requirement cap, a split trigger")
        for relative, _heading, _why in LIFTED:
            print(f"{relative}: lifted verbatim")
        return OK

    try:
        sys.stdout.write(render())
    except (RubricError, ValueError) as exc:
        message = exc.render() if isinstance(exc, RubricError) else f"[spec_rubric] {exc}"
        print(message, file=sys.stderr)
        return ERROR
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
