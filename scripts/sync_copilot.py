#!/usr/bin/env python3
"""sync_copilot.py - generate the GitHub Copilot adapter from the canonical pipeline.

- Transpiles agents/*.md -> .github/agents/*.agent.md (with pipeline handoffs; all tools enabled).
- Symlinks skills/ -> .github/skills/ (same SKILL.md standard, no copy).
- Copies commands/*.md -> .github/prompts/*.prompt.md.

Run from the repo root: `python3 scripts/sync_copilot.py`. Idempotent.
"""
import glob
import os
import sys

# Optional: pin Copilot models per Claude tier. Leave empty to use Copilot's default.
# e.g. {"opus": "Claude Opus 4.5", "sonnet": "Claude Sonnet 4.5", "haiku": "Claude Haiku 4.5"}
MODEL_MAP = {}

# Pipeline handoff chain: agent -> (next agent, pre-filled prompt for the handoff button).
HANDOFF_MAP = {
    "task-analyst":           ("solution-architect",     "Design the architecture for the scoped feature."),
    "solution-architect":     ("implementation-planner", "Break the architecture into dependency-ordered phases."),
    "implementation-planner": ("spec-writer",            "Write the testable spec for phase 1."),
    "spec-writer":            ("test-author",            "Write the locked RED tests for this phase."),
    "test-author":            ("backend-architect",      "Implement the code to turn the RED tests green. Do not edit tests."),
    "backend-architect":      ("handover",               "Document the completed phase."),
    "frontend-developer":     ("handover",               "Document the completed phase."),
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(md):
    if not md.startswith("---"):
        return {}, md
    end = md.find("\n---", 3)
    if end == -1:
        return {}, md
    head, body = md[3:end].strip(), md[end + 4:].lstrip("\n")
    fm = {}
    for line in head.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def yq(s):  # YAML single-quoted scalar
    return "'" + str(s).replace("'", "''") + "'"


def build_agent_header(name, fm):
    lines = ["---", f"name: {name}", f"description: {yq(fm.get('description', '').strip())}"]
    tier = fm.get("model", "")
    if MODEL_MAP.get(tier):
        lines.append(f"model: {MODEL_MAP[tier]}")
    if name in HANDOFF_MAP:
        nxt, prompt = HANDOFF_MAP[name]
        lines += [
            "handoffs:",
            f"  - label: {yq('Next: ' + nxt)}",
            f"    agent: {nxt}",
            f"    prompt: {yq(prompt)}",
            "    send: false",
        ]
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def main():
    # agents -> .github/agents/*.agent.md
    out = os.path.join(ROOT, ".github", "agents")
    os.makedirs(out, exist_ok=True)
    n = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "agents", "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            fm, body = parse(f.read())
        with open(os.path.join(out, name + ".agent.md"), "w", encoding="utf-8") as f:
            f.write(build_agent_header(name, fm) + body)
        n += 1
        print(f"  agent: {name}.agent.md" + (" (+handoff)" if name in HANDOFF_MAP else ""))

    # commands -> .github/prompts/*.prompt.md
    pout = os.path.join(ROOT, ".github", "prompts")
    os.makedirs(pout, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(ROOT, "commands", "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        fm, body = parse(open(path, encoding="utf-8").read())
        head = "---\nmode: agent\n" + f"description: {yq(fm.get('description', name).strip())}\n---\n\n"
        with open(os.path.join(pout, name + ".prompt.md"), "w", encoding="utf-8") as f:
            f.write(head + body)
        print(f"  prompt: {name}.prompt.md")

    # skills: symlink .github/skills -> ../skills (identical SKILL.md)
    link = os.path.join(ROOT, ".github", "skills")
    if not os.path.lexists(link):
        os.symlink(os.path.join("..", "skills"), link)
        print("  skills: linked .github/skills -> ../skills")
    elif os.path.islink(link):
        print("  skills: symlink already present")
    else:
        print("  skills: WARNING .github/skills exists as a real dir, not linking", file=sys.stderr)

    print(f"done: {n} agents transpiled.")


if __name__ == "__main__":
    main()
