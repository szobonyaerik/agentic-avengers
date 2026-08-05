"""Integration tests for the tavern monitor server.

The seam under test is the HTTP API — the same surface the pixel frontend polls — served over a
real socket against a real fixture tree on disk: an activity log the hook format writes, a
firstmate-shaped FM_HOME, and a feature tree the actual pipeline_state.py resolver runs against.
Mocks would just re-state the adapters; the point is that the merged snapshot degrades honestly
when a source is missing and reports it when present.
"""

import json
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tavern"))

from server import Config, Handler, StateBuilder, serve  # noqa: E402

SPEC = """---
feature: demo
phase: 1-core
spec: "1.1"
status: done
review_status: approved
fidelity_verdict: GO
criticality: normal
---

# Spec
"""


@pytest.fixture()
def project(tmp_path):
    """A watched root with one feature, the real resolver vendored in, and an activity log."""
    root = tmp_path / "proj"
    spec_dir = root / "docs" / "features" / "demo" / "phases" / "1-core" / "specs" / "1.1-thing"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(SPEC)
    (spec_dir.parents[1] / "verdict.json").write_text(json.dumps({
        "verdict": "pass", "tests": {"total": 3, "passed": 3, "failed": 0}, "findings": [],
    }))
    (root / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "pipeline_state.py", root / "scripts" / "pipeline_state.py")
    (root / ".agent-activity.jsonl").write_text(
        json.dumps({"ts": "2026-08-05T10:00:00+0000", "event": "SubagentStart",
                    "agent_type": "avenger-verifier", "agent_id": "a1"}) + "\n"
        + json.dumps({"ts": "2026-08-05T10:01:00+0000", "event": "SubagentStart",
                      "agent_type": "avenger-handover", "agent_id": "a2"}) + "\n"
        + json.dumps({"ts": "2026-08-05T10:02:00+0000", "event": "SubagentStop",
                      "agent_type": "avenger-handover", "agent_id": "a2"}) + "\n"
    )
    return root


@pytest.fixture()
def fm_home(tmp_path):
    """A firstmate-shaped home: state/<id>.meta + .status, data/<id>/brief.md."""
    home = tmp_path / "fm"
    (home / "state").mkdir(parents=True)
    (home / "state" / "brave-anvil.meta").write_text(
        "window=fm:brave-anvil\nworktree=/tmp/wt/1\nproject=proj\nharness=claude\n"
        "kind=ship\nmode=no-mistakes\n"
    )
    (home / "state" / "brave-anvil.status").write_text(
        "spawned: brief delivered\nworking: running the suite\n"
    )
    (home / "data" / "brave-anvil").mkdir(parents=True)
    (home / "data" / "brave-anvil" / "brief.md").write_text("# Charter\nDo the thing.")
    return home


@pytest.fixture()
def tavern(project, fm_home):
    cfg = Config()
    cfg.roots = [project]
    cfg.fm_home = fm_home
    cfg.fm_bin = None
    cfg.port = 0  # ephemeral: tests must not fight over a fixed port
    cfg.cache_secs = 0.0
    server = serve(cfg)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read())


def post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def test_state_merges_all_sources(tavern):
    state = get(tavern, "/api/state")
    assert state["mode"] == "live"

    # fleet: the crewmate from FM_HOME, with the wake-event tail condensed to a sentence
    (member,) = state["crew"]
    assert member["id"] == "brave-anvil"
    assert member["mode"] == "no-mistakes"
    assert member["sentence"] == "running the suite"

    # pipeline: the real resolver ran against the fixture feature
    (feature,) = state["features"]
    assert feature["feature"] == "demo"
    assert feature["stage"]  # whatever the resolver owes next — presence is the contract
    assert feature["specs"][0]["fidelity_verdict"] == "GO"
    assert feature["verdicts"][0]["verdict"] == "pass"

    # activity: started-not-stopped agents only
    (live,) = state["live_agents"]
    assert live["agent_type"] == "avenger-verifier"
    assert any("avenger-handover leaves" in m["text"] for m in state["moments"])


def test_agent_detail_and_focus(tavern):
    detail = get(tavern, "/api/agent/crew:brave-anvil")
    assert detail["kind"] == "crew"
    assert detail["crew_kind"] == "ship"
    assert "Do the thing." in detail["brief"]
    assert detail["status_tail"][-1] == "working: running the suite"

    live = get(tavern, "/api/agent/live:a1")
    assert live["kind"] == "live"
    assert live["agent_type"] == "avenger-verifier"

    missing = get(tavern, "/api/agent/crew:nobody")
    assert "error" in missing

    # focus on a live subagent is refused with the honest explanation, not a tmux attempt
    res = post(tavern, "/api/focus", {"id": "live:a1"})
    assert res["ok"] is False
    assert "parent session" in res["error"]


def test_index_served(tavern):
    with urllib.request.urlopen(tavern + "/", timeout=10) as resp:
        body = resp.read().decode()
    assert "GRINNING GATE" in body


def test_state_degrades_when_sources_absent(tmp_path):
    cfg = Config()
    cfg.roots = [tmp_path / "nothing-here"]
    cfg.cache_secs = 0.0
    state = StateBuilder(cfg).state()
    assert state["mode"] == "live"
    assert state["crew"] == [] and state["features"] == [] and state["live_agents"] == []
    assert state["sources"]["fleet"] == "absent"


def test_demo_state_shape_matches_live(tmp_path):
    cfg = Config()
    cfg.demo = True
    state = StateBuilder(cfg).state()
    assert state["mode"] == "demo"
    assert {m["id"] for m in state["crew"]} == {"brave-anvil", "quiet-lantern", "gilded-fox"}
    for member in state["crew"]:
        assert {"id", "project", "worktree", "window", "kind", "last_status", "sentence"} <= set(member)


def test_handler_has_no_default_logging():
    # the browser polls every 2s; BaseHTTPRequestHandler's default stderr log would flood a tmux pane
    assert Handler.log_message.__qualname__.startswith("Handler")
