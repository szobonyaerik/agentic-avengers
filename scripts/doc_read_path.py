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

  check --contract - the third check, and the general form of a defect this repository keeps
                   producing one instance at a time: **documentation making claims nothing
                   enforces.** It asks that every documented claim about an artifact's frontmatter
                   has a writer instructed to produce it, in both directions - the table's declared
                   `emitted_by` exists and really instructs `readers:`, AND every artifact class a
                   canonical source names is one this table governs. The second half is what
                   `--sources` structurally cannot see, and it is issue #29: four classes named by
                   stage instructions with no read-path decision recorded either way, one of them
                   (`implementation-report.md`) keying a Stop hook while nothing was told to write
                   it. **It runs in the canonical repository ONLY** - every source it reads lives
                   upstream, so downstream none of the remedies it prescribes exists; elsewhere it
                   says on stderr that neither direction ran. See `is_canonical_repo`. Its limits
                   are stated at `check_contract` rather than implied.

The artifact check is **diff-scoped**: an artifact the current diff touches is held to the table, and
one it does not is *counted on stderr and never blocked*. The rule is **you are responsible for what
you change** - it is the applicability boundary (`scripts/applicability.py`, which owns the
`changed_paths` mechanism this imports), and the same rule `scripts/verifier_evidence.py` (execution
evidence), `scripts/spec_gate_cache.py` (spec re-gates) and the mutation gate
(`scripts/mutation_scope.py`) already run on.
A further mechanism in one system does not get to invent a different one, and unlike a
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

import guard_scope  # noqa: E402

# The diff scope is ONE mechanism and `applicability.py` owns it — this check was simply the first to
# need it. Imported rather than kept here, and re-exported for the callers that already ask this
# module for it: a second copy of a rule is the copy that goes blind.
from applicability import changed_paths, report_unenforced  # noqa: E402,F401  (re-exported)

# `applicability.py` WRITES the `readers` key into every exception ledger from its own list, so the
# list it emits and the list this table declares must be one list. Two copies is how a ledger ships
# declaring readers that are no longer its readers, with nothing to catch it: the check below asserts
# the key is present, never that it names the truth.
from applicability import READERS as APPLICABILITY_READERS  # noqa: E402

# `breaker_gate.py` REFUSES a record that declares no readers, so the list it enforces and the list
# this table declares must be one list. Two copies is how a record passes the gate that closes the
# phase and fails the gate that reads it a commit later.
from breaker_gate import READERS as BREAKER_READERS  # noqa: E402

# Same rule, same reason: `verifier_evidence.py` WRITES the `readers` key into every transcript it
# records and refuses one that carries none, so the list it emits and the list this table declares
# must be one list.
from verifier_evidence import READERS as EVIDENCE_READERS  # noqa: E402

# --- the table -----------------------------------------------------------------------------------
# extent: whole | header | table | card | none. `none` means the document is written but no stage is
# instructed to read it - it is an archive, kept on disk, off the read path.

CLEAN = 0
VIOLATIONS = (
    1  # an artifact or an instruction really breaks the table. The remedy is to fix it.
)
UNDECIDABLE = (
    2  # the check could not answer. §6: every stop names which of the two it is.
)

HANDOVER_MAX_BYTES = 6144  # the contract card's hard cap (F1). Enforced, not requested.
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
        "readers": [
            "avenger-spec-writer @ per spec",
            "phase-handover @ per phase (next phase only)",
            "carried_items.py @ per deferral (this phase's Done when table only)",
        ],
        "extent": "whole",
    },
    "spec.md": {
        "written_by": "avenger-spec-writer",
        "emitted_by": "docs/templates/spec.template.md",
        "readers": [
            "spec gate @ on write (observe pass, then triage pass for context)",
            "implementer @ once, its own spec",
            "avenger-verifier @ per phase",
            "carried_items.py @ per deferral (requirement `done_when:` tags only)",
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
        "readers": list(APPLICABILITY_READERS),
        "extent": "whole",
        # A disclosed exception to ONE mechanical rule, for ONE subject. Small by construction — a
        # rule, a subject, who recorded it and why — and it is what lets a phase closed under a
        # captain-ordered cap be read as CLOSED rather than parking the resolver forever.
    },
    "carried.json": {
        "written_by": "avenger-spec-writer / implementer (scripts/carried_items.py)",
        "emitted_by": "scripts/carried_items.py",
        "readers": [
            "avenger-spec-writer @ per phase, to see what it still owes an answer to",
            "phase-handover @ per phase",
            "avenger-verifier @ per phase, for the deferrals that back a `status: deferred` finding",
        ],
        "extent": "whole",
        # How the PREVIOUS phase's carried items were answered: built, tested, or declined with a
        # reason. Small by construction - an id, a kind, one pointer - and it is what stops a
        # handover's forward-looking claim becoming nothing, which is how phase 8's correct,
        # specific prediction about caller-supplied identifiers shipped as a phase-9 defect.
    },
    "breaker.json": {
        "written_by": "avenger-breaker",
        "emitted_by": "agents/avenger-breaker.md",
        "readers": list(BREAKER_READERS),
        "extent": "whole",
        # The Breaker's own record: a verdict, and what it actually attacked or found. Small by
        # construction - a verdict and one list - and it is what makes a stage that emits nothing
        # distinguishable from a stage that never ran, which is how two owed Breaker runs went
        # missing on one feature with nothing noticing (issue #45).
    },
    "verification-evidence.json": {
        "written_by": "avenger-verifier (scripts/verifier_evidence.py)",
        "emitted_by": "scripts/verifier_evidence.py",
        "readers": list(EVIDENCE_READERS),
        "extent": "whole",
        # The Verifier's proof that it EXECUTED: every command it ran, that command's exit code and
        # measured wall clock, the sha256 of its output, and the digest of the specs and tests it ran
        # against. Small by construction - one entry per command, with the output itself in a sibling
        # log this table does not govern - and it is what replaced `test_quality.reviewed`, a boolean
        # the verifying agent wrote about itself and every gate believed.
    },
    "test-mapping.md": {
        "written_by": "implementer",
        "emitted_by": "docs/templates/test-mapping.template.md",
        "readers": ["avenger-verifier @ per phase"],
        "extent": "table",
    },
    "test-evidence.md": {
        "written_by": "implementer",
        "emitted_by": "docs/templates/test-evidence.template.md",
        "readers": [
            "implementer @ on route-back only",
            "avenger-verifier @ on route-back only",
        ],
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
            "skills/ponytail/SKILL.md",  # forbids minimising it away — the opposite of a read
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
        # It gained exactly ONE reader, and only because nothing else can answer the question. A
        # superseded attempt is where a finding that was RAISED AND FIXED lives, and `found_by` is
        # the one field firstmate's record cannot reconstruct afterwards — a phase that raised six
        # findings on attempt 1 and passed on attempt 2 closed reporting none. The read is `id` and
        # `kind` per finding, once per phase close, by a script; no stage reads the prose, and
        # nothing here relocates any.
        "readers": [
            "pipeline_metrics.py + emission_gate.py @ per phase close (finding ids only)"
        ],
        "extent": "finding ids",
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
        "readers": [
            "retrospective triage @ once, at feature close",
            "preflight sweep @ frontmatter only",
        ],
        "extent": "whole",
    },
    "e2e-mapping.md": {
        "written_by": "implementer (e2e-author)",
        "emitted_by": "skills/e2e-author/SKILL.md",
        "readers": ["feature close @ once"],
        "extent": "whole",
    },
    "review-<slice>.md": {
        "written_by": "spec-isolation-review",
        "emitted_by": "skills/spec-isolation-review/SKILL.md",
        # One reviewer's verdict on ONE slice of one spec, written by a fork that is forbidden to
        # read any other reviewer's output - that isolation is the whole value, and it is also why
        # the only possible reader is whoever invoked the fan-out. Same shape as
        # `pipeline-observations.md`, which is written as the run goes and read once at the triage.
        # This is an ON-DEMAND stage: nothing in the phase loop invokes it, so nothing SCHEDULES the
        # read either. Its output is small by construction - a verdict and a list of findings - and
        # it is read once, by the review that asked for it, and never by a later phase.
        #
        # The skill used to say the findings were for "the gate". That gate was the automated half
        # of spec-review, deleted in the one-gate collapse (SKILL § Output now names the real
        # reader). A document whose only stated consumer no longer exists is exactly the state
        # issue #29 found this class in.
        "readers": ["the invoking spec review @ once, at the end of the fan-out"],
        "extent": "whole",
        "needle": "review-",
    },
}

# Documents that are OFF the read path, or that left part of it. Naming one from a stage instruction
# outside its allowlist is how the cost came back last time - one caller at a time.
GUARDED = {name: spec for name, spec in READ_PATH.items() if "named_only_by" in spec}

SOURCE_DIRS = ("agents", "skills", "commands", "prompts")

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
READERS_RE = re.compile(r"^readers:[ \t]*(.*)$", re.MULTILINE)


def _read(path: Path) -> str:
    """Read a file, or fail closed. A file we cannot read is not a file we can clear.

    `ValueError` is caught beside `OSError` because `UnicodeDecodeError` is a `ValueError`, and a
    single non-UTF-8 file anywhere under the scanned directories would otherwise turn the CI gate
    into a stack trace instead of a named finding. Same failure shape either way: unreadable is
    unreadable.

    It exits `UNDECIDABLE`, never `VIOLATIONS`. The module said "exit 2" for as long as it has
    existed and produced 1, because `SystemExit("<message>")` prints the message and exits 1 - so an
    unreadable file arrived at every caller wearing the code that means "an artifact is owed", and
    `hook_artifact_check.sh` turned that into "create them before stopping", which is not a remedy
    for a file nothing can decode.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(
            f"[doc_read_path] cannot read {path}: {exc} - fail closed", file=sys.stderr
        )
        raise SystemExit(UNDECIDABLE) from exc


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
        if (
            needle
            and filename.startswith(needle)
            and filename.endswith(name.rsplit(".", 1)[-1])
        ):
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
            problems.append(
                (
                    "cap",
                    f"{path}: {size} bytes over the {cap}-byte cap. This is a contract card, not a "
                    f"record — move the narrative to handover-archive.md beside it, which no stage "
                    f"is instructed to read.",
                )
            )

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
            problems.append(
                ("unparseable", f"{path}: not parseable JSON ({exc}) — fail closed")
            )
            return problems
        if not isinstance(payload, dict) or "readers" not in payload:
            problems.append(missing_readers)
        report = payload.get("report") if isinstance(payload, dict) else None
        cap_chars = spec.get("report_max_chars")
        if cap_chars and isinstance(report, str) and len(report) > cap_chars:
            problems.append(
                (
                    "report",
                    f"{path}: `report` is {len(report)} chars, over the {cap_chars}-char cap. It "
                    f"carries the headline judgement and what the structured fields cannot say — "
                    f"not a prose retelling of tests, coverage and findings.",
                )
            )
    return problems


def _report_unenforced(count: int, breakdown: Counter) -> None:
    """Say how much history predates the rule, in the boundary's ONE spelling.

    The sentence belongs to `applicability.report_unenforced` — out of scope must look the same,
    and different from clean, in every check on this boundary. Only the breakdown is this check's
    own, so only the breakdown is passed. Visibility without coercion: never an exit code.
    """
    parts = ", ".join(
        f"{n} {UNENFORCED_LABELS.get(category, category)}"
        for category, n in sorted(breakdown.items())
    )
    report_unenforced(
        "doc_read_path",
        count,
        f"{parts} - they are checked when you next change them, and `check --all` audits them now",
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


# --- check --contract: the claims about an artifact's frontmatter ---------------------------------
#
# `check --sources` guards ONE direction: a read this table removed cannot come back. This guards
# the other two, and it is the general form of the defect issue #29 was filed for - documentation
# making a claim nothing enforces. Three claims, all mechanical:
#
#   C1  every entry's `emitted_by` names a file that EXISTS. A declared writer instruction that is
#       not on disk is fiction, and nothing else would ever notice.
#   C2  that file actually INSTRUCTS the `readers:` line. Declaring a reader in this table is not
#       the same as telling anyone to write it down: three document classes shipped with the first
#       and not the second, which is what put `emitted_by` in the table in the first place. A
#       markdown template must carry the line in its own frontmatter, not merely mention it in
#       prose - a template that only talks about `readers:` teaches a document that fails
#       `check_artifacts`. Any OTHER markdown emitter - an agent definition, a skill - must show it
#       inside a fenced block, the shape it tells its writer to produce (`instructs_readers`).
#       **What C2 does not decide, said rather than implied**: for a NON-markdown emitter, a `.py`
#       or a `.sh` that writes the key in code, it confirms the key appears somewhere in the file
#       and NOT where, because there is no shipped shape to point at and locating a dict key by
#       parsing arbitrary code buys less than it costs.
#   C3  the INVERSE, which nothing checked before: an artifact class a canonical source names is
#       one this table governs. A stage told to write, link or read a document the read path has no
#       entry for is a class with no decision recorded either way - `fidelity-report.md`,
#       `implementation-report.md`, `test-execution-report.md` and `scoped/review-<slice>.md` sat in
#       exactly that state, one of them (`implementation-report.md`) load-bearing for a Stop hook.
#
# All three are asked in the CANONICAL REPOSITORY ONLY (`is_canonical_repo`), and there they are NOT
# diff-scoped, for the same reason `--sources` and `stage_effort.py check` are not: canonical stage
# instructions and the table itself are always open to change, never shipped artifacts a later rule
# would hold hostage.
#
# What C3 does NOT see, said rather than implied. It reads two inventories - artifact PATH literals
# under `docs/features/`, and the canonical layout block in `skills/pipeline-conventions`. So a class
# named only as a bare filename in prose is invisible to it: `fidelity-report.md` was named exactly
# that way, in two artifact lists and nowhere else, so this check would not have caught it; it was
# caught by reading. Generalising to bare filenames means guessing which backticked `*.md` in a
# sentence is a pipeline artifact, which needs a denylist of everything else in the repository - a
# list that rots, which is worse than a stated limit. And a class named only OUTSIDE the directories
# in `CONTRACT_SOURCE_DIRS` is invisible too. Both are the same trade, a stated limit over a guess.

#: Where a stage instruction or a template may name an artifact path. These are read only in the
#: canonical repository (`is_canonical_repo`), where every one of them is the pipeline's own file
#: and always open to change - which is what keeps this check honestly non-diff-scoped. `scripts/`,
#: `README.md` and `CLAUDE.md` are deliberately absent even there: they are the CONSUMER's code,
#: readme and instructions in a vendored tree, and a boundary that holds in only one of the two
#: trees is the boundary that keeps being defeated.
CONTRACT_SOURCE_DIRS = SOURCE_DIRS + ("docs/templates",)
CONTRACT_SOURCE_FILES = ("AGENTS.md",)
CONTRACT_SOURCE_SUFFIXES = (".md", ".json", ".py", ".sh")

#: A path literal pointing into the artifact tree. `<feature>`, `<n>-<slug>` and `*` are template
#: placeholders the sources write literally, so they are part of the character class.
ARTIFACT_PATH_RE = re.compile(r"docs/features/[A-Za-z0-9_<>*./-]+")

#: A `<...>` placeholder segment, stripped before deciding whether a filename names anything real.
PLACEHOLDER_RE = re.compile(r"<[^>]*>|\*")

#: The file that says this tree is the pipeline's own repository. Chosen because `scripts/install.sh`
#: SRC_SETS does not vendor it, so no consumer repo acquires it by installing the pipeline.
CANONICAL_MARKER = "scripts/sync_opencode.py"

#: What a non-canonical tree is told instead of a verdict. One line, both directions, naming where
#: the remedy lives, because an absent dimension that reports nothing reads exactly like a pass.
NOT_CHECKED = (
    "[doc_read_path] frontmatter contract: NOT CHECKED under {root} - neither the `emitted_by` "
    "direction nor the artifact-class direction ran, because this tree is not the canonical "
    "pipeline repository ({marker} is absent, and `scripts/install.sh` does not vendor it). Every "
    "source this check reads lives upstream, so the remedy for anything it could find lives in "
    "agentic-avengers and not here. This is not a pass."
)

#: The other inventory: the fenced tree under `- **Layout:**` in the canonical rulebook.
LAYOUT_SOURCE = "skills/pipeline-conventions/SKILL.md"
LAYOUT_HEADING = "**Layout:**"
LAYOUT_NAME_RE = re.compile(r"[A-Za-z0-9_<>*.-]+\.(?:md|json)")

#: Tokens that mean a source instructs the declaration. JSON and Python write the key; markdown and
#: YAML frontmatter write the line.
READERS_TOKENS = ("readers:", '"readers"', "'readers'")

#: A markdown fence, opening or closing. A stage that instructs a writer to produce a document ships
#: that document's SHAPE inside one - YAML frontmatter for a markdown artifact, a JSON object for a
#: JSON one - so a fence is where C2 looks in a markdown emitter, and prose outside every fence does
#: not count. See `instructs_readers`.
FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~)")


def _contract_sources(root: Path) -> list[Path]:
    """Every canonical file that may name an artifact, in a stable order."""
    found: list[Path] = []
    for name in CONTRACT_SOURCE_FILES:
        path = root / name
        if path.is_file():
            found.append(path)
    for source_dir in CONTRACT_SOURCE_DIRS:
        base = root / source_dir
        if not base.is_dir():
            continue
        found += [
            path
            for path in sorted(base.rglob("*"))
            if path.is_file() and path.suffix in CONTRACT_SOURCE_SUFFIXES
        ]
    return found


def is_canonical_repo(root: Path) -> bool:
    """Whether this tree is the pipeline's OWN repository rather than a repo that installed it.

    **`check --contract` runs here and nowhere else, in BOTH directions.** Every source it reads
    lives upstream: `READ_PATH` itself, the templates it names, and the canonical stage instructions
    it scans. So downstream there is no remedy for anything it could find - not one of the three
    outcomes it prescribes is available to a repository that merely installed the pipeline - and a
    rule whose remedy is unavailable is a wedge rather than a gate.

    Narrowing it once per direction was tried and is what this ends. `scripts/install.sh` SRC_SETS
    vendors `skills/`, `prompts/`, `docs/templates/`, `AGENTS.md` and part of `scripts/`, and
    neither `agents/` nor `commands/`, so in a consumer repo those two hold that project's own files
    and `skills/` holds theirs beside the vendored ones. Asked there, C1 reports every agent-emitted
    entry as a broken declaration and C3 reports the consumer's own documents as undecided classes,
    and `gate_ci.sh` runs this with no diff scope, no `--all` escape and no exception ledger, so
    that repository fails every commit and every CI run from installation onwards. **A half-running
    check is worse than an honestly absent one**, because a result that means one thing here and
    another there cannot be read as meaning anything.

    The marker is a file `install.sh` deliberately does not vendor, NOT the presence of a directory
    named `agents/`. Keying on the name was the first attempt and it is defeated by any consumer
    that owns a top-level `agents/` of its own, which is ordinary in this kind of project.
    `scripts/sync_opencode.py` is the canonical-source tooling of §7, it exists only where canonical
    sources are edited, and `tests/test_frontmatter_contract.py` pins it as absent from SRC_SETS so
    a future vendoring change cannot turn this marker into a silent no-op.

    What this does NOT decide: it says nothing about `check` or `check --sources`, which read
    artifacts and stage instructions this repository is responsible for wherever it runs.
    """
    return (root / CANONICAL_MARKER).is_file()


def fenced_blocks(text: str) -> list[str]:
    """The body of every fenced code block, which is where a stage ships a document's shape.

    Unclosed fences are tolerated: the last block simply runs to the end of the file. The fence's
    info string (` ```markdown `, ` ```json `) is deliberately not read - what matters is that the
    declaration sits in the shipped shape rather than in a sentence about it.
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if FENCE_RE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current))
    return blocks


def instructs_readers(text: str) -> bool:
    """Whether a markdown emitter really INSTRUCTS `readers:`, rather than talking about it.

    C2's whole claim is that the declared writer instruction produces the line, and for a markdown
    emitter that is only true when the declaration is in the shape the writer is told to copy. A
    bare substring test over the file is satisfied by any nearby sentence, which is how the
    `review-<slice>.md` instruction this table added could be deleted from its own output block with
    the check that exists to catch that removal staying green: two lines below it a sentence reads
    "`readers:` is not decoration", and the token is in it.
    """
    return any(
        token in block for block in fenced_blocks(text) for token in READERS_TOKENS
    )


def _emitter_problems(root: Path) -> list[str]:
    """C1 and C2: the table's declared writer instruction exists, and it instructs the line."""
    problems: list[str] = []
    for name, spec in READ_PATH.items():
        emitter = spec.get("emitted_by")
        if not emitter:
            problems.append(
                f"`{name}` declares its readers in READ_PATH and names no `emitted_by` - a "
                f"declared reader nobody is instructed to write down is a promise with no "
                f"mechanism, which is the gap `emitted_by` exists to close."
            )
            continue
        path = root / emitter
        if not path.is_file():
            problems.append(
                f"`{name}` names `{emitter}` as the source that makes its writer declare "
                f"`readers:`, and that file does not exist."
            )
            continue
        text = _read(path)
        if emitter.startswith("docs/templates/") and emitter.endswith(".md"):
            if declared_readers(text) is None:
                problems.append(
                    f"{emitter} is the template for `{name}` and carries no `readers:` in its own "
                    f"frontmatter. A template that mentions the line only in prose teaches a "
                    f"document that fails `check` - authored exactly as instructed."
                )
            continue
        if emitter.endswith(".md"):
            if not instructs_readers(text):
                problems.append(
                    f"{emitter} is where `{name}`'s writer is told to declare `readers:`, and no "
                    f"fenced block in it shows the line. Prose ABOUT `readers:` is not an "
                    f"instruction to write it: put the declaration inside the block that ships the "
                    f"document's shape, or stop declaring readers for `{name}` in READ_PATH."
                )
            continue
        if not any(token in text for token in READERS_TOKENS):
            problems.append(
                f"{emitter} is where `{name}`'s writer is told to declare `readers:`, and it "
                f"never says so. Either instruct it there, or stop declaring readers for "
                f"`{name}` in READ_PATH."
            )
    return problems


def layout_inventory(root: Path) -> set[str]:
    """The artifact filenames listed in the canonical layout block, or an empty set if absent.

    The second inventory, and a deliberately different KIND of one: the path scan below reads what
    stages are told to write, this reads what the rulebook says the tree contains. A class can be
    listed in the layout and instructed nowhere - `scoped/review-<slice>.md` was - so neither
    inventory subsumes the other.
    """
    path = root / LAYOUT_SOURCE
    if not path.is_file():
        return set()
    lines = _read(path).splitlines()
    heading = next((i for i, line in enumerate(lines) if LAYOUT_HEADING in line), None)
    if heading is None:
        return set()
    fence = next(
        (
            i
            for i in range(heading + 1, len(lines))
            if lines[i].strip().startswith("```")
        ),
        None,
    )
    if fence is None:
        return set()
    names: set[str] = set()
    for line in lines[fence + 1 :]:
        if line.strip().startswith("```"):
            break
        names.update(LAYOUT_NAME_RE.findall(line))
    return names


def names_a_class(base: str) -> bool:
    """Whether a filename names an artifact CLASS at all, rather than matching a set of them.

    `ARTIFACT_PATH_RE` admits `*` and `<...>` because the sources write those placeholders
    literally, and a class like `scoped/review-<slice>.md` is only nameable with one. But a base
    whose whole stem is placeholder - `*.md` out of `docs/features/**/*.md` - is a glob, and a glob
    is not a class: reported as one it prescribes three remedies for a document that does not exist.
    A placeholder is tolerated only where a real stem resolves it.
    """
    stem = base.rsplit(".", 1)[0]
    return bool(PLACEHOLDER_RE.sub("", stem).strip(" -_."))


def _named_artifacts(root: Path) -> dict[str, list[str]]:
    """Every artifact filename a canonical source names, mapped to where it named it."""
    named: dict[str, list[str]] = {}
    for path in _contract_sources(root):
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for match in ARTIFACT_PATH_RE.finditer(line):
                base = match.group(0).rstrip("./").split("/")[-1]
                if not base.endswith((".md", ".json")) or not names_a_class(base):
                    continue
                named.setdefault(base, []).append(f"{rel}:{lineno}")
    for base in sorted(layout_inventory(root)):
        if names_a_class(base):
            named.setdefault(base, []).append(LAYOUT_SOURCE)
    return named


def _governed_problems(root: Path) -> list[str]:
    """C3: an artifact class a canonical source names is one this table governs."""
    problems: list[str] = []
    for base, sites in sorted(_named_artifacts(root).items()):
        if spec_for(base) is not None:
            continue
        where = ", ".join(sites[:4]) + (
            f" (+{len(sites) - 4} more)" if len(sites) > 4 else ""
        )
        problems.append(
            f"`{base}` is named by a canonical source and READ_PATH has no entry for it, so no "
            f"stage is declared to read it and nothing makes its writer say who does. Named at: "
            f"{where}. It has exactly three honest outcomes: give it a READ_PATH entry with real "
            f"readers, declare `readers: none (<why it still exists>)` the way an archive does, or "
            f"stop writing it and remove what names it. Guessing is what issue #29 exists to stop."
        )
    return problems


def check_contract(root: Path) -> list[str]:
    """Every documented claim about an artifact's frontmatter has a writer instructed to make it.

    Asked only in the canonical repository (`is_canonical_repo`, where the reason is stated).
    Elsewhere NEITHER direction runs and that is said out loud rather than returning an empty list
    a caller would read as a pass.
    """
    if not is_canonical_repo(root):
        print(NOT_CHECKED.format(root=root, marker=CANONICAL_MARKER), file=sys.stderr)
        return []
    return _emitter_problems(root) + _governed_problems(root)


# --- table ---------------------------------------------------------------------------------------


def render_table() -> str:
    lines = ["| document | written by | read by | extent |", "|---|---|---|---|"]
    for name, spec in READ_PATH.items():
        readers = (
            "<br>".join(spec["readers"])
            or f"**nobody** (archive of `{spec.get('archive_of')}`)"
        )
        lines.append(
            f"| `{name}` | {spec['written_by']} | {readers} | {spec['extent']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="validate artifacts on disk (and, with --sources, the stage instructions)",
    )
    check.add_argument(
        "root", nargs="?", default=".", help="repository root (default: .)"
    )
    check.add_argument(
        "--sources",
        action="store_true",
        help="also check the canonical stage instructions",
    )
    check.add_argument(
        "--sources-only", action="store_true", help="check only the stage instructions"
    )
    check.add_argument(
        "--contract",
        action="store_true",
        help="also check that every documented claim about an artifact's frontmatter has a writer "
        "instructed to produce it, and that every artifact class a canonical source names is "
        "one this table governs",
    )
    check.add_argument(
        "--contract-only",
        action="store_true",
        help="check only the frontmatter contract",
    )
    check.add_argument(
        "--all",
        action="store_true",
        dest="enforce_all",
        help="enforce every artifact, not only the ones the current diff touches (full audit)",
    )

    sub.add_parser("table", help="print the read path as a markdown table")

    canonical = sub.add_parser(
        "canonical",
        help="exit 0 when this tree is the canonical pipeline repository, 1 when it is not",
    )
    canonical.add_argument(
        "root", nargs="?", default=".", help="repository root (default: .)"
    )

    args = parser.parse_args(argv)
    if args.command == "table":
        print(render_table())
        return 0
    if args.command == "canonical":
        return 0 if is_canonical_repo(Path(args.root).resolve()) else 1

    root = Path(args.root).resolve()
    only = args.sources_only or args.contract_only
    problems: list[str] = []
    if not only:
        problems += check_artifacts(root, enforce_all=args.enforce_all)
    if args.sources or args.sources_only:
        problems += check_sources(root)
    if args.contract or args.contract_only:
        problems += check_contract(root)

    if problems:
        print("read-path violations:", file=sys.stderr)
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        return VIOLATIONS
    print("[doc_read_path] clean")
    return CLEAN


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
