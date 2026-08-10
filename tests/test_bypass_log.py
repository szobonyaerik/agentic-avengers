"""Tests for the break-glass logger.

`gate-overrides.log` is the accountability record for the only sanctioned way to override a gate, so
the invariant under test is structural: **one bypass produces exactly one parseable record**, whatever
the reason text contains. That became reachable when the reason started arriving from a file
(`GATE_BYPASS="$(cat <file>)" …`, see skills/pipeline-conventions) — files have newlines, and a raw
newline in a one-record-per-line TSV splits one override into two, the second carrying no timestamp,
no author and no gate: indistinguishable from a separate entry, i.e. a forgeable audit trail.

The reason's *content* is equally load-bearing — it is written to be read later, and
`skills/phase-handover` mirrors it into `handover.md` — so collapsing must never truncate.

The Verifier's per-finding waiver goes through the same writer, and is the case that most needs it:
`waiver_reason` is a JSON string whose content `skills/verifier-triage` explicitly does *not* judge,
so nothing stops it being two lines.

These drive the real `bypass_log.sh`, not a reimplementation of its `printf`.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BYPASS_LOG = ROOT / "scripts" / "bypass_log.sh"

SEPARATORS = ["\n", "\r", "\t", "\r\n"]

pytestmark = pytest.mark.subprocess(
    "the subject is a bash script and a real git repo; in-process there is nothing to test"
)


def run_bypass(
    project: Path,
    reason: str,
    gate: str = "fidelity",
    finding: str | None = None,
    waived_by: str | None = None,
) -> str:
    """Run the real writer for one override; return the log body."""
    argv = [gate] if finding is None else [gate, finding, waived_by or ""]
    subprocess.run(
        ["bash", str(BYPASS_LOG), *argv],
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            "GATE_BYPASS": reason,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return (project / "gate-overrides.log").read_text(encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway repo root with a git identity, so `who` resolves deterministically."""
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "dev@example.com"], cwd=tmp_path, check=True
    )
    return tmp_path


@pytest.mark.parametrize("separator", SEPARATORS)
def test_record_separator_in_reason_still_writes_one_record(
    project: Path, separator: str
) -> None:
    """The defect this pins: a multi-line reason must not become two log records."""
    reason = f"docs gate is stale here; overriding{separator}second sentence on its own line"

    body = run_bypass(project, reason)

    assert body.count("\n") == 1
    assert len(body.strip().split("\n")) == 1


@pytest.mark.parametrize("separator", SEPARATORS)
def test_the_one_record_keeps_every_audit_field(project: Path, separator: str) -> None:
    """A split record loses timestamp/author/gate — assert the surviving one still has all three."""
    reason = f"first line{separator}second line"

    record = run_bypass(project, reason).strip()
    when, who, gate, rest = record.split("\t")

    assert when.endswith("Z") and when[:4].isdigit()
    assert who == "dev@example.com"
    assert gate == "gate:fidelity"
    assert rest.startswith("reason: ")


@pytest.mark.parametrize("separator", SEPARATORS)
def test_reason_content_survives_collapsing(project: Path, separator: str) -> None:
    """Collapse, never truncate: losing everything after the newline is its own bug."""
    reason = f"the docs gate is stale{separator}and the fix is tracked in issue 42"

    record = run_bypass(project, reason).strip()

    assert "the docs gate is stale" in record
    assert "and the fix is tracked in issue 42" in record


def test_multiline_reason_from_a_file_is_safe(project: Path) -> None:
    """The documented delivery end to end: prose written to a file, read in through the env."""
    reason_file = project / "bypass.md"
    reason_file.write_text(
        "The docs gate flags the new section as undocumented.\n"
        "It is documented in AGENTS.md; the gate's index is stale.\n",
        encoding="utf-8",
    )

    record = run_bypass(project, reason_file.read_text(encoding="utf-8")).strip()

    assert len(record.split("\n")) == 1
    assert record.count("\t") == 3
    assert "the gate's index is stale" in record


def test_consecutive_bypasses_stay_one_record_each(project: Path) -> None:
    """The log is append-only, so a split record would also corrupt every later reader's count."""
    run_bypass(project, "first override\nwith a second line", gate="fidelity")
    run_bypass(project, "second override\nwith a second line", gate="verifier")

    lines = (project / "gate-overrides.log").read_text(encoding="utf-8").strip().split("\n")

    assert len(lines) == 2
    assert [line.split("\t")[2] for line in lines] == ["gate:fidelity", "gate:verifier"]


def test_ordinary_single_line_reason_is_unchanged(project: Path) -> None:
    """Normalisation must be a no-op on the shape that always worked."""
    record = run_bypass(project, "docs gate is stale here; overriding").strip()

    assert record.endswith("\treason: docs gate is stale here; overriding")


def test_gate_bypass_record_has_no_finding_column(project: Path) -> None:
    """The finding column is waiver-only, so a plain gate bypass keeps its original four fields."""
    record = run_bypass(project, "docs gate is stale").strip()

    assert "finding:" not in record
    assert record.count("\t") == 3


@pytest.mark.parametrize("separator", SEPARATORS)
def test_multiline_waiver_reason_writes_one_record(project: Path, separator: str) -> None:
    """The Verifier path: `waiver_reason` is unjudged JSON prose, so it may arrive multi-line."""
    reason = (
        f"the finding assumes a seam this phase does not own{separator}"
        "tracked in issue 42 for the next phase"
    )

    body = run_bypass(
        project, reason, gate="verifier", finding="R2.1.3", waived_by="erik@example.com"
    )

    assert len(body.strip().split("\n")) == 1
    assert "the finding assumes a seam this phase does not own" in body
    assert "tracked in issue 42 for the next phase" in body


def test_waiver_record_keeps_who_when_gate_and_finding_id(project: Path) -> None:
    """A split waiver loses the finding id — assert all four identity fields survive."""
    record = run_bypass(
        project,
        "first line\nsecond line",
        gate="verifier",
        finding="R2.1.3",
        waived_by="erik@example.com",
    ).strip()
    when, who, gate, finding, rest = record.split("\t")

    assert when.endswith("Z") and when[:4].isdigit()
    assert who == "erik@example.com"
    assert gate == "gate:verifier"
    assert finding == "finding:R2.1.3"
    assert rest.startswith("reason: ")


def test_waiver_falls_back_to_git_identity_when_waived_by_is_absent(project: Path) -> None:
    """`waived_by` is only recommended, so its absence must not shift the columns."""
    record = run_bypass(project, "engineer decision", gate="verifier", finding="R2.1.3").strip()

    assert record.split("\t")[1] == "dev@example.com"
    assert record.split("\t")[3] == "finding:R2.1.3"


def test_separator_injected_through_finding_id_or_author(project: Path) -> None:
    """Both extra fields come from verdict.json too, so neither may split the record either."""
    record = run_bypass(
        project,
        "engineer decision",
        gate="verifier",
        finding="R2.1.3\ninjected",
        waived_by="erik\nexample",
    ).strip()

    assert len(record.split("\n")) == 1
    assert record.count("\t") == 4


def test_waiver_and_gate_bypass_share_one_log(project: Path) -> None:
    """One file, one grammar: mixed override kinds stay one parseable record each, reason last."""
    run_bypass(project, "docs gate stale\nsecond line", gate="fidelity")
    run_bypass(project, "seam not owned here\nsecond line", gate="verifier", finding="R2.1.3")

    lines = (project / "gate-overrides.log").read_text(encoding="utf-8").strip().split("\n")

    assert len(lines) == 2
    assert all(line.split("\t")[-1].startswith("reason: ") for line in lines)
    assert [line.count("\t") for line in lines] == [3, 4]
