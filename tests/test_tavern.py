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
    cfg.scan = False  # auto-discovery would pull in this machine's real sessions
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
    cfg.scan = False
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


def test_firstmate_panel_detail(tavern):
    detail = get(tavern, "/api/agent/fm")
    assert detail["kind"] == "fm"
    assert detail["crew_count"] == 1
    assert detail["crew"][0]["id"] == "brave-anvil"
    assert "read-only" in detail["note"]


def test_live_agent_detail_links_its_crewmate(tmp_path):
    # the crewmate's worktree IS the watched root -> the live agent panel gets a focus target
    root = tmp_path / "wt"
    root.mkdir()
    (root / ".agent-activity.jsonl").write_text(json.dumps({
        "ts": "2026-08-05T10:00:00+0000", "event": "SubagentStart",
        "agent_type": "plan-build-verify:avenger-spec-writer", "agent_id": "a9",
    }) + "\n")
    home = tmp_path / "fm"
    (home / "state").mkdir(parents=True)
    (home / "state" / "r1.meta").write_text(f"window=firstmate:fm-r1\nworktree={root}\nkind=ship\n")
    cfg = Config()
    cfg.roots = [root]
    cfg.fm_home = home
    cfg.cache_secs = 0.0
    cfg.scan = False
    builder = StateBuilder(cfg)
    (live,) = builder.state()["live_agents"]
    assert live["crew_id"] == "r1"
    detail = builder.agent_detail("live:a9")
    assert detail["crew_id"] == "r1"
    assert detail["crew_window"] == "firstmate:fm-r1"


def test_sessions_are_discovered_and_watched_without_configuration(tmp_path, monkeypatch):
    """The '+1' ask: agents appear without the operator naming their paths.

    A transcript under CLAUDE_CONFIG_DIR/projects names its session's cwd; that cwd becomes a
    watched root, so its activity log feeds live agents with zero --root flags.
    """
    workdir = tmp_path / "someproject"
    workdir.mkdir()
    (workdir / ".agent-activity.jsonl").write_text(json.dumps({
        "ts": "2026-08-05T10:00:00+0000", "event": "SubagentStart",
        "agent_type": "general-purpose", "agent_id": "g1",
    }) + "\n")
    config_dir = tmp_path / "claude-home"
    proj = config_dir / "projects" / "-someproject"
    proj.mkdir(parents=True)
    (proj / "sess-1234.jsonl").write_text(json.dumps({"cwd": str(workdir)}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    cfg = Config()
    cfg.cache_secs = 0.0  # scan stays on: that is the feature under test
    cfg.proc_check = False  # tmp fixture dirs have no live harness process
    state = StateBuilder(cfg).state()

    (session,) = state["sessions"]
    assert session["session_id"] == "sess-1234"
    assert session["cwd"] == str(workdir)
    (live,) = state["live_agents"]
    assert live["agent_id"] == "g1"
    assert live["root"] == str(workdir)


def test_stale_break_glass_is_history_not_a_moment(project, fm_home):
    # the vibrating-tavern bug: a day-old gate-overrides.log line replayed as a fresh moment
    import os
    log = project / "gate-overrides.log"
    log.write_text("2026-08-04T07:18:03Z\tuser\tgate:fidelity\treason: credit exhausted\n")
    old = 1_700_000_000
    os.utime(log, (old, old))
    cfg = Config()
    cfg.roots = [project]
    cfg.fm_home = fm_home
    cfg.cache_secs = 0.0
    cfg.scan = False
    state = StateBuilder(cfg).state()
    assert not [m for m in state["moments"] if m["kind"] == "break_glass"]


def test_fresh_break_glass_is_a_moment(project, fm_home):
    log = project / "gate-overrides.log"
    log.write_text("2026-08-05T14:00:00Z\tuser\tgate:fidelity\treason: fresh override\n")
    cfg = Config()
    cfg.roots = [project]
    cfg.fm_home = fm_home
    cfg.cache_secs = 0.0
    cfg.scan = False
    state = StateBuilder(cfg).state()
    (moment,) = [m for m in state["moments"] if m["kind"] == "break_glass"]
    assert "fresh override" in moment["text"]


def test_gone_quiet_subagent_leaves_the_tavern(tmp_path):
    # a start with no stop, whose own transcript stopped moving: the ghost-at-the-table bug
    import os
    root = tmp_path / "wt"
    root.mkdir()
    parent = tmp_path / "claude" / "sess.jsonl"
    sub_dir = tmp_path / "claude" / "sess" / "subagents"
    sub_dir.mkdir(parents=True)
    parent.write_text("{}\n")
    quiet = sub_dir / "agent-a1.jsonl"
    quiet.write_text("{}\n")
    os.utime(quiet, (1_700_000_000, 1_700_000_000))
    (root / ".agent-activity.jsonl").write_text(json.dumps({
        "ts": "2026-08-05T10:00:00+0000", "event": "SubagentStart", "agent_type": "avenger-verifier",
        "agent_id": "a1", "transcript_path": str(parent),
    }) + "\n")
    import sources
    assert sources.read_activity(root)["live"] == []
    # a transcript still being written keeps the agent seated
    os.utime(quiet, None)
    (live,) = sources.read_activity(root)["live"]
    assert live["agent_id"] == "a1"


def test_live_agent_detail_reaches_discovered_roots(tmp_path, monkeypatch):
    # the 'gone: no live agent' bug — the sprite came from a discovered root the detail
    # lookup never searched
    workdir = tmp_path / "someproject"
    workdir.mkdir()
    (workdir / ".agent-activity.jsonl").write_text(json.dumps({
        "ts": "2026-08-05T10:00:00+0000", "event": "SubagentStart",
        "agent_type": "general-purpose", "agent_id": "g1",
    }) + "\n")
    config_dir = tmp_path / "claude-home"
    proj = config_dir / "projects" / "-someproject"
    proj.mkdir(parents=True)
    (proj / "sess-9.jsonl").write_text(json.dumps({"cwd": str(workdir)}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    cfg = Config()
    cfg.cache_secs = 0.0
    cfg.proc_check = False  # tmp fixture dirs have no live harness process
    detail = StateBuilder(cfg).agent_detail("live:g1")
    assert detail["kind"] == "live"
    assert detail["root"] == str(workdir)


def test_summarize_turns_a_report_into_a_glance():
    from server import summarize
    report = ("needs-decision: phase 4 halted at the spec-review gate. Specs 4.1-4.5 written and "
              "pushed (c0c7be3), all fidelity REVIEW, none approved. The gate returned NO-GO...")
    short = summarize(report.split(": ", 1)[-1])
    assert short == "phase 4 halted at the spec-review gate."
    assert len(summarize("x" * 500)) <= 110
    assert summarize("") == ""


def test_closed_sessions_lose_their_seat(tmp_path, monkeypatch):
    # a fresh transcript whose harness process is gone: recency is not liveness
    import sources
    workdir = tmp_path / "closedproject"
    workdir.mkdir()
    config_dir = tmp_path / "claude-home"
    proj = config_dir / "projects" / "-closedproject"
    proj.mkdir(parents=True)
    (proj / "sess-dead.jsonl").write_text(json.dumps({"cwd": str(workdir)}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(sources, "_live_cwds", lambda: set())
    assert sources.discover_sessions() == []
    # and when the process table is unreadable, fall back to recency rather than an empty bar
    monkeypatch.setattr(sources, "_live_cwds", lambda: None)
    (session,) = sources.discover_sessions()
    assert session["session_id"] == "sess-dead"


def test_session_debug_reports_verdict_per_session(tmp_path, monkeypatch):
    import sources
    workdir = tmp_path / "someproject"
    workdir.mkdir()
    config_dir = tmp_path / "claude-home"
    proj = config_dir / "projects" / "-someproject"
    proj.mkdir(parents=True)
    (proj / "sess-1.jsonl").write_text(json.dumps({"cwd": str(workdir)}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(sources, "_live_cwds", lambda: set())
    monkeypatch.setattr(sources, "_harness_processes", lambda: [])
    report = sources.session_debug()
    (verdict,) = report["sessions"]
    assert verdict["kept"] is False
    assert "no harness process" in verdict["reason"]
    monkeypatch.setattr(sources, "_live_cwds", lambda: {str(workdir)})
    report = sources.session_debug()
    (verdict,) = report["sessions"]
    assert verdict["kept"] is True


def test_harness_helpers_are_not_liveness(tmp_path):
    # verbatim shapes from the field: daemon + pty-host + spare helpers outlive closed sessions
    import sources
    ps = "\n".join([
        '72037 /Users/x/.local/bin/claude daemon run --origin transient --spawned-by {"cwd":"/p"}',
        "54331 claude bg-pty-host --bg-pty-host /tmp/cc-daemon/a.pty.sock 200 50 -- /x/2.1.221",
        "54351 claude bg-spare --bg-spare /tmp/cc-daemon/a.claim.sock",
        "9606 claude --dangerously-skip-permissions --effort high -cFIRSTMATE_OP: v1 launch-brief",
        "38370 claude",
        "1259 opencode run --format json -m opencode-go/deepseek-v4-pro You are a reviewer",
        "1300 grep claude something",
    ])
    rows = sources._parse_harness_rows(ps)
    pids = [pid for pid, _ in rows]
    assert pids == ["9606", "38370", "1259"]


def test_each_character_gets_its_own_viewer_session(tmp_path, monkeypatch):
    # firstmate crews share ONE tmux session; two clicks must yield two independent viewers
    import shutil
    import subprocess as sp
    import sources
    if not shutil.which("tmux"):
        pytest.skip("no tmux on this machine")
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))  # private tmux server for this test
    def tmux(*a):
        return sp.run(["tmux", *a], capture_output=True, text=True, timeout=10)
    try:
        assert tmux("new-session", "-d", "-s", "fleet", "-n", "mate").returncode == 0
        assert tmux("new-window", "-t", "fleet", "-n", "crew").returncode == 0

        r1 = sources.focus_window("fleet:mate")
        r2 = sources.focus_window("fleet:crew")
        # no terminal can open headless, but the viewer sessions must exist and point apart
        sessions = tmux("list-sessions", "-F", "#{session_name}").stdout.split()
        assert "gate-fleet-mate" in sessions and "gate-fleet-crew" in sessions
        w1 = tmux("display-message", "-p", "-t", "gate-fleet-mate", "#{window_name}").stdout.strip()
        w2 = tmux("display-message", "-p", "-t", "gate-fleet-crew", "#{window_name}").stdout.strip()
        assert (w1, w2) == ("mate", "crew")
        # clicking again reuses the same viewer instead of minting a third
        sources.focus_window("fleet:mate")
        assert tmux("list-sessions", "-F", "#{session_name}").stdout.split().count("gate-fleet-mate") == 1
        # the two field lessons: the server survives an empty moment, viewers reap themselves
        assert tmux("show", "-s", "exit-empty").stdout.strip() == "exit-empty off"
        hooks = tmux("show-hooks", "-t", "gate-fleet-mate").stdout
        assert "client-detached" in hooks and "kill-session" in hooks
        # a vanished base session is a clear error, not a half-created viewer
        gone = sources.focus_window("nosuch:win")
        assert gone["ok"] is False and "no longer exists" in gone["error"]
        for r in (r1, r2):
            assert r["attach_cmd"].startswith("tmux attach -t gate-fleet-")
    finally:
        tmux("kill-server")
