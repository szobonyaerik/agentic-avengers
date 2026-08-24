"""The mutation gate leaves a record when it could NOT run (agents#16, the class of issue #69).

`MUTATION_POLICY=advisory` was set for two whole phases while the gate never once ran: no
`cosmic-ray.toml` at the repo root and the tool not installed. The hook said so on stderr and exited
0 - correctly, since advisory never blocks - but nothing durable distinguished "the gate did not
run" from "the gate ran and found nothing". A hypothesis then settled on the premise that
`MUTATION_POLICY=advisory` was its one changed variable, while the gate under test had never
executed, and what actually caught defects was hand-built drills the implementer substituted after
the gate produced nothing.

Advisory must keep NOT blocking. That is a deliberate decision, not the defect. What is asserted
here is that the absence is now written where a later reader looks.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from metrics_support import DOUBLE  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "hook_mutation.sh"

pytestmark = pytest.mark.subprocess(
    "the subject is a bash hook and the writer process it shells out to"
)


@pytest.fixture
def project(tmp_path: Path):
    """A project with a phase and NO cosmic-ray.toml - the measured shape exactly."""
    root = tmp_path / "proj"
    phase = root / "docs" / "features" / "demo" / "phases" / "8-auth"
    phase.mkdir(parents=True)
    store = tmp_path / "store"
    store.mkdir()
    writer = tmp_path / "fm-pipeline-metrics.sh"
    writer.write_text(DOUBLE, encoding="utf-8")
    writer.chmod(0o755)
    return root, phase, store, writer, tmp_path


def run_hook(project, policy: str = "advisory") -> subprocess.CompletedProcess:
    root, phase, store, writer, tmp = project
    payload = json.dumps({"tool_input": {"file_path": str(phase / "handover.md")}})
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload, capture_output=True, text=True, check=False,
        env={
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp),
            "CLAUDE_PROJECT_DIR": str(root),
            "MUTATION_POLICY": policy,
            "AVENGER_METRICS_CMD": str(writer),
            "AVENGER_METRICS_PROJECT": "unit-test",
            "AVENGER_METRICS_LOG": str(tmp / "diagnostics.log"),
            "DOUBLE_LOG": str(tmp / "calls.log"),
            "DOUBLE_STORE": str(store),
        },
    )


def recorded(store: Path) -> list[dict]:
    record = store / "phase-08.json"
    if not record.exists():
        return []
    return json.loads(record.read_text(encoding="utf-8"))["gate_calls"]


def test_a_missing_config_is_recorded_and_still_does_not_block(project) -> None:
    result = run_hook(project)

    assert result.returncode == 0, "advisory never blocks — that is not the defect"
    assert "could not run" in result.stderr
    (row,) = [c for c in recorded(project[2]) if c["stage"] == "mutation"]
    assert row["failure_cause"] == "did-not-run"
    assert row["verdict"] == "REVIEW", "a gate that did not run reached no verdict"
    assert "cosmic-ray.toml missing" in row["note"]


def test_the_record_is_written_under_enforce_too(project) -> None:
    """Under `enforce` the hook blocks, and the reason it blocked is still worth recording."""
    result = run_hook(project, policy="enforce")

    assert result.returncode == 2
    assert [c for c in recorded(project[2]) if c["stage"] == "mutation"]


def test_a_run_that_records_nothing_leaves_no_row(project) -> None:
    """`MUTATION_POLICY=off` runs no mutation tool anywhere, so there is no absence to report."""
    result = run_hook(project, policy="off")

    assert result.returncode == 0
    assert recorded(project[2]) == []
