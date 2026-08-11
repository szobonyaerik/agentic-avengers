"""Tests for the document read path.

The defect this check exists for is not a big file. It is a *small* file read on a schedule nobody
costed: `task-analysis.md` at 31 KB cost ~465k tokens by being opened 60 times for one frontmatter
field, and `handover.md` cost 485k-1,475k by being re-read for every spec of every later phase. The
template asked for a ≤5-line summary the whole time and writers produced 37 KB averages — an
enforcement gap, not a template gap.

So the dangerous direction here is a MISS in two places, and both get pinned below:

  * an artifact that grows past its cap or declares no reader, and
  * a stage instruction that quietly re-acquires a read that was removed. That is how the cost came
    back last time — one caller at a time — so the guard is attached to the invariant, and the last
    test in this file runs it against the real repository.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from doc_read_path import (  # noqa: E402
    HANDOVER_MAX_BYTES,
    READ_PATH,
    VERDICT_REPORT_MAX_CHARS,
    check_artifacts,
    check_sources,
    declared_readers,
    main,
    render_table,
    spec_for,
)

REPO = Path(__file__).resolve().parents[1]


def audit(root: Path) -> list[str]:
    """Every artifact, whatever the diff touched - what `check --all` does.

    The default is diff-scoped and answers to git, so the cases below that are about the RULE pass
    --all; the scoping itself is pinned against real repositories in test_doc_read_path_scope.py.
    """
    return check_artifacts(root, enforce_all=True)

CARD_READERS = "avenger-spec-writer @ per spec (prior cards); spec-review @ the immediately prior card"


def phase_dir(root: Path) -> Path:
    d = root / "docs" / "features" / "demo" / "phases" / "1-webhook"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_card(root: Path, body: str, readers: str = CARD_READERS) -> Path:
    path = phase_dir(root) / "handover.md"
    path.write_text(f"---\nfeature: demo\nphase: 1-webhook\nreaders: {readers}\n---\n{body}\n")
    return path


# --- the cap -------------------------------------------------------------------------------------

def test_a_card_within_the_cap_is_clean(tmp_path: Path) -> None:
    write_card(tmp_path, "delivered the receiver; phase 2 reads delivery_id.")
    assert audit(tmp_path) == []


def test_a_card_over_the_cap_is_a_violation(tmp_path: Path) -> None:
    write_card(tmp_path, "x" * (HANDOVER_MAX_BYTES + 1))
    problems = audit(tmp_path)
    assert len(problems) == 1
    assert "over the" in problems[0] and "cap" in problems[0]
    # It names the relocation, not a deletion — nothing leaves disk.
    assert "handover-archive.md" in problems[0]


def test_the_cap_is_bytes_not_lines(tmp_path: Path) -> None:
    """A 5-line summary of very long lines is still over budget — the bill is bytes read."""
    write_card(tmp_path, "\n".join(["y" * 2000] * 5))
    assert audit(tmp_path)


# --- the readers declaration ---------------------------------------------------------------------

def test_a_document_that_declares_no_reader_is_a_violation(tmp_path: Path) -> None:
    (phase_dir(tmp_path) / "handover.md").write_text("---\nfeature: demo\n---\nshort\n")
    problems = audit(tmp_path)
    assert len(problems) == 1
    assert "readers:" in problems[0]


def test_a_document_with_no_frontmatter_at_all_is_a_violation(tmp_path: Path) -> None:
    (phase_dir(tmp_path) / "handover.md").write_text("# Phase 1\n\nshort\n")
    assert audit(tmp_path)


def test_an_archive_declares_none_and_is_accepted(tmp_path: Path) -> None:
    """`readers: none` is the point, not an escape hatch — an archive has to say so out loud."""
    (phase_dir(tmp_path) / "handover-archive.md").write_text(
        "---\nfeature: demo\nreaders: none (archive of handover.md)\n---\n" + "z" * 40000
    )
    assert audit(tmp_path) == []


def test_an_archive_is_not_capped(tmp_path: Path) -> None:
    """Nothing is deleted. The archive holds the record; it is simply off the read path."""
    write_card(tmp_path, "short")
    (phase_dir(tmp_path) / "handover-archive.md").write_text(
        "---\nreaders: none (archive of handover.md)\n---\n" + "z" * (HANDOVER_MAX_BYTES * 10)
    )
    assert audit(tmp_path) == []


def test_declared_readers_reads_only_frontmatter() -> None:
    assert declared_readers("---\nreaders: a @ once\n---\nbody\n") == "a @ once"
    assert declared_readers("body only\nreaders: a @ once\n") is None
    assert declared_readers("---\nreaders:\n---\nbody\n") is None


# --- verdict.json --------------------------------------------------------------------------------

def verdict(root: Path, payload: dict, name: str = "verdict.json") -> Path:
    path = phase_dir(root) / name
    path.write_text(json.dumps(payload))
    return path


def test_a_verdict_declares_readers_as_a_top_level_key(tmp_path: Path) -> None:
    verdict(tmp_path, {"verdict": "pass"})
    problems = audit(tmp_path)
    assert len(problems) == 1 and "readers" in problems[0]

    verdict(tmp_path, {"verdict": "pass", "readers": ["phase-handover @ per phase"]})
    assert audit(tmp_path) == []


def test_a_verdict_report_over_the_cap_is_a_violation(tmp_path: Path) -> None:
    verdict(tmp_path, {"readers": [], "report": "p" * (VERDICT_REPORT_MAX_CHARS + 1)})
    problems = audit(tmp_path)
    assert len(problems) == 1 and "report" in problems[0]


def test_a_verdict_report_within_the_cap_is_clean(tmp_path: Path) -> None:
    verdict(tmp_path, {"readers": [], "report": "p" * VERDICT_REPORT_MAX_CHARS})
    assert audit(tmp_path) == []


def test_an_archived_attempt_is_matched_by_its_numbered_name(tmp_path: Path) -> None:
    assert spec_for("verdict-attempt-3.json") is not None
    assert spec_for("verdict-attempt-3.json")["archive_of"] == "verdict.json"
    verdict(tmp_path, {"report": "x" * 99999}, name="verdict-attempt-1.json")
    problems = audit(tmp_path)
    # Missing `readers` is a violation; the report cap is NOT applied to the archive, which nothing
    # reads. Relocating prose is the whole mechanism — capping it there would delete it instead.
    assert len(problems) == 1 and "readers" in problems[0]


def test_unparseable_json_fails_closed(tmp_path: Path) -> None:
    (phase_dir(tmp_path) / "verdict.json").write_text("{not json")
    problems = audit(tmp_path)
    assert len(problems) == 1 and "fail closed" in problems[0]


# --- absent tree ---------------------------------------------------------------------------------

def test_an_absent_docs_tree_scans_nothing_and_says_so(tmp_path: Path, capsys) -> None:
    assert check_artifacts(tmp_path) == []
    assert "nothing to check" in capsys.readouterr().err


# --- the recurrence guard ------------------------------------------------------------------------

def stage_file(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_a_stage_that_re_acquires_a_removed_read_is_caught(tmp_path: Path) -> None:
    stage_file(tmp_path, "agents/avenger-backend-architect.md", "Read `task-analysis.md` for work_kind.\n")
    problems = check_sources(tmp_path)
    assert len(problems) == 1
    assert "task-analysis.md" in problems[0]
    # It routes the fix to the invariant, not to this one caller — the failure mode the check exists
    # for is a guard bolted onto one command that the next command does not have.
    assert "doc_read_path.py" in problems[0] and "not this one caller" in problems[0]


def test_the_allowlisted_writer_may_still_name_its_own_document(tmp_path: Path) -> None:
    stage_file(tmp_path, "agents/avenger-task-analyst.md", "Write `task-analysis.md`.\n")
    assert check_sources(tmp_path) == []


def test_a_stage_taught_to_open_an_archive_is_caught(tmp_path: Path) -> None:
    stage_file(tmp_path, "commands/spec-review.md", "Also read `handover-archive.md` for context.\n")
    problems = check_sources(tmp_path)
    assert len(problems) == 1 and "handover-archive.md" in problems[0]


def test_a_stage_naming_a_superseded_attempt_is_caught(tmp_path: Path) -> None:
    stage_file(tmp_path, "skills/x/SKILL.md", "Read every verdict-attempt-2.json in the phase.\n")
    problems = check_sources(tmp_path)
    assert len(problems) == 1 and "verdict-attempt-" in problems[0]


def test_only_the_canonical_stage_dirs_are_scanned(tmp_path: Path) -> None:
    """README and CLAUDE.md legitimately inventory artifacts; they are not stage instructions."""
    stage_file(tmp_path, "README.md", "task-analysis.md handover-archive.md\n")
    stage_file(tmp_path, "docs/USAGE.md", "task-analysis.md\n")
    assert check_sources(tmp_path) == []


def test_the_real_repository_obeys_its_own_read_path() -> None:
    """The regression guard itself. If a future edit re-adds one of these reads, this goes red."""
    assert check_sources(REPO) == []


# --- CLI -----------------------------------------------------------------------------------------

def test_table_lists_every_document_and_marks_the_archives() -> None:
    table = render_table()
    for name in ("task-analysis.md", "handover.md", "handover-archive.md", "test-evidence.md"):
        assert f"`{name}`" in table
    assert "**nobody**" in table


def test_cli_returns_one_on_a_violation_and_zero_when_clean(tmp_path: Path) -> None:
    write_card(tmp_path, "x" * (HANDOVER_MAX_BYTES + 1))
    assert main(["check", "--all", str(tmp_path)]) == 1
    write_card(tmp_path, "short")
    assert main(["check", "--all", str(tmp_path)]) == 0
    assert main(["table"]) == 0


# --- the documented promises are the code's actual behaviour --------------------------------------
# A doc asserting behaviour is a promise. These pin the three places this PR states a number in
# prose, so a later edit to the constant cannot leave the documentation quietly lying.

def test_the_shipped_card_template_fits_the_cap_it_teaches() -> None:
    template = REPO / "docs" / "templates" / "handover.template.md"
    assert template.stat().st_size <= HANDOVER_MAX_BYTES, (
        "the handover template — instructional comments and all — must fit under the cap it asks "
        "writers to respect"
    )


# The file set here is DERIVED, never pinned. A pinned list is a guard attached to a list, and a list
# omits things: the first version of this test named five paths and missed `README.md` and
# `docs/USAGE.md`, both of which hand-copy the cap — so changing the constant would have left two
# user-facing documents quietly lying while this test stayed green. The invariant is "any file that
# states a cap states the real one", so the test finds the files by their claim.

DOC_SURFACE_ROOTS = ("docs", "skills", "agents", "commands", "prompts")
DOC_SURFACE_FILES = ("README.md", "CLAUDE.md", "AGENTS.md", "AVENGERS.md")

# A number presented AS one of these caps — not a bare digit run anywhere in a file. The character
# pattern is additionally anchored to `report`, because this repo states two other char counts that
# are legitimately different numbers: VERIFIER_SRC_LIMIT ("400000 chars") and a measured size
# ("12,991 characters"). A drift check that flagged those would be noise, and noise gets deleted.
# `(?<![\d,])` keeps a thousands-separated measurement ("12,991 characters") from matching on its
# tail; the char pattern additionally requires a CAP verb, so a stated measurement or a different
# limit is not mistaken for this one.
CAP_VERB = r"(?:capped at|caps[^.\n]{0,24}?at|cap of|under|<=|at most|no more than)\s*\**"
BYTE_CAP_RE = re.compile(r"(?<![\d,])(\d{3,6})[- ](?:byte|bytes)\b", re.IGNORECASE)
CHAR_CAP_RE = re.compile(
    CAP_VERB + r"(?<![\d,])(\d{3,6})[- ](?:char|chars|characters)\b", re.IGNORECASE
)

# The same cap is also stated in KB prose, and the byte pattern above cannot see any of it — six
# shipped files said "6 KB" and would have gone silently stale on a change to the constant, which is
# the exact failure the derived list exists to end. KB needs a tighter anchor than bytes do: these
# documents are full of KB MEASUREMENTS that are legitimately different numbers ("31 KB", "272 KB",
# "990 KB", "37 KB averages", "at 34 KB each", "53.6 KB"), and a check that flagged those would be
# noise — noise gets deleted rather than heeded. So a KB figure counts only when it is presented AS
# this cap, in one of the three ways the shipped files present it:
#   1. behind a cap verb          — "capped at 6 KB"
#   2. in front of a cap noun     — "the 6 KB cap", "the 6 KB contract card"
#   3. opening a sentence with it — "At 6 KB it is affordable to read all of them."
# Branch 3 is case-SENSITIVE on purpose: sentence-initial "At 6 KB" states the cap, while mid-sentence
# "at 34 KB each" reports a measurement.
KB_NUM = r"(?<![\d,.])(\d{1,4}(?:\.\d+)?)\s?KB\b"
KB_CAP_RE = re.compile(
    "|".join((
        CAP_VERB + KB_NUM,
        KB_NUM + r"\s+(?:cap\b|contract card\b)",
        r"(?-i:At)\s" + KB_NUM,
    )),
    re.IGNORECASE,
)


def doc_surface() -> list[Path]:
    """Every tracked prose file that could state a cap."""
    found = [REPO / name for name in DOC_SURFACE_FILES]
    for root in DOC_SURFACE_ROOTS:
        found += sorted((REPO / root).rglob("*.md"))
    return [p for p in found if p.is_file()]


def stated_caps(pattern: re.Pattern[str]) -> list[tuple[Path, float]]:
    """Every stated cap on the doc surface, as (file, number). One group per pattern branch."""
    return [
        (path, float(next(g for g in m.groups() if g is not None)))
        for path in doc_surface()
        for m in pattern.finditer(path.read_text(encoding="utf-8"))
    ]


def test_every_document_stating_a_byte_cap_states_the_real_one() -> None:
    wrong = [(str(p.relative_to(REPO)), n) for p, n in stated_caps(BYTE_CAP_RE)
             if n != HANDOVER_MAX_BYTES]
    assert not wrong, (
        f"these documents state a byte cap that is not HANDOVER_MAX_BYTES={HANDOVER_MAX_BYTES}: "
        f"{wrong}. Change the constant and this test names every document that drifted — no list to "
        f"remember to update."
    )


def test_every_document_stating_a_char_cap_states_the_real_one() -> None:
    wrong = [(str(p.relative_to(REPO)), n) for p, n in stated_caps(CHAR_CAP_RE)
             if n != VERDICT_REPORT_MAX_CHARS]
    assert not wrong, (
        f"these documents state a character cap that is not "
        f"VERDICT_REPORT_MAX_CHARS={VERDICT_REPORT_MAX_CHARS}: {wrong}"
    )


def test_every_document_stating_the_cap_in_kb_states_the_real_one() -> None:
    expected_kb = HANDOVER_MAX_BYTES / 1024
    wrong = [(str(p.relative_to(REPO)), n) for p, n in stated_caps(KB_CAP_RE) if n != expected_kb]
    assert not wrong, (
        f"these documents state the handover cap in KB, and it is not "
        f"HANDOVER_MAX_BYTES={HANDOVER_MAX_BYTES} ({expected_kb} KB): {wrong}. Prose that states the "
        f"cap in KB drifts exactly as silently as a byte literal does."
    )


def test_the_kb_scan_reaches_every_file_that_states_the_cap_in_prose() -> None:
    """The KB claims are phrased three different ways; a scan that finds only one phrasing is the bug."""
    covered = {str(p.relative_to(REPO)) for p, _ in stated_caps(KB_CAP_RE)}
    assert {
        "commands/spec-review.md",
        "agents/avenger-spec-writer.md",
        "skills/e2e-author/SKILL.md",
        "skills/phase-handover/SKILL.md",
        "docs/AUTOMATE.md",
        "docs/templates/handover-archive.template.md",
    } <= covered, f"the KB scan no longer reaches every file stating the cap in prose: {sorted(covered)}"


def test_the_kb_scan_does_not_flag_measurements() -> None:
    """A drift check that cries wolf gets deleted. KB figures that report a SIZE are not caps."""
    measured = {
        "handover.md held 272 KB and cost",
        "writers produced 37 KB averages",
        "prior phases' cards; at 34 KB each that was",
        "a well-organised 30 KB document that no one will read",
        "prose across 53.6 KB of Open Items",
        "`task-analysis.md` is 31 KB and trivial",
        "one measured feature carried 285 KB",
        "130 KB of which 83 KB (64%) was superseded",
    }
    flagged = [text for text in measured if KB_CAP_RE.search(text)]
    assert not flagged, f"these are measurements, not statements of the cap: {flagged}"


def test_the_documents_required_to_state_a_cap_still_do() -> None:
    """The derived check above catches a STALE number; this catches a MISSING one."""
    for rel in ("skills/phase-handover/SKILL.md", "skills/pipeline-conventions/SKILL.md",
                "CLAUDE.md", "AGENTS.md", "skills/ponytail/SKILL.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert str(HANDOVER_MAX_BYTES) in text, f"{rel} must state the handover cap"
    for rel in ("skills/verifier-triage/SKILL.md", "agents/avenger-verifier.md", "CLAUDE.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert str(VERDICT_REPORT_MAX_CHARS) in text, f"{rel} must state the report cap"


def test_the_derived_check_finds_the_hand_copied_literals() -> None:
    """Pins the omission that motivated this: README and USAGE hand-copy the cap and are covered."""
    covered = {str(p.relative_to(REPO)) for p, _ in stated_caps(BYTE_CAP_RE)}
    assert {"README.md", "docs/USAGE.md"} <= covered, (
        f"the derived scan no longer reaches the files that hand-copy the cap: {sorted(covered)}"
    )


# --- the declaration has an owner ------------------------------------------------------------------
# The class of bug pinned here: the table declares who reads a document, and nothing tells that
# document's WRITER to say so. Three classes shipped that way — pipeline-observations.md,
# e2e-mapping.md and test-mapping.md — each an artifact that fails the check above when authored
# exactly as the pipeline instructs. A fourth goes red here instead of reaching a reviewer.

READERS_TOKENS = ("readers:", '"readers"')


def test_every_document_names_the_source_that_makes_its_writer_declare_readers() -> None:
    for name, spec in READ_PATH.items():
        emitter = spec.get("emitted_by")
        assert emitter, f"{name} declares its readers in the table but names no source that emits them"
        path = REPO / emitter
        assert path.is_file(), f"{name} names a missing emitter: {emitter}"
        text = path.read_text(encoding="utf-8")
        assert any(token in text for token in READERS_TOKENS), (
            f"{emitter} is where {name}'s writer is told to declare `readers:`, and it never says "
            f"so — a document written exactly as instructed would fail check_artifacts"
        )


def test_every_shipped_markdown_template_declares_readers_in_its_own_frontmatter() -> None:
    """A template that mentions `readers:` only in prose teaches a document that fails the check."""
    for name, spec in READ_PATH.items():
        emitter = spec.get("emitted_by", "")
        if not (emitter.startswith("docs/templates/") and emitter.endswith(".md")):
            continue
        text = (REPO / emitter).read_text(encoding="utf-8")
        assert declared_readers(text), f"{emitter} ({name}) carries no `readers:` in its frontmatter"
