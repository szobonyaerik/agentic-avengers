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
    "opus": "openrouter/anthropic/claude-opus-5",
    "sonnet": "openrouter/anthropic/claude-sonnet-5",
    "haiku": "openrouter/anthropic/claude-haiku-4.5",
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
    head, body = md[3:end].strip(), md[end + 4 :].lstrip("\n")
    fm = {}
    for line in head.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def tools_block(claude_tools):
    """Map a canonical agent's tool list to opencode's {write, edit, bash} booleans.

    Recognizes both the clean Claude names (Read/Write/Edit/Bash/Glob/Grep, any case, with or
    without a YAML flow-list `[...]`) and the older snake_case names some agents still use
    (replace_string_in_file, run_in_terminal). Case-insensitive.
    """
    raw = (claude_tools or "").strip().strip("[]")
    toks = {x.strip().strip("[]").lower() for x in raw.split(",") if x.strip()}
    write_names = {
        "write",
        "edit",
        "multiedit",
        "replace_string_in_file",
        "create_file",
    }
    bash_names = {"bash", "run_in_terminal", "run_in_bash", "shell"}
    has_write = bool(toks & write_names)
    return {"write": has_write, "edit": has_write, "bash": bool(toks & bash_names)}


def prune(out_dir: str, generated: set[str]) -> int:
    """Delete generated agents whose canonical source no longer exists.

    Without this, deleting an agent from `agents/` leaves its transpiled twin behind and opencode
    keeps offering a stage the pipeline has removed — the adapter silently drifts from canonical.
    """
    removed = 0
    for path in sorted(glob.glob(os.path.join(out_dir, "*.md"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name not in generated:
            os.remove(path)
            removed += 1
            print(f"  pruned: {name} (no canonical agents/{name}.md)")
    return removed


def main():
    out_dir = os.path.join(ROOT, ".opencode", "agents")
    os.makedirs(out_dir, exist_ok=True)
    count = 0
    generated: set[str] = set()

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
        generated.add(name)
        print(f"  agent: {name} -> {model} ({mode})")

    pruned = prune(out_dir, generated)

    # skills: symlink .opencode/skills -> ../skills (identical SKILL.md, no copy)
    link = os.path.join(ROOT, ".opencode", "skills")
    if not os.path.lexists(link):
        os.symlink(os.path.join("..", "skills"), link)
        print("  skills: linked .opencode/skills -> ../skills")
    elif os.path.islink(link):
        print("  skills: symlink already present")
    else:
        print(
            "  skills: WARNING .opencode/skills exists as a real dir, not linking",
            file=sys.stderr,
        )

    print(f"done: {count} agents transpiled, {pruned} pruned.")


if __name__ == "__main__":
    main()
