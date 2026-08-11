#!/usr/bin/env python3
"""The pipeline's read path: who reads each document, when, and how much of it.

Documentation cost is **not size**. It is `size x how often a document is read x how long it stays
resident in context`. A measured run made that concrete: `task-analysis.md` is 31 KB and trivial,
but was read 60 times to extract one frontmatter field (~465k tokens); `handover.md` held 272 KB
yet cost 485k-1,475k tokens, because every spec write and every spec review re-read *every prior
phase's* handover. Meanwhile `spec.md` was 990 KB - the largest artifact on disk - and cost
comparatively little, because each one is read mostly once, by its own implementer.

So the thing to govern is the **read directive**, and the failure mode to design against is a
directive re-appearing one caller at a time. This module is the single place the read path is
declared. `READ_PATH` below is the table; the stage prose in `agents/`, `skills/`, `commands/` and
`prompts/` names files, but the *rule* about who may read what lives here and in
`skills/pipeline-conventions` § "The document read path", and nowhere else.

Two checks keep the declaration true rather than aspirational:

  check          - artifacts on disk obey it: `handover.md` is under its byte cap, and every
                   artifact declares `readers:` in its own frontmatter. A document no stage reads
                   does not get written; an archive declares `readers: none` and says whose it is.
  check --sources - the canonical stage instructions obey it: a document that LEFT the read path is
                   named only by the files allowed to name it. This is the recurrence guard. A
                   `Read task-analysis.md` added back to one implementer, or a stage taught to open
                   `handover-archive.md`, fails here rather than being discovered by the next
                   measurement.

Fails closed: an unreadable file is exit 2, never a silent clean pass.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --- the table -----------------------------------------------------------------------------------
# extent: whole | header | table | card | none. `none` means the document is written but no stage is
# instructed to read it - it is an archive, kept on disk, off the read path.

HANDOVER_MAX_BYTES = 6144       # the contract card's hard cap (F1). Enforced, not requested.
VERDICT_REPORT_MAX_CHARS = 1500  # the verdict's free-prose `report` cap (F5).

READ_PATH: dict[str, dict] = {
    "task-analysis.md": {
        "written_by": "avenger-task-analyst",
        "readers": ["avenger-solution-architect @ once, at feature start"],
        "extent": "whole",
        # It left the PER-SPEC path: `work_kind` now rides in the spec's own frontmatter.
        "named_only_by": {
            "agents/avenger-task-analyst.md",
            "agents/avenger-solution-architect.md",
            "skills/pipeline-conventions/SKILL.md",
        },
    },
    "overview.md": {
        "written_by": "avenger-solution-architect",
        "readers": [
            "avenger-implementation-planner @ once",
            "avenger-spec-writer @ per spec (whole)",
            "spec-review @ per spec (## Contracts and Decisions header only)",
            "e2e-author @ once, at feature close (the goal)",
        ],
        "extent": "whole | header",
    },
    "plan.md": {
        "written_by": "avenger-implementation-planner",
        "readers": ["avenger-spec-writer @ per spec", "phase-handover @ per phase (next phase only)"],
        "extent": "whole",
    },
    "spec.md": {
        "written_by": "avenger-spec-writer",
        "readers": [
            "fidelity gate @ on write",
            "spec-review @ per spec",
            "implementer @ once, its own spec",
            "verifier bundle @ per phase, changed specs only",
        ],
        "extent": "whole",
        # Leaves the read path when its phase verifies: later phases read the contract card.
    },
    "test-mapping.md": {
        "written_by": "implementer",
        "readers": ["avenger-verifier @ per phase", "verifier bundle @ per phase, changed specs only"],
        "extent": "table",
    },
    "test-evidence.md": {
        "written_by": "implementer",
        "readers": ["implementer @ on route-back only", "avenger-verifier @ on route-back only"],
        "extent": "whole",
        "named_only_by": {
            "agents/avenger-backend-architect.md",
            "agents/avenger-frontend-developer.md",
            "agents/avenger-verifier.md",
            "agents/avenger-breaker.md",
            "agents/avenger-bug-hunter.md",
            "skills/tdd/SKILL.md",
            "skills/verifier-triage/SKILL.md",
            "skills/pipeline-conventions/SKILL.md",
            "prompts/verifier-review.md",   # tells the reviewer its ABSENCE is never a finding
            "skills/ponytail/SKILL.md",     # forbids minimising it away — the opposite of a read
        },
    },
    "verdict.json": {
        "written_by": "avenger-verifier",
        "readers": ["phase-handover @ per phase", "feature close @ once"],
        "extent": "whole",
        "report_max_chars": VERDICT_REPORT_MAX_CHARS,
    },
    "verdict-attempt-<n>.json": {
        "written_by": "avenger-verifier",
        "readers": [],
        "extent": "none",
        "archive_of": "verdict.json",
        "needle": "verdict-attempt-",
        "named_only_by": {
            "agents/avenger-verifier.md",
            "skills/verifier-triage/SKILL.md",
            "skills/pipeline-conventions/SKILL.md",
        },
    },
    "handover.md": {
        "written_by": "phase-handover",
        "readers": [
            "avenger-spec-writer @ per spec, prior phases' cards",
            "spec-review @ per spec, the immediately prior phase's card",
            "e2e-author @ once, at feature close",
        ],
        "extent": "card",
        "max_bytes": HANDOVER_MAX_BYTES,
    },
    "handover-archive.md": {
        "written_by": "phase-handover",
        "readers": [],
        "extent": "none",
        "archive_of": "handover.md",
        "named_only_by": {
            "agents/avenger-handover.md",
            "skills/phase-handover/SKILL.md",
            "skills/pipeline-conventions/SKILL.md",
            # ponytail names it to forbid minimising it away. That is the opposite of a read
            # directive, and it is on this list by a deliberate edit to the table — which is the
            # sanctioned way to change the read path, and the only one.
            "skills/ponytail/SKILL.md",
        },
    },
    "pipeline-observations.md": {
        "written_by": "orchestrator",
        "readers": ["retrospective triage @ once, at feature close", "preflight sweep @ frontmatter only"],
        "extent": "whole",
    },
    "e2e-mapping.md": {
        "written_by": "implementer (e2e-author)",
        "readers": ["feature close @ once"],
        "extent": "whole",
    },
}

# Documents that are OFF the read path, or that left part of it. Naming one from a stage instruction
# outside its allowlist is how the cost came back last time - one caller at a time.
GUARDED = {name: spec for name, spec in READ_PATH.items() if "named_only_by" in spec}

SOURCE_DIRS = ("agents", "skills", "commands", "prompts")

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
READERS_RE = re.compile(r"^readers:[ \t]*(.*)$", re.MULTILINE)


def _read(path: Path) -> str:
    """Read a file, or fail closed. A file we cannot read is not a file we can clear."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"[doc_read_path] cannot read {path}: {exc} — fail closed")


def frontmatter(text: str) -> str | None:
    match = FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def declared_readers(text: str) -> str | None:
    """The `readers:` value from a document's own frontmatter, or None if it declares none."""
    block = frontmatter(text)
    if block is None:
        return None
    found = READERS_RE.search(block)
    if not found:
        return None
    return found.group(1).strip() or None


def spec_for(filename: str) -> dict | None:
    """The table entry governing a file, resolving the one `<n>`-numbered name in the table."""
    entry = READ_PATH.get(filename)
    if entry is not None:
        return entry
    for name, candidate in READ_PATH.items():
        needle = candidate.get("needle")
        if needle and filename.startswith(needle) and filename.endswith(name.rsplit(".", 1)[-1]):
            return candidate
    return None


# --- check: the artifacts on disk ----------------------------------------------------------------

def check_artifacts(root: Path) -> list[str]:
    features = root / "docs" / "features"
    if not features.is_dir():
        # An absent tree scans nothing. That is CLEAN, but it is said out loud rather than passing
        # invisibly - the same discipline `subprocess_check.py` uses for an absent test root.
        print(f"[doc_read_path] no {features} — nothing to check", file=sys.stderr)
        return []

    problems: list[str] = []
    for path in sorted(features.rglob("*")):
        if not path.is_file():
            continue
        spec = spec_for(path.name)
        if spec is None:
            continue

        cap = spec.get("max_bytes")
        if cap is not None:
            size = path.stat().st_size
            if size > cap:
                problems.append(
                    f"{path}: {size} bytes over the {cap}-byte cap. This is a contract card, not a "
                    f"record — move the narrative to handover-archive.md beside it, which no stage "
                    f"is instructed to read."
                )

        expected = spec["readers"] or [f"none (archive of {spec.get('archive_of')})"]
        missing_readers = (
            f"{path}: does not declare `readers:`. Every pipeline document states who reads it and "
            f"when, in its own frontmatter — a document no stage reads does not get written, and an "
            f"archive says `none` rather than staying silent. Expected: {'; '.join(expected)}"
        )

        if path.suffix == ".md":
            if declared_readers(_read(path)) is None:
                problems.append(missing_readers)
        elif path.suffix == ".json":
            # JSON has no frontmatter, so the same declaration is a top-level `readers` key. The
            # rule is about the document, not about YAML.
            try:
                payload = json.loads(_read(path))
            except json.JSONDecodeError as exc:
                problems.append(f"{path}: not parseable JSON ({exc}) — fail closed")
                continue
            if not isinstance(payload, dict) or "readers" not in payload:
                problems.append(missing_readers)
            report = payload.get("report") if isinstance(payload, dict) else None
            cap_chars = spec.get("report_max_chars")
            if cap_chars and isinstance(report, str) and len(report) > cap_chars:
                problems.append(
                    f"{path}: `report` is {len(report)} chars, over the {cap_chars}-char cap. It "
                    f"carries the headline judgement and what the structured fields cannot say — "
                    f"not a prose retelling of tests, coverage and findings."
                )
    return problems


# --- check --sources: the stage instructions -----------------------------------------------------

def check_sources(root: Path) -> list[str]:
    problems: list[str] = []
    for name, spec in GUARDED.items():
        allowed = spec["named_only_by"]
        needle = spec.get("needle", name)
        for source_dir in SOURCE_DIRS:
            base = root / source_dir
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.md")):
                rel = path.relative_to(root).as_posix()
                if rel in allowed:
                    continue
                text = _read(path)
                for lineno, line in enumerate(text.splitlines(), 1):
                    if needle in line:
                        problems.append(
                            f"{rel}:{lineno}: names `{name}`, which is off the read path "
                            f"({'archive of ' + spec['archive_of'] if spec.get('archive_of') else 'read only where the table says'}). "
                            f"Allowed to name it: {', '.join(sorted(allowed))}. "
                            f"If this stage genuinely needs it, change the table in "
                            f"scripts/doc_read_path.py and the read path in "
                            f"skills/pipeline-conventions — not this one caller."
                        )
    return problems


# --- table ---------------------------------------------------------------------------------------

def render_table() -> str:
    lines = ["| document | written by | read by | extent |", "|---|---|---|---|"]
    for name, spec in READ_PATH.items():
        readers = "<br>".join(spec["readers"]) or f"**nobody** (archive of `{spec.get('archive_of')}`)"
        lines.append(f"| `{name}` | {spec['written_by']} | {readers} | {spec['extent']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate artifacts on disk (and, with --sources, the stage instructions)")
    check.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
    check.add_argument("--sources", action="store_true", help="also check the canonical stage instructions")
    check.add_argument("--sources-only", action="store_true", help="check only the stage instructions")

    sub.add_parser("table", help="print the read path as a markdown table")

    args = parser.parse_args(argv)
    if args.command == "table":
        print(render_table())
        return 0

    root = Path(args.root).resolve()
    problems: list[str] = []
    if not args.sources_only:
        problems += check_artifacts(root)
    if args.sources or args.sources_only:
        problems += check_sources(root)

    if problems:
        print("read-path violations:", file=sys.stderr)
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        return 1
    print("[doc_read_path] clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
