"""Three species of override used to land in one number, and the number could not tell them apart.

The metric counted "any override naming a record correction", so a corrupted measurement, an account
corrected because better evidence arrived, and an authorised waiver were the same fact. Phase 12
carried two firstmate self-corrections plus a captain waiver; phase 13 carried a single deliberate
break-glass. It was named in five consecutive retros.

The rule these pin is that nothing is GUESSED. A class is stated, or the override is `unclassified`
and counted as such - because inferring one from prose is how one number came to hold three answers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from metrics_support import real_sink, stub_sink  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import metrics_sink as sink  # noqa: E402
import override_classes as classes  # noqa: E402


def record_with(store: Path, phase: str, overrides: list[dict]) -> None:
    sink.ensure(phase)
    path = store / f"phase-{phase}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["overrides"] = overrides
    path.write_text(json.dumps(record), encoding="utf-8")


# ── the vocabulary is closed and nothing is inferred ─────────────────────────────────────────────


def test_the_three_classes_are_the_whole_vocabulary() -> None:
    """Closed MECHANICALLY, the way `spec_gate_triage.BLOCKING` is: a fourth species is a deliberate
    edit here, not something a caller can introduce by writing a new word."""
    assert set(classes.CLASSES) == {"waiver", "measurement-correction", "account-correction"}


def test_tagging_with_a_class_nobody_declared_is_refused() -> None:
    with pytest.raises(classes.UnknownClass) as raised:
        classes.tag("close-correction", "re-stamped the phase close")
    assert "close-correction" in str(raised.value)


def test_a_tagged_scope_round_trips_and_keeps_its_text() -> None:
    scope = classes.tag("waiver", "gate stages run on Claude for the rest of phase 12")
    assert classes.class_of(scope) == "waiver"
    assert classes.scope_text(scope) == "gate stages run on Claude for the rest of phase 12"


def test_an_untagged_scope_is_unclassified_and_never_guessed() -> None:
    """The whole defect in one assertion: this scope NAMES a correction, and reading the prose is
    exactly how three species came to be one number."""
    scope = "CORRECTED 2026-08-21: the phase did not span two runner versions after all"
    assert classes.class_of(scope) is None


def test_a_word_that_merely_looks_like_a_tag_is_not_one() -> None:
    """The tag is the whole prefix up to the first colon, matched against the closed set. Free prose
    routinely contains a colon, and treating any leading word as a class would invent classes."""
    assert classes.class_of("2026-08-21: re-stamped the close") is None
    assert classes.class_of("waiver of the cross-family rule") is None


# ── the metric counts them separately ────────────────────────────────────────────────────────────


def test_each_class_is_counted_on_its_own(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    record_with(store, "12", [
        {"id": "a", "authoriser": "firstmate",
         "scope": classes.tag("measurement-correction", "re-stamped a premature close")},
        {"id": "b", "authoriser": "firstmate",
         "scope": classes.tag("account-correction", "the phase did span two runner versions")},
        {"id": "c", "authoriser": "captain",
         "scope": classes.tag("waiver", "gates run on Claude for the rest of the phase")},
        {"id": "d", "authoriser": "firstmate",
         "scope": classes.tag("measurement-correction", "corrected the final suite size")},
    ])

    counted = classes.count("12")

    assert counted["measurement-correction"] == ["a", "d"]
    assert counted["account-correction"] == ["b"]
    assert counted["waiver"] == ["c"]
    assert counted["unclassified"] == []


def test_an_untagged_override_is_reported_rather_than_folded(stub_sink):  # noqa: F811
    """Folding it into any class is what produced the one number. `unclassified` is an answer."""
    project, store, _ = stub_sink
    record_with(store, "12", [
        {"id": "a", "authoriser": "captain", "scope": classes.tag("waiver", "cross-family")},
        {"id": "b", "authoriser": "firstmate", "scope": "CORRECTED: this entry was wrong"},
    ])

    counted = classes.count("12")

    assert counted["unclassified"] == ["b"]
    assert sum(len(ids) for ids in counted.values()) == 2, "every override lands in exactly one line"


def test_a_phase_with_no_record_is_named_rather_than_counted_as_zero(stub_sink, monkeypatch):  # noqa: F811,E501
    """Zero overrides and no record are different answers, and this metric exists because one of
    them was read as the other."""
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    with pytest.raises(classes.NoRecord):
        classes.count("12")


def test_a_record_with_no_overrides_counts_zero_of_each(stub_sink):  # noqa: F811
    project, store, _ = stub_sink
    record_with(store, "12", [])

    counted = classes.count("12")

    assert all(ids == [] for ids in counted.values())
    assert set(counted) == set(classes.CLASSES) | {"unclassified"}


# ── the CLI ──────────────────────────────────────────────────────────────────────────────────────


def test_the_cli_prints_one_line_per_class(stub_sink, capsys):  # noqa: F811
    project, store, _ = stub_sink
    record_with(store, "12", [
        {"id": "a", "authoriser": "captain", "scope": classes.tag("waiver", "cross-family")},
        {"id": "b", "authoriser": "firstmate", "scope": "CORRECTED: this entry was wrong"},
    ])

    assert classes.main(["count", "12"]) == 0

    out = capsys.readouterr().out
    for name in (*classes.CLASSES, "unclassified"):
        assert name in out
    assert "b" in out, "an unclassified override is named, so it can be classified"


def test_the_cli_refuses_an_invented_class(capsys) -> None:
    assert classes.main(["tag", "close-correction", "anything"]) == 2
    assert "close-correction" in capsys.readouterr().err


def test_the_cli_reports_a_missing_record_rather_than_printing_zeroes(
    stub_sink, monkeypatch, capsys  # noqa: F811
) -> None:
    monkeypatch.setenv("AVENGER_METRICS_OFF", "1")
    assert classes.main(["count", "12"]) == 2
    assert "unclassified" not in capsys.readouterr().out


def test_a_tagged_scope_is_something_firstmates_own_writer_accepts(real_sink):  # noqa: F811
    """The claim that matters and the double cannot make. The class rides on `scope` precisely
    because the record's key surface is closed - so the tag has to be something `validate` accepts,
    not merely something this repo agrees with itself about."""
    project, home = real_sink
    assert sink.ensure("12")

    assert sink.add(
        "12", "overrides",
        id="captain-waive-cross-family-gates",
        authoriser="captain",
        scope=classes.tag("waiver", "gate stages run on Claude for the rest of the phase"),
        reason="no non-Claude provider was reachable",
        at="2026-08-21T00:00:00Z",
    )

    validated = sink.run("validate", "12")
    assert validated is not None and validated[0] == 0, validated
    assert classes.count("12")["waiver"] == ["captain-waive-cross-family-gates"]
