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
from pathlib import Path

AVENGER_STAGES = [
    "task-analyst",
    "solution-architect",
    "implementation-planner",
    "spec-writer",
    "fidelity-gate",
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


def read_activity(root: Path) -> dict:
    """Live subagents per root: start events not yet closed by a stop.

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
    return {"status": "ok", "live": live, "recent": events[-40:]}


# ---------------------------------------------------------------- pipeline


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
                "fidelity_verdict": fm.get("fidelity_verdict", ""),
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
        try:
            proc = subprocess.run(
                ["bash", str(snapshot_script), "--json"],
                capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
                cwd=str(fm_bin.parent), env=_fm_env(fm_home),
            )
            if proc.returncode == 0:
                result["snapshot"] = json.loads(proc.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            result["snapshot"] = None
    result["crew"] = _crew_from_state_dir(fm_home)
    if not result["crew"] and result["snapshot"] is None:
        result["status"] = "empty"
    return result


def _fm_env(fm_home: Path) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["FM_HOME"] = str(fm_home)
    return env


# ---------------------------------------------------------------- focus / detail helpers


def focus_window(window: str) -> dict:
    """Jump the user's tmux client to a crewmate's window. Never raises; reports what it did."""
    if not window:
        return {"ok": False, "error": "no window recorded in meta"}
    try:
        proc = subprocess.run(
            ["tmux", "select-window", "-t", window],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return {"ok": True, "method": "tmux select-window", "window": window}
        return {
            "ok": False,
            "error": (proc.stderr or "tmux failed").strip()[:200],
            "attach_cmd": f"tmux attach \\; select-window -t {window}",
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "attach_cmd": f"tmux select-window -t {window}"}


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
