"""A verification verdict is backed by evidence it executed - and every way it is not, proven red.

## The defect this pins

A pass was consumable with no evidence anything ran. `verdict.json` carried
`test_quality.reviewed: true`, a boolean the verifying agent wrote about itself, and both
`hook_verifier.sh` and `gate_ci.sh` accepted it as the phase's independence. A stage that skipped its
work looked exactly like one that did it.

## Issue #69's standing rule, applied here

**A guard proven only by passing is not proven.** Every check below is driven RED first - the record
tampered with, the log edited, the tree moved on, the verdict pointed at a different transcript - and
then repaired and driven GREEN, so each assertion is about the guard rather than about the fixture.

Every recorded run in this file is a REAL subprocess through `verifier_evidence.record`. A fixture
that hand-wrote a record would be testing the check against precisely the shape it exists to refuse.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import applicability  # noqa: E402
import evidence_redaction  # noqa: E402
import verifier_evidence as ve  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the recorder's whole job is to run a real child in its own process group and record what it "
    "did; a mocked child would record a mock"
)

CLI = [sys.executable, str(ROOT / "scripts" / "verifier_evidence.py")]


@pytest.fixture
def phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A phase with one spec and one test file, with cwd at the project root."""
    project = tmp_path / "project"
    phase_dir = project / "docs" / "features" / "demo" / "phases" / "1-alpha"
    (phase_dir / "specs" / "1.1-sub").mkdir(parents=True)
    (phase_dir / "specs" / "1.1-sub" / "spec.md").write_text(
        "---\nfeature: demo\nphase: 1-alpha\n---\n\n## Acceptance criteria\n\nDone.\n",
        encoding="utf-8",
    )
    tests = project / "tests" / "demo" / "1-alpha"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.chdir(project)
    return phase_dir


def record(phase_dir: Path, kind: str = "suite", argv: list[str] | None = None) -> dict:
    entry, _rc = ve.record(phase_dir, kind, argv or ["/bin/echo", "1 passed"])
    return entry


def verdict_for(phase_dir: Path, chain: str | None = None) -> Path:
    """A passing verdict naming the transcript on disk."""
    if chain is None:
        chain = ve.chain_head(ve.load(phase_dir)["runs"])
    path = phase_dir / "verdict.json"
    path.write_text(json.dumps({
        "verdict": "pass",
        "findings": [],
        "execution": {"evidence": ve.FILENAME, "chain": chain},
    }), encoding="utf-8")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*CLI, *args], capture_output=True, text=True, check=False)  # noqa: S603


# ── outcome 1: a verdict is backed by evidence it executed ───────────────────


def test_a_verdict_with_no_transcript_is_refused_and_recording_one_clears_it(phase: Path) -> None:
    """RED: this is the exact state every pre-existing pass was in - a verdict, and nothing else."""
    verdict_for(phase, chain="")
    found = ve.problems(phase, verdict_path=phase / "verdict.json")
    assert found and "no verification-evidence.json" in found[0]

    record(phase)
    verdict_for(phase)
    assert ve.problems(phase, verdict_path=phase / "verdict.json") == []


def test_an_empty_transcript_is_not_evidence(phase: Path) -> None:
    """A record file that exists and records nothing would otherwise satisfy 'has evidence'."""
    ve.save(phase, {"schema": ve.SCHEMA, "phase": phase.name, "runs": []})
    found = ve.problems(phase)
    assert found and "records no runs at all" in found[0]

    record(phase)
    assert ve.problems(phase) == []


def test_a_transcript_with_no_passing_suite_run_is_refused(phase: Path) -> None:
    """Adversarial execution alone is not a verification: something has to have run the tests."""
    record(phase, kind="adversarial")
    found = ve.problems(phase)
    assert found and f"no current run of kind '{ve.REQUIRED_KIND}'" in found[0]

    record(phase, kind="suite")
    assert ve.problems(phase) == []


def test_a_suite_run_that_failed_does_not_back_a_pass(phase: Path) -> None:
    """The recorder passes the child's exit code through, so a red suite is recorded as red - and a
    verdict standing on a red run is refused rather than reading as 'the suite ran'."""
    record(phase, kind="suite", argv=["/bin/sh", "-c", "echo '1 failed'; exit 1"])
    found = ve.problems(phase)
    assert found and "exited non-zero" in found[0]

    record(phase, kind="suite")
    assert ve.problems(phase) == []


def test_the_verdict_must_name_the_transcript_it_stands_on(phase: Path) -> None:
    """A verdict pointing at no specific transcript points at whatever is on disk later."""
    record(phase)
    path = phase / "verdict.json"
    path.write_text(json.dumps({"verdict": "pass", "findings": []}), encoding="utf-8")
    found = ve.problems(phase, verdict_path=path)
    assert found and "no `execution` block" in found[0]

    path.write_text(json.dumps({
        "verdict": "pass", "findings": [], "execution": {"evidence": ve.FILENAME, "chain": ""},
    }), encoding="utf-8")
    found = ve.problems(phase, verdict_path=path)
    assert found and "names no `chain`" in found[0]

    verdict_for(phase)
    assert ve.problems(phase, verdict_path=path) == []


def test_a_verdict_written_against_a_different_set_of_runs_is_refused(phase: Path) -> None:
    """The chain is what stops an earlier attempt's verdict being paired with a later transcript."""
    record(phase)
    path = verdict_for(phase)
    record(phase, kind="adversarial")          # the transcript moves on; the verdict does not
    found = ve.problems(phase, verdict_path=path)
    assert found and "a different set of runs" in found[0]

    verdict_for(phase)
    assert ve.problems(phase, verdict_path=path) == []


# ── tampering: the record is checked, not trusted ────────────────────────────


def test_an_edited_output_log_no_longer_hashes_to_its_record(phase: Path) -> None:
    entry = record(phase)
    log = phase / entry["log"]
    log.write_text("1 passed (definitely)\n", encoding="utf-8")
    found = ve.problems(phase)
    assert found and "does not hash to the recorded digest" in found[0]


def test_a_deleted_output_log_is_refused(phase: Path) -> None:
    entry = record(phase)
    (phase / entry["log"]).unlink()
    found = ve.problems(phase)
    assert found and "is missing" in found[0]


def test_an_edited_record_no_longer_matches_its_own_chain(phase: Path) -> None:
    """Editing an entry in place - a failing exit code turned into a passing one - breaks the chain
    the verdict names, so the edit cannot be laundered by leaving the chain alone."""
    record(phase, kind="suite", argv=["/bin/sh", "-c", "exit 1"])
    data = json.loads((phase / ve.FILENAME).read_text(encoding="utf-8"))
    data["runs"][0]["exit_code"] = 0
    (phase / ve.FILENAME).write_text(json.dumps(data, indent=2), encoding="utf-8")
    found = ve.problems(phase)
    assert any("was edited after it was written" in line for line in found)


def test_a_run_recording_no_elapsed_time_did_not_start_a_process(phase: Path) -> None:
    """A fork+exec of even `/bin/true` costs more than the floor. `0` is a hand-written number."""
    record(phase)
    data = json.loads((phase / ve.FILENAME).read_text(encoding="utf-8"))
    data["runs"][0]["elapsed_ms"] = 0
    ve.save(phase, data)      # re-chained, so the ONLY remaining objection is the floor
    found = ve.problems(phase)
    assert found and "below the" in found[0] and "floor for starting a process" in found[0]


def test_a_sub_millisecond_run_still_records_above_the_floor(phase: Path) -> None:
    """The floor means 'no process started', never 'a fast machine'.

    Truncating the measured seconds made it the latter: a real fork+exec costs under a millisecond on
    a fast CI runner, `int(0.0007 * 1000)` is 0, and the floor then refused a run that happened. The
    conversion is asserted clock-free, so the guard is proven rather than the machine.
    """
    assert ve.elapsed_ms(0.0000007) >= ve.PROCESS_FLOOR_MS
    assert ve.elapsed_ms(0.0) == 0                    # a zero measurement is still zero
    assert ve.elapsed_ms(1.5) == 1500                 # and a real duration is unchanged


def test_evidence_recorded_against_different_content_is_refused(phase: Path) -> None:
    """Evidence is only evidence about what it was recorded against. RED after the tests change,
    GREEN once the commands are re-run against the tree that is actually there."""
    record(phase)
    verdict_for(phase)
    assert ve.problems(phase, verdict_path=phase / "verdict.json") == []

    (Path("tests") / "demo" / "1-alpha" / "test_y.py").write_text(
        "def test_new():\n    assert True\n", encoding="utf-8")
    found = ve.problems(phase, verdict_path=phase / "verdict.json")
    assert found and "made against different content" in found[0]

    record(phase)
    verdict_for(phase)
    assert ve.problems(phase, verdict_path=phase / "verdict.json") == []


def test_a_changed_spec_also_invalidates_the_transcript(phase: Path) -> None:
    """The subject is the specs AND the tests: a requirement edited after verification is a
    different thing verified."""
    record(phase)
    spec = phase / "specs" / "1.1-sub" / "spec.md"
    spec.write_text(spec.read_text(encoding="utf-8") + "\n- R1.1.9 something new\n", encoding="utf-8")
    assert any("made against different content" in line for line in ve.problems(phase))


# ── outcome 4: a stage that cannot produce its evidence fails LOUDLY ─────────


def test_a_command_that_cannot_run_is_a_loud_error_not_a_recorded_pass(phase: Path) -> None:
    """Evidence is what a command PRODUCES. A recorder that filed 'could not run' as a run would be
    the boolean it replaced, with more steps."""
    with pytest.raises(ve.EvidenceError) as excinfo:
        ve.record(phase, "suite", ["/definitely/not/a/binary"])
    assert "cannot run" in str(excinfo.value)
    assert not (phase / ve.FILENAME).exists(), "a failed start must record nothing"


def test_a_failing_command_returns_its_own_exit_code_to_the_caller(phase: Path) -> None:
    """The Verifier has to see the real result of its own command."""
    _entry, rc = ve.record(phase, "suite", ["/bin/sh", "-c", "exit 3"])
    assert rc == 3


def test_an_unreadable_record_is_an_error_never_an_empty_one(phase: Path) -> None:
    """Read as 'no runs yet', a corrupted transcript could be repaired by recording one more command
    over the top of it - a fabricated pass with extra steps."""
    (phase / ve.FILENAME).write_text("{ not json", encoding="utf-8")
    with pytest.raises(ve.EvidenceError):
        ve.load(phase)
    assert run_cli("check", str(phase)).returncode == ve.ERROR


def test_an_unknown_run_kind_is_refused_by_name(phase: Path) -> None:
    """The kind set is closed. Filed under `other`, a mis-typed `suite` becomes a run that silently
    stops counting toward the one kind the gate requires."""
    with pytest.raises(ve.EvidenceError) as excinfo:
        ve.record(phase, "sute", ["/bin/echo", "hi"])
    assert "sute" in str(excinfo.value) and ve.REQUIRED_KIND in str(excinfo.value)


def test_every_refusal_names_what_would_satisfy_it(phase: Path) -> None:
    """A rule whose remedy is unavailable is a wedge, not a gate. The remedy is in the output."""
    verdict_for(phase, chain="")
    result = run_cli("check", str(phase), "--verdict", str(phase / "verdict.json"))
    assert result.returncode == ve.MISSING
    assert "To satisfy this" in result.stderr
    assert "record <phase-dir> --kind suite" in result.stderr
    assert "verifier_evidence.py chain" in result.stderr
    assert f"--rule {ve.RULE}" in result.stderr, "the disclosed-exception route is part of the remedy"


# ── the applicability boundary: it binds what is OPEN ────────────────────────


def test_a_disclosed_exception_clears_the_obligation_and_says_so(phase: Path, capsys) -> None:
    """A phase that genuinely cannot produce a transcript has a route that is narrow and audited -
    the same one every other rule here uses. Without it this rule is a wedge for such a phase."""
    verdict_for(phase, chain="")
    assert ve.due(phase, verdict_path=phase / "verdict.json") != []

    reason = phase / "why.txt"
    reason.write_text("no runnable collaborator in this environment\n", encoding="utf-8")
    applicability.record_exception(
        phase, rule=ve.RULE, subject=phase.name, reason=reason.read_text(encoding="utf-8"),
        recorded_by="test",
    )
    assert ve.due(phase, verdict_path=phase / "verdict.json") == []
    assert ve.RULE in capsys.readouterr().err, "an applied exception is never silent"


def test_the_sweep_counts_untouched_phases_rather_than_blocking_them(phase: Path, capsys) -> None:
    """Every phase that closed before this rule existed has no transcript and can never acquire one.
    A full audit would fail a consumer repo's CI over a remedy that does not exist for it."""
    verdict_for(phase, chain="")
    # No git repository here, so the scope is unknowable: nothing is enforced, and it is said.
    assert ve.sweep(Path.cwd()).failures == []
    assert "scope is unknowable" in capsys.readouterr().err
    # The deliberate audit still sees it.
    assert ve.sweep(Path.cwd(), enforce_all=True).failures != []


def test_one_unreadable_record_fails_only_the_phase_that_owns_it(phase: Path) -> None:
    """RED before the containment: `due()` was called for every phase before the scope was applied,
    and `load()` raises on a malformed record, so ONE corrupt file anywhere aborted the whole sweep
    with a single undecidable error - holding every unrelated commit hostage, which is the exact
    failure the applicability boundary exists to remove."""
    record(phase)
    verdict_for(phase)
    other = phase.parent / "9-corrupt"
    (other / "specs").mkdir(parents=True)
    (other / "verdict.json").write_text('{"verdict": "pass"}', encoding="utf-8")
    (other / ve.FILENAME).write_text("{ this is not json", encoding="utf-8")

    result = ve.sweep(Path.cwd(), enforce_all=True)
    assert len(result.undecidable) == 1 and "9-corrupt" in result.undecidable[0]
    assert not any("1-alpha" in line for line in result.undecidable), \
        "the good phase was still examined and still decided"
    assert result.failures == [], "and it passes on its own evidence"


def test_an_unreadable_record_exits_undecidable_rather_than_as_a_missing_obligation(
    phase: Path,
) -> None:
    """Exit 1 means the obligation and exit 2 means it could not be DECIDED. `gate_ci.sh` records
    them under different names: "record your runs" cannot repair a record nothing can parse."""
    verdict_for(phase, chain="")
    ve.record_path(phase).write_text("{ not json", encoding="utf-8")
    result = run_cli("sweep", "--root", str(Path.cwd()), "--all")
    assert result.returncode == ve.ERROR
    assert "could not be decided" in result.stderr or "could not be read" in result.stderr


def test_the_sweep_does_not_examine_a_phase_it_is_not_going_to_enforce(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope is applied BEFORE `due()`, which recomputes `subject_digest` over every spec,
    mapping and test file a phase owns. This runs on every commit, so examining a phase in order to
    discard the answer re-hashes a repository's whole historical test corpus to produce a count."""
    # A real record, so the digest path is actually reachable: a phase with no record short-circuits
    # before any hashing, and a test built on one would pass whichever order the sweep used.
    record(phase)
    verdict_for(phase)
    monkeypatch.setattr(applicability, "changed_paths", lambda _root: set())
    examined: list[Path] = []
    real = ve.subject_digest
    monkeypatch.setattr(
        ve, "subject_digest",
        lambda phase_dir, root=None: (examined.append(Path(phase_dir)), real(phase_dir, root))[1],
    )
    assert ve.sweep(Path.cwd()) == ve.SweepResult([], [])
    assert examined == [], "an out-of-scope phase is counted, never hashed"


def test_the_rule_is_on_the_closed_set_with_a_call_site_that_reads_it() -> None:
    """A ledger entry nothing reads is an exception that does not exist."""
    assert ve.RULE in applicability.RULES
    assert any("verifier_evidence" in reader for reader in applicability.READERS)


# ── the record's own shape ───────────────────────────────────────────────────


def test_the_record_declares_its_readers_and_the_read_path_agrees() -> None:
    """JSON has no frontmatter, so `readers` is a top-level key the writer emits — and the read-path
    table reads THIS list rather than keeping a second copy that could disagree."""
    import doc_read_path

    assert doc_read_path.READ_PATH[ve.FILENAME]["readers"] == list(ve.READERS)


def test_the_chain_identifies_the_sequence_of_runs_not_just_their_number(phase: Path) -> None:
    """Two runs in a different order are a different transcript, so the head must differ."""
    record(phase, kind="suite")
    record(phase, kind="adversarial")
    first = ve.chain_head(ve.load(phase)["runs"])

    data = ve.load(phase)
    data["runs"] = list(reversed(data["runs"]))
    assert ve.chain_head(data["runs"]) != first


def test_a_note_is_prose_and_does_not_change_the_chain(phase: Path) -> None:
    """Editing why a command was run must not invalidate what the command did."""
    ve.record(phase, "suite", ["/bin/echo", "ok"], note="first pass")
    before = ve.chain_head(ve.load(phase)["runs"])
    data = ve.load(phase)
    data["runs"][0]["note"] = "rewritten afterwards"
    assert ve.chain_head(data["runs"]) == before


def test_the_recorded_digest_is_of_the_log_that_is_actually_on_disk(phase: Path) -> None:
    entry = record(phase)
    on_disk = hashlib.sha256((phase / entry["log"]).read_bytes()).hexdigest()
    assert entry["output_sha256"] == on_disk
    assert entry["output_bytes"] > 0


def test_a_superseded_run_is_counted_not_held_against_the_phase(phase: Path) -> None:
    """The wedge this shape exists to avoid. A stale run stays on the record forever, so refusing
    the phase for holding one would have no remedy: re-recording appends a fresh run and leaves the
    old one in place. Stale runs are SUPERSEDED - counted and named - and only current ones carry
    the verdict."""
    record(phase)
    (Path("tests") / "demo" / "1-alpha" / "test_y.py").write_text("def test_n():\n    assert True\n",
                                                                  encoding="utf-8")
    record(phase)                                   # the remedy: run it again against what is here
    verdict_for(phase)
    assert ve.problems(phase, verdict_path=phase / "verdict.json") == []

    runs = ve.load(phase)["runs"]
    current, stale = ve.partition_by_currency(runs, phase, Path.cwd())
    assert len(stale) == 1 and len(current) == 1, "the old run is kept, and it is not current"

    result = run_cli("check", str(phase), "--verdict", str(phase / "verdict.json"))
    assert result.returncode == ve.OK
    assert "superseded" in result.stderr, "a run that stopped counting is never silent"


def test_the_subject_digest_does_not_move_when_the_recorder_is_run_from_elsewhere(
    phase: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest that depended on cwd would report "the code changed" for a command that changed
    nothing, and the prescribed remedy — run it again — could not clear it. That is a wedge, so the
    labels are relative to the phase and to its test root rather than to wherever the caller stood."""
    project = phase.parents[3]
    from_root = ve.subject_digest(phase, project)

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert ve.subject_digest(phase, project) == from_root


# ── what a stored log is allowed to carry ────────────────────────────────────


def echo_run(phase_dir: Path, text: str, kind: str = "suite") -> dict:
    """A REAL child that prints `text`, so the redaction path is exercised on real captured output
    rather than on a string handed straight to the redactor."""
    entry, _rc = ve.record(phase_dir, kind, ["/bin/echo", text])
    return entry


def test_a_credential_the_adversarial_run_surfaced_never_reaches_the_log(phase: Path) -> None:
    """RED without redaction: `adversarial` exists to plant a value and look at what came back, so
    a reproduced leak wrote the credential into a committed file that no later commit scrubs."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    entry = echo_run(phase, f"connected as {secret}", kind="adversarial")
    stored = (phase / entry["log"]).read_text(encoding="utf-8")
    assert secret not in stored, "the log is committed; a live credential in it is permanent"
    assert "[REDACTED:aws-access-key-id:" in stored, "a removed span is marked, never deleted"
    assert "connected as" in stored, "only the secret goes - the evidence stays evidence"


@pytest.mark.parametrize(
    ("name", "line"),
    [
        ("url-credentials", "postgres://app:hunter2@db.internal:5432/prod"),
        ("secret-assignment", "DATABASE_PASSWORD=s3cr3t-value"),
        ("bearer-token", "Bearer abcdefghijklmnopqrstuvwxyz012345"),
        ("github-token", "ghp_0123456789abcdefghijklmnopqrstuvwx"),
        ("jwt", "eyJhbGciOi.eyJzdWIiOjEyMw.dBjftJeZ4CVPmB92K27u"),
        ("private-key-block",
         "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----"),
    ],
)
def test_every_documented_secret_shape_is_removed_and_named(name: str, line: str) -> None:
    out = evidence_redaction.redact(line)
    assert f"[REDACTED:{name}:" in out, out
    for token in ("hunter2", "s3cr3t-value", "abcdefghijklmnopqrstuvwxyz012345",
                  "ghp_0123456789abcdefghijklmnopqrstuvwx", "MIIBOgIBAAJBAK"):
        if token in line:
            assert token not in out, out


def test_the_note_is_redacted_too_because_it_names_what_was_planted(phase: Path) -> None:
    """`--note "<what you planted>"` is the field the Verifier is instructed to describe the planted
    value in, and it is stored on the record beside the log."""
    entry, _rc = ve.record(phase, "adversarial", ["/bin/echo", "ok"],
                           note="planted API_KEY=ZZ-live-credential-9999")
    assert "ZZ-live-credential-9999" not in entry["note"]
    assert "[REDACTED:secret-assignment:" in entry["note"]
    assert "ZZ-live-credential-9999" not in ve.record_path(phase).read_text(encoding="utf-8")


def test_a_stored_log_is_bounded_and_says_how_much_it_dropped(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED without the cap: nothing bounded the file at all. RED without the marker: a cut log reads
    as complete, and a later reader draws a conclusion from an absence that was really a cut."""
    monkeypatch.setenv(evidence_redaction.MAX_BYTES_ENV, "2048")
    entry = echo_run(phase, "x" * 20000)
    stored = (phase / entry["log"]).read_text(encoding="utf-8")
    assert len(stored.encode("utf-8")) <= 2048, "the cap binds the bytes that are stored"
    assert "[TRUNCATED:" in stored and "bytes dropped" in stored
    assert evidence_redaction.MAX_BYTES_ENV in stored, "the marker names the knob that cut it"
    # Head AND tail survive: a suite log's failure summary is at the end.
    assert stored.startswith("x") and stored.rstrip("\n").endswith("x")


def test_the_digest_covers_exactly_the_capped_redacted_bytes_that_were_stored(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof of execution is the point of this record; a digest over the RAW output would mean
    `check` could never verify the log on disk again."""
    monkeypatch.setenv(evidence_redaction.MAX_BYTES_ENV, "2048")
    entry = echo_run(phase, "AKIAIOSFODNN7EXAMPLE " + "y" * 20000)
    on_disk = (phase / entry["log"]).read_bytes()
    assert entry["output_sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert entry["output_bytes"] == len(on_disk)
    verdict_for(phase)
    assert ve.problems(phase, verdict_path=phase / "verdict.json") == []


def test_an_edited_log_still_fails_its_record_after_redaction(phase: Path) -> None:
    """The integrity guard is unchanged by any of the above - asserted, not assumed."""
    entry = echo_run(phase, "1 passed")
    (phase / entry["log"]).write_text("1 passed (I promise)", encoding="utf-8")
    found = ve.problems(phase)
    assert any("does not hash to the recorded digest" in line for line in found), found


def test_redaction_failing_writes_no_log_and_records_no_run(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAIL CLOSED, proven by breaking it: turn the redactor into a raw-log fallback and this goes
    green while a live credential lands in git. There is deliberately no such fallback."""
    monkeypatch.setattr(
        evidence_redaction, "prepare",
        lambda _text: (_ for _ in ()).throw(evidence_redaction.RedactionError("pattern exploded")),
    )
    with pytest.raises(ve.EvidenceError) as exc:
        ve.record(phase, "adversarial", ["/bin/echo", "AKIAIOSFODNN7EXAMPLE"])

    assert "pattern exploded" in str(exc.value), "the stop names what failed"
    assert "evidence_redaction.py" in str(exc.value), "and where the remedy is"
    assert "does NOT count as recorded" in str(exc.value)
    assert not ve.log_dir(phase).exists() or not any(ve.log_dir(phase).iterdir())
    assert ve.load(phase)["runs"] == [], "a run whose log could not be written is not a run"


def test_a_malformed_cap_is_a_named_config_failure_never_a_silent_default(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(evidence_redaction.MAX_BYTES_ENV, "lots")
    with pytest.raises(ve.EvidenceError, match="lots"):
        ve.record(phase, "suite", ["/bin/echo", "ok"])
    assert ve.load(phase)["runs"] == []


def test_an_uncompilable_extra_pattern_stops_the_run_rather_than_being_skipped(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pattern set is extendable, so a bad extension is a real failure mode - and skipping it
    silently would store the raw bytes the extension was added to remove."""
    monkeypatch.setenv(evidence_redaction.EXTRA_ENV, "([unclosed")
    with pytest.raises(ve.EvidenceError, match=evidence_redaction.EXTRA_ENV):
        ve.record(phase, "suite", ["/bin/echo", "ok"])
    assert ve.load(phase)["runs"] == []


def test_an_extra_pattern_extends_the_set_at_a_call_site(phase: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(evidence_redaction.EXTRA_ENV, r"INTERNAL-[0-9]{6}")
    entry = echo_run(phase, "leaked INTERNAL-424242 here")
    stored = (phase / entry["log"]).read_text(encoding="utf-8")
    assert "INTERNAL-424242" not in stored
    assert f"[REDACTED:{evidence_redaction.EXTRA_ENV.lower()}:" in stored


def test_redaction_runs_before_the_cap_so_no_half_secret_survives_the_cut() -> None:
    """Capping first would leave a truncated token in the kept region, where its own pattern no
    longer fires and the surviving half still leaks."""
    secret = "ghp_0123456789abcdefghijklmnopqrstuvwx"
    text = "a" * 400 + secret + "b" * 400
    out = evidence_redaction.cap(evidence_redaction.redact(text), 300)
    assert "ghp_0123456789" not in out


def test_a_credential_on_the_command_line_never_reaches_the_committed_record(phase: Path) -> None:
    """RED without argv redaction. The record is committed exactly like the log, and this is the
    LIKELIEST carrier: `agents/avenger-verifier.md` documents `--kind adversarial -- <cmd>` and tells
    the Verifier to keep the planted value recognisably credential-shaped, so the documented happy
    path puts the secret on the command line before the child prints anything."""
    ve.record(phase, "adversarial",
              ["/bin/echo", "postgres://app:hunter2@db/prod", "AWS_SECRET_ACCESS_KEY=zzz-live-9999"])

    written = ve.record_path(phase).read_text(encoding="utf-8")
    assert "hunter2" not in written
    assert "zzz-live-9999" not in written
    assert "[REDACTED:url-credentials:" in written
    assert "[REDACTED:secret-assignment:" in written
    # The command still identifies itself - redaction removes the secret, not the evidence.
    assert "/bin/echo" in written


def test_the_stored_argv_is_what_the_chain_hashes_so_redacting_it_costs_nothing(phase: Path) -> None:
    """`entry_digest` hashes the STORED argv and `check` recomputes from what is on disk, so the
    redaction cannot desynchronise a verdict from its transcript."""
    ve.record(phase, "suite", ["/bin/echo", "TOKEN=abcd-secret-1234"])
    verdict_for(phase)
    assert ve.problems(phase, verdict_path=phase / "verdict.json") == []


def test_the_record_does_not_carry_an_absolute_developer_path(phase: Path) -> None:
    """`cwd` had no reader and was outside the digest, so it bought nothing for the disclosure."""
    entry = ve.record(phase, "suite", ["/bin/echo", "ok"])[0]
    assert "cwd" not in entry
    assert str(Path.cwd()) not in ve.record_path(phase).read_text(encoding="utf-8")


def test_argv_redaction_failing_writes_no_log_and_records_no_run(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same fail-closed rule as the log: no raw-argv fallback, and the stop names what failed."""
    real = evidence_redaction.redact

    def only_argv_fails(text: str) -> str:
        if "AKIA" in text:
            raise evidence_redaction.RedactionError("pattern exploded")
        return real(text)

    monkeypatch.setattr(evidence_redaction, "redact", only_argv_fails)
    # The child prints NOTHING, so the output path redacts cleanly and only the argv path can fail.
    # Echoing the secret instead would make the output raise first and the assertions below would
    # hold whether or not argv is redacted at all - a test green for the wrong reason.
    with pytest.raises(ve.EvidenceError) as exc:
        ve.record(phase, "adversarial",
                  ["/bin/sh", "-c", "exit 0", "AKIAIOSFODNN7EXAMPLE"])

    assert "pattern exploded" in str(exc.value)
    assert "does NOT count as recorded" in str(exc.value)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(exc.value), "the stop must not leak what it refused"
    assert not ve.log_dir(phase).exists() or not any(ve.log_dir(phase).iterdir())
    assert ve.load(phase)["runs"] == []


def test_a_ceiling_too_small_to_hold_its_own_truncation_marker_is_refused_by_name(
    phase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED before the floor: `EVIDENCE_LOG_MAX_BYTES=64` produced a 117-byte log - a config value
    that silently did not bind, which is the class of defect this whole change is about."""
    monkeypatch.setenv(evidence_redaction.MAX_BYTES_ENV, "64")
    with pytest.raises(ve.EvidenceError, match="floor"):
        ve.record(phase, "suite", ["/bin/echo", "ok"])
    assert ve.load(phase)["runs"] == []

    monkeypatch.setenv(evidence_redaction.MAX_BYTES_ENV,
                       str(evidence_redaction.MIN_MAX_BYTES))
    entry = ve.record(phase, "suite", ["/bin/echo", "x" * 5000])[0]
    stored = (phase / entry["log"]).read_bytes()
    assert len(stored) <= evidence_redaction.MIN_MAX_BYTES, "the ceiling now means what it says"
    assert "[TRUNCATED:" in stored.decode("utf-8")
