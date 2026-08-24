"""Data adapters for the tavern monitor.

Every adapter is optional and degrades to an explicit absence marker instead of failing the
snapshot: the tavern must render whatever subset of the fleet exists on this machine — a lone
Claude Code session, a full firstmate fleet, or nothing but a demo. Nothing here mutates state;
the tavern is read-only over the same files the pipeline and firstmate already write.

Sources, in trust order:
- activity:  `.agent-activity.jsonl` per watched root (scripts/hook_activity.sh) — subagent
  lifecycle; a start without a matching stop is "in the bar".
- pipeline:  `scripts/pipeline_state.py` + spec frontmatter + verdict.json per watched root —
  the resolver is the single source of truth for "where is the run right now".
- fleet:     firstmate crewmates. Prefers `fm-fleet-snapshot.sh --json` (schema
  fm-fleet-snapshot.v1) when a firstmate checkout is configured; falls back to reading
  `$FM_HOME/state/<id>.meta` / `.status` directly, whose formats firstmate documents as stable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

AVENGER_STAGES = [
    "task-analyst",
    "solution-architect",
    "implementation-planner",
    "spec-writer",
    "spec-gate",
    "spec-review",
    "implementer",
    "verifier",
    "handover",
    "e2e-author",
    "done",
]

_SUBPROCESS_TIMEOUT = 15


def _read_text(path: Path, limit: int = 200_000) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return None


def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal scalar-only frontmatter parse — the pipeline's stamps are all flat `key: value`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip("'\"")
    return out


# ---------------------------------------------------------------- activity


def _subagent_transcript(entry: dict) -> Path | None:
    """Where this subagent's own transcript lives, derived from the parent's path."""
    parent, agent_id = entry.get("transcript_path"), entry.get("agent_id")
    if not parent or not agent_id or not str(parent).endswith(".jsonl"):
        return None
    return Path(str(parent)[: -len(".jsonl")]) / "subagents" / f"agent-{agent_id}.jsonl"


def _is_stale(entry: dict, stale_secs: int) -> bool:
    """A 'live' agent with no evidence of life is gone, SubagentStop event or not.

    Stops go missing in the real world — background subagents, killed sessions — and a ghost
    sitting at the table forever misreads as work happening. Evidence of life is the subagent's
    own transcript still being written; when that file exists and has gone quiet, the agent is
    treated as finished.
    """
    import time

    transcript = _subagent_transcript(entry)
    if transcript is None:
        return False  # nothing to judge by: keep showing it, the pairing logic owns this case
    try:
        return time.time() - transcript.stat().st_mtime > stale_secs
    except OSError:
        return False  # transcript not on disk (yet): too early to call it dead


def read_activity(root: Path, stale_secs: int = 900) -> dict:
    """Live subagents per root: start events not yet closed by a stop, minus the gone-quiet.

    Pairing is by agent_id when the harness provides one; otherwise starts and stops of the same
    agent_type pair LIFO — good enough for presence, and honest about it in the record ("paired").
    """
    log = root / ".agent-activity.jsonl"
    text = _read_text(log)
    if text is None:
        return {"status": "absent", "live": [], "recent": []}
    events = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("event") in ("SubagentStart", "SubagentStop"):
            events.append(rec)

    live_by_id: dict[str, dict] = {}
    live_by_type: dict[str, list[dict]] = {}
    for rec in events:
        agent_id = rec.get("agent_id")
        agent_type = rec.get("agent_type", "unknown")
        if rec["event"] == "SubagentStart":
            entry = {
                "agent_type": agent_type,
                "agent_id": agent_id,
                "since": rec.get("ts"),
                "transcript_path": rec.get("agent_transcript_path") or rec.get("transcript_path"),
                "paired": "id" if agent_id else "type",
            }
            if agent_id:
                live_by_id[agent_id] = entry
            else:
                live_by_type.setdefault(agent_type, []).append(entry)
        else:
            if agent_id and agent_id in live_by_id:
                live_by_id.pop(agent_id)
            elif live_by_type.get(agent_type):
                live_by_type[agent_type].pop()

    live = list(live_by_id.values()) + [e for stack in live_by_type.values() for e in stack]
    live = [e for e in live if not _is_stale(e, stale_secs)]
    return {"status": "ok", "live": live, "recent": events[-40:]}


# ---------------------------------------------------------------- pipeline


def _spec_gate_status(fields: dict) -> str:
    """A spec's machine-gate status, resolved by the pipeline's own module when it is vendored.

    The monitor watches other repositories, and one of them may not have `spec_gate_state.py` yet.
    Falling back to reading the raw stamp is right here and only here: this is a display, and a
    display that shows nothing is better than one that decides a gate question on its own.
    """
    try:
        import spec_gate_state
    except ImportError:
        return fields.get("spec_gate", "") or fields.get("fidelity_verdict", "")
    return spec_gate_state.status(fields)


def read_pipeline(root: Path) -> dict:
    """Pipeline state for every feature under a watched root, via the vendored resolver."""
    features_dir = root / "docs" / "features"
    resolver = root / "scripts" / "pipeline_state.py"
    if not features_dir.is_dir():
        return {"status": "absent", "features": []}

    features = []
    for feature_dir in sorted(p for p in features_dir.iterdir() if p.is_dir()):
        feature = feature_dir.name
        state: dict = {"feature": feature}
        if resolver.is_file():
            try:
                proc = subprocess.run(
                    ["python3", str(resolver), feature, "--root", str(root)],
                    capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
                )
                if proc.returncode == 0:
                    state.update(json.loads(proc.stdout))
                else:
                    state["error"] = (proc.stderr or "resolver failed").strip()[:300]
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                state["error"] = f"resolver: {exc}"[:300]
        else:
            state["error"] = "no scripts/pipeline_state.py under this root"

        specs = []
        for spec_md in sorted(feature_dir.glob("phases/*/specs/*/spec.md")):
            fm = parse_frontmatter(_read_text(spec_md, 4000) or "")
            specs.append({
                "spec": fm.get("spec") or spec_md.parent.name,
                "phase": fm.get("phase") or spec_md.parents[2].name,
                "status": fm.get("status", ""),
                # The ONE machine gate's stamp. `spec_gate_state.status` is the only place that
                # decides what it means — including how a legacy `fidelity_verdict` reads — so the
                # monitor never grows a second copy of that rule.
                "spec_gate": _spec_gate_status(fm),
                "review_status": fm.get("review_status", ""),
            })
        verdicts = []
        for verdict_json in sorted(feature_dir.glob("phases/*/verdict.json")):
            try:
                data = json.loads(_read_text(verdict_json, 20_000) or "")
                verdicts.append({
                    "phase": verdict_json.parent.name,
                    "verdict": data.get("verdict"),
                    "tests": data.get("tests"),
                    "findings": len(data.get("findings") or []),
                })
            except ValueError:
                continue
        state["specs"] = specs
        state["verdicts"] = verdicts
        features.append(state)
    return {"status": "ok", "features": features}


# ---------------------------------------------------------------- fleet (firstmate)


def _parse_meta(text: str) -> dict[str, str]:
    """`state/<id>.meta` is `key=value` records; tolerate both newline- and comma-separated."""
    out: dict[str, str] = {}
    for chunk in text.replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def _crew_from_state_dir(fm_home: Path) -> list[dict]:
    crew = []
    state_dir = fm_home / "state"
    if not state_dir.is_dir():
        return crew
    for meta_path in sorted(state_dir.glob("*.meta")):
        crew_id = meta_path.stem
        meta = _parse_meta(_read_text(meta_path, 8000) or "")
        status_text = _read_text(state_dir / f"{crew_id}.status", 20_000) or ""
        status_lines = [ln.strip() for ln in status_text.splitlines() if ln.strip()]
        crew.append({
            "id": crew_id,
            "meta": meta,
            "project": meta.get("project", ""),
            "worktree": meta.get("worktree", ""),
            "window": meta.get("window", ""),
            "harness": meta.get("harness", ""),
            "mode": meta.get("mode", ""),
            "kind": meta.get("kind", ""),
            # Firstmate is explicit that a status line is a wake EVENT, not current state — the
            # tavern shows it as "last heard", never as a liveness verdict.
            "last_status": status_lines[-1] if status_lines else "",
            "status_tail": status_lines[-12:],
        })
    return crew


def read_fleet(fm_home: Path | None, fm_bin: Path | None) -> dict:
    """Firstmate crewmates. Snapshot script first (one structured contract), state-dir fallback."""
    if fm_home is None:
        return {"status": "absent", "crew": [], "snapshot": None}
    result: dict = {"status": "ok", "crew": [], "snapshot": None}
    snapshot_script = None
    if fm_bin is not None:
        candidate = fm_bin / "fm-fleet-snapshot.sh"
        if candidate.is_file():
            snapshot_script = candidate
    if snapshot_script is not None:
        # The snapshot script runs foreign (firstmate) code with its own children. On timeout,
        # subprocess.run kills only the direct bash — grandchildren survive and PILE UP under a
        # polling server (observed in the field: five stuck fm-fleet-snapshot.sh). Run it in its
        # own process group and kill the whole group.
        import os
        import signal

        proc = None
        try:
            proc = subprocess.Popen(
                ["bash", str(snapshot_script), "--json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                cwd=str(fm_bin.parent), env=_fm_env(fm_home), start_new_session=True,
            )
            out, _err = proc.communicate(timeout=_SUBPROCESS_TIMEOUT)
            if proc.returncode == 0:
                result["snapshot"] = json.loads(out)
        except (OSError, ValueError, subprocess.SubprocessError):
            result["snapshot"] = None
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
    result["crew"] = _crew_from_state_dir(fm_home)
    if not result["crew"] and result["snapshot"] is None:
        result["status"] = "empty"
    return result


def _fm_env(fm_home: Path) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["FM_HOME"] = str(fm_home)
    return env


# ---------------------------------------------------------------- session discovery


def _transcript_cwd(path: Path) -> str | None:
    """The session's working directory, from the tail of its transcript JSONL."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        cwd = rec.get("cwd") if isinstance(rec, dict) else None
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()
    return None


def _live_cwds() -> set[str] | None:
    """Working directories of running harness processes (claude/opencode), or None if unknowable.

    A transcript's mtime says "recently written", not "still running" — a session closed twenty
    minutes ago looks identical. The process table is the liveness truth: no harness process
    working in a directory, no seat at the bar.
    """
    import os

    procs = _harness_processes()
    if procs is None:
        return None
    if not procs:
        return set()
    pids = [pid for pid, _ in procs]
    cwds: set[str] = set()
    if Path("/proc").is_dir():
        for pid in pids:
            try:
                cwds.add(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                continue
        return cwds
    try:
        lsof = subprocess.run(
            ["lsof", "-a", "-p", ",".join(pids), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):  # TimeoutExpired is NOT an OSError
        return None
    if lsof.returncode not in (0, 1):  # lsof exits 1 when some pids vanished mid-query
        return None
    for line in lsof.stdout.splitlines():
        if line.startswith("n"):
            cwds.add(line[1:])
    return cwds


# Claude Code 2.x keeps a daemon plus pty-host/spare helper processes running AFTER their
# sessions close, and they inherit real project cwds — counting them as liveness resurrects
# every closed session (observed in the field: `claude daemon run --spawned-by {...}`,
# `claude bg-pty-host ...`, `claude bg-spare ...`). Sessions are interactive processes; helpers
# are plumbing.
_HELPER_MARKERS = ("bg-pty-host", "bg-spare", "daemon run")


def _parse_harness_rows(ps_output: str) -> list[tuple[str, str]]:
    """(pid, args) rows that are interactive claude/opencode processes, helpers excluded."""
    procs: list[tuple[str, str]] = []
    for line in ps_output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, args = parts
        tokens = args.split()
        harness = {"claude", "opencode"}
        # argv0 may be the binary itself, or an interpreter with the binary's PATH as argv1
        # ("node /usr/local/bin/claude"); a bare word in argv1 ("grep claude") is not a harness.
        is_harness = tokens and tokens[0].rsplit("/", 1)[-1] in harness
        if not is_harness and len(tokens) > 1 and "/" in tokens[1]:
            is_harness = tokens[1].rsplit("/", 1)[-1] in harness
        if not is_harness:
            continue
        if any(marker in args for marker in _HELPER_MARKERS):
            continue
        procs.append((pid, args))
    return procs


def _harness_processes() -> list[tuple[str, str]] | None:
    """(pid, args) of running interactive claude/opencode processes; None when ps is unusable."""
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if ps.returncode != 0:
        return None
    return _parse_harness_rows(ps.stdout)


def session_debug() -> dict:
    """Everything the liveness filter saw, for /api/debug — inspection instead of assumption."""
    import os

    procs = _harness_processes()
    cwds = _live_cwds()
    candidates = discover_sessions(require_process=False)
    alive = {os.path.realpath(c) for c in cwds} if cwds is not None else None
    verdicts = []
    for session in candidates:
        real = os.path.realpath(session["cwd"])
        if alive is None:
            kept, reason = True, "process table unreadable -> mtime fallback"
        elif real in alive:
            kept, reason = True, "live harness process working here"
        else:
            kept, reason = False, "no harness process with this cwd"
        verdicts.append({**session, "kept": kept, "reason": reason})
    return {
        "harness_processes": [{"pid": p, "args": a[:200]} for p, a in (procs or [])],
        "ps_readable": procs is not None,
        "live_cwds": sorted(cwds) if cwds is not None else None,
        "sessions": verdicts,
    }


def discover_sessions(max_age_secs: int = 3600, limit: int = 12,
                      require_process: bool = True) -> list[dict]:
    """Every recently-active Claude Code session on this machine, no configuration needed.

    The harness writes one transcript per session under ~/.claude/projects/<munged-cwd>/, and each
    record carries the session's cwd. Recency comes from the transcript's mtime — an agent that is
    thinking or running tools keeps appending. This is how the tavern seats sessions the operator
    never told it about.
    """
    import os
    import time

    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"
    if not base.is_dir():
        return []
    now = time.time()
    sessions = []
    for transcript in base.glob("*/*.jsonl"):
        try:
            age = now - transcript.stat().st_mtime
        except OSError:
            continue
        if age > max_age_secs:
            continue
        cwd = _transcript_cwd(transcript)
        if not cwd:
            continue
        sessions.append({
            "session_id": transcript.stem,
            "cwd": cwd,
            "transcript_path": str(transcript),
            "age_secs": int(age),
        })
    sessions.sort(key=lambda s: s["age_secs"])
    # one seat per cwd: the newest session in a directory represents it
    seen: set[str] = set()
    unique = []
    for session in sessions:
        if session["cwd"] in seen:
            continue
        seen.add(session["cwd"])
        unique.append(session)
    if require_process:
        cwds = _live_cwds()
        if cwds is not None:  # tools unavailable -> keep mtime behaviour rather than empty the bar
            import os

            alive = {os.path.realpath(c) for c in cwds}
            unique = [s for s in unique if os.path.realpath(s["cwd"]) in alive]
    return unique[:limit]


def match_crew(crew: list[dict], root: str) -> dict | None:
    """Which crewmate owns this watched root? Worktree containment first, then exact project."""
    import os

    for member in crew:
        worktree = os.path.expanduser(member.get("worktree") or "")
        if worktree and (root == worktree or root.startswith(worktree.rstrip("/") + "/")):
            return member
    for member in crew:
        if member.get("project") and member["project"] == root:
            return member
    return None


def find_pane_by_path(target: Path) -> str | None:
    """The tmux window whose pane is working in `target`, if any — how the tavern focuses things
    that never wrote a `window=` meta line (the first mate, discovered sessions)."""
    try:
        proc = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_current_path}\t#{session_name}:#{window_index}"],
            capture_output=True, text=True, timeout=5,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        wanted = str(target.expanduser().resolve())
    except OSError:
        wanted = str(target)
    for line in proc.stdout.splitlines():
        path, _, window = line.partition("\t")
        if path and window and path == wanted:
            return window
    return None


# ---------------------------------------------------------------- focus / detail helpers


def _activate_app(app: str) -> bool:
    """Bring a named macOS app to the front. Best effort; False when it could not."""
    if sys.platform != "darwin" or not app:
        return False
    try:
        proc = subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to activate'],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except OSError:
        return False


def _client_tty_for_session(session: str) -> str | None:
    """The tty of a tmux client attached to THIS session (not just any client)."""
    try:
        proc = subprocess.run(
            ["tmux", "list-clients", "-F", "#{client_session}\t#{client_tty}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        client_session, _, tty = line.partition("\t")
        if client_session == session and tty:
            return tty
    return None


def _raise_terminal_by_tty(terminal_app: str, tty: str) -> bool:
    """Bring the specific Terminal.app WINDOW hosting `tty` to the front.

    Terminal windows expose their tty over AppleScript, so the right window — not just the app —
    can be raised. Other terminal apps don't expose this uniformly; they get an app-level
    activate instead.
    """
    if sys.platform != "darwin":
        return False
    if terminal_app in ("", "Terminal"):
        tty_name = tty.rsplit("/", 1)[-1]
        script = (
            'tell application "Terminal"\n'
            "  repeat with w in windows\n"
            f'    if (tty of w) contains "{tty_name}" then set frontmost of w to true\n'
            "  end repeat\n"
            "  activate\n"
            "end tell"
        )
        try:
            osa = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=10)
            return osa.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return _activate_app(terminal_app)


def _tmux(*args: str, timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)


def viewer_name(window: str) -> str:
    """The dedicated grouped-session name for one tavern character's terminal window."""
    import re

    return "gate-" + re.sub(r"[^A-Za-z0-9_-]", "-", window)


def focus_window(window: str, terminal_app: str = "") -> dict:
    """Give every character its OWN terminal window — even when they share one tmux session.

    Firstmate spawns crewmates as windows of a single tmux session, and a session has one current
    window per client: steering the shared client is how clicking the crewmate overwrote the
    firstmate's terminal. The fix is a tmux GROUPED session per character (`new-session -t`):
    same windows, independent current-window. Each character's viewer session gets its own
    attached Terminal window, raised by tty on later clicks and never shared with the others.
    """
    if not window:
        return {"ok": False, "error": "no window recorded in meta"}
    viewer = viewer_name(window)
    attach_cmd = f"tmux attach -t {viewer}"
    try:
        session, _, win_part = window.partition(":")
        if _tmux("has-session", "-t", "=" + session).returncode != 0:
            # No `attach_cmd`: the base session is gone, so there is nothing to attach to and
            # printing a command that cannot work would be worse than printing none.
            return {"ok": False,
                    "error": f"tmux session '{session}' no longer exists — the fleet restarted?"}
        # Field lesson: grouped sessions die with their group's last window, and with zero
        # sessions the tmux SERVER exits — a crewmate teardown once collapsed the whole fleet's
        # terminals. exit-empty off makes the server survive an empty moment; the client-detached
        # hook reaps a viewer when its terminal window closes (destroy-unattached would kill it
        # at birth: it destroys DETACHED sessions immediately, and viewers are born detached).
        _tmux("set", "-s", "exit-empty", "off")
        if _tmux("has-session", "-t", "=" + viewer).returncode != 0:
            made = _tmux("new-session", "-d", "-s", viewer, "-t", session)
            if made.returncode != 0:
                return {"ok": False, "error": (made.stderr or "tmux new-session failed").strip()[:200],
                        "attach_cmd": f"tmux attach -t {session} \\; select-window -t {window}"}
            _tmux("set-hook", "-t", viewer, "client-detached", f"kill-session -t {viewer}")
        target = f"{viewer}:{win_part}" if win_part else viewer
        sel = _tmux("select-window", "-t", target)
        if sel.returncode != 0:
            return {"ok": False, "error": (sel.stderr or "tmux select-window failed").strip()[:200],
                    "attach_cmd": attach_cmd}
        tty = _client_tty_for_session(viewer)
        if tty:
            # Every return that can name a viewer carries `attach_cmd` (issue #60). The success
            # paths were the only ones without it, and the case that needs it most is the degraded
            # success below: the viewer exists, its terminal could not be raised, and the hint tells
            # the operator to bring it forward themselves — with no command to do it with.
            if _raise_terminal_by_tty(terminal_app, tty):
                return {"ok": True, "method": f"raised this character's own terminal ({tty})",
                        "window": window, "attach_cmd": attach_cmd}
            return {"ok": True, "method": "switched this character's viewer session",
                    "window": window, "attach_cmd": attach_cmd,
                    "hint": "its terminal window exists but could not be raised — set "
                            "terminal_app in tavern.toml, or bring it forward yourself"}
        if sys.platform == "darwin":
            script = f'tell application "Terminal" to do script "tmux attach -t {viewer}"'
            osa = subprocess.run(
                ["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'],
                capture_output=True, text=True, timeout=10,
            )
            if osa.returncode == 0:
                return {"ok": True, "method": "opened this character's own Terminal window",
                        "window": window, "attach_cmd": attach_cmd}
        return {"ok": False,
                "error": "viewer session is ready but no terminal could be opened for it",
                "attach_cmd": attach_cmd}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "attach_cmd": attach_cmd}


def transcript_tail(path: Path, limit: int = 30) -> list[dict]:
    """Condense the tail of a Claude Code transcript JSONL into human-scale doings."""
    text = _read_text(path, 2_000_000)
    if text is None:
        return []
    doings: list[dict] = []
    for line in text.splitlines()[-400:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        message = rec.get("message") if isinstance(rec, dict) else None
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_input = block.get("input") or {}
                summary = (
                    tool_input.get("file_path")
                    or tool_input.get("command")
                    or tool_input.get("description")
                    or tool_input.get("pattern")
                    or ""
                )
                doings.append({
                    "kind": "tool",
                    "name": block.get("name", "?"),
                    "summary": str(summary)[:160],
                })
            elif block.get("type") == "text" and str(block.get("text", "")).strip():
                doings.append({"kind": "text", "summary": str(block["text"]).strip()[:160]})
    return doings[-limit:]
