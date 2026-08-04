---
name: codemap
description: Generate and read the precomputed codebase map that grounds the planning agents. Use at setup time and whenever an agent needs the real module structure, dependency flow, and entry points of the project — especially before architecture, planning, or provisioning. The map is produced deterministically with tree-sitter and works without any LLM. Always consult the codemap before assuming a file path or module boundary.
compatibility: Requires scripts/codemap.py and the tree-sitter grammar for the project's language.
---

# codemap

The planning agents in this pipeline are **stack-agnostic** — all of their awareness of *this*
project's structure comes from the codemap. Treat `codebase/MOC.md` as ground truth for module
boundaries, dependency direction, and entry points. Never invent structure; read the map.

## Prerequisites (once per environment, per language)
```bash
pip install tree-sitter tree-sitter-python      # + tree-sitter-java / tree-sitter-c as needed
pip install rich                                # optional: progress bar + summary
```

## Generate the map
Run from the project root, writing into `codebase/` so the agents find it:
```bash
python scripts/codemap.py . --lang <python|java|c> --output codebase
```
Outputs under `codebase/`: `MOC.md` (the map the agents read), `INDEX.md`, one detail doc per source
file, and `.codemap-manifest.json` (cache — do not hand-edit).

- Re-run after changes; hand-written notes inside each file doc's `<!-- notes -->` block survive.
- Full rebuild: add `--force` — but see the caveat below, it discards cached purposes.
- Each module's *purpose* comes from its docstring / KDoc / Javadoc; a file that documents itself gets
  a real purpose line, one that does not is marked `(undocumented)`.

**The optional LLM purpose backfill is currently disabled.** Do not pass `--provider`, `--model`,
`--base-url` or `--api-key` — they now exit non-zero. `--no-llm` is still accepted but is a no-op, and
`--changed` has no effect while purposes are disabled. An existing `.codemap-manifest.json` is still
*read*, so purposes an earlier model-backed run cached keep rendering as long as the file is
unchanged; it is never refreshed or rewritten. This is why `--force` is a bad default — it throws
those cached purposes away.

## Per-language status
- **Python** — rich (tree-sitter): exports, imports, module-path dependency graph.
- **Java** — supported (tree-sitter): FQN declarations + import graph; Gradle module discovery.
- **Kotlin** — supported (tree-sitter), if a team uses it.
- **C** — supported (tree-sitter, `.c`/`.h`): structural extraction via `#include`/declarations.
  Note this is text-structural, not compiler-driven, so it's thinner on heavy preprocessor/macro use.
- **C++** — NOT supported. There is no `cpp` LanguageSpec (`.cpp`/`.hpp`, `tree_sitter_cpp`,
  namespaces, templates). A C++ team needs that spec added first; even then templates/preprocessor
  limit fidelity, so a C++ team should lean harder on the human spec review to compensate.

## How agents use it
- **solution-architect / implementation-planner / spec-writer** read `codebase/MOC.md` to ground
  architecture, phasing, and spec paths in reality.
- **agent-factory** reads it to detect the stack and whether a UI surface exists (which decides
  whether `avenger-frontend-developer` is provisioned).
- **handover** checks `codebase/MOC.md` staleness.