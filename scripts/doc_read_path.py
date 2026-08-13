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

`check --sources` guards **one direction only**: a read this table REMOVED cannot come back. It says
nothing about the inverse - a stage instructed to read something that `READ_PATH` never declares. So
the table is not self-verifying, and an instructed-but-undeclared reader makes it *incomplete* rather
than wrong, which is harder to notice. The human spec-review survived exactly that way: it was
reading `overview.md`'s header and the prior phase's card per spec, on nobody's `readers:` line.
Adding a reader here is how that is fixed; bending the prose to match a silent table is not.

The artifact check is **diff-scoped**: an artifact the current diff touches is held to the table, and
one it does not is *counted on stderr and never blocked*. The rule is **you are responsible for what
you change** - it is the applicability boundary (`scripts/applicability.py`, which owns the
`changed_paths` mechanism this imports), and the same rule `scripts/verifier_bundle_scope.py` (the
verifier bundle), `scripts/spec_gate_cache.py` (spec re-gates) and the mutation gate
(`cr-filter-git`) already run on.
The fifth mechanism in one system does not get to invent a different one, and unlike a
grandfathering list or a marker file it needs no maintenance and cannot rot as new artifact kinds
appear. It is also what lets a repository upgrade: history it has not touched is visible without
holding it hostage. `check --all` audits everything; `check --sources` is unaffected and stays full.
When git cannot answer - not a repository, git missing, a command that fails - the scope is
unknowable, so nothing is enforced and that is said out loud. Falling back to enforcing everything
would reinstate exactly the hostage failure the scoping removes.

Fails closed: an unreadable file is exit 2, never a silent clean pass.

Every entry carries `emitted_by`: the canonical source that tells the writer to emit `readers:`.
Declaring a reader in this table is not the same as instructing anyone to write it down, and three
document classes shipped with the first because nothing owned the second.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The diff scope is ONE mechanism and `applicability.py` owns it — this check was simply the first to
# need it. Imported rather than kept here, and re-exported for the callers that already ask this
# module for it: a second copy of a rule is the copy that goes blind.
from applicability import changed_paths  # noqa: E402,F401  (re-exported)

# --- the table -----------------------------------------------------------------------------------
# extent: whole | header | table | card | none. `none` means the document is written but no stage is
# instructed to read it - it is an archive, kept on disk, off the read path.

HANDOVER_MAX_BYTES = 6144       # the contract card's hard cap (F1). Enforced, not requested.
VERDICT_REPORT_MAX_CHARS = 1500  # the verdict's free-prose `report` cap (F5).

READ_PATH: dict[str, dict] = {
    "task-analysis.md": {
        "written_by": "avenger-task-analyst",
        "emitted_by": "docs/templates/task-analysis.template.md",
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
        "emitted_by": "docs/templates/overview.template.md",
        "readers": [
            "avenger-implementation-planner @ once",
            "avenger-spec-writer @ per spec (whole)",
            "spec gate @ per spec (## Contracts and Decisions header only)",
            "spec-review (human grill) @ per spec (## Contracts and Decisions header only)",
            "e2e-author @ once, at feature close (the goal)",
        ],
        "extent": "whole | header",
    },
    "plan.md": {
        "written_by": "avenger-implementation-planner",
        "emitted_by": "docs/templates/plan.template.md",
        "readers": ["avenger-spec-writer @ per spec", "phase-handover @ per phase (next phase only)"],
        "extent": "whole",
    },
    "spec.md": {
        "written_by": "avenger-spec-writer",
        "emitted_by": "docs/templates/spec.template.md",
        "readers": [
            "spec gate @ on write (observe pass, then triage pass for context)",
            "implementer @ once, its own spec",
            "verifier bundle @ per phase, changed specs only",
        ],
        "extent": "whole",
        # Leaves the read path when its phase verifies: later phases read the contract card.
        # It used to be read by TWO model gates on every write — the Fidelity Gate and the automated
        # half of spec-review — asking overlapping questions of the same bytes at the same moment.
        # They are one gate now, and its two passes read different things: the observe pass reads the
        # spec, the triage pass reads the OBSERVATIONS plus the spec for context.
    },
    "spec-notes.md": {
        "written_by": "spec gate",
        "emitted_by": "docs/templates/spec-notes.template.md",
        "readers": ["implementer @ once, before building its own spec"],
        "extent": "whole",
        # The known-open list: observations the gate recorded and deliberately did not block on.
        # It is a SIDECAR rather than a section of spec.md on purpose — the spec is read whole by its
        # implementer and bundled to the verifier, so notes appended to it would ride on every one of
        # those reads and grow the artifact the requirement cap exists to bound.
        "named_only_by": {
            "agents/avenger-spec-writer.md",
            "agents/avenger-backend-architect.md",
            "agents/avenger-frontend-developer.md",
            "skills/pipeline-conventions/SKILL.md",
            "skills/spec-review-checklist/SKILL.md",
            "commands/spec-review.md",
        },
    },
    "amendments.json": {
        "written_by": "orchestrator / verifier (scripts/amendments.py)",
        "emitted_by": "docs/templates/amendments.template.json",
        "readers": [
            "avenger-verifier @ per phase, to scope the re-verify set",
            "phase-handover @ per phase",
        ],
        "extent": "whole",
        # A post-verification change and the requirement ids it touched. It is small by construction
        # — ids, a reason, a status — and it is what stops a one-line correction costing a full
        # verification round, which is what rounds 3 through 8 of one measured phase were.
    },
    "exceptions.json": {
        "written_by": "orchestrator / captain (scripts/applicability.py)",
        "emitted_by": "docs/templates/exceptions.template.json",
        "readers": [
            "pipeline_state.py @ per phase, resolving the next stage",
            "requirement_cap.py @ per spec write",
            "phase-handover @ per phase",
        ],
        "extent": "whole",
        # A disclosed exception to ONE mechanical rule, for ONE subject. Small by construction — a
        # rule, a subject, who recorded it and why — and it is what lets a phase closed under a
        # captain-ordered cap be read as CLOSED rather than parking the resolver forever.
    },
    "test-mapping.md": {
        "written_by": "implementer",
        "emitted_by": "docs/templates/test-mapping.template.md",
        "readers": ["avenger-verifier @ per phase", "verifier bundle @ per phase, changed specs only"],
        "extent": "table",
    },
    "test-evidence.md": {
        "written_by": "implementer",
        "emitted_by": "docs/templates/test-evidence.template.md",
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
        "emitted_by": "docs/templates/verdict.template.json",
        "readers": ["phase-handover @ per phase", "feature close @ once"],
        "extent": "whole",
        "report_max_chars": VERDICT_REPORT_MAX_CHARS,
    },
    "verdict-attempt-<n>.json": {
        "written_by": "avenger-verifier",
        # An archived copy of the verdict body, so it carries the verdict's own declaration.
        "emitted_by": "docs/templates/verdict.template.json",
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
        "emitted_by": "docs/templates/handover.template.md",
        "readers": [
            "avenger-spec-writer @ per spec, prior phases' cards",
            "spec gate @ per spec, the immediately prior phase's card (CONTEXT, never gated)",
            "spec-review (human grill) @ per spec, the immediately prior phase's card",
            "e2e-author @ once, at feature close",
        ],
        "extent": "card",
        "max_bytes": HANDOVER_MAX_BYTES,
    },
    "handover-archive.md": {
        "written_by": "phase-handover",
        "emitted_by": "docs/templates/handover-archive.template.md",
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
        "emitted_by": "scripts/pipeline_observations.py",
        "readers": ["retrospective triage @ once, at feature close", "preflight sweep @ frontmatter only"],
        "extent": "whole",
    },
    "e2e-mapping.md": {
        "written_by": "implementer (e2e-author)",
        "emitted_by": "skills/e2e-author/SKILL.md",
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

#: What an unenforced artifact is counted as, in the one stderr line the scan prints.
UNENFORCED_LABELS = {
    "cap": "over the handover cap",
    "readers": "with no `readers:`",
    "report": "over the report cap",
    "unparseable": "unparseable",
}


def _artifact_problems(path: Path, spec: dict) -> list[tuple[str, str]]:
    """Every way one artifact breaks the read path, as (category, message)."""
    problems: list[tuple[str, str]] = []

    cap = spec.get("max_bytes")
    if cap is not None:
        size = path.stat().st_size
        if size > cap:
            problems.append((
                "cap",
                f"{path}: {size} bytes over the {cap}-byte cap. This is a contract card, not a "
                f"record — move the narrative to handover-archive.md beside it, which no stage "
                f"is instructed to read.",
            ))

    expected = spec["readers"] or [f"none (archive of {spec.get('archive_of')})"]
    missing_readers = (
        "readers",
        f"{path}: does not declare `readers:`. Every pipeline document states who reads it and "
        f"when, in its own frontmatter — a document no stage reads does not get written, and an "
        f"archive says `none` rather than staying silent. Expected: {'; '.join(expected)}",
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
            problems.append(("unparseable", f"{path}: not parseable JSON ({exc}) — fail closed"))
            return problems
        if not isinstance(payload, dict) or "readers" not in payload:
            problems.append(missing_readers)
        report = payload.get("report") if isinstance(payload, dict) else None
        cap_chars = spec.get("report_max_chars")
        if cap_chars and isinstance(report, str) and len(report) > cap_chars:
            problems.append((
                "report",
                f"{path}: `report` is {len(report)} chars, over the {cap_chars}-char cap. It "
                f"carries the headline judgement and what the structured fields cannot say — "
                f"not a prose retelling of tests, coverage and findings.",
            ))
    return problems


def _report_unenforced(count: int, breakdown: Counter) -> None:
    """Say how much history predates the rule. Visibility without coercion: never an exit code."""
    if not count:
        return
    parts = ", ".join(
        f"{n} {UNENFORCED_LABELS.get(category, category)}"
        for category, n in sorted(breakdown.items())
    )
    print(
        f"[doc_read_path] {count} pre-existing artifact(s) predate this rule and are not enforced: "
        f"{parts} - they are checked when you next change them.",
        file=sys.stderr,
    )


def check_artifacts(root: Path, *, enforce_all: bool = False) -> list[str]:
    features = root / "docs" / "features"
    if not features.is_dir():
        # An absent tree scans nothing. That is CLEAN, but it is said out loud rather than passing
        # invisibly - the same discipline `subprocess_check.py` uses for an absent test root.
        print(f"[doc_read_path] no {features} — nothing to check", file=sys.stderr)
        return []

    scope: set[Path] | None = None
    if not enforce_all:
        scope = changed_paths(root)
        if scope is None:
            print(
                f"[doc_read_path] git cannot say what changed under {root}, so the scope is "
                f"unknowable and no artifact is enforced. Run `check --all` for a full audit.",
                file=sys.stderr,
            )
            return []

    problems: list[str] = []
    unenforced = 0
    breakdown: Counter = Counter()
    for path in sorted(features.rglob("*")):
        if not path.is_file():
            continue
        spec = spec_for(path.name)
        if spec is None:
            continue

        found = _artifact_problems(path, spec)
        if not found:
            continue
        if enforce_all or path.resolve() in scope:
            problems.extend(message for _, message in found)
        else:
            unenforced += 1
            breakdown.update({category for category, _ in found})

    _report_unenforced(unenforced, breakdown)
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
    check.add_argument(
        "--all",
        action="store_true",
        dest="enforce_all",
        help="enforce every artifact, not only the ones the current diff touches (full audit)",
    )

    sub.add_parser("table", help="print the read path as a markdown table")

    args = parser.parse_args(argv)
    if args.command == "table":
        print(render_table())
        return 0

    root = Path(args.root).resolve()
    problems: list[str] = []
    if not args.sources_only:
        problems += check_artifacts(root, enforce_all=args.enforce_all)
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
