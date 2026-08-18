"""Shared fixtures for the metrics tests: a double writer, and the real firstmate CLI when present.

Two levels, deliberately.

The DOUBLE proves the exact commands this pipeline emits — argv, value encoding, identity fields,
and the read-modify-write behaviour the emission points depend on — without needing anything outside
this repo, so the suite never goes quiet on a machine that has no firstmate.

The REAL firstmate CLI proves the records those commands produce actually VALIDATE against the
schema firstmate owns. That is the only claim that matters and the one a double can never make: a
double agrees with whatever it was written to agree with. It skips when firstmate is not installed.

The double implements `init`, `show`, `set` and `add` only — the four the sink uses — and it
enforces neither the schema nor the ledger invariant, on purpose. Anything that depends on those
belongs in a `real_sink` test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: A writer that records its argv and keeps just enough state for read-modify-write to work.
DOUBLE = r'''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
log, store = os.environ["DOUBLE_LOG"], os.environ["DOUBLE_STORE"]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\n")

forced = os.environ.get("DOUBLE_EXIT")
if forced:
    sys.exit(int(forced))
if argv and argv[0] == os.environ.get("DOUBLE_REFUSE"):
    sys.stderr.write("refusing %s on purpose\n" % argv[0])
    sys.exit(3)

def path_for(phase):
    return os.path.join(store, "phase-%02d.json" % int(phase))

def parse(assignments):
    out = {}
    for item in assignments:
        key, _, raw = item.partition("=")
        if key.endswith(":"):
            out[key[:-1]] = json.loads(raw)
        else:
            out[key] = raw
    return out

verb = argv[0] if argv else ""
if verb == "init":
    target = path_for(argv[1])
    if os.path.exists(target):
        sys.exit(3)
    os.makedirs(store, exist_ok=True)
    record = {"schema": "fm-pipeline-metrics.v1", "phase": "%02d" % int(argv[1]),
              "project": None, "baseline_phases": [], "opened": None, "closed": None,
              "elapsed_minutes": None, "spec_rounds": None, "verification_attempts": None,
              "connection_drops": None, "tests_before": None, "tests_after": None,
              "specs": [], "gate_calls": [], "overrides": [], "stalls": [],
              "skill_loads": [], "defects": [], "hypotheses": [], "findings": []}
    if "--project" in argv:
        record["project"] = argv[argv.index("--project") + 1]
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    sys.exit(0)

target = path_for(argv[1])
if not os.path.exists(target):
    sys.exit(3)
with open(target, encoding="utf-8") as fh:
    record = json.load(fh)

if verb == "show":
    json.dump(record, sys.stdout)
    sys.exit(0)
if verb == "set":
    record.update(parse(argv[2:]))
elif verb == "add":
    collection = argv[2]
    fields = parse(argv[3:])
    identity = "key" if collection == "findings" else "id"
    entries = record.setdefault(collection, [])
    for entry in entries:
        if entry.get(identity) == fields.get(identity):
            entry.update(fields)
            break
    else:
        entries.append(fields)
else:
    sys.exit(2)
with open(target, "w", encoding="utf-8") as fh:
    json.dump(record, fh)
sys.exit(0)
'''


def read_calls(log: Path) -> list[list[str]]:
    """Every argv the double was invoked with, in order."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _reset_sink(monkeypatch) -> None:
    """The sink abandons an unusable writer for the life of the process; tests get a fresh one."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import metrics_sink

    monkeypatch.setattr(metrics_sink, "_writer_unusable", False)
    monkeypatch.setattr(metrics_sink, "_announced", False)


def _clean_env(monkeypatch) -> None:
    monkeypatch.delenv("AVENGER_METRICS_OFF", raising=False)
    for name in ("PHASE", "PHASE_DIR", "SPEC", "SPEC_PATH", "STAGE", "ATTEMPT", "LOG"):
        monkeypatch.delenv(f"AVENGER_METRICS_{name}", raising=False)
    for name in ("DOUBLE_EXIT", "DOUBLE_REFUSE"):
        monkeypatch.delenv(name, raising=False)
    _reset_sink(monkeypatch)


@pytest.fixture
def stub_sink(tmp_path, monkeypatch):
    """Point the sink at the double. Yields (project_dir, store_dir, call_log)."""
    project = tmp_path / "project"
    project.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    log = tmp_path / "calls.log"
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    monkeypatch.setenv("AVENGER_METRICS_CMD", str(double))
    monkeypatch.setenv("DOUBLE_LOG", str(log))
    monkeypatch.setenv("DOUBLE_STORE", str(store))
    monkeypatch.setenv("AVENGER_METRICS_PROJECT", "unit-test")
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "diagnostics.log"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    _clean_env(monkeypatch)
    return project, store, log


def firstmate_cli() -> str | None:
    """firstmate's own writer, if this machine has it."""
    explicit = os.environ.get("FM_PIPELINE_METRICS")
    if explicit and os.access(explicit, os.X_OK):
        return explicit
    beside = Path.home() / "Documents" / "GitHub" / "firstmate" / "bin" / "fm-pipeline-metrics.sh"
    if os.access(beside, os.X_OK):
        return str(beside)
    return shutil.which("fm-pipeline-metrics.sh")


@pytest.fixture
def real_sink(tmp_path, monkeypatch):
    """Point the sink at the real firstmate CLI in a throwaway home. Yields (project, fm_home)."""
    cli = firstmate_cli()
    if cli is None:
        pytest.skip("firstmate's fm-pipeline-metrics.sh is not available on this machine")
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "fm-home"
    home.mkdir()
    monkeypatch.setenv("FM_HOME", str(home))
    monkeypatch.setenv("AVENGER_METRICS_CMD", cli)
    monkeypatch.setenv("AVENGER_METRICS_PROJECT", "unit-test")
    monkeypatch.setenv("AVENGER_METRICS_LOG", str(tmp_path / "diagnostics.log"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    _clean_env(monkeypatch)
    return project, home


def stored(store_or_home: Path, phase: str) -> dict:
    """The record on disk — asserted as bytes, never through the command that wrote it."""
    direct = store_or_home / f"phase-{phase}.json"
    if direct.exists():
        return json.loads(direct.read_text(encoding="utf-8"))
    return json.loads(
        (store_or_home / "data" / "pipeline-metrics" / f"phase-{phase}.json").read_text("utf-8")
    )


def git_init(project: Path) -> None:
    """A real repo at `project` — `record_phase_close`'s landing check needs one to answer at all."""
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=project, check=True)


def git_land(project: Path, message: str = "land") -> None:
    """Commit everything under `project` — what makes a phase directory LANDED for issue #46."""
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=project, check=True)


def write_spec(project: Path, phase: int, spec: str, body: str) -> Path:
    """A spec at the layout every emission point derives its phase and spec id from."""
    path = (project / "docs" / "features" / "demo" / "phases" / f"{phase}-slug"
            / "specs" / f"{spec}-sub" / "spec.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nfeature: demo\nstatus: draft\n---\n{body}", encoding="utf-8")
    return path
