"""Synthetic fleet for the tavern monitor.

Produces the exact same state shape the live server builds, so the frontend cannot tell them
apart — that is the point: the demo exists to validate the UI end-to-end (and to give a first
look before any fleet exists), never to be passed off as live. The server stamps `mode: "demo"`
and the frontend shows it.

Everything is a pure function of elapsed seconds since server start: deterministic, replayable,
and free of wall-clock/randomness so two viewers of the same server agree on what they see.
"""

from __future__ import annotations

import time

from sources import AVENGER_STAGES

# Which avenger sits at the table while a stage runs. The two gate stages are moments at the
# door, not patrons — the bouncer checks papers; nobody new sits down.
STAGE_AGENT = {
    "task-analyst": "avenger-task-analyst",
    "solution-architect": "avenger-solution-architect",
    "implementation-planner": "avenger-implementation-planner",
    "spec-writer": "avenger-spec-writer",
    "implementer": "avenger-backend-architect",
    "verifier": "avenger-verifier",
    "handover": "avenger-handover",
    "e2e-author": "avenger-frontend-developer",
}

STAGE_SENTENCE = {
    "task-analyst": "reading the captain's brief by candlelight",
    "solution-architect": "sketching the tower on a napkin",
    "implementation-planner": "charting phases on the war map",
    "spec-writer": "inking spec 2.1 with a steady hand",
    "spec-gate": "the bouncer squints at the spec's papers",
    "spec-review": "a rival guild reviews the contract",
    "implementer": "hammering a failing test until it rings green",
    "verifier": "weighing the tests on brass scales",
    "handover": "sealing the phase into a courier scroll",
    "e2e-author": "walking the whole quest end to end",
    "done": "raising a tankard — feature shipped",
}

SCOUT_STATUSES = [
    "working: mapping the docs cellar",
    "working: grepping for dragons in module three",
    "note: found two stale references, logging them",
    "working: cross-checking README claims against code",
    "paused: waiting on a slow CI raven",
]

THIRD_STATUSES = [
    "working: reproducing the reported bug",
    "working: bisecting — suspect commit found",
    "working: writing the regression test first",
    "working: fix in place, suite running",
    "ready: branch clean, awaiting merge word",
]

_STAGE_SECS = 18


def demo_state(started_monotonic: float) -> dict:
    elapsed = max(0.0, time.monotonic() - started_monotonic)
    tick = int(elapsed // _STAGE_SECS)
    stage = AVENGER_STAGES[tick % len(AVENGER_STAGES)]
    phase_no = (tick // len(AVENGER_STAGES)) % 3 + 1

    crew = [
        {
            "id": "brave-anvil",
            "project": "<project-a>",
            "worktree": "~/.treehouse/project-a/1",
            "window": "fm:brave-anvil",
            "harness": "claude",
            "mode": "no-mistakes",
            "kind": "ship",
            "last_status": f"working: /avenger-run --auto — stage {stage}",
            "sentence": STAGE_SENTENCE.get(stage, "at work"),
        },
        {
            "id": "quiet-lantern",
            "project": "<project-a>",
            "worktree": "~/.treehouse/project-a/2",
            "window": "fm:quiet-lantern",
            "harness": "claude",
            "mode": "local-only",
            "kind": "scout",
            "last_status": SCOUT_STATUSES[tick % len(SCOUT_STATUSES)],
            "sentence": SCOUT_STATUSES[tick % len(SCOUT_STATUSES)].split(": ", 1)[-1],
        },
        {
            "id": "gilded-fox",
            "project": "<project-b>",
            "worktree": "~/.treehouse/project-b/1",
            "window": "fm:gilded-fox",
            "harness": "opencode",
            "mode": "direct-PR",
            "kind": "ship",
            "last_status": THIRD_STATUSES[tick % len(THIRD_STATUSES)],
            "sentence": THIRD_STATUSES[tick % len(THIRD_STATUSES)].split(": ", 1)[-1],
        },
    ]

    agent = STAGE_AGENT.get(stage)
    live_agents = []
    if agent:
        live_agents.append({
            "agent_type": agent,
            "agent_id": f"demo-{tick}",
            "since": None,
            "root": "<project-a>",
            "crew_id": "brave-anvil",
            "sentence": STAGE_SENTENCE.get(stage, "at work"),
        })

    moments = []
    stage_pos = elapsed % _STAGE_SECS
    if stage == "spec-gate":
        kind = "gate_fail" if tick % 7 == 3 else "gate_pass"
        moments.append({"kind": kind, "text": "spec gate: " + ("blocked — routed back" if kind == "gate_fail" else "approved")})
    elif stage == "verifier" and stage_pos > _STAGE_SECS - 6:
        kind = "route_back" if tick % 5 == 2 else "gate_pass"
        moments.append({"kind": kind, "text": "verifier: " + ("finding routed to implementer" if kind == "route_back" else "phase verdict: pass")})
    elif stage == "done":
        moments.append({"kind": "gate_pass", "text": "no-mistakes ship gate: checks passed"})

    features = [{
        "root": "<project-a>",
        "feature": "tavern-demo",
        "stage": stage,
        "phase": f"{phase_no}-demo-phase",
        "spec": f"{phase_no}.1",
        "criticality": "normal",
        "reason": STAGE_SENTENCE.get(stage, ""),
        "specs": [
            {"spec": f"{phase_no}.1", "phase": f"{phase_no}-demo-phase",
             "status": "done" if stage in ("verifier", "handover", "e2e-author", "done") else "in-progress",
             "spec_gate": "approved" if AVENGER_STAGES.index(stage) > 4 else "pending",
             "review_status": "approved" if AVENGER_STAGES.index(stage) > 5 else ""},
        ],
        "verdicts": (
            [{"phase": f"{phase_no}-demo-phase", "verdict": "pass",
              "tests": {"total": 12, "passed": 12, "failed": 0}, "findings": 0}]
            if stage in ("handover", "e2e-author", "done") else []
        ),
    }]

    return {
        "mode": "demo",
        "sources": {"fleet": "demo", "pipeline": "demo", "activity": "demo"},
        "crew": crew,
        "features": features,
        "live_agents": live_agents,
        "moments": moments,
    }
