#!/usr/bin/env python3
"""gate_runner.py - cross-family verdict gate for the agentic pipeline.

Called by a Claude Code command hook (PostToolUse). Reads a target artifact,
sends it with a rubric to a FRESH model via opencode (subprocess, default) or
OpenRouter (HTTP), and turns the model's verdict into an exit code:
    GO / PASS  -> exit 0   (pipeline continues)
    otherwise  -> exit 2   (Claude stops and shows the report)
Any error also exits 2 with a clear message.

The model must reply with strict JSON:
    {"verdict":"GO|REVIEW|NO-GO","report":"...","route_back":"<agent or stage>"}

Model ids use the chosen provider's namespace (e.g. deepseek/deepseek-chat,
google/gemini-3.1-pro-preview). Stdlib only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Pull OPENROUTER_API_KEY / GATE_MODEL / AUTHOR_FAMILY from the project's .env when the environment
# does not already define them. Shell callers get the same values via load_env.sh; this covers being
# invoked directly. The real environment always wins, so a CI secret is never shadowed.
try:
    from env_file import load_into

    load_into(os.environ, root=os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
except ImportError:  # vendored without env_file.py — proceed on the real environment alone
    pass

VERDICT_OK = {"GO", "PASS"}


def model_family(model):
    """Vendor family of a model id, ignoring an OpenRouter prefix.

    deepseek/deepseek-chat            -> deepseek
    google/gemini-3.1-pro-preview     -> google
    openrouter/anthropic/claude-opus  -> anthropic
    anthropic/claude-sonnet-5         -> anthropic
    """
    parts = [p for p in str(model).split("/") if p]
    if parts and parts[0] == "openrouter":
        parts = parts[1:]
    return parts[0].lower() if parts else ""


def assert_cross_family(model, author_family):
    """Fail closed if the gate model shares the author's family (no decorrelation)."""
    if not author_family:
        return
    gate_family = model_family(model)
    if gate_family and gate_family == author_family.strip().lower():
        raise RuntimeError(
            f"cross-family violation: gate model '{model}' (family '{gate_family}') "
            f"is the same family as the author '{author_family}'. A gate must run on a "
            "different family than the work it judges."
        )


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
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())["choices"][0]["message"]["content"]


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
    prompt = f"{system}\n\n=== ARTIFACT TO JUDGE ===\n{user}"
    cmd = ["opencode", "run", "--format", "json", "-m", opencode_model(model), prompt]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise RuntimeError("opencode CLI not found on PATH")
    if proc.returncode != 0:
        raise RuntimeError(f"opencode run failed: {proc.stderr.strip()[:400]}")
    # Reconstruct the model's reply from the event stream; fall back to raw
    # stdout if the output shape was unexpected (so extract_verdict can still try).
    return opencode_text(proc.stdout) or proc.stdout


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
    ap.add_argument("--emit-json", metavar="PATH",
                    help="write the full parsed verdict object to PATH (machine-readable). "
                         "Used by the Verifier review, which needs the findings array, not just "
                         "the verdict token.")
    ap.add_argument("--print-verdict", action="store_true",
                    help="print the raw verdict token (GO/REVIEW/NO-GO) to stdout and exit 0 for any "
                         "reached verdict; the caller decides. Still fails closed (exit 2) on error.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    try:
        # Cross-family invariant: a gate must not run on the author's family.
        assert_cross_family(args.model, args.author_family)
        if args.selftest:
            rubric = ('Return ONLY JSON {"verdict":"GO|REVIEW|NO-GO",'
                      '"report":"...","route_back":"..."}. Reply GO if the text says OK.')
            artifact = "status: OK"
        else:
            if not args.rubric:
                raise RuntimeError("--rubric is required")
            target = args.target or stdin_target()
            if not target:
                sys.exit(0)  # no artifact to judge (wrong tool/path) -> no objection
            rubric = open(args.rubric, encoding="utf-8").read()
            artifact = open(target, encoding="utf-8").read()

        call = call_opencode if args.provider == "opencode" else call_openrouter
        raw = call(args.model, rubric, artifact)
        verdict = extract_verdict(raw)
        if verdict is None:
            raise RuntimeError(f"no JSON verdict in {args.provider} output:\n{raw[:800]}")
    except Exception as e:  # any failure is a hard stop
        print(f"[gate_runner] error: {e}", file=sys.stderr)
        sys.exit(2)

    v = str(verdict.get("verdict", "")).upper()
    if args.emit_json:
        # Written before any exit branch: a NO-GO verdict is exactly when the caller most needs the
        # findings. Failing to persist them is itself fail-closed.
        try:
            with open(args.emit_json, "w", encoding="utf-8") as fh:
                json.dump(verdict, fh, indent=2)
        except OSError as e:
            print(f"[gate_runner] could not write --emit-json {args.emit_json}: {e}", file=sys.stderr)
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