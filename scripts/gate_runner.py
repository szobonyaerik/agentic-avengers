#!/usr/bin/env python3
"""gate_runner.py - cross-family verdict gate for the agentic pipeline.

Called by a Claude Code command hook (PostToolUse). Reads a target artifact,
sends it with a rubric to a FRESH model via opencode (subprocess, default) or
OpenRouter (HTTP), and turns the model's verdict into an exit code:
    GO / PASS  -> exit 0   (pipeline continues)
    otherwise  -> exit 2   (Claude stops and shows the report)
Any error also exits 2 — and says WHICH error: every failure carries a `cause=` from
`gate_errors.CAUSES` and reproduces the provider's own words verbatim. A timeout kill, a 402
out-of-credit reply and an unreachable provider used to be one indistinguishable line.

The model must reply with strict JSON:
    {"verdict":"GO|REVIEW|NO-GO","report":"...","route_back":"<agent or stage>"}

Model ids use the chosen provider's namespace (e.g. deepseek/deepseek-chat,
google/gemini-3.1-pro-preview). Stdlib only, plus these siblings:
    model_vendors.py  family of a model id (unknown vendor = loud refusal)
    gate_errors.py    the failure taxonomy
    proc_group.py     a child that a timeout actually stops
    gate_timeouts.py  the call budget, and the hook-vs-call relation

`--identify` prints this runner's ABI and its own digest, so a caller can refuse a runner that is
not the shipped one — see scripts/gate_runner_guard.sh.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_errors import GateError, classify_provider_failure  # noqa: E402
from gate_timeouts import call_timeout  # noqa: E402
from model_vendors import UnknownVendor, model_family  # noqa: E402
from proc_group import run_bounded  # noqa: E402

# Every gate call in the pipeline passes through this file, so this is where a gate call gets
# recorded — once, rather than once per caller. Measurement, never a gate: `record_gate_call`
# swallows its own failures, and a runtime without the metrics modules simply records nothing.
try:
    from pipeline_metrics import record_gate_call
except ImportError:  # pragma: no cover - vendored without the metrics modules
    def record_gate_call(**_kwargs):
        return False

# Pull OPENROUTER_API_KEY / GATE_MODEL / AUTHOR_FAMILY from the project's .env when the environment
# does not already define them. Shell callers get the same values via load_env.sh; this covers being
# invoked directly. The real environment always wins, so a CI secret is never shadowed.
try:
    from env_file import load_into

    load_into(os.environ, root=os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
except ImportError:  # vendored without env_file.py — proceed on the real environment alone
    pass

VERDICT_OK = {"GO", "PASS"}

#: What this runner IS. A caller that finds anything else where it expected a gate runner has found
#: something it cannot vouch for — a scaffold that printed a bare `GO` having checked nothing sat on
#: a temp path once and was believed. Bump the version when the caller-visible contract changes.
RUNNER_ABI = "agentic-avengers/gate_runner v1"


def self_digest() -> str:
    """sha256 of this file, so `--identify` states what is actually running, not what is claimed."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def assert_cross_family(model, author_family, override=None):
    """Fail closed if the gate model shares the author's family (no decorrelation).

    Resolving the family is itself a check: a model whose vendor is unknown cannot be compared, and
    an uncomparable model used to pass by returning its own id as its "family".
    """
    try:
        gate_family = model_family(model, override)
    except UnknownVendor as exc:
        raise GateError("unknown-vendor", str(exc)) from exc
    if not author_family:
        return gate_family
    if gate_family == author_family.strip().lower():
        raise GateError(
            "cross-family",
            f"cross-family violation: gate model '{model}' (family '{gate_family}') "
            f"is the same family as the author "
            f"'{author_family}'. A gate must run on a different family than the work it judges.",
        )
    return gate_family


def stdin_target():
    """Pull .tool_input.file_path from the hook JSON on stdin (if any)."""
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        return json.loads(raw).get("tool_input", {}).get("file_path")
    except json.JSONDecodeError:
        return None


def call_openrouter(model, system, user):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise GateError("config", "OPENROUTER_API_KEY is not set")
    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=call_timeout()) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        # The body is the provider's own explanation — a 402 says which credit ran out. Reproduced
        # verbatim rather than summarised, because the classifier below is a heuristic.
        detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
        cause = ("provider-payment-required" if exc.code == 402
                 else classify_provider_failure(f"{exc.code} {exc.reason}\n{detail}"))
        raise GateError(cause, f"openrouter returned HTTP {exc.code} {exc.reason}", detail) from exc
    except urllib.error.URLError as exc:
        raise GateError(
            "provider-unreachable", f"openrouter could not be reached: {exc.reason}", str(exc.reason)
        ) from exc
    except TimeoutError as exc:
        raise GateError(
            "timeout", f"openrouter did not answer within {call_timeout()}s", str(exc)
        ) from exc
    # A 200 whose body is not JSON is the provider misbehaving, not the gate being misconfigured.
    # Decoded here rather than left to the catch-all in main(), which would have reported an HTML
    # error page or a proxy's interstitial to the operator as `cause=config` — the exact
    # wrong-cause shape this taxonomy exists to remove. The raw body is the appeal, as ever.
    raw_body = body.decode("utf-8", "replace")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise GateError(
            classify_provider_failure(raw_body),
            f"openrouter returned HTTP 200 with a body that is not JSON: {exc}",
            raw_body,
        ) from exc
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GateError(
            "no-verdict", "openrouter reply had no message content", json.dumps(payload)[:4000]
        ) from exc


def opencode_text(raw):
    """Reconstruct the assistant's reply from opencode's NDJSON event stream.

    `opencode run --format json` streams one JSON event per line; the reply text
    lives in the `part.text` of `type:"text"` events. opencode sends the full
    text per part (snapshot), so we keep the latest text seen for each part id
    and join the parts. Returns "" if no text events were found.
    """
    parts = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "text":
            p = ev.get("part") or {}
            pid, txt = p.get("id"), p.get("text")
            if pid and isinstance(txt, str):
                parts[pid] = txt
    return "\n".join(parts.values())


def opencode_model(model):
    """Adapt a canonical OpenRouter model id to opencode's `provider/model` form.

    The pipeline names gate models as OpenRouter ids (e.g. `deepseek/deepseek-chat`,
    `google/gemini-3.1-pro-preview`) — the shape the openrouter HTTP provider wants. opencode routes the
    same models through its OpenRouter credential, so it needs an explicit `openrouter/` provider
    prefix. Ids that already carry a provider prefix are left untouched.
    """
    known_providers = ("openrouter/", "anthropic/", "openai/", "google/vertex", "zai/",
                       "opencode/", "opencode-go/")
    if model.startswith(known_providers):
        return model
    return "openrouter/" + model


def call_opencode(model, system, user):
    """Ask opencode for a verdict, in a process group a timeout can actually stop.

    The child is a CLI that spawns its own workers; killing only the direct child left them running
    and billing long after the gate reported the call dead. `run_bounded` signals the group and
    reports MEASURED elapsed time, so the number in the failure is the run's, not the config's.
    """
    prompt = f"{system}\n\n=== ARTIFACT TO JUDGE ===\n{user}"
    cmd = ["opencode", "run", "--format", "json", "-m", opencode_model(model), prompt]
    budget = call_timeout()
    try:
        result = run_bounded(cmd, budget)
    except FileNotFoundError as exc:
        raise GateError("provider-not-found", "opencode CLI not found on PATH", str(exc)) from exc
    if result.timed_out:
        raise GateError(
            "timeout",
            f"opencode exceeded its {budget}s budget and its process group was killed after "
            f"{result.elapsed:.1f}s of wall clock",
            result.stderr,
        )
    if result.returncode != 0:
        raise GateError(
            classify_provider_failure(f"{result.stderr}\n{result.stdout}"),
            f"opencode run exited {result.returncode} after {result.elapsed:.1f}s",
            result.stderr or result.stdout,
        )
    # Reconstruct the model's reply from the event stream; fall back to raw
    # stdout if the output shape was unexpected (so extract_verdict can still try).
    return opencode_text(result.stdout) or result.stdout


def extract_verdict(raw):
    """Find the {'verdict': ...} object in arbitrary provider output."""
    for text in (raw, raw.replace('\\"', '"').replace('\\n', '\n')):
        for m in re.finditer(r"\{", text):
            depth = 0
            for i in range(m.start(), len(text)):
                depth += 1 if text[i] == "{" else -1 if text[i] == "}" else 0
                if depth == 0:
                    try:
                        obj = json.loads(text[m.start():i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict) and "verdict" in obj:
                        return obj
                    break
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rubric", help="path to the markdown rubric")
    ap.add_argument("--model", default="deepseek/deepseek-chat")
    ap.add_argument("--provider", choices=["openrouter", "opencode"], default="opencode")
    ap.add_argument("--target", help="file to judge (else read from stdin hook JSON)")
    ap.add_argument("--author-family",
                    default=os.environ.get("AUTHOR_FAMILY"),
                    help="vendor family of the model that authored the work (e.g. 'anthropic'); "
                         "the gate fails closed if its own model shares this family")
    ap.add_argument("--model-family",
                    default=os.environ.get("GATE_MODEL_FAMILY"),
                    help="declare the gate model's vendor family when scripts/model_vendors.py does "
                         "not know it. Without this an unknown vendor is refused, never guessed.")
    ap.add_argument("--emit-json", metavar="PATH",
                    help="write the full parsed verdict object to PATH (machine-readable). "
                         "Used by the Verifier review, which needs the findings array, not just "
                         "the verdict token.")
    ap.add_argument("--print-verdict", action="store_true",
                    help="print the raw verdict token (GO/REVIEW/NO-GO) to stdout and exit 0 for any "
                         "reached verdict; the caller decides. Still fails closed (exit 2) on error.")
    ap.add_argument("--identify", action="store_true",
                    help="print '<abi> <sha256-of-this-file>' and exit; how a caller refuses a "
                         "runner that is not the shipped one")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.identify:
        print(f"{RUNNER_ABI} {self_digest()}")
        return

    # Measured wall clock of the call, and the family we resolved for it — both are recorded on
    # every outcome below. `started` covers the pre-call checks too, so a refusal that never reached
    # a provider records the near-zero latency that says so.
    started = time.monotonic()
    target = args.target
    family = None

    def elapsed_ms():
        return int((time.monotonic() - started) * 1000)

    try:
        # Cross-family invariant: a gate must not run on the author's family.
        family = assert_cross_family(args.model, args.author_family, args.model_family)
        if args.selftest:
            rubric = ('Return ONLY JSON {"verdict":"GO|REVIEW|NO-GO",'
                      '"report":"...","route_back":"..."}. Reply GO if the text says OK.')
            artifact = "status: OK"
        else:
            if not args.rubric:
                raise GateError("config", "--rubric is required")
            target = target or stdin_target()
            if not target:
                sys.exit(0)  # no artifact to judge (wrong tool/path) -> no objection
            try:
                rubric = open(args.rubric, encoding="utf-8").read()
                artifact = open(target, encoding="utf-8").read()
            except OSError as exc:
                raise GateError("io", f"cannot read the gate's inputs: {exc}") from exc

        call = call_opencode if args.provider == "opencode" else call_openrouter
        raw = call(args.model, rubric, artifact)
        verdict = extract_verdict(raw)
        if verdict is None:
            raise GateError(
                "no-verdict",
                f"{args.provider} replied, but the reply contained no JSON verdict object",
                raw,
            )
    except GateError as e:  # any failure is a hard stop, and it says which failure
        # Recorded before the exit, with the cause PR 1 gave it: a timeout kill, a 402 and an
        # unreachable provider are three different numbers in the record, not one "it failed".
        record_gate_call(model=args.model, rubric=args.rubric, target=target,
                         model_family=family, latency_ms=elapsed_ms(), cause=e.cause,
                         detail=e.detail, provider=args.provider)
        print(e.render(), file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # never fail open on an unexpected shape
        # `internal`, not `config`: this is the backstop for failures nothing above recognised, and
        # calling them configuration problems sends the operator to their .env for a bug in the gate.
        # Every path that CAN name itself raises GateError above; reaching here is itself a defect.
        record_gate_call(model=args.model, rubric=args.rubric, target=target,
                         model_family=family, latency_ms=elapsed_ms(), cause="internal",
                         detail=f"{type(e).__name__}: {e}", provider=args.provider)
        print(GateError("internal", f"{type(e).__name__}: {e}").render(), file=sys.stderr)
        sys.exit(2)

    v = str(verdict.get("verdict", "")).upper()
    record_gate_call(model=args.model, rubric=args.rubric, target=target, model_family=family,
                     latency_ms=elapsed_ms(), verdict=v, provider=args.provider)
    if args.emit_json:
        # Written before any exit branch: a NO-GO verdict is exactly when the caller most needs the
        # findings. Failing to persist them is itself fail-closed.
        try:
            with open(args.emit_json, "w", encoding="utf-8") as fh:
                json.dump(verdict, fh, indent=2)
        except OSError as e:
            print(GateError("io", f"could not write --emit-json {args.emit_json}: {e}").render(),
                  file=sys.stderr)
            sys.exit(2)
    if args.print_verdict:
        # Hand the token to the caller (e.g. spec-review-auto branches GO/REVIEW vs NO-GO).
        # A verdict was reached, so this is not a fail-closed error -> exit 0.
        print(v or "NO-GO")
        if verdict.get("report"):
            print(verdict["report"], file=sys.stderr)
        if verdict.get("route_back"):
            print(f"Route back to: {verdict['route_back']}", file=sys.stderr)
        sys.exit(0)
    if v in VERDICT_OK:
        print(f"OK ({v})")
        sys.exit(0)
    print(f"=== GATE: {v or 'FAIL'} ===", file=sys.stderr)
    print(verdict.get("report", "(no report)"), file=sys.stderr)
    if verdict.get("route_back"):
        print(f"Route back to: {verdict['route_back']}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
