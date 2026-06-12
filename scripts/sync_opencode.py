#!/usr/bin/env python3
"""sync_opencode.py - generate the opencode adapter from the canonical pipeline.

- Transpiles agents/*.md (Claude format) -> .opencode/agents/*.md (opencode format).
- Symlinks skills/ -> .opencode/skills/ (same SKILL.md standard, no copy needed).

Run from the repo root: `python3 scripts/sync_opencode.py`. Idempotent.
Edit MODEL_MAP for your OpenRouter model ids before first run.
"""
import glob
import os
import sys

# Map Claude model tiers -> your opencode/OpenRouter model ids. EDIT THESE.
MODEL_MAP = {
    "opus":   "openrouter/anthropic/claude-opus-4",
    "sonnet": "openrouter/anthropic/claude-sonnet-4",
    "haiku":  "openrouter/anthropic/claude-haiku-4",
}
# Agents that should drive sessions directly (primary). Others are @-invoked subagents.
PRIMARY = set()  # e.g. {"backend-architect"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(md):
    """Return (frontmatter dict, body) from a markdown file with --- frontmatter."""
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


def tools_block(claude_tools):
    t = [x.strip() for x in claude_tools.split(",")] if claude_tools else []
    has_write = ("Write" in t) or ("Edit" in t)
    return {"write": has_write, "edit": has_write, "bash": "Bash" in t}


def main():
    out_dir = os.path.join(ROOT, ".opencode", "agents")
    os.makedirs(out_dir, exist_ok=True)
    count = 0

    for path in sorted(glob.glob(os.path.join(ROOT, "agents", "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            fm, body = parse(f.read())
        model = MODEL_MAP.get(fm.get("model", "sonnet"), fm.get("model", "sonnet"))
        mode = "primary" if name in PRIMARY else "subagent"
        tb = tools_block(fm.get("tools", ""))
        out = (
            "---\n"
            f"description: {fm.get('description', '').strip()}\n"
            f"mode: {mode}\n"
            f"model: {model}\n"
            "tools:\n"
            f"  write: {str(tb['write']).lower()}\n"
            f"  edit: {str(tb['edit']).lower()}\n"
            f"  bash: {str(tb['bash']).lower()}\n"
            "---\n\n" + body
        )
        with open(os.path.join(out_dir, name + ".md"), "w", encoding="utf-8") as f:
            f.write(out)
        count += 1
        print(f"  agent: {name} -> {model} ({mode})")

    # skills: symlink .opencode/skills -> ../skills (identical SKILL.md, no copy)
    link = os.path.join(ROOT, ".opencode", "skills")
    if not os.path.lexists(link):
        os.symlink(os.path.join("..", "skills"), link)
        print("  skills: linked .opencode/skills -> ../skills")
    elif os.path.islink(link):
        print("  skills: symlink already present")
    else:
        print("  skills: WARNING .opencode/skills exists as a real dir, not linking",
              file=sys.stderr)

    print(f"done: {count} agents transpiled.")


if __name__ == "__main__":
    main()
