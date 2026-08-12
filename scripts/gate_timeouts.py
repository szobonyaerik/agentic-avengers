#!/usr/bin/env python3
"""The nested-timeout relation, asserted instead of assumed.

`hooks/hooks.json` gave the two gate hooks a 120s budget. Inside them, the gate's provider call was
given 300s. The harness therefore killed the hook 180s before the gate could possibly answer, and a
hook killed by the harness leaves no verdict, no report and no cause — it leaves nothing, which the
run reads as "the gate said no".

The tell was a clean split by duration: spec 8.0 returned a verdict in 106s and passed; spec 8.1
took 143s and "failed". Everything under 120s gated. Everything over did not. Read at the time as a
model size ceiling in the gate model. There was no ceiling. There was an inverted pair of timeouts.

The relation that must hold, for every hook that can reach `gate_runner.py`:

    hook timeout (hooks.json)  >=  gate calls x provider call timeout  +  HOOK_HEADROOM_S

The headroom is not padding: the hook also loads env, checks the gate cache, assembles a diff
bundle, stamps frontmatter and writes the cache, all outside the call it wraps.

**The call count is derived, not assumed to be one.** The spec gate makes two sequential provider
calls in a single hook - it observes, then triages what it observed - and a budget sized for one of
them reinstates the original defect exactly: the harness kills the hook mid-second-call, and a
killed hook leaves no verdict, no report and no cause. `gate_calls()` counts invocation *sites*
(an interpreter followed by the runner's path) across the hook and everything it reaches, so a hook
that grows a second call is covered without anyone remembering to record it here. A hook that calls
the runner inside a loop is the residual case this cannot count; no shipped hook does, and one that
did would owe its own accounting.

Metrics emission spends the headroom, so it is asserted here rather than described somewhere:

    metrics processes on the hook's path  x  AVENGER_METRICS_TIMEOUT  <=  HOOK_HEADROOM_S

An unwritable record makes firstmate's CLI BLOCK rather than fail, and the sink's breaker is
per-process state, so each process on a hook's path can pay the full per-call bound once. The
process count is DERIVED the same way gate hooks and gate calls are — from what the scripts actually
spawn and import — so a fourth emission point added to a hook moves this number by itself.

Which hooks are gate hooks is DERIVED, not listed: this walks the `$SD/…`, `$SCRIPT_DIR/…` and
`${CLAUDE_PLUGIN_ROOT}/scripts/…` references out of each hook script and follows them, so a hook
that starts calling the gate tomorrow is covered without anyone remembering to add it here. That
guarantee is only as wide as the reference forms below, which is why they are pinned to the same
three `tests/test_install_manifest.py` audits install.sh on.

    python3 scripts/gate_timeouts.py verify [hooks.json]    exit 0 = sound, 2 = inverted

Stdlib only, plus `gate_errors.py` for the one failure this module can raise.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_sink  # noqa: E402
from gate_errors import GateError  # noqa: E402

#: Seconds the provider call gets, and the value `gate_runner.py` passes to the child runner.
#: Overridable so a slow model can be given room — the relation below is checked against whatever
#: it actually is, so raising it without raising the hook budget is a loud failure, not a silent one.
DEFAULT_CALL_TIMEOUT_S = 300


def call_timeout() -> int:
    """The provider-call budget in seconds (`GATE_CALL_TIMEOUT`, default 300).

    A value that does not parse is a HARD ERROR, never a fallback. `GATE_CALL_TIMEOUT=600s` used to
    become 300 silently: the operator believed the call had the budget they wrote, the relation below
    validated the hook against 300 and passed, and the call was killed at 300s with nothing saying
    why. That is the same defect as the 120s hook around the 300s call — a timeout believed rather
    than checked — inside the module whose entire job is to make that relation loud.
    """
    raw = os.environ.get("GATE_CALL_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_CALL_TIMEOUT_S
    try:
        value = int(float(raw))
    except ValueError as exc:
        raise GateError(
            "config",
            f"GATE_CALL_TIMEOUT={raw!r} is not a number of seconds. It is the budget the provider "
            f"call is given and the number every hook budget is checked against, so it is not "
            f"guessed: write a positive integer (e.g. 300), not a duration string.",
        ) from exc
    if value <= 0:
        raise GateError(
            "config",
            f"GATE_CALL_TIMEOUT={raw!r} is not a positive number of seconds. A call budget of "
            f"{value} would kill every gate before it could answer.",
        )
    return value


#: Seconds a gate hook needs on top of the call it wraps: env load, gate-cache check, re-gate diff
#: bundle, frontmatter stamp, cache write. Generous on purpose — the cost of too much headroom is a
#: hook that hangs around longer before being killed; the cost of too little is this whole defect.
HOOK_HEADROOM_S = 120

#: The name of the module every gate hook ultimately reaches. A hook that references it, directly or
#: through another script, is a gate hook.
GATE_RUNNER = "gate_runner.py"

#: The three ways a shipped file names another script — the same forms scripts/install.sh is audited
#: on in tests/test_install_manifest.py, and they must stay the same three. A spelling this regex
#: does not know is a reference this walk cannot follow, so the hook that made it would not be
#: recognised as a gate hook at all: `violations()` would return nothing for it and `verify` would
#: exit 0 with the inversion live. A check whose whole point is failing closed, failing open.
REFERENCE = re.compile(
    r"\$\{CLAUDE_PLUGIN_ROOT[^}]*\}/scripts/([A-Za-z0-9_.-]+\.(?:py|sh))"
    r"|\$SD/([A-Za-z0-9_.-]+\.(?:py|sh))"
    r"|\$SCRIPT_DIR/([A-Za-z0-9_.-]+\.(?:py|sh))"
)

#: A hook command names its script the same way.
HOOK_SCRIPT = re.compile(r"/scripts/([A-Za-z0-9_.-]+\.(?:py|sh))")

#: An INVOCATION of the gate runner: an interpreter, then the runner's path in one of the three
#: reference forms above. Deliberately narrower than `REFERENCE` — `require_gate_runner "$SD/
#: gate_runner.py"` names the runner to verify its identity and costs no provider call, and counting
#: that as a call would inflate every gate hook's required budget for no reason.
GATE_INVOCATION = re.compile(
    r"python3?[ \t]+\"?(?:\$\{CLAUDE_PLUGIN_ROOT[^}]*\}/scripts|\$SD|\$SCRIPT_DIR)/gate_runner\.py"
)

#: The metrics CLI. Every process running it is counted at its spawn site below, so the module
#: itself is never counted a second time for the emission code it obviously contains.
METRICS_CLI = "pipeline_metrics.py"

#: How a shipped script spawns that CLI — pinned to the same three reference forms as `REFERENCE`,
#: for the same reason: a spawn this cannot see is a process whose timeout goes uncounted.
METRICS_SPAWN = re.compile(
    r"(?:\$\{CLAUDE_PLUGIN_ROOT[^}]*\}/scripts|\$SD|\$SCRIPT_DIR)/" + METRICS_CLI.replace(".", r"\.")
)

#: A process that records in-process instead of spawning the CLI imports the metrics module AND
#: calls one of its `record_*` entry points. Both are required: `gate_timeouts.py` imports the sink
#: to read its budget and records nothing, and counting it would inflate every hook on the path.
METRICS_MODULE = re.compile(r"\b(?:from|import)\s+(?:pipeline_metrics|metrics_sink)\b")
METRICS_RECORD = re.compile(r"\brecord_[a-z][a-z_]*\(")

#: A shell function definition, `name () {` … `}` with the brace back at column 0.
SHELL_FUNCTION = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*\([ \t]*\)[ \t]*\{$", re.MULTILINE)


def _function_bodies(text: str) -> dict[str, tuple[int, int]]:
    """(start, end) character spans of each shell function body in `text`."""
    spans: dict[str, tuple[int, int]] = {}
    for match in SHELL_FUNCTION.finditer(text):
        close = text.find("\n}", match.end())
        if close >= 0:
            spans[match.group(1)] = (match.start(), close + 2)
    return spans


def _call_sites(text: str, name: str) -> int:
    """How many times `name` is invoked as a command, outside its own definition."""
    pattern = re.compile(
        rf"(?:^|[;&|(]|\bif[ \t]+(?:![ \t]*)?|\bthen[ \t]+|\belse[ \t]+)[ \t]*{re.escape(name)}\b",
        re.MULTILINE,
    )
    return len(pattern.findall(text))


def _invocations(text: str) -> int:
    """Provider calls one script makes, counting a helper function once PER CALL SITE.

    A hook that wraps its gate call in a function and calls it twice makes two calls, and sizing its
    budget for one reinstates the original defect exactly. Counting raw invocation *sites* would say
    one — the spec gate factors its observe and triage passes through a single `run_pass` helper
    precisely because that is the better shape — so the helper is expanded here instead of the hook
    being written worse to suit the counter.
    """
    bodies = _function_bodies(text)
    outside = text
    total = 0
    for name, (start, end) in sorted(bodies.items(), key=lambda kv: -kv[1][0]):
        body = text[start:end]
        outside = outside[: outside.find(body)] + outside[outside.find(body) + len(body) :] \
            if body in outside else outside
        inner = len(GATE_INVOCATION.findall(body))
        if inner:
            total += inner * max(1, _call_sites(outside, name))
    return total + len(GATE_INVOCATION.findall(outside))


def gate_calls(script: Path, scripts_dir: Path, seen: set[str] | None = None) -> int:
    """How many provider calls one hook can make, counted over everything it reaches.

    At least 1 for any hook that reaches the runner at all: a hook whose call is made by a script
    this cannot parse still makes one, and sizing its budget for zero is the failure this module
    exists to remove.
    """
    seen = set() if seen is None else seen
    if script.name in seen:
        return 0
    seen.add(script.name)
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    total = _invocations(text)
    for ref in sorted(references(script)):
        target = scripts_dir / ref
        if target.is_file():
            total += gate_calls(target, scripts_dir, seen)
    return total


def required_hook_timeout(call_s: int | None = None, calls: int = 1) -> int:
    """The smallest hooks.json timeout that can outlive the calls it wraps."""
    return max(calls, 1) * (call_timeout() if call_s is None else call_s) + HOOK_HEADROOM_S


def references(script: Path) -> set[str]:
    """Script basenames `script` names as executables ($SD/…, $SCRIPT_DIR/…, plugin-root/scripts/…)."""
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {plugin or sd or script_dir for plugin, sd, script_dir in REFERENCE.findall(text)}


def reaches_gate_runner(script: Path, scripts_dir: Path, seen: set[str] | None = None) -> bool:
    """True when `script` can reach gate_runner.py, directly or through what it calls."""
    seen = set() if seen is None else seen
    if script.name in seen:
        return False
    seen.add(script.name)
    refs = references(script)
    if GATE_RUNNER in refs:
        return True
    return any(
        reaches_gate_runner(scripts_dir / ref, scripts_dir, seen)
        for ref in sorted(refs)
        if (scripts_dir / ref).is_file()
    )


def metrics_processes(script: Path, scripts_dir: Path, seen: set[str] | None = None) -> int:
    """How many separate processes on `script`'s path can each pay the metrics timeout once.

    Derived, never listed. The sink's breaker is per-process state, so a blocked writer costs one
    full `AVENGER_METRICS_TIMEOUT` in every process that touches it — on the spec-gate path today
    that is phase-open, spec-round, the two gate-killed traps and the gate runner's own recording,
    and whatever a future emission point adds, without anyone remembering to update a number here.
    """
    seen = set() if seen is None else seen
    if script.name in seen or not script.is_file():
        return 0
    seen.add(script.name)
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    count = len(METRICS_SPAWN.findall(text))
    if (
        script.name != METRICS_CLI
        and METRICS_MODULE.search(text)
        and METRICS_RECORD.search(text)
    ):
        count += 1
    return count + sum(
        metrics_processes(scripts_dir / ref, scripts_dir, seen)
        for ref in sorted(references(script))
    )


def metrics_worst_case_s(script: Path, scripts_dir: Path) -> int:
    """The most wall clock measurement can add to one invocation of `script`."""
    return metrics_processes(script, scripts_dir) * metrics_sink.timeout()


def gate_hooks(hooks_json: Path, scripts_dir: Path) -> list[tuple[str, str, int, int]]:
    """(event, script name, timeout, gate calls) for every hook entry that reaches the gate runner."""
    config = json.loads(hooks_json.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int, int]] = []
    for event, groups in (config.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                match = HOOK_SCRIPT.search(str(hook.get("command", "")))
                if not match:
                    continue
                script = scripts_dir / match.group(1)
                if script.is_file() and reaches_gate_runner(script, scripts_dir):
                    calls = max(1, gate_calls(script, scripts_dir))
                    found.append((event, script.name, int(hook.get("timeout", 0)), calls))
    return found


def violations(hooks_json: Path, scripts_dir: Path, call_s: int | None = None) -> list[str]:
    """Every gate hook whose harness budget cannot outlive what runs inside it.

    Two ways to lose the same way: a budget that cannot outlive the provider calls the hook makes
    (however many of them there are), and measurement eating the headroom those calls leave. Both
    end with the harness killing a hook that then reports nothing at all, which is the absence a run
    reads as a rejection.
    """
    inner = call_timeout() if call_s is None else call_s
    per_call = metrics_sink.timeout()
    out = []
    for event, name, hook_timeout, calls in gate_hooks(hooks_json, scripts_dir):
        need = required_hook_timeout(call_s, calls)
        if hook_timeout < need:
            plural = "" if calls == 1 else f" x {calls} sequential calls"
            # `calls * inner - hook_timeout` is the honest gap: the time the CALLS still need when
            # the harness kills the hook. The headroom on top is what the hook does around them, and
            # folding it in here would overstate the number the operator is trying to reconcile.
            out.append(
                f"INVERTED TIMEOUT — {event}/{name}: hooks.json timeout is {hook_timeout}s but the "
                f"provider call inside it is given {inner}s{plural} — the harness kills the hook "
                f"{calls * inner - hook_timeout}s before the gate can answer, and a killed hook "
                f"reports nothing at all. Needs >= {need}s "
                f"({calls} x {inner}s call + {HOOK_HEADROOM_S}s headroom)."
            )
        processes = metrics_processes(scripts_dir / name, scripts_dir)
        worst = processes * per_call
        if worst > HOOK_HEADROOM_S:
            out.append(
                f"HEADROOM EXHAUSTED — {event}/{name}: measurement can add up to {worst}s inside "
                f"this hook ({processes} metrics processes x {per_call}s "
                f"AVENGER_METRICS_TIMEOUT, one per process because the sink's breaker is "
                f"per-process), but the hook budget carries only {HOOK_HEADROOM_S}s of headroom "
                f"over the {calls} x {inner}s of provider calls. A hung writer would get the hook "
                f"killed and the gate would report nothing. Lower AVENGER_METRICS_TIMEOUT to <= "
                f"{HOOK_HEADROOM_S // processes}s, remove an emission point from this hook's path, "
                f"or raise HOOK_HEADROOM_S and every hooks.json budget with it."
            )
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "verify" or len(args) > 2:
        print("usage: gate_timeouts.py verify [hooks.json]", file=sys.stderr)
        return 2
    here = Path(__file__).resolve().parent
    hooks_json = Path(args[1]) if len(args) == 2 else here.parent / "hooks" / "hooks.json"
    if not hooks_json.is_file():
        # Fail closed: the relation is what keeps a gate answerable, and an unverifiable relation is
        # exactly the state that produced a day of misread failures.
        print(
            f"[gate_timeouts] cannot verify the nested-timeout relation: {hooks_json} is missing. "
            "A gate whose hook budget cannot be checked may be killed mid-call and report nothing.",
            file=sys.stderr,
        )
        return 2
    try:
        found = violations(hooks_json, here)
    except GateError as exc:
        # Both hooks run `verify` before they call the gate, so a budget that cannot be read stops
        # the turn here — named — instead of surfacing later as a call killed at a budget nobody set.
        print(exc.render(), file=sys.stderr)
        return 2
    for line in found:
        print(f"[gate_timeouts] {line}", file=sys.stderr)
    return 2 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
