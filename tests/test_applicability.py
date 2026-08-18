"""Tests for the applicability boundary — what a mechanical rule may bind, and what it may only count.

Three separate blocks hit one measured phase, and they were one defect: a check added later asking
whether the whole tree satisfies it. The boundary's three evidences are pinned here (untouched,
shipped, excepted), and so is the direction that matters more — **an exception must be narrow and
audited**. A ledger that granted more than it names, or one that could be written without reaching
`gate-overrides.log`, would be the silent bypass this file exists to replace.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import applicability  # noqa: E402
from applicability import (  # noqa: E402
    ApplicabilityError,
    RULES,
    changed_paths,
    excepted,
    exceptions,
    load,
    main,
    record_exception,
    report_unenforced,
    spec_shipped,
    touched,
)

pytestmark = pytest.mark.subprocess(
    "the scope is whatever git reports and the audit is whatever bypass_log.sh writes; stubbing "
    "either would only ever test the stub"
)


def git_repo(root: Path) -> Path:
    for args in (
        ("init", "-q"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("commit", "-q", "--allow-empty", "-m", "root"),
    ):
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)
    return root


def phase(root: Path, name: str = "8-clickup-client-and-onboarding") -> Path:
    path = root / "docs" / "features" / "demo" / "phases" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def reason_file(root: Path, text: str = "captain-ordered cap; the gate provider was out of credit") -> Path:
    path = root / "reason.txt"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _project_dir(tmp_path, monkeypatch):
    """bypass_log.sh writes gate-overrides.log under $CLAUDE_PROJECT_DIR — never this repository."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))


class TestUntouched:
    def test_a_committed_file_is_not_in_scope(self, tmp_path):
        git_repo(tmp_path)
        target = tmp_path / "tests" / "test_old.py"
        target.parent.mkdir()
        target.write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "old"], cwd=tmp_path, check=True, capture_output=True)
        assert not touched(target, changed_paths(tmp_path))

    def test_an_untracked_file_is_in_scope_before_any_commit(self, tmp_path):
        """An artifact written this session must be enforced from the moment it exists."""
        git_repo(tmp_path)
        target = tmp_path / "tests" / "test_new.py"
        target.parent.mkdir()
        target.write_text("x = 1\n")
        assert touched(target, changed_paths(tmp_path))

    def test_outside_a_repository_the_scope_is_unknowable(self, tmp_path):
        """None is not "nothing changed" — the caller must be able to tell them apart."""
        assert changed_paths(tmp_path) is None


class TestShipped:
    def test_a_spec_stamped_done_has_shipped(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("---\nfeature: demo\nstatus: done\n---\n\n# Spec\n")
        assert spec_shipped(spec)

    @pytest.mark.parametrize("status", ["pending", "in-progress", "draft"])
    def test_a_spec_still_being_written_has_not(self, tmp_path, status):
        spec = tmp_path / "spec.md"
        spec.write_text(f"---\nfeature: demo\nstatus: {status}\n---\n\n# Spec\n")
        assert not spec_shipped(spec)

    def test_status_in_the_body_is_not_a_stamp(self, tmp_path):
        """Only frontmatter decides. Prose saying "status: done" is prose."""
        spec = tmp_path / "spec.md"
        spec.write_text("---\nfeature: demo\nstatus: pending\n---\n\nstatus: done\n")
        assert not spec_shipped(spec)

    def test_a_spec_with_no_frontmatter_at_all_has_not_shipped(self, tmp_path):
        """A loose split reads the whole body as a header, so prose alone would ship the spec."""
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n\nstatus: done\n")
        assert not spec_shipped(spec)

    def test_an_unreadable_spec_has_not_shipped(self, tmp_path):
        """Under-report: the rule keeps binding when the evidence cannot be read."""
        assert not spec_shipped(tmp_path / "nothing.md")


class TestRecording:
    def test_recording_names_the_rule_the_subject_and_the_reason(self, tmp_path):
        target = phase(tmp_path)
        record = record_exception(
            target, "spec-review", "8.1-clickup-client", "captain-ordered cap", "captain"
        )
        assert record.id == "X1"
        stored = json.loads((target / "exceptions.json").read_text())["exceptions"][0]
        assert stored["rule"] == "spec-review"
        assert stored["subject"] == "8.1-clickup-client"
        assert stored["recorded_by"] == "captain"
        assert stored["reason"] == "captain-ordered cap"

    def test_it_is_audited_to_gate_overrides_log(self, tmp_path):
        """An exception that is not logged is a silent bypass, whatever the ledger says."""
        record_exception(phase(tmp_path), "verdict", "8-x", "captain-ordered cap", "captain")
        log = (tmp_path / "gate-overrides.log").read_text()
        assert "exception:verdict" in log and "captain-ordered cap" in log

    def test_a_multi_line_reason_stays_one_record(self, tmp_path):
        """gate-overrides.log is one tab-separated record per line; a reason cannot split it."""
        record_exception(
            phase(tmp_path), "verdict", "8-x", "first line\nsecond line\tthird", "captain"
        )
        assert len((tmp_path / "gate-overrides.log").read_text().strip().splitlines()) == 1

    def test_an_unknown_rule_is_refused_and_names_what_was_invented(self, tmp_path):
        with pytest.raises(ApplicabilityError) as caught:
            record_exception(phase(tmp_path), "spec-vibes", "8.1", "because", "captain")
        assert "spec-vibes" in str(caught.value)

    def test_an_exception_with_no_subject_is_refused(self, tmp_path):
        """An exception with no subject is a rule switched off for everything."""
        with pytest.raises(ApplicabilityError):
            record_exception(phase(tmp_path), "spec-review", "   ", "because", "captain")

    def test_an_exception_with_no_reason_is_refused(self, tmp_path):
        """The reason is the whole disclosure."""
        with pytest.raises(ApplicabilityError):
            record_exception(phase(tmp_path), "spec-review", "8.1", "  ", "captain")

    def test_nothing_is_recorded_when_the_audit_writer_is_missing(self, tmp_path, monkeypatch):
        """Fail closed: no log, no ledger entry."""
        target = phase(tmp_path)
        monkeypatch.setattr(applicability, "__file__", str(tmp_path / "elsewhere" / "x.py"))
        with pytest.raises(ApplicabilityError):
            record_exception(target, "spec-review", "8.1", "because", "captain")
        assert not (target / "exceptions.json").exists()

    def test_nothing_is_recorded_when_the_audit_APPEND_fails(self, tmp_path, monkeypatch):
        """The writer running is not the guarantee — the record landing is.

        A present writer whose append cannot land (an unwritable root, a read-only mount, a full
        disk) leaves durable ledger state with no line in gate-overrides.log, which is exactly the
        silent bypass the ledger replaces.
        """
        target = phase(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "no-such-root"))
        with pytest.raises(ApplicabilityError):
            record_exception(target, "spec-review", "8.1", "because", "captain")
        assert not (target / "exceptions.json").exists()

    def test_ids_increment_within_a_phase(self, tmp_path):
        target = phase(tmp_path)
        record_exception(target, "spec-review", "8.1", "because", "captain")
        second = record_exception(target, "spec-review", "8.2", "because", "captain")
        assert second.id == "X2"


class TestReading:
    def test_an_exception_covers_only_its_own_subject(self, tmp_path):
        target = phase(tmp_path)
        record_exception(target, "spec-review", "8.1-clickup-client", "because", "captain")
        assert excepted(target, "spec-review", "8.1-clickup-client") is not None
        assert excepted(target, "spec-review", "8.2-credentials") is None

    def test_an_exception_covers_only_its_own_rule(self, tmp_path):
        target = phase(tmp_path)
        record_exception(target, "spec-review", "8.1", "because", "captain")
        assert excepted(target, "spec-gate", "8.1") is None

    def test_an_exception_covers_only_its_own_phase(self, tmp_path):
        target = phase(tmp_path)
        record_exception(target, "spec-review", "8.1", "because", "captain")
        assert excepted(phase(tmp_path, "9-create-task-flow"), "spec-review", "8.1") is None

    def test_no_ledger_is_no_exceptions(self, tmp_path):
        assert exceptions(phase(tmp_path)) == []

    def test_an_unreadable_ledger_is_an_error_never_an_empty_one(self, tmp_path):
        target = phase(tmp_path)
        (target / "exceptions.json").write_text("{not json")
        with pytest.raises(ApplicabilityError):
            exceptions(target)

    def test_a_ledger_naming_an_unknown_rule_is_an_error(self, tmp_path):
        """A ledger entry nothing reads is an exception that does not exist."""
        target = phase(tmp_path)
        (target / "exceptions.json").write_text(
            json.dumps({"exceptions": [{"id": "X1", "rule": "vibes", "subject": "8.1"}]})
        )
        with pytest.raises(ApplicabilityError):
            exceptions(target)

    def test_asking_about_an_unknown_rule_raises_rather_than_answering_no(self, tmp_path):
        with pytest.raises(ApplicabilityError):
            excepted(phase(tmp_path), "spec-vibes", "8.1")

    def test_the_ledger_declares_its_readers(self, tmp_path):
        """Every document the read path governs says who reads it, in the document."""
        target = phase(tmp_path)
        record_exception(target, "spec-review", "8.1", "because", "captain")
        assert json.loads((target / "exceptions.json").read_text())["readers"]


class TestReporting:
    def test_nothing_unenforced_prints_nothing(self, capsys):
        report_unenforced("check", 0, "detail")
        assert capsys.readouterr().err == ""

    def test_what_was_counted_is_named(self, capsys):
        report_unenforced("check", 3, "three things")
        err = capsys.readouterr().err
        assert "3" in err and "NOT enforced" in err and "three things" in err


class TestCli:
    def test_record_prints_the_id_and_check_finds_it(self, tmp_path, capsys):
        target = phase(tmp_path)
        assert main(["record", str(target), "--rule", "spec-review", "--subject", "8.1",
                     "--reason-file", str(reason_file(tmp_path)), "--recorded-by", "captain"]) == 0
        assert capsys.readouterr().out.strip() == "X1"
        assert main(["check", str(target), "--rule", "spec-review", "--subject", "8.1"]) == 0

    def test_check_exits_one_when_nothing_covers_it(self, tmp_path):
        assert main(["check", str(phase(tmp_path)), "--rule", "spec-review", "--subject", "8.1"]) == 1

    def test_check_on_an_unknown_rule_is_an_error_not_a_no(self, tmp_path):
        assert main(["check", str(phase(tmp_path)), "--rule", "vibes", "--subject", "8.1"]) == 2

    def test_an_unreadable_reason_file_records_nothing(self, tmp_path):
        target = phase(tmp_path)
        assert main(["record", str(target), "--rule", "verdict", "--subject", "8-x",
                     "--reason-file", str(tmp_path / "missing.txt")]) == 2
        assert not (target / "exceptions.json").exists()

    def test_rules_prints_the_closed_set(self, capsys):
        assert main(["rules"]) == 0
        printed = capsys.readouterr().out
        assert all(name in printed for name in RULES)

    def test_list_names_every_exception(self, tmp_path, capsys):
        target = phase(tmp_path)
        record_exception(target, "spec-review", "8.1", "captain-ordered cap", "captain")
        assert main(["list", str(target)]) == 0
        assert "captain-ordered cap" in capsys.readouterr().out


def test_the_rule_set_is_the_one_the_ledger_validates_against():
    """One closed set, read by every caller. A sixth rule is a deliberate edit in one place."""
    assert set(RULES) == {
        "spec-gate",
        "spec-review",
        "verdict",
        "requirement-cap",
        "breaker",
    }


def test_load_of_a_ledger_that_is_not_a_ledger(tmp_path):
    target = phase(tmp_path)
    (target / "exceptions.json").write_text(json.dumps({"exceptions": "all of them"}))
    with pytest.raises(ApplicabilityError):
        load(target)
