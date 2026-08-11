#!/usr/bin/env python3
"""The spec's known-open list - where a non-blocking observation goes instead of nowhere.

The gate this replaces had two states for anything it noticed: block the spec, or say nothing. With
"when unsure, choose NO-GO" as the tie-break, everything drifted toward blocking, and a blocked spec
can only answer with more text. Notes are the counterweight: an observation that is real but does
not stop the implementer is **written down and read once**, and it blocks nothing, ever.

They live beside the spec rather than inside it, for the reason the whole documentation re-path
exists: a spec is read whole by its implementer and bundled to the verifier, so appending a growing
notes section to it would put the notes on every one of those reads and grow the artifact the
requirement cap exists to bound. The sidecar is read once, by the implementer, and by nothing else.

`spec-notes.md` is regenerated from scratch on every gate run: it is the current gate's view, not a
log. A gate run that produced no notes writes no file, and removes a stale one - a document no stage
reads does not get written.

Usage:
    spec_notes.py write <spec.md> <decision.json>    write/refresh/remove the sidecar
    spec_notes.py path  <spec.md>                    print where it would go
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OK = 0
ERROR = 2

FILENAME = "spec-notes.md"


def notes_path(spec: Path) -> Path:
    """Beside the spec it belongs to - one per spec, same directory."""
    return spec.parent / FILENAME


def render(spec: Path, notes: list[dict], blocking: list[dict]) -> str:
    """The sidecar's text. Frontmatter first, because every pipeline document declares its readers."""
    fields = _spec_fields(spec)
    lines = [
        "---",
        f"feature: {fields.get('feature', '<feature>')}",
        f"phase: {fields.get('phase', '<n>-<slug>')}",
        f"spec: {fields.get('spec', spec.parent.name)}",
        "stage: spec-gate",
        "readers: implementer @ once, before building this spec",
        "links: spec.md",
        f"created: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "---",
        "",
        "# Known-open notes",
        "",
        "Observations the spec gate recorded and **deliberately did not block on**. None of these",
        "stops you building this spec. Read them once, use your judgement, and do not treat any of",
        "them as a requirement: the spec's `## Requirements` section is the contract, not this file.",
        "",
    ]
    if blocking:
        lines += [
            f"> This spec was BLOCKED on {len(blocking)} finding(s) from the closed set when these",
            "> notes were written. The blocking findings are not repeated here - they are in the",
            "> gate's own report and must be answered in `spec.md`.",
            "",
        ]
    if not notes:
        lines += ["_No open notes._", ""]
    else:
        for note in notes:
            ref = str(note.get("spec_ref") or "-").strip()
            area = str(note.get("area") or "other").strip()
            lines.append(f"- **{ref}** ({area}) — {str(note.get('statement') or '').strip()}")
            why = str(note.get("why") or "").strip()
            if why:
                lines.append(f"  - triaged as a note because: {why}")
        lines.append("")
    return "\n".join(lines)


def _spec_fields(spec: Path) -> dict[str, str]:
    """The spec's frontmatter, so the sidecar carries the same feature/phase/spec identity."""
    try:
        text = spec.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.split("#")[0].strip()
    return fields


def write(spec: Path, decision: dict) -> Path | None:
    """Write (or remove) the sidecar for one gate run. Returns the path written, or None."""
    notes = [n for n in (decision.get("notes") or []) if isinstance(n, dict)]
    blocking = [b for b in (decision.get("blocking") or []) if isinstance(b, dict)]
    target = notes_path(spec)
    if not notes:
        # Nothing open. A document no stage reads does not get written, and a stale one is worse
        # than none: it tells the implementer something the gate no longer says.
        target.unlink(missing_ok=True)
        return None
    target.write_text(render(spec, notes, blocking), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "path":
        print(notes_path(Path(args[1])))
        return OK
    if len(args) != 3 or args[0] != "write":
        print(__doc__, file=sys.stderr)
        return ERROR
    spec = Path(args[1])
    try:
        decision = json.loads(Path(args[2]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[spec_notes] cannot read the decision: {exc}", file=sys.stderr)
        return ERROR
    try:
        written = write(spec, decision)
    except OSError as exc:
        # Best-effort by design: the caller ignores this failure. Losing a note costs the implementer
        # context, not correctness, and it must never be the reason an approved spec is blocked.
        print(f"[spec_notes] could not write notes beside {spec}: {exc}", file=sys.stderr)
        return ERROR
    if written:
        count = len(decision.get("notes") or [])
        print(f"  {count} non-blocking note(s) -> {written}", file=sys.stderr)
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
