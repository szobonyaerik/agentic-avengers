"""The tavern monitor server — a read-only window onto every agent this machine is running.

Stdlib only, by the same ponytail rule the implementers live under: the repo has zero Python
runtime dependencies and a monitor is no reason to grow one. Run it with:

    python3 tavern/server.py                    # watch the roots in tavern/tavern.toml
    python3 tavern/server.py --demo             # synthetic 3-job fleet (see demo.py)
    python3 tavern/server.py --root . --fm-home ~/fm --fm-bin ~/src/firstmate/bin

Endpoints:
    GET  /                → tavern/index.html (the pixel-art bar)
    GET  /api/state       → merged snapshot: firstmate crew + pipeline features + live subagents
    GET  /api/agent/<id>  → detail panel data; id is "crew:<id>" or "live:<agent_id|agent_type>"
    POST /api/focus       → {"id": "crew:<id>"} → tmux select-window to that crewmate's terminal

Binds 127.0.0.1 on purpose — this shows worktree paths and transcript excerpts; putting it on a
public interface is a decision the operator must make by hand (--bind), not a default.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo as demo_mod  # noqa: E402
import sources  # noqa: E402

HERE = Path(__file__).resolve().parent

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11 has no stdlib TOML; config is optional
    tomllib = None


class Config:
    def __init__(self) -> None:
        self.bind = "127.0.0.1"
        self.port = 8377
        self.roots: list[Path] = []
        self.fm_home: Path | None = None
        self.fm_bin: Path | None = None
        self.cache_secs = 2.0
        self.demo = False


def load_config(args: argparse.Namespace) -> Config:
    cfg = Config()
    config_path = Path(args.config) if args.config else HERE / "tavern.toml"
    if tomllib is not None and config_path.is_file():
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
        base = config_path.resolve().parent
        cfg.bind = data.get("bind", cfg.bind)
        cfg.port = int(data.get("port", cfg.port))
        cfg.cache_secs = float(data.get("cache_secs", cfg.cache_secs))
        cfg.roots = [(base / r).resolve() for r in data.get("roots", [])]
        if data.get("fm_home"):
            cfg.fm_home = Path(data["fm_home"]).expanduser().resolve()
        if data.get("fm_bin"):
            cfg.fm_bin = Path(data["fm_bin"]).expanduser().resolve()
    if args.root:
        cfg.roots = [Path(r).expanduser().resolve() for r in args.root]
    if args.fm_home:
        cfg.fm_home = Path(args.fm_home).expanduser().resolve()
    if args.fm_bin:
        cfg.fm_bin = Path(args.fm_bin).expanduser().resolve()
    if args.port:
        cfg.port = args.port
    if args.bind:
        cfg.bind = args.bind
    cfg.demo = bool(args.demo)
    return cfg


class StateBuilder:
    """Builds the merged snapshot, cached briefly so the browser poll never stampedes the
    subprocess-backed adapters (pipeline_state.py, fm-fleet-snapshot.sh)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._cached_at = 0.0

    def state(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self.cfg.cache_secs:
                return self._cached
            state = self._build()
            self._cached, self._cached_at = state, now
            return state

    def _build(self) -> dict:
        if self.cfg.demo:
            state = demo_mod.demo_state(self.started)
        else:
            state = self._build_live()
        state["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return state

    def _build_live(self) -> dict:
        fleet = sources.read_fleet(self.cfg.fm_home, self.cfg.fm_bin)
        crew = []
        for member in fleet["crew"]:
            sentence = member["last_status"].split(": ", 1)[-1] if member["last_status"] else ""
            crew.append({**member, "sentence": sentence})

        features: list[dict] = []
        live_agents: list[dict] = []
        moments: list[dict] = []
        source_notes = {"fleet": fleet["status"]}
        for root in self.cfg.roots:
            pipeline = sources.read_pipeline(root)
            source_notes[f"pipeline:{root.name}"] = pipeline["status"]
            for feature in pipeline["features"]:
                features.append({"root": str(root), **feature})

            activity = sources.read_activity(root)
            source_notes[f"activity:{root.name}"] = activity["status"]
            for entry in activity["live"]:
                sentence = ""
                transcript = entry.get("transcript_path")
                if transcript:
                    doings = sources.transcript_tail(Path(transcript), limit=1)
                    if doings:
                        doing = doings[-1]
                        name = doing.get("name", "")
                        sentence = f"{name}: {doing['summary']}" if name else doing["summary"]
                live_agents.append({**entry, "root": str(root), "sentence": sentence})
            for rec in activity["recent"][-10:]:
                kind = "spawn" if rec["event"] == "SubagentStart" else "finish"
                moments.append({
                    "kind": kind, "ts": rec.get("ts"),
                    "text": f"{rec.get('agent_type', 'agent')} {'enters' if kind == 'spawn' else 'leaves'} the tavern",
                })

            overrides = root / "gate-overrides.log"
            text = overrides.is_file() and overrides.read_text(encoding="utf-8", errors="replace")
            if text:
                last = text.strip().splitlines()[-1]
                moments.append({"kind": "break_glass", "ts": last.split("\t", 1)[0],
                                "text": f"break-glass on record: {last[:120]}"})

        return {
            "mode": "live",
            "sources": source_notes,
            "crew": crew,
            "features": features,
            "live_agents": live_agents,
            "moments": moments[-15:],
        }

    # ------------------------------------------------------------ detail + actions

    def agent_detail(self, agent_id: str) -> dict:
        if self.cfg.demo:
            return self._demo_detail(agent_id)
        kind, _, key = agent_id.partition(":")
        if kind == "crew":
            fleet = sources.read_fleet(self.cfg.fm_home, self.cfg.fm_bin)
            for member in fleet["crew"]:
                if member["id"] == key:
                    # merge order matters: the member's own "kind" (ship/scout) must not clobber
                    # the panel discriminator, so it moves to crew_kind
                    detail = {**member, "crew_kind": member.get("kind", ""), "kind": "crew"}
                    if self.cfg.fm_home is not None:
                        for name in ("brief", "report"):
                            path = self.cfg.fm_home / "data" / key / f"{name}.md"
                            if path.is_file():
                                detail[name] = path.read_text(encoding="utf-8",
                                                              errors="replace")[:20_000]
                    return detail
            return {"error": f"no crewmate {key!r}"}
        if kind == "live":
            for root in self.cfg.roots:
                for entry in sources.read_activity(root)["live"]:
                    if key in (entry.get("agent_id"), entry.get("agent_type")):
                        detail = {**entry, "root": str(root), "kind": "live"}
                        transcript = entry.get("transcript_path")
                        if transcript:
                            detail["doings"] = sources.transcript_tail(Path(transcript))
                        return detail
            return {"error": f"no live agent {key!r}"}
        return {"error": f"unknown id scheme {agent_id!r}"}

    def _demo_detail(self, agent_id: str) -> dict:
        state = demo_mod.demo_state(self.started)
        kind, _, key = agent_id.partition(":")
        if kind == "crew":
            for member in state["crew"]:
                if member["id"] == key:
                    return {**member, "crew_kind": member.get("kind", ""), "kind": "crew",
                            "brief": f"# Demo brief\n\nA placeholder charter for `{key}`.",
                            "status_tail": [member["last_status"]]}
        if kind == "live":
            for entry in state["live_agents"]:
                if key in (entry.get("agent_id"), entry.get("agent_type")):
                    return {"kind": "live", **entry, "doings": [
                        {"kind": "tool", "name": "Edit", "summary": "src/example.py"},
                        {"kind": "tool", "name": "Bash", "summary": "pytest tests/demo -q"},
                        {"kind": "text", "summary": entry.get("sentence", "")},
                    ]}
        return {"error": f"no demo agent {agent_id!r}"}

    def focus(self, agent_id: str) -> dict:
        if self.cfg.demo:
            return {"ok": False, "error": "demo mode has no terminals"}
        kind, _, key = agent_id.partition(":")
        if kind != "crew":
            return {"ok": False, "error": "only crewmates have terminals; "
                                          "in-session subagents live inside their parent session"}
        fleet = sources.read_fleet(self.cfg.fm_home, self.cfg.fm_bin)
        for member in fleet["crew"]:
            if member["id"] == key:
                return sources.focus_window(member.get("window", ""))
        return {"ok": False, "error": f"no crewmate {key!r}"}


class Handler(BaseHTTPRequestHandler):
    builder: StateBuilder  # set by serve()

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path in ("/", "/index.html"):
            try:
                body = (HERE / "index.html").read_bytes()
            except OSError:
                self.send_error(404, "index.html missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self._send_json(self.builder.state())
        elif self.path.startswith("/api/agent/"):
            agent_id = unquote(self.path[len("/api/agent/"):])
            self._send_json(self.builder.agent_detail(agent_id))
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/api/focus":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            agent_id = str(payload.get("id", ""))
        except (ValueError, TypeError):
            self._send_json({"ok": False, "error": "bad request body"}, status=400)
            return
        self._send_json(self.builder.focus(agent_id))

    def log_message(self, fmt: str, *args) -> None:  # quiet: the poll would spam the terminal
        pass


def serve(cfg: Config) -> ThreadingHTTPServer:
    Handler.builder = StateBuilder(cfg)
    server = ThreadingHTTPServer((cfg.bind, cfg.port), Handler)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="pixel-art tavern monitor for agent fleets")
    parser.add_argument("--config", help="path to tavern.toml (default: alongside server.py)")
    parser.add_argument("--root", action="append",
                        help="project root to watch (repeatable; overrides config roots)")
    parser.add_argument("--fm-home", help="firstmate FM_HOME (state/ + data/)")
    parser.add_argument("--fm-bin", help="firstmate bin/ dir (enables fm-fleet-snapshot.sh)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--bind", default="")
    parser.add_argument("--demo", action="store_true", help="serve the synthetic 3-job fleet")
    args = parser.parse_args()

    cfg = load_config(args)
    if not cfg.demo and not cfg.roots and cfg.fm_home is None:
        parser.error("nothing to watch: give --root/--fm-home, a tavern.toml, or --demo")
    server = serve(cfg)
    mode = "demo" if cfg.demo else f"live, {len(cfg.roots)} root(s)"
    print(f"tavern open at http://{cfg.bind}:{cfg.port}/  [{mode}]")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
