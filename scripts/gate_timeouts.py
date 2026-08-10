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

    hook timeout (hooks.json)  >=  provider call timeout  +  HOOK_HEADROOM_S

The headroom is not padding: the hook also loads env, checks the gate cache, assembles a diff
bundle, stamps frontmatter and writes the cache, all outside the call it wraps.

Which hooks are gate hooks is DERIVED, not listed: this walks the `$SD/…`, `$SCRIPT_DIR/…` and
`${CLAUDE_PLUGIN_ROOT}/scripts/…` references out of each hook script and follows them, so a hook
that starts calling the gate tomorrow is covered without anyone remembering to add it here. That
guarantee is only as wide as the reference forms below, which is why they are pinned to the same
three `tests/test_install_manifest.py` audits install.sh on.

    python3 scripts/gate_timeouts.py verify [hooks.json]    exit 0 = sound, 2 = inverted

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: Seconds the provider call gets, and the value `gate_runner.py` passes to the child runner.
#: Overridable so a slow model can be given room — the relation below is checked against whatever
#: it actually is, so raising it without raising the hook budget is a loud failure, not a silent one.
DEFAULT_CALL_TIMEOUT_S = 300


def call_timeout() -> int:
    """The provider-call budget in seconds (`GATE_CALL_TIMEOUT`, default 300)."""
    raw = os.environ.get("GATE_CALL_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_CALL_TIMEOUT_S
    try:
        value = int(float(raw))
    except ValueError:
        return DEFAULT_CALL_TIMEOUT_S
    return value if value > 0 else DEFAULT_CALL_TIMEOUT_S


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


def required_hook_timeout(call_s: int | None = None) -> int:
    """The smallest hooks.json timeout that can outlive the call it wraps."""
    return (call_timeout() if call_s is None else call_s) + HOOK_HEADROOM_S


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


def gate_hooks(hooks_json: Path, scripts_dir: Path) -> list[tuple[str, str, int]]:
    """(event, script name, timeout) for every hook entry that can reach the gate runner."""
    config = json.loads(hooks_json.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []
    for event, groups in (config.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                match = HOOK_SCRIPT.search(str(hook.get("command", "")))
                if not match:
                    continue
                script = scripts_dir / match.group(1)
                if script.is_file() and reaches_gate_runner(script, scripts_dir):
                    found.append((event, script.name, int(hook.get("timeout", 0))))
    return found


def violations(hooks_json: Path, scripts_dir: Path, call_s: int | None = None) -> list[str]:
    """Every gate hook whose harness budget cannot outlive the provider call inside it."""
    need = required_hook_timeout(call_s)
    inner = call_timeout() if call_s is None else call_s
    out = []
    for event, name, timeout in gate_hooks(hooks_json, scripts_dir):
        if timeout < need:
            out.append(
                f"{event}/{name}: hooks.json timeout is {timeout}s but the provider call inside it "
                f"is given {inner}s — the harness kills the hook {inner - timeout}s before the gate "
                f"can answer, and a killed hook reports nothing at all. Needs >= {need}s "
                f"({inner}s call + {HOOK_HEADROOM_S}s headroom)."
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
    found = violations(hooks_json, here)
    for line in found:
        print(f"[gate_timeouts] INVERTED TIMEOUT — {line}", file=sys.stderr)
    return 2 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
