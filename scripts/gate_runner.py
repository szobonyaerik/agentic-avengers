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
google/gemini-2.5-pro). Stdlib only.
"""
import argparse, json, os, re, subprocess, sys, urllib.request

VERDICT_OK = {"GO", "PASS"}


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


def call_opencode(model, system, user):
    prompt = f"{system}\n\n=== ARTIFACT TO JUDGE ===\n{user}"
    cmd = ["opencode", "run", "--format", "json", "-m", model, prompt]
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
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    try:
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