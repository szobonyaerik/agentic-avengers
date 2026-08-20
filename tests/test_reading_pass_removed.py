"""The cross-family reading pass is gone, and nothing quietly grows back in its place.

## Why this file exists

The pass handed a bounded set of a phase's tests to a model on another vendor's family and asked it
to find tautological, implementation-coupled and missing-negative patterns. It **returned GO with
zero findings on a phase that contained real defects**, and the hypothesis testing whether it earned
its cost came back unmeasured. It was removed.

A deletion is not a decision until something keeps it deleted. Nothing here checks that the pass was
a bad idea - that judgement is in the PR and in `CLAUDE.md` §4. What it checks is the failure mode
this pipeline keeps producing: a rule written down and enforced by nothing. Two specific shapes:

* **the machinery coming back** - the scripts, the prompt, the env vars, the verdict field;
* **the gap being papered over** - a stage instructed to do the reading itself, which is same-family
  self-review wearing the removed gate's name, since every subagent here is Anthropic.

The uncovered surface is named in the pipeline's own documents rather than left implied, and that is
asserted too: an honest gap stated out loud is the thing that stops it being rediscovered as a
surprise.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: What the pass WAS. Each of these is a file that must not exist again under this name.
REMOVED_FILES = (
    "scripts/verifier_review.sh",
    "scripts/verifier_review_check.py",
    "scripts/verifier_bundle_scope.py",
    "prompts/verifier-review.md",
)

#: What configured it. An env var nothing reads is a promise with no mechanism - which is how a
#: removed gate comes back as documentation first and code second.
REMOVED_ENV = ("VERIFIER_GATE_MODEL", "VERIFIER_SRC_LIMIT", "VERIFIER_SCOPE")

#: The verdict field that recorded it. `test_quality.reviewed` was a boolean the verifying agent
#: wrote about itself; `execution` replaced it, at the schema.
REMOVED_VERDICT_FIELD = "test_quality"

#: Where a stage could be told to do the reading itself. Canonical instructions only - these are the
#: documents a stage actually runs on.
INSTRUCTION_DIRS = ("agents", "skills", "commands", "prompts")

#: Files that are allowed to NAME the removed pass, because their job is to explain that it is gone.
#: A history that cannot be written down is a decision nobody can audit later.
#:
#: This only means anything for a file the guards actually OPEN. `CLAUDE.md` and `AGENTS.md` sat here
#: while `shipped_files()` walked neither, so the set read as "scanned and exempted" when the truth
#: was "never scanned" - a guard that appears to cover a file it never opens is worse than one that
#: visibly does not. `governed_files()` below is now the one corpus, and this set does the exempting
#: it always looked like it was doing.
#: Kept as small as it can be. Every entry is a whole FILE waved through, so a stale live claim
#: anywhere in one escapes - `AGENTS.md` sat here and shipped "narrowed to three jobs" past a green
#: suite. A file earns a place only if it states the removal in the affirmative ("the third job WAS
#: X, and it is gone"); a file that states it in the negative is handled by `NEGATED` below and stays
#: governed.
#:
#: The two documents the Verifier itself runs on - `agents/avenger-verifier.md` and
#: `skills/verifier-triage/SKILL.md` - are deliberately NOT here. They sat in this set and matched
#: neither pattern, so they were exempted for nothing while being the likeliest place for the
#: instruction to come back: a planted "read the phase tests for gamed patterns" landed green in
#: both, and red in every other agent definition. They state the removal in the negative, which
#: `NEGATED` already handles, so they earn no place by the rule above.
NARRATIVE = {
    "CLAUDE.md",
    # The historical transformation brief, superseded by pipeline-conventions per its own
    # frontmatter. It records what the pipeline WAS at each step; a stage does not run on it.
    "AVENGERS.md",
    "skills/pipeline-conventions/SKILL.md",
}

#: Where the instruction would be re-acquired if it came back: the documents the Verifier stage
#: actually runs on. Named here so the exemption set cannot quietly grow to cover them again -
#: `offenders_matching` skips a NARRATIVE file whole, and these two must never be skipped.
VERIFIER_STAGE_INSTRUCTIONS = (
    "agents/avenger-verifier.md",
    "skills/verifier-triage/SKILL.md",
)

#: A negation of THIS verb - "**No stage** reads the suite for gamed tests", "**Nothing** reads the
#: suite for gamed tests". Those are the removal being stated, not a stage being instructed, and
#: treating them as offences would force whole files (`README.md`, `docs/AUTOMATE.md`) into
#: NARRATIVE, where a REAL stale claim would then ride along unseen.
#:
#: **ADJACENCY, not proximity.** The first version allowed anything but `. : ;` between the negation
#: and the verb, so a negation belonging to a NEIGHBOURING clause waved a real re-introduction
#: through - including the one written in this repo's own house style, "gamed tests have no dedicated
#: reader, so the Verifier reads the suite for gamed patterns", whose prefix appears verbatim in
#: three shipped documents. The negation must now be the verb's own subject: itself, or one token
#: before it, and that token may not carry a clause break of any kind (a comma ends the clause just
#: as a full stop does). `without` is deliberately NOT in the set - "must without fail read the tests
#: for gamed patterns" is an instruction, and nothing on disk relies on it.
#: Checked against the run of text before the match rather than as a lookbehind, which `re` allows
#: only at fixed width.
NEGATED = re.compile(r"\b(?:no|nothing|never|neither)\b(?:\s+[^\s,;:.]+)?\s*$", re.IGNORECASE)
NEGATION_WINDOW = 60


def shipped_files() -> list[Path]:
    """Every canonical instruction and script, excluding generated and vendored trees."""
    found: list[Path] = []
    for rel in (*INSTRUCTION_DIRS, "scripts"):
        for path in (ROOT / rel).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                found.append(path)
    return found


def governed_files() -> list[Path]:
    """Every document this removal binds - ONE corpus, so the guards cannot disagree about scope.

    `shipped_files()` alone is the canonical stage instructions, and it misses the documents an agent
    most reliably reads: `AGENTS.md` (shipped through install.sh's SRC_SETS), `CLAUDE.md` (the
    project rulebook), `README.md` and `docs/`. Each guard used to widen the corpus for itself or not
    at all, which is how a stale "narrowed to three jobs" survived in `AGENTS.md` with a green suite.
    Root markdown is GLOBBED rather than listed: a corpus that has to be extended by hand is one a
    new document silently escapes.
    """
    seen: dict[str, Path] = {}
    for path in [
        *shipped_files(),
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        ROOT / "docs" / "templates" / "env.example",
    ]:
        if path.is_file() and "__pycache__" not in path.parts:
            seen.setdefault(str(path.relative_to(ROOT)), path)
    return list(seen.values())


def test_the_pass_and_its_machinery_are_gone() -> None:
    for rel in REMOVED_FILES:
        assert not (ROOT / rel).exists(), f"{rel} is back"


def test_nothing_shipped_still_invokes_the_removed_scripts() -> None:
    """A dangling call is worse than the pass itself: it fails closed at the moment of use, in the
    stage least able to do anything about it."""
    offenders = []
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for rel in REMOVED_FILES:
            name = Path(rel).name
            # A prose mention inside a narrative document is fine; an invocation is not.
            if re.search(rf"(?:bash |python3 |sh |\./|\$SD/|\$SCRIPT_DIR/|scripts/){re.escape(name)}",
                         text):
                offenders.append(f"{path.relative_to(ROOT)} invokes {name}")
    assert offenders == [], offenders


def test_the_env_vars_that_configured_it_are_gone_from_every_shipped_document() -> None:
    offenders = []
    for path in governed_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in REMOVED_ENV:
            if name in text:
                offenders.append(f"{path.relative_to(ROOT)} still documents {name}")
    assert offenders == [], offenders


def test_the_verdict_schema_and_its_template_no_longer_carry_the_self_reported_field() -> None:
    """`test_quality.reviewed: true` is the artifact this whole change exists to remove. The schema
    and the template are where a writer would learn to emit it again."""
    import json

    template = json.loads(
        (ROOT / "docs" / "templates" / "verdict.template.json").read_text(encoding="utf-8"))
    # Asserted on the parsed KEYS, not the raw text: the template's own `_comment` narrates what
    # `execution` replaced, and a check that could not tell a field from an explanation of its
    # removal would force the explanation out — which is how a decision loses its reason.
    assert REMOVED_VERDICT_FIELD not in template
    assert "execution" in template and "chain" in template["execution"]

    schema = (ROOT / "skills" / "verifier-triage" / "SKILL.md")
    body = schema.read_text(encoding="utf-8")
    # The narrative explains what it replaced; no line may still SHOW the field being emitted.
    assert f'"{REMOVED_VERDICT_FIELD}"' not in body


#: An instruction to do the reading. **Inflected**: the form a real instruction takes is "it *reads*
#: the phase's tests for gamed patterns" or "*reading* a green suite for gamed tests", and a pattern
#: anchored on the bare stem `read ` matched neither - it passed on two live strings in this very
#: repository while reading as the guard that kept them out. Deliberately loose between the verb and
#: its object: a regex matching only the removed prompt's wording would catch a copy-paste of it,
#: which is the one form nobody would write.
READS_THE_SUITE = re.compile(
    r"read(?:s|ing)?\s+(?:\S+\s+){0,4}?(?:suites?|tests?|test set)\s+for\s+"
    r"(?:gamed|tautological|implementation-coupled)",
    re.IGNORECASE,
)

#: The pass described as a LIVE stage rather than as a removed one. The regex above only sees the
#: verb+object phrasing; the pass was also advertised as a noun - "a bounded test-quality review",
#: "the cross-family reviewer" - in eight shipped documents that outlived the code. A stage
#: instruction describing machinery that does not exist is how an agent rebuilds it by hand.
DESCRIBES_A_LIVE_PASS = re.compile(
    r"(?:bounded\s+)?test[-\s]quality\s+review"
    r"|cross-family\s+review(?:er)?"
    r"|verifier\s+bundle",
    re.IGNORECASE,
)


def offenders_matching(pattern: re.Pattern[str]) -> list[str]:
    """Every governed document matching `pattern`, minus the ones whose job is to explain it."""
    offenders: list[str] = []
    for path in governed_files():
        rel = str(path.relative_to(ROOT))
        if rel in NARRATIVE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - NEGATION_WINDOW):match.start()]
            if not NEGATED.search(before):
                offenders.append(rel)
                break
    return offenders


def test_no_stage_is_instructed_to_do_the_reading_itself() -> None:
    """The gap is left open on purpose. A stage told to read the suite for gamed patterns would be
    same-family self-review under the removed gate's name - every subagent here is Anthropic."""
    assert offenders_matching(READS_THE_SUITE) == []


def test_the_verifier_own_stage_instructions_are_governed_not_exempted() -> None:
    """The exemption is per FILE, so the one it must never cover is the Verifier's own brief.

    Both of these sat in `NARRATIVE` while matching neither pattern - exempted for nothing, and
    exempted exactly where the instruction would be written if it came back. Planting "read the
    phase tests for gamed patterns" in either passed the guard above while failing in every other
    agent definition, which is the whole-file-waved-through failure `NARRATIVE`'s own comment warns
    about. They are governed, and this pins them there."""
    for rel in VERIFIER_STAGE_INSTRUCTIONS:
        assert (ROOT / rel).is_file(), f"{rel} is missing"
        assert rel not in NARRATIVE, (
            f"{rel} is the document the Verifier runs on - exempting it lets the removed "
            f"instruction come back with a green suite"
        )
        assert rel in {str(p.relative_to(ROOT)) for p in governed_files()}, f"{rel} is not scanned"


def test_the_guard_catches_the_inflected_forms_an_instruction_is_actually_written_in() -> None:
    """RED-first, on the two strings that were live in this repository while the guard read green.

    A guard proven only by passing is not proven (issue #69). These are the exact bytes
    `scripts/hook_verifier.sh` shipped, and the previous pattern matched neither.
    """
    for planted in (
        "it traces coverage, reads the phase's tests for gamed patterns, and writes verdict.json.",
        "reading a green suite for gamed tests",
        "read the tests for gamed patterns",
        "Read the phase's test set for tautological assertions",
    ):
        assert READS_THE_SUITE.search(planted), planted
    # And it stays narrow enough to be usable: describing WHAT a gamed test is, is not an
    # instruction to go looking for one.
    for benign in (
        "a gamed test noticed while tracing coverage is still a fail",
        "read the spec for its acceptance criteria",
    ):
        assert not READS_THE_SUITE.search(benign), benign


def test_the_negation_filter_waves_through_the_removal_and_not_an_instruction() -> None:
    """The filter is what keeps `README.md` and `docs/AUTOMATE.md` GOVERNED instead of waved through
    as whole files. It has to be narrow enough that a real instruction cannot hide behind a nearby
    "no" - so the negation must be the reading verb's OWN subject, not one belonging to a
    neighbouring clause.

    The first version tested only two cases and neither crossed a comma, which is exactly why the
    suite was green on a filter that waved three plausible re-introductions through. The false cases
    below are the ones it waved; `instruction_house_style` is the likeliest of them, because its
    prefix is written verbatim in CLAUDE.md, AGENTS.md and README.md, so it is how the next author
    would open the sentence that puts the job back.
    """
    # Both are on disk today - README.md and docs/AUTOMATE.md - and both must stay waved.
    waved = (
        "That means the author of the code also authors its judge. **No stage** reads the suite "
        "for gamed tests.",
        "a pass with no transcript is refused. Nothing reads the suite for gamed tests: that pass "
        "was removed",
    )
    instructions = (
        "gamed tests have no dedicated reader, so the Verifier reads the suite for gamed patterns",
        "There is no dedicated reader, so you read the tests for gamed patterns yourself",
        "The Verifier must without fail read the tests for gamed patterns",
        "There is no reason to skip it: read the tests for gamed patterns.",
        "There is no reason, read the tests for gamed patterns.",
    )
    for text, expected in ((t, True) for t in waved):
        match = READS_THE_SUITE.search(text)
        assert match, text
        before = text[max(0, match.start() - NEGATION_WINDOW):match.start()]
        assert bool(NEGATED.search(before)) is expected, f"must stay waved: {text}"
    for text in instructions:
        match = READS_THE_SUITE.search(text)
        assert match, text
        before = text[max(0, match.start() - NEGATION_WINDOW):match.start()]
        assert not NEGATED.search(before), f"must be caught: {text}"


def test_no_stage_instruction_still_describes_the_pass_as_a_live_stage() -> None:
    """The removal was complete in code and incomplete in text: the canonical rulebook every stage
    loads still called the Verifier's job "a targeted test-quality review on a cross-family model",
    and a live gate message still told the operator the stage would read the tests. Both were the
    same defect as the machinery coming back, one layer out."""
    assert offenders_matching(DESCRIBES_A_LIVE_PASS) == []


def test_what_the_removal_leaves_uncovered_is_stated_rather_than_implied() -> None:
    """"If removing it leaves a real gap, say so rather than quietly leaving a stub that reads like
    coverage." The three partial covers are named, in the documents a reader actually opens."""
    for rel in ("CLAUDE.md", "skills/pipeline-conventions/SKILL.md",
                "skills/verifier-triage/SKILL.md", "agents/avenger-verifier.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "uncovered" in text or "leaves uncovered" in text, f"{rel} does not name the gap"
        assert "mutation" in text.lower(), f"{rel} does not name what partially covers it"


def test_the_finding_kind_survives_so_a_gamed_test_seen_in_passing_can_still_be_raised() -> None:
    """Removing the reader must not remove the vocabulary: a gamed test noticed while tracing
    coverage is still a fail on a green suite, and it needs somewhere to go."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import pipeline_metrics

    assert "gamed-test" in pipeline_metrics.VERIFIER_KIND_REAL
    schema = (ROOT / "skills" / "verifier-triage" / "SKILL.md").read_text(encoding="utf-8")
    assert "gamed-test" in schema
