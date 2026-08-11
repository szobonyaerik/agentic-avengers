"""The cap is a split trigger, and it is decided before any model sees the spec.

Two things are pinned. First the arithmetic: what counts as a requirement, and where the count is
taken from. Second, and more important, the *shape of the remedy* — the message must tell the writer
to SPLIT, never to shorten, because a rejection for size is one more thing for a spec to grow around
and that is exactly how a 25k spec reached 51k across four rejected rounds.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from requirement_cap import (  # noqa: E402
    DEFAULT_MAX,
    cap,
    declared_ids,
    main,
    split_message,
)

HEAD = "---\nfeature: demo\nspec_gate: pending\n---\n\n# Spec\n\n"


def spec(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "spec.md"
    path.write_text(HEAD + body, encoding="utf-8")
    return path


def requirements(n: int, start: int = 1) -> str:
    return "## Requirements\n" + "".join(
        f"- R1.1.{i} — `binding: e2e` — behavior {i}\n" for i in range(start, start + n)
    )


# ── what counts ──────────────────────────────────────────────────────────────


def test_the_default_cap_is_twelve() -> None:
    assert DEFAULT_MAX == 12
    assert cap() == 12


def test_a_declaration_is_a_line_that_starts_with_an_id() -> None:
    ids, scope = declared_ids(HEAD + requirements(3))
    assert ids == ["R1.1.1", "R1.1.2", "R1.1.3"]
    assert scope == "requirements-section"


def test_a_mention_mid_sentence_is_not_a_declaration() -> None:
    """A journey row lists the ids it carries. Counting those would make the cap a function of how
    thoroughly the spec cross-references itself."""
    body = requirements(2) + "\n## Journeys\n- J1 — the user signs in — covers R1.1.1, R1.1.2\n"
    ids, _ = declared_ids(HEAD + body)
    assert ids == ["R1.1.1", "R1.1.2"]


def test_an_id_restated_under_acceptance_criteria_is_not_counted_twice() -> None:
    body = requirements(2) + "\n## Acceptance criteria\n- R1.1.1 — passes when: …; fails when: …\n"
    ids, _ = declared_ids(HEAD + body)
    assert ids == ["R1.1.1", "R1.1.2"]


def test_a_spec_with_no_requirements_heading_is_counted_over_the_whole_body() -> None:
    """Otherwise the cap is evaded by moving a heading, and a silent fallback is how a check comes
    to mean something other than what its caller believes — so the mode is reported."""
    body = "## Behaviours\n" + "".join(f"- R1.1.{i} — thing {i}\n" for i in range(1, 4))
    ids, scope = declared_ids(HEAD + body)
    assert ids == ["R1.1.1", "R1.1.2", "R1.1.3"]
    assert scope == "whole-body"


def test_bold_declarations_count() -> None:
    ids, _ = declared_ids(HEAD + "## Requirements\n**R1.1.1** — a thing\n")
    assert ids == ["R1.1.1"]


# ── the remedy is a split, never a rejection ─────────────────────────────────


def test_the_over_cap_message_says_split_and_never_says_shorten() -> None:
    message = split_message(Path("spec.md"), [f"R1.1.{i}" for i in range(1, 14)], 12, "requirements-section")
    assert "SPLIT TRIGGER" in message
    assert "not a gate rejection" in message
    assert "Do not answer it with" in message and "more text" in message
    lowered = message.lower()
    assert "shorten" not in lowered
    assert "too long" not in lowered and "too large" not in lowered


def test_the_over_cap_message_names_the_ids_so_the_split_is_obvious() -> None:
    ids = [f"R1.1.{i}" for i in range(1, 14)]
    message = split_message(Path("spec.md"), ids, 12, "requirements-section")
    for rid in ids:
        assert rid in message


def test_the_message_says_no_model_judged_it() -> None:
    """The distinction the whole design rests on: this ran BEFORE the gate."""
    message = split_message(Path("spec.md"), ["R1.1.1"], 0, "requirements-section")
    assert "no model has judged it" in message


# ── the CLI contract the hook branches on ────────────────────────────────────


def test_at_the_cap_is_within_and_one_over_is_over(tmp_path: Path) -> None:
    assert main([str(spec(tmp_path, requirements(12)))]) == 0
    assert main([str(spec(tmp_path, requirements(13)))]) == 1


def test_an_unreadable_spec_is_an_error_not_a_pass(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope.md")]) == 2


def test_the_cap_can_be_overridden_but_never_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEC_REQUIREMENT_MAX", "20")
    assert cap() == 20
    monkeypatch.setenv("SPEC_REQUIREMENT_MAX", "twelve")
    with pytest.raises(ValueError, match="not a whole number"):
        cap()
    monkeypatch.setenv("SPEC_REQUIREMENT_MAX", "0")
    with pytest.raises(ValueError, match="at least 1"):
        cap()


def test_a_bad_cap_stops_the_check_rather_than_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same rule as GATE_CALL_TIMEOUT: a budget believed rather than checked is the defect."""
    monkeypatch.setenv("SPEC_REQUIREMENT_MAX", "lots")
    assert main([str(spec(tmp_path, requirements(3)))]) == 2
