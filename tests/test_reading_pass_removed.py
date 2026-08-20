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
NARRATIVE = {
    "CLAUDE.md",
    "AGENTS.md",
    "skills/pipeline-conventions/SKILL.md",
    "skills/verifier-triage/SKILL.md",
    "agents/avenger-verifier.md",
}


def shipped_files() -> list[Path]:
    """Every canonical instruction and script, excluding generated and vendored trees."""
    found: list[Path] = []
    for rel in (*INSTRUCTION_DIRS, "scripts"):
        for path in (ROOT / rel).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                found.append(path)
    return found


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
    for path in [*shipped_files(), *(ROOT / "docs").rglob("*.md"),
                 ROOT / "docs" / "templates" / "env.example", ROOT / "README.md"]:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
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


def test_no_stage_is_instructed_to_do_the_reading_itself() -> None:
    """The gap is left open on purpose. A stage told to read the suite for gamed patterns would be
    same-family self-review under the removed gate's name - every subagent here is Anthropic."""
    # Deliberately loose between the verb and its object: an instruction to do this can be phrased
    # a dozen ways, and a regex that only matched the removed prompt's exact wording would catch
    # only a copy-paste of it — which is the one form nobody would write.
    banned = re.compile(
        r"read\s+(?:\S+\s+){0,4}?(?:suites?|tests?|test set)\s+for\s+"
        r"(?:gamed|tautological|implementation-coupled)",
        re.IGNORECASE,
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in shipped_files()
        if str(path.relative_to(ROOT)) not in NARRATIVE
        and banned.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert offenders == [], offenders


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
