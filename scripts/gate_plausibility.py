#!/usr/bin/env python3
"""Is this verdict's measured latency possible for the model that supposedly produced it?

## The defect

Phase 10 of one measured feature records a **4 ms GO** from a model gate. Four milliseconds is not a
slow reply or a cached one - it is less time than the provider CLI takes to start, and far less than
any network round trip to a hosted model. A sweep of **all 178 gate calls across phases 08 to 11**
found it is the *only* implausible latency attached to a **passing** verdict, which is exactly the
shape a gate that never ran would take: the call is skipped or short-circuited, something GO-shaped
comes back, and the pipeline proceeds on a judgement nobody made.

The verdict was consumed. Nothing looked at the number.

## The rule

**A verdict whose latency is impossible for the model that supposedly produced it is presumed not to
have run, and the gate stops rather than proceeding on it.** This is a floor on the physics of the
call - process spawn, TLS handshake, time to first token - not a judgement about the answer. It is
applied to any verdict a gate reaches, pass or fail alike: a NO-GO returned in 4 ms did not run
either, and a stop whose cause is "the gate did not run" is a different stop from "the work is
wrong". The pass is what makes it dangerous; the refusal is what makes it honest.

It is deliberately NOT applied to failures that never reached a provider. `gate_runner.py` records a
near-zero latency for a config refusal or a cross-family refusal on purpose - "the near-zero latency
that says so" - and those already carry their own `cause`. Only a REACHED verdict is checked here.

## The floor

`PROVIDER_FLOOR_MS` (250 ms) sits roughly two orders of magnitude below the fastest real gate call in
that 178-call sweep and 60x above the 4 ms that started this. It is not tuned to a model, because it
is not a performance budget: a gate call slower than the floor tells us nothing, and one faster than
it tells us the call did not happen.

`GATE_MIN_LATENCY_MS` moves it, and **`GATE_MIN_LATENCY_MS=0` disables the check** - for a local or
in-process model where the floor genuinely does not hold. Disabling is never silent: it says so on
stderr, because a plausibility check quietly doing nothing is the failure it exists to remove.

Stdlib only - imported by `gate_runner.py`, which ships vendored.
"""

from __future__ import annotations

import os
import sys

#: Milliseconds below which a REACHED gate verdict is presumed not to have run. See the module
#: docstring for where the number comes from; it is a floor on the physics, not a budget.
PROVIDER_FLOOR_MS = 250

#: The one env var that moves the floor. `0` disables the check, loudly.
FLOOR_ENV = "GATE_MIN_LATENCY_MS"

#: How to satisfy a refusal, named at the refusal. A rule whose remedy is unavailable is a wedge.
REMEDY = (
    f"If this model genuinely answers faster than the floor (a local or in-process model), set "
    f"{FLOOR_ENV} to a lower value, or {FLOOR_ENV}=0 to disable the check. Otherwise the call did "
    f"not happen: check the provider, the runner and the caller before trusting this verdict."
)


class FloorMisconfigured(ValueError):
    """`GATE_MIN_LATENCY_MS` holds something that is not a non-negative integer."""


def provider_floor_ms(env: dict[str, str] | None = None) -> int:
    """The floor in force, from the environment or the default.

    A malformed value is refused rather than silently ignored: reading `GATE_MIN_LATENCY_MS=fast` as
    "use the default" means an operator who tried to relax the floor gets the strict one and no
    indication why, and one who tried to raise it gets no protection. The caller turns this into a
    named configuration failure, which is a remedy the operator can act on.
    """
    source = os.environ if env is None else env
    raw = (source.get(FLOOR_ENV) or "").strip()
    if not raw:
        return PROVIDER_FLOOR_MS
    try:
        value = int(raw)
    except ValueError as exc:
        raise FloorMisconfigured(
            f"{FLOOR_ENV}={raw!r} is not an integer number of milliseconds. Set it to a "
            f"non-negative integer, or unset it to use the default of {PROVIDER_FLOOR_MS} ms."
        ) from exc
    if value < 0:
        raise FloorMisconfigured(
            f"{FLOOR_ENV}={raw!r} is negative. A floor below zero admits every latency, which is "
            f"the same as no check but harder to notice - use {FLOOR_ENV}=0 to disable it openly."
        )
    return value


def implausible(latency_ms: int | float | None, *, floor: int | None = None,
                model: str | None = None, provider: str | None = None) -> str | None:
    """Why this measured latency cannot be a real call, or None when it can be.

    An unmeasured latency (`None`) is NOT implausible: `gate_runner` measures every call it makes, so
    the only way to arrive here without a number is a caller that does not measure, and refusing that
    would block a gate over a missing measurement rather than over a missing call. The check is about
    a number that contradicts itself, never about the absence of one.
    """
    if floor is None:
        floor = provider_floor_ms()
    if floor <= 0:
        print(
            f"[gate_plausibility] {FLOOR_ENV}=0 - the latency plausibility check is DISABLED. A "
            f"verdict returned faster than any real provider call will be believed.",
            file=sys.stderr,
        )
        return None
    if latency_ms is None:
        return None
    try:
        measured = float(latency_ms)
    except (TypeError, ValueError):
        return None
    if measured >= floor:
        return None
    who = model or "the gate model"
    via = f" via {provider}" if provider else ""
    return (
        f"the verdict came back in {measured:.0f} ms, below the {floor} ms floor for a real call to "
        f"{who}{via}. That is faster than the call itself can be made, so this verdict is presumed "
        f"NOT to have run and is refused rather than consumed. {REMEDY}"
    )


def main(argv: list[str] | None = None) -> int:
    """`gate_plausibility.py <latency-ms> [model] [provider]` - exit 1 when implausible.

    Here so the rule can be exercised from a shell and from a test the same way the gate applies it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: gate_plausibility.py <latency-ms> [model] [provider]", file=sys.stderr)
        return 2
    try:
        floor = provider_floor_ms()
    except FloorMisconfigured as exc:
        print(f"[gate_plausibility] {exc}", file=sys.stderr)
        return 2
    try:
        latency = float(args[0])
    except ValueError:
        print(f"[gate_plausibility] {args[0]!r} is not a number of milliseconds", file=sys.stderr)
        return 2
    reason = implausible(latency, floor=floor,
                         model=args[1] if len(args) > 1 else None,
                         provider=args[2] if len(args) > 2 else None)
    if reason is None:
        print(f"plausible ({latency:.0f} ms >= {floor} ms floor)")
        return 0
    print(f"[gate_plausibility] {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
