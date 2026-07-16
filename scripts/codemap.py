#!/usr/bin/env python3
"""
codemap.py — generic, language-agnostic codebase map generator
===============================================================

Produces a pre-computed, token-cheap map of a codebase so an agent can navigate
it without reading source. Structure (exports, dependencies, used-by) is
extracted *deterministically* with tree-sitter; a local LLM (Ollama) is used
only to fill the one thing AST cannot give — a one-line semantic purpose — and
only when the source has no docstring/KDoc/Javadoc of its own.

The same script runs on a Python repo or a Kotlin repo with only configuration
changing — in the simplest case just `--lang`.

Outputs (all under --output, default `codemap/`):
  INDEX.md                      Root navigator: one line + link per file, grouped by dir.
  MOC.md                        Map of Content: entry points + layers (by tag).
  <path>/<name>.md              Per-file detail (mirrors source name): purpose, exports,
                                deps, used-by, tags, and a preserved notes block.
  .codemap-manifest.json        Change-tracking + LLM-purpose cache (do not hand-edit).

Quick start:
  pip install tree-sitter tree-sitter-python          # + -java -c -kotlin as needed
  pip install rich                                     # optional: progress bar + panels

  # Python repo (defaults are Python-flavored, local Ollama for purposes):
  python codemap.py /path/to/py-repo

  # Kotlin repo — only the language (and scan model) changes:
  python codemap.py /path/to/kt-repo --lang kotlin

  # Use OpenRouter (or any OpenAI-compatible endpoint) for purposes:
  export OPENROUTER_API_KEY=sk-or-...
  python codemap.py /path/to/repo --provider openrouter --model openai/gpt-4o-mini

Common flags:
  --lang python,kotlin,java,c   Languages to scan (comma-sep or repeatable).
  --output DIR                  Output root (default: codemap).
  --scan auto|walk|gradle       Source-root discovery (auto picks gradle for JVM repos).
  --changed                     Only resolve purpose for git-staged files (pre-commit).
  --no-llm                      Skip Ollama; docstring/KDoc + structure only.
  --force                       Ignore the manifest; rebuild everything.

The build runs in six phases (1-6), each independently verifiable:
  1. Config + ProjectModel (source-root discovery) + file collection.
  2. tree-sitter extraction -> language-neutral FileInfo.
  3. Symbol table + reverse-dependency (used-by) graph.
  4. Manifest cache + docstring/KDoc-first purpose resolution.
  5. Writers: INDEX.md, per-file detail docs (mirrored names), MOC.md.
  6. Language registry (Python/Kotlin/Java/C) — driven entirely by config.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

# Optional pretty output. The tool runs fine without rich (plain prints); when
# present it shows a config banner, a live progress bar, and a summary panel.
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    _RICH = True
except ImportError:  # pragma: no cover
    _RICH = False

# ════════════════════════════════════════════════════════════════════════════
# SLICE 6 (defined first because everything else is driven by it):
# LANGUAGE REGISTRY — the only thing that differs between a Python and a Kotlin
# repo. Each LanguageSpec is pure configuration over a single extraction engine.
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LanguageSpec:
    """Per-language configuration for the (single) tree-sitter extractor.

    Node-type names below were verified against the installed grammars rather
    than guessed; they are what each grammar actually emits for top-level
    declarations, imports and package headers.
    """

    name: str
    extensions: frozenset[str]
    ts_module: str                       # importable wheel, e.g. "tree_sitter_python"

    # Node types collected as the file's public "exports" (direct children only).
    class_types: frozenset[str]
    func_types: frozenset[str]
    prop_types: frozenset[str]
    import_types: frozenset[str]
    package_type: Optional[str]          # None -> module identity comes from the path

    # How the file-level purpose comment is found.
    #   "docstring"     : Python — first string literal in the module body
    #   "leading_block" : Kotlin/Java/C — first /** ... */ before any declaration
    doc_style: str

    # How this language contributes to / consumes the cross-file symbol graph.
    #   "module_path"  : Python — file == module 'a.b.c'; imports name modules
    #   "fqn_decls"    : Kotlin/Java — file exports 'package.Decl'; imports name FQNs
    #   "include_path" : C — file is keyed by header name; #include names headers
    symbol_strategy: str

    # Default tag signals (annotation/keyword/import substrings -> "#tag").
    tag_signals: dict[str, tuple[str, ...]] = field(default_factory=dict)


LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=frozenset({".py"}),
        ts_module="tree_sitter_python",
        class_types=frozenset({"class_definition"}),
        func_types=frozenset({"function_definition"}),
        prop_types=frozenset(),
        import_types=frozenset({"import_statement", "import_from_statement"}),
        package_type=None,
        doc_style="docstring",
        symbol_strategy="module_path",
        tag_signals={
            "#api": ("fastapi", "flask", "APIRouter", "router", "endpoint", "starlette"),
            "#db": ("sqlalchemy", "psycopg", "sqlmodel", "asyncpg", "Session", "engine"),
            "#config": ("settings", "BaseSettings", "pydantic_settings", "config", "env"),
            "#model": ("BaseModel", "dataclass", "pydantic", "schema"),
            "#cli": ("argparse", "click", "typer", "ArgumentParser"),
            "#task": ("celery", "rq", "scheduler", "cron"),
            "#test": ("pytest", "unittest", "fixture", "test_"),
        },
    ),
    "kotlin": LanguageSpec(
        name="kotlin",
        extensions=frozenset({".kt"}),
        ts_module="tree_sitter_kotlin",
        # interfaces parse as class_declaration with an `interface` keyword child.
        class_types=frozenset({"class_declaration", "object_declaration"}),
        func_types=frozenset({"function_declaration"}),
        prop_types=frozenset({"property_declaration"}),
        import_types=frozenset({"import"}),
        package_type="package_header",
        doc_style="leading_block",
        symbol_strategy="fqn_decls",
        tag_signals={
            "#controller": ("Controller", "RestController", "GraphQLController"),
            "#service": ("Service",),
            "#repository": ("Repository", "R2dbcRepository", "ReactiveCrudRepository"),
            "#config": ("Configuration", "ConfigurationProperties"),
            "#graphql": ("QueryMapping", "MutationMapping", "SchemaMapping"),
            "#kafka": ("KafkaListener", "KafkaTemplate"),
            "#entity": ("Entity", "Table", "Document"),
            "#event": ("ApplicationEvent", "EventListener"),
            "#test": ("Test", "TestContainers", "ExtendWith"),
        },
    ),
    "java": LanguageSpec(
        name="java",
        extensions=frozenset({".java"}),
        ts_module="tree_sitter_java",
        class_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
        ),
        func_types=frozenset({"method_declaration"}),
        prop_types=frozenset(),
        import_types=frozenset({"import_declaration"}),
        package_type="package_declaration",
        doc_style="leading_block",
        symbol_strategy="fqn_decls",
        tag_signals={
            "#controller": ("Controller", "RestController"),
            "#service": ("Service",),
            "#repository": ("Repository",),
            "#config": ("Configuration", "Bean"),
            "#entity": ("Entity", "Table"),
            "#test": ("Test", "Junit", "Mock"),
        },
    ),
    "c": LanguageSpec(
        name="c",
        extensions=frozenset({".c", ".h"}),
        ts_module="tree_sitter_c",
        class_types=frozenset({"struct_specifier", "enum_specifier", "union_specifier", "type_definition"}),
        func_types=frozenset({"function_definition"}),
        prop_types=frozenset(),
        import_types=frozenset({"preproc_include"}),
        package_type=None,
        doc_style="leading_block",
        symbol_strategy="include_path",
        tag_signals={
            "#header": (".h",),
            "#entry": ("int main",),
        },
    ),
}


# ════════════════════════════════════════════════════════════════════════════
# SLICE 1: CONFIG + PROJECT MODEL (source-root discovery) + FILE COLLECTION
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "build", "dist",
    "out", "target", "coverage", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", "vendor", ".gradle", ".idea",
}

MANIFEST_FILENAME = ".codemap-manifest.json"
MANIFEST_VERSION = 1
# Bump this (or change the model) to invalidate every cached LLM purpose at once.
PROMPT_VERSION = "1"
MAX_FILE_BYTES_DEFAULT = 40_000

# Rough token-estimate heuristics for --dry-run (English/code ≈ 4 chars/token).
CHARS_PER_TOKEN = 4
PROMPT_OVERHEAD_TOKENS = 60       # the fixed instruction sent with each purpose call
PURPOSE_OUTPUT_TOKENS = 30        # one short sentence back


@dataclass
class Config:
    """Single source of truth for a run. Everything downstream reads this, never
    argv — so a future `Config.from_toml(path)` is a drop-in alternative to the CLI."""

    root: Path
    output: Path
    langs: list[str]
    scan: str                       # "auto" | "walk" | "gradle"
    use_llm: bool
    changed_only: bool
    force: bool
    dry_run: bool
    price_in: Optional[float]       # USD per 1M input tokens (optional)
    price_out: Optional[float]      # USD per 1M output tokens (optional)
    provider: str                   # "ollama" | "openrouter" | "openai"
    base_url: str                   # resolved endpoint URL
    api_key: Optional[str]          # None for ollama / local
    model: str
    max_file_bytes: int
    ignore_dirs: set[str]
    internal_prefixes: tuple[str, ...]              # display hint only (see notes)
    tag_overrides: dict[str, dict[str, tuple[str, ...]]]   # lang -> {tag: signals}

    @property
    def specs(self) -> list[LanguageSpec]:
        return [LANGUAGES[l] for l in self.langs]

    def spec_for(self, path: Path) -> Optional[LanguageSpec]:
        for spec in self.specs:
            if path.suffix in spec.extensions:
                return spec
        return None

    def active_extensions(self) -> set[str]:
        exts: set[str] = set()
        for spec in self.specs:
            exts |= set(spec.extensions)
        return exts

    def tag_signals_for(self, spec: LanguageSpec) -> dict[str, tuple[str, ...]]:
        signals = dict(spec.tag_signals)
        signals.update(self.tag_overrides.get(spec.name, {}))
        return signals


class ProjectModel:
    """Discovers the source roots to scan. The grouping unit downstream is always
    a directory; this class only decides *which* directories enter the scan, so
    Gradle awareness is a scope provider — not a second grouping model."""

    def source_roots(self, config: Config) -> list[Path]:
        raise NotImplementedError


class WalkProjectModel(ProjectModel):
    """Default: the whole repo is one source root; ignore-dirs prune the walk."""

    def source_roots(self, config: Config) -> list[Path]:
        return [config.root]


class GradleProjectModel(ProjectModel):
    """JVM repos: read settings.gradle.kts, map each `include(...)` module to its
    src/main/kotlin (and src/main/java) directory. Falls back to a whole-repo walk
    if no settings file is present."""

    _INCLUDE_RE = re.compile(r"include\s*\(([^)]+)\)", re.DOTALL)
    _MODULE_RE = re.compile(r'"([^"]+)"')

    def source_roots(self, config: Config) -> list[Path]:
        settings = config.root / "settings.gradle.kts"
        if not settings.exists():
            settings = config.root / "settings.gradle"
        if not settings.exists():
            return [config.root]

        content = settings.read_text(encoding="utf-8", errors="replace")
        roots: list[Path] = []
        for block in self._INCLUDE_RE.finditer(content):
            for m in self._MODULE_RE.finditer(block.group(1)):
                module_dir = config.root / m.group(1).lstrip(":").replace(":", "/")
                for sub in ("src/main/kotlin", "src/main/java", "src/test/kotlin", "src/test/java"):
                    candidate = module_dir / sub
                    if candidate.exists():
                        roots.append(candidate)
        return roots or [config.root]


def select_project_model(config: Config) -> ProjectModel:
    if config.scan == "walk":
        return WalkProjectModel()
    if config.scan == "gradle":
        return GradleProjectModel()
    # auto: Gradle when a JVM language is active and a settings file exists.
    jvm = {"kotlin", "java"} & set(config.langs)
    has_settings = (config.root / "settings.gradle.kts").exists() or (config.root / "settings.gradle").exists()
    return GradleProjectModel() if (jvm and has_settings) else WalkProjectModel()


def collect_files(config: Config, model: ProjectModel) -> list[Path]:
    """Gather all eligible source files under the discovered source roots."""
    exts = config.active_extensions()
    seen: set[Path] = set()
    files: list[Path] = []
    for root in model.source_roots(config):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in exts:
                continue
            if any(part in config.ignore_dirs for part in path.parts):
                continue
            try:
                if path.stat().st_size > config.max_file_bytes:
                    continue
            except OSError:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return files


# ════════════════════════════════════════════════════════════════════════════
# SLICE 2: TREE-SITTER EXTRACTION -> LANGUAGE-NEUTRAL FileInfo
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class Declaration:
    name: str
    kind: str
    doc: Optional[str] = None


@dataclass
class FileInfo:
    """Language-neutral structural metadata for one source file."""

    rel_path: str
    package: Optional[str] = None
    file_doc: Optional[str] = None
    declarations: list[Declaration] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    source_snippet: str = ""
    # Filled in later phases:
    internal_deps: list[str] = field(default_factory=list)   # resolved repo file paths
    external_deps: list[str] = field(default_factory=list)   # unresolved (libraries)
    used_by: list[str] = field(default_factory=list)
    purpose: str = ""
    tags: list[str] = field(default_factory=list)


_PARSER_CACHE: dict[str, object] = {}


def _get_parser(spec: LanguageSpec):
    """Lazily import a language wheel and build its Parser (cached)."""
    if spec.name in _PARSER_CACHE:
        return _PARSER_CACHE[spec.name]
    try:
        from tree_sitter import Language, Parser
        module = importlib.import_module(spec.ts_module)
    except ImportError as exc:
        sys.exit(
            f"Error: missing grammar for '{spec.name}'. Install it with:\n"
            f"  pip install {spec.ts_module.replace('_', '-')}\n  ({exc})"
        )
    parser = Parser(Language(module.language()))
    _PARSER_CACHE[spec.name] = parser
    return parser


def _node_name(node) -> Optional[str]:
    """Best-effort declaration name across grammars.

    Handles: a 'name' field (Python/Kotlin/Java types & funcs); the C declarator
    chain (function_definition -> function_declarator -> identifier); and Kotlin
    properties (property_declaration -> variable_declaration -> identifier)."""
    field_node = node.child_by_field_name("name")
    if field_node is not None:
        return field_node.text.decode("utf-8", "replace")

    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        ident = _first_identifier(declarator)
        if ident is not None:
            return ident.text.decode("utf-8", "replace")

    ident = _first_identifier(node)
    return ident.text.decode("utf-8", "replace") if ident is not None else None


_IDENT_TYPES = {"identifier", "type_identifier", "simple_identifier", "field_identifier"}


def _first_identifier(node):
    """Descend through declarator/variable wrappers to the first identifier node."""
    if node.type in _IDENT_TYPES:
        return node
    for child in node.children:
        found = _first_identifier(child)
        if found is not None:
            return found
    return None


_KOTLIN_KIND = [
    ("enum", "enum"), ("sealed", "sealed class"), ("data", "data class"),
    ("abstract", "abstract class"), ("interface", "interface"),
]


def _declaration_kind(node, spec: LanguageSpec, first_line: str) -> str:
    """Map a node + its first source line to a human-readable kind."""
    t = node.type
    if t in spec.func_types:
        return "function"
    if t in spec.prop_types:
        return "property"
    if t == "object_declaration":
        return "object"
    if t in {"struct_specifier"}:
        return "struct"
    if t in {"enum_specifier", "enum_declaration"}:
        return "enum"
    if t in {"union_specifier"}:
        return "union"
    if t in {"type_definition"}:
        return "typedef"
    if t == "interface_declaration":
        return "interface"
    if t == "record_declaration":
        return "record"
    # class_declaration (Kotlin/Java) — refine from modifiers on the first line.
    for keyword, label in _KOTLIN_KIND:
        if re.search(rf"\b{keyword}\b", first_line):
            return label
    return "class"


def _strip_doc_comment(text: str) -> Optional[str]:
    """First meaningful line of a /** ... */ block (or // line) comment."""
    for raw in text.splitlines():
        line = raw.strip().lstrip("/").lstrip("*").strip()
        line = line.rstrip("*/").strip()
        if line and not line.startswith("@"):
            return line
    return None


def _extract_file_doc(spec: LanguageSpec, root, source: str) -> Optional[str]:
    if spec.doc_style == "docstring":
        for child in root.children:
            if child.type == "expression_statement" and child.children:
                inner = child.children[0]
                if inner.type == "string":
                    raw = inner.text.decode("utf-8", "replace").strip("\"'").strip()
                    return raw.splitlines()[0].strip() if raw else None
            if child.type not in {"comment"}:
                break
        return None
    # leading_block: first /** comment before the first real declaration.
    for child in root.children:
        if child.type in {"comment", "block_comment", "line_comment"}:
            text = child.text.decode("utf-8", "replace")
            if text.lstrip().startswith("/**"):
                return _strip_doc_comment(text)
        elif child.type not in {"package_header", "package_declaration", "import",
                                "import_declaration", "import_statement",
                                "import_from_statement", "preproc_include", "comment"}:
            break
    return None


def _py_anchor(rel: str) -> list[str]:
    """Package components of a Python file == its parent directory, dotted.
    Used to turn relative imports into absolute module paths."""
    parent = Path(rel).parent
    return [] if str(parent) == "." else list(parent.parts)


def _extract_imports(spec: LanguageSpec, node, source: str, anchor: list[str]) -> list[str]:
    """Return normalized absolute import target(s) for one import node. Handles
    multi-target imports and resolves Python relative imports (`.`, `..`) against
    `anchor` (the importing file's package) so they match the symbol table."""
    if spec.name == "c":
        m = re.search(r'[<"]([^>"]+)[>"]', node.text.decode("utf-8", "replace"))
        return [m.group(1)] if m else []
    if spec.name in {"kotlin", "java"}:
        text = re.sub(r"^import\s+", "", node.text.decode("utf-8", "replace").strip())
        text = re.sub(r"\s+as\s+\w+$", "", text.rstrip(";").strip())
        return [text] if text else []

    # python
    if node.type == "import_statement":  # import a.b, c  -> ["a.b", "c"]
        return [n.text.decode("utf-8", "replace").split(" as ")[0].strip()
                for n in node.children_by_field_name("name")]
    if node.type == "import_from_statement":
        mn = node.child_by_field_name("module_name")
        if mn is None:
            return []
        mtext = mn.text.decode("utf-8", "replace")
        if not mtext.startswith("."):            # absolute: from a.b import X -> module a.b
            return [mtext]
        dots = len(mtext) - len(mtext.lstrip("."))
        tail = mtext[dots:]
        # N dots climb (N-1) packages up from the importing file's package.
        base = anchor[: len(anchor) - (dots - 1)] if (dots - 1) <= len(anchor) else []
        if tail:                                 # from .mod import X -> base.mod (a file)
            return [".".join([*base, *tail.split(".")])]
        # from . import a, b -> base.a, base.b (the imported names are submodules)
        return [".".join([*base, n.text.decode("utf-8", "replace")])
                for n in node.children_by_field_name("name")]
    return []


def _unwrap(node):
    """Python decorators wrap the real definition in decorated_definition."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in {"function_definition", "class_definition"}:
                return child
    return node


def extract_file_info(path: Path, config: Config, spec: LanguageSpec) -> FileInfo:
    """Parse one file with tree-sitter and return language-neutral metadata."""
    rel = path.relative_to(config.root).as_posix()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileInfo(rel_path=rel)

    parser = _get_parser(spec)
    root = parser.parse(bytes(source, "utf-8")).root_node

    info = FileInfo(rel_path=rel, source_snippet=source[:3000])
    info.file_doc = _extract_file_doc(spec, root, source)
    anchor = _py_anchor(rel) if spec.name == "python" else []

    for raw_child in root.children:
        child = _unwrap(raw_child)
        ctype = child.type

        if spec.package_type and ctype == spec.package_type:
            text = child.text.decode("utf-8", "replace")
            info.package = re.sub(r"^package\s+", "", text).strip().rstrip(";")
            continue

        if ctype in spec.import_types:
            info.imports.extend(imp for imp in _extract_imports(spec, child, source, anchor) if imp)
            continue

        if ctype in spec.class_types or ctype in spec.func_types or ctype in spec.prop_types:
            name = _node_name(child)
            if not name or name.startswith("_"):
                continue
            child_text = child.text.decode("utf-8", errors="replace")
            if not child_text.strip():
                continue
            first_line = child_text.splitlines()[0]
            info.declarations.append(
                Declaration(name=name, kind=_declaration_kind(child, spec, first_line))
            )

    # Annotations / decorators anywhere in the file (cheap signal for tagging).
    info.annotations = sorted(set(re.findall(r"@(\w+)", source)))
    info.imports = sorted(set(info.imports))
    return info


# ════════════════════════════════════════════════════════════════════════════
# SLICE 3: SYMBOL TABLE + REVERSE-DEPENDENCY (USED-BY) GRAPH
# ════════════════════════════════════════════════════════════════════════════


def _module_path(rel: str) -> str:
    """'pkg/sub/mod.py' -> 'pkg.sub.mod'; 'pkg/__init__.py' -> 'pkg'."""
    p = Path(rel).with_suffix("")
    if p.name == "__init__":
        p = p.parent
    return ".".join(p.parts)


def build_symbol_table(
    all_info: dict[str, FileInfo], spec_of: Callable[[str], LanguageSpec]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build {fully-qualified-symbol -> file} and {package -> [files]} (wildcards)."""
    symbols: dict[str, str] = {}
    packages: dict[str, list[str]] = defaultdict(list)

    for rel, info in all_info.items():
        strategy = spec_of(rel).symbol_strategy
        if strategy == "module_path":
            symbols[_module_path(rel)] = rel
        elif strategy == "fqn_decls":
            pkg = info.package or ""
            if pkg:
                packages[pkg].append(rel)
            for decl in info.declarations:
                fqn = f"{pkg}.{decl.name}" if pkg else decl.name
                symbols[fqn] = rel
        elif strategy == "include_path":
            symbols[rel] = rel
            symbols[Path(rel).name] = rel  # basename match for #include "x.h"

    return symbols, dict(packages)


def _imported_symbols(imp: str, spec: LanguageSpec) -> list[str]:
    """Resolvable candidate targets for a single import."""
    out: list[str] = [imp]                                   # module path / FQN / include
    if spec.symbol_strategy == "include_path":
        out.append(Path(imp).name)                           # also match by header basename
    return out


def resolve_graph(all_info: dict[str, FileInfo], config: Config) -> None:
    """Populate internal_deps / external_deps / used_by on every FileInfo."""
    spec_by_rel = {rel: config.spec_for(config.root / rel) for rel in all_info}
    spec_of = lambda rel: spec_by_rel[rel] or config.specs[0]

    symbols, packages = build_symbol_table(all_info, spec_of)
    used_by: dict[str, set[str]] = defaultdict(set)

    for rel, info in all_info.items():
        spec = spec_of(rel)
        resolved: set[str] = set()
        unresolved: list[str] = []

        for imp in info.imports:
            targets: set[str] = set()
            if spec.symbol_strategy == "fqn_decls" and imp.endswith(".*"):
                for f in packages.get(imp[:-2], []):
                    targets.add(f)
            else:
                for cand in _imported_symbols(imp, spec):
                    if cand in symbols:
                        targets.add(symbols[cand])
                        break
            targets.discard(rel)
            if targets:
                resolved |= targets
                for t in targets:
                    used_by[t].add(rel)
            else:
                unresolved.append(_collapse_external(imp, spec))

        info.internal_deps = sorted(resolved)
        info.external_deps = sorted(set(d for d in unresolved if d))

    for rel, info in all_info.items():
        info.used_by = sorted(used_by.get(rel, set()))


def _collapse_external(imp: str, spec: LanguageSpec) -> str:
    """Reduce an unresolved import to a readable library identifier."""
    if spec.symbol_strategy == "include_path":
        return Path(imp).name
    parts = imp.rstrip(".*").split(".")
    if parts and parts[0] in {"kotlin", "java", "javax", "kotlinx", "sun"}:
        return ".".join(parts[:2])
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


# ════════════════════════════════════════════════════════════════════════════
# SLICE 4: MANIFEST CACHE + DOCSTRING/KDOC-FIRST PURPOSE RESOLUTION
# ════════════════════════════════════════════════════════════════════════════


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class Manifest:
    """Versioned, content-hash-keyed cache. Stores only the LLM purpose (the
    expensive, non-deterministic output). Invalidates cached purposes when the
    prompt version or model changes; tracks the file set for stale-doc cleanup."""

    def __init__(self, config: Config) -> None:
        self._path = config.output / MANIFEST_FILENAME
        # Provider+model is part of the cache key: switching backend or model
        # invalidates cached LLM purposes (file content alone is not enough).
        self._model = f"{config.provider}:{config.model}"
        self._data: dict[str, dict] = {}
        self._force = config.force

    def load(self) -> None:
        if self._force or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("version") != MANIFEST_VERSION:
            return
        # Prompt/model identity is part of the cache key for LLM purposes.
        if payload.get("prompt_version") == PROMPT_VERSION and payload.get("model") == self._model:
            self._data = payload.get("files", {})

    def cached_purpose(self, rel: str, h: str) -> Optional[str]:
        entry = self._data.get(rel)
        if entry and entry.get("hash") == h:
            return entry.get("purpose")
        return None

    def record(self, rel: str, h: str, purpose: str) -> None:
        self._data[rel] = {"hash": h, "purpose": purpose}

    def known_files(self) -> set[str]:
        return set(self._data)

    def save(self, current_files: set[str]) -> None:
        self._data = {rel: e for rel, e in self._data.items() if rel in current_files}
        payload = {
            "version": MANIFEST_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": self._model,
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": self._data,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class PurposeProvider:
    """Generates a one-line semantic purpose for a file. Subclasses talk to a
    specific backend; all degrade gracefully to a fallback string on failure so
    one bad request never aborts the build."""

    def purpose(self, snippet: str, filename: str) -> str:
        raise NotImplementedError


_PURPOSE_INSTRUCTION = (
    "Summarize what this source file does in ONE sentence, max 14 words. "
    "Output only the sentence — no preamble, no trailing period."
)


def _purpose_prompt(snippet: str, filename: str) -> str:
    return f"Source file: {filename}\n{_PURPOSE_INSTRUCTION}\n\n```\n{snippet[:2000]}\n```"


class OllamaProvider(PurposeProvider):
    """Local Ollama via its native /api/generate endpoint (no API key)."""

    def __init__(self, url: str, model: str, timeout: int = 120) -> None:
        self.url, self.model, self.timeout = url, model, timeout
        self._down = False

    def purpose(self, snippet: str, filename: str) -> str:
        if self._down:
            return "(undocumented — model unavailable)"
        body = json.dumps({
            "model": self.model, "prompt": _purpose_prompt(snippet, filename),
            "stream": False, "think": False,
            "options": {"num_predict": 60, "temperature": 0.1},
        }).encode()
        try:
            req = urllib.request.Request(
                self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = json.loads(resp.read()).get("response", "").strip().rstrip(".")
                return text or "(undocumented)"
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ Ollama unavailable ({exc}); continuing without it", file=sys.stderr)
            self._down = True
            return "(undocumented — model unavailable)"


class OpenAICompatProvider(PurposeProvider):
    """Any OpenAI-compatible /chat/completions endpoint — OpenRouter, OpenAI,
    Azure, GitHub Models, or a local server. Bearer auth, stdlib urllib, with a
    small exponential backoff on 429/5xx (OpenRouter is rate-limited)."""

    def __init__(
        self, base_url: str, api_key: Optional[str], model: str,
        timeout: int = 120, max_retries: int = 4,
    ) -> None:
        self.url, self.api_key, self.model = base_url, api_key, model
        self.timeout, self.max_retries = timeout, max_retries
        self._down = False

    def purpose(self, snippet: str, filename: str) -> str:
        if self._down:
            return "(undocumented — model unavailable)"
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": _PURPOSE_INSTRUCTION},
                {"role": "user", "content": _purpose_prompt(snippet, filename)},
            ],
            "max_tokens": 60, "temperature": 0.1,
        }).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            # OpenRouter ignores these if absent; harmless elsewhere.
            "HTTP-Referer": "https://localhost/codemap",
            "X-Title": "codemap",
        }
        backoff = 2.0
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                    text = data["choices"][0]["message"]["content"].strip().rstrip(".")
                    return text or "(undocumented)"
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                print(f"  ⚠ {self.model} request failed (HTTP {exc.code}); continuing without LLM",
                      file=sys.stderr)
                self._down = True
                return "(undocumented — model unavailable)"
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠ LLM request failed ({exc}); continuing without LLM", file=sys.stderr)
                self._down = True
                return "(undocumented — model unavailable)"
        return "(undocumented — model unavailable)"


# provider -> (default endpoint, default model, API-key env var). --model / --base-url
# / --api-key override these; the env var is the convenient default for the key.
PROVIDER_DEFAULTS: dict[str, dict] = {
    "ollama":     {"base_url": "http://localhost:11434/api/generate",       "model": "gemma3:1b",            "env": None},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1/chat/completions", "model": "openai/gpt-4o-mini", "env": "OPENROUTER_API_KEY"},
    "openai":     {"base_url": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o-mini",          "env": "OPENAI_API_KEY"},
}


def make_provider(config: Config) -> Optional[PurposeProvider]:
    if not config.use_llm:
        return None
    if config.provider == "ollama":
        return OllamaProvider(config.base_url, config.model)
    return OpenAICompatProvider(config.base_url, config.api_key, config.model)


def resolve_purpose(
    info: FileInfo, path: Path, h: str, manifest: Manifest, provider: Optional[PurposeProvider]
) -> str:
    """Ladder: file doc -> cached LLM purpose -> fresh LLM -> fallback. The LLM is
    never asked about a file that already documents itself."""
    if info.file_doc:
        return info.file_doc.rstrip(".")
    cached = manifest.cached_purpose(info.rel_path, h)
    if cached:
        return cached
    if provider is not None:
        purpose = provider.purpose(info.source_snippet, path.name)
        manifest.record(info.rel_path, h, purpose)
        return purpose
    return "(undocumented — add a docstring/KDoc or run without --no-llm)"


def infer_tags(info: FileInfo, signals: dict[str, tuple[str, ...]]) -> list[str]:
    # Key off external libraries + annotations + names — NOT internal import
    # paths, which produce false positives (e.g. "app.config" -> #config).
    haystack = " ".join([
        Path(info.rel_path).name, info.file_doc or "", *info.annotations,
        *info.external_deps, *(d.name for d in info.declarations),
    ])
    tags = [tag for tag, subs in signals.items() if any(s in haystack for s in subs)]
    if "/src/test/" in info.rel_path or "/test" in info.rel_path.lower():
        tags.append("#test")
    return sorted(set(tags))


# ════════════════════════════════════════════════════════════════════════════
# SLICE 5: WRITERS — INDEX.md, per-FILE docs (mirrored names), MOC.md
# ════════════════════════════════════════════════════════════════════════════

# Hand-written notes survive regeneration: anything between these markers in a
# per-file doc is preserved on the next run.
_NOTES_RE = re.compile(r"<!-- notes -->\n?(.*?)<!-- /notes -->", re.DOTALL)


def _load_file_notes(md_path: Path) -> str:
    if not md_path.exists():
        return ""
    m = _NOTES_RE.search(md_path.read_text(encoding="utf-8", errors="replace"))
    return m.group(1).strip() if m else ""


def _wikilink(rel: str) -> str:
    return f"[[{Path(rel).with_suffix('').as_posix()}]]"


def _doc_path(config: Config, rel: str) -> Path:
    """Per-file doc mirrors the source path & name: app/x/auth.py -> app/x/auth.md."""
    return config.output / Path(rel).with_suffix(".md")


def _fmt_list(items: list[str], limit: int = 8) -> str:
    if not items:
        return "—"
    shown = [f"`{i}`" for i in items[:limit]]
    return ", ".join(shown) + (" …" if len(items) > limit else "")


def _fmt_exports(decls: list[Declaration], limit: int = 8) -> str:
    if not decls:
        return "—"
    parts = [f"`{d.name}()`" if d.kind == "function" else f"`{d.name}`" for d in decls[:limit]]
    return ", ".join(parts) + (" …" if len(decls) > limit else "")


def write_index(all_info: dict[str, FileInfo], config: Config) -> Path:
    lines = [
        "# Codebase Index",
        "<!-- auto-generated by codemap.py — do not hand-edit -->",
        "<!-- One line per file. Follow the link for that file's detail doc. -->",
        "",
    ]
    by_dir: dict[str, list[str]] = defaultdict(list)
    for rel in all_info:
        by_dir[str(Path(rel).parent)].append(rel)

    for directory in sorted(by_dir):
        lines.append(f"## {directory}/")
        for rel in sorted(by_dir[directory]):
            info = all_info[rel]
            tags = " ".join(info.tags)
            suffix = f"  {tags}" if tags else ""
            lines.append(f"- {_wikilink(rel)} — {info.purpose}{suffix}")
        lines.append("")

    out = config.output / "INDEX.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_file_docs(all_info: dict[str, FileInfo], config: Config) -> int:
    """One detail doc per source file, mirroring its path and name. Hand-written
    content inside the <!-- notes --> block is preserved across runs."""
    written = 0
    for rel, info in all_info.items():
        doc = _doc_path(config, rel)
        doc.parent.mkdir(parents=True, exist_ok=True)
        notes = _load_file_notes(doc)

        lines = [
            f"# {Path(rel).name}",
            f"`{rel}`",
            "<!-- auto-generated by codemap.py — edit ONLY inside the notes block -->",
            "",
            "## Purpose",
            info.purpose,
            "",
            "## Exports",
            _fmt_exports(info.declarations),
            "",
            "## Internal dependencies",
            ", ".join(_wikilink(d) for d in info.internal_deps) or "—",
            "",
            "## External dependencies",
            _fmt_list(info.external_deps),
            "",
            "## Used by",
            ", ".join(_wikilink(u) for u in info.used_by) or "—",
        ]
        if info.tags:
            lines += ["", "## Tags", " ".join(info.tags)]
        lines += ["", "## Notes", "<!-- notes -->", notes, "<!-- /notes -->", ""]

        doc.write_text("\n".join(lines), encoding="utf-8")
        written += 1
    return written


def cleanup_stale_docs(all_info: dict[str, FileInfo], config: Config) -> int:
    """Delete generated .md docs whose source file no longer exists (mirrors the
    Cartographer's orphan cleanup). INDEX.md / MOC.md are never touched."""
    keep = {_doc_path(config, rel).resolve() for rel in all_info}
    keep |= {(config.output / "INDEX.md").resolve(), (config.output / "MOC.md").resolve()}
    removed = 0
    for md in config.output.rglob("*.md"):
        if md.resolve() not in keep:
            md.unlink()
            removed += 1
    return removed


def write_moc(all_info: dict[str, FileInfo], config: Config) -> Path:
    """Deterministic Map of Content: entry points + tag-grouped layers. No LLM,
    grounded entirely in the real dependency graph and inferred tags."""
    today = datetime.date.today().isoformat()
    lines = [
        "# Codebase Map of Content",
        f"> Auto-generated by codemap.py. Last updated: {today}",
        "",
        "## Overview",
        f"{len(all_info)} source file(s) across {len({str(Path(r).parent) for r in all_info})} "
        f"director(ies), languages: {', '.join(config.langs)}.",
        "",
        "## Entry Points",
        "<!-- files nothing else imports — likely mains, controllers, scripts -->",
    ]
    entry_points = sorted(
        rel for rel, info in all_info.items()
        if not info.used_by and "#test" not in info.tags
    )
    if entry_points:
        for rel in entry_points:
            lines.append(f"- {_wikilink(rel)} — {all_info[rel].purpose}")
    else:
        lines.append("- —")
    lines.append("")

    by_tag: dict[str, list[str]] = defaultdict(list)
    for rel, info in all_info.items():
        for tag in info.tags or ["#other"]:
            by_tag[tag].append(rel)

    lines.append("## Layers")
    for tag in sorted(by_tag):
        lines.append(f"### {tag}")
        for rel in sorted(by_tag[tag]):
            lines.append(f"- {_wikilink(rel)} — {all_info[rel].purpose}")
        lines.append("")

    out = config.output / "MOC.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ════════════════════════════════════════════════════════════════════════════
# BUILD PIPELINE (orchestrates phases 1-6)
# ════════════════════════════════════════════════════════════════════════════


class UI:
    """Thin console wrapper. Uses rich (banner / progress bar / summary panel)
    when available, and degrades to plain prints when it is not — so output is
    nice but the dependency is never required."""

    def __init__(self) -> None:
        self.console = Console() if _RICH else None

    def banner(self, rows: list[tuple[str, str]]) -> None:
        if _RICH:
            body = "\n".join(f"[dim]{k:<11}[/dim] {v}" for k, v in rows)
            self.console.print(Panel.fit(
                f"[bold cyan]codemap[/bold cyan]\n{body}", border_style="cyan"))
        else:
            print("codemap")
            for k, v in rows:
                print(f"  {k:<11} {v}")

    def info(self, msg: str) -> None:
        self.console.print(f"[dim]{msg}[/dim]") if _RICH else print(f"  {msg}")

    @contextmanager
    def track(self, description: str, total: int) -> Iterator[Callable[[], None]]:
        """Yield an `advance()` callable; renders a live bar under rich."""
        if _RICH and total > 0:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=self.console,
            ) as progress:
                task = progress.add_task(description, total=total)
                yield lambda: progress.advance(task)
        else:
            print(f"  {description} ({total})")
            yield lambda: None

    def summary(self, rows: list[tuple[str, str]]) -> None:
        if _RICH:
            body = "\n".join(f"[dim]{k:<13}[/dim] {v}" for k, v in rows)
            self.console.print(Panel.fit(
                f"[bold green]Done[/bold green]\n{body}", border_style="green"))
        else:
            print("Done")
            for k, v in rows:
                print(f"  {k:<13} {v}")

    def estimate(self, rows: list[tuple[str, str]]) -> None:
        if _RICH:
            body = "\n".join(f"[dim]{k:<15}[/dim] {v}" for k, v in rows)
            self.console.print(Panel.fit(
                f"[bold yellow]Dry run — estimated generation cost[/bold yellow]\n{body}",
                border_style="yellow"))
        else:
            print("Dry run — estimated generation cost")
            for k, v in rows:
                print(f"  {k:<15} {v}")


def git_staged(config: Config) -> Optional[set[str]]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=config.root, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    exts = config.active_extensions()
    return {rel for rel in result.stdout.splitlines() if Path(rel).suffix in exts}


def estimate_generation(
    all_info: dict[str, FileInfo], to_resolve: set[str], manifest: Manifest, config: Config
) -> dict[str, int]:
    """Estimate the LLM cost a real run would incur: counts only files that would
    actually hit the model (in scope, no doc comment, not already cached) and
    sums snippet-sized input + one-sentence output tokens."""
    calls = in_tok = out_tok = free = 0
    for rel, info in all_info.items():
        if rel not in to_resolve:
            free += 1
            continue
        if info.file_doc or manifest.cached_purpose(rel, file_hash(config.root / rel)) is not None:
            free += 1
            continue
        calls += 1
        snippet_chars = min(2000, len(info.source_snippet))
        in_tok += snippet_chars // CHARS_PER_TOKEN + PROMPT_OVERHEAD_TOKENS
        out_tok += PURPOSE_OUTPUT_TOKENS
    return {"calls": calls, "free": free, "in_tok": in_tok, "out_tok": out_tok}


def build(config: Config) -> None:
    ui = UI()
    model = select_project_model(config)
    files = collect_files(config, model)
    if not files:
        print("No eligible source files found.", file=sys.stderr)
        sys.exit(0)

    provider_label = f"{config.provider}:{config.model}" if config.use_llm else "disabled (--no-llm)"
    ui.banner([
        ("Repo", str(config.root)),
        ("Output", str(config.output)),
        ("Languages", ", ".join(config.langs)),
        ("Scan", type(model).__name__),
        ("Purpose", provider_label),
        ("Files", str(len(files))),
    ])

    # Phase 2: extract structure for EVERY file (needed for a complete used-by graph).
    all_info: dict[str, FileInfo] = {}
    with ui.track("Extracting structure", len(files)) as advance:
        for path in files:
            spec = config.spec_for(path)
            if spec is not None:
                info = extract_file_info(path, config, spec)
                all_info[info.rel_path] = info
            advance()

    # Phase 3: cross-file graph.
    resolve_graph(all_info, config)
    edges = sum(len(i.used_by) for i in all_info.values())
    ui.info(f"Resolved {edges} used-by edge(s)")

    # Phase 4: purpose resolution (LLM only where needed, and only for changed set).
    manifest = Manifest(config)
    manifest.load()
    provider = make_provider(config)

    to_resolve: set[str] = set(all_info)
    if config.changed_only:
        staged = git_staged(config)
        if staged is not None:
            to_resolve = staged & set(all_info)
            ui.info(f"--changed: {len(to_resolve)} staged file(s) eligible for the model")

    if config.dry_run:
        est = estimate_generation(all_info, to_resolve, manifest, config)
        total = est["in_tok"] + est["out_tok"]
        rows = [
            ("Files scanned", str(len(all_info))),
            ("Would call LLM", str(est["calls"])),
            ("Free (doc/cache)", str(est["free"])),
            ("Est input tok", f"{est['in_tok']:,}"),
            ("Est output tok", f"{est['out_tok']:,}"),
            ("Est total tok", f"{total:,}"),
        ]
        if config.price_in is not None and config.price_out is not None:
            cost = est["in_tok"] / 1e6 * config.price_in + est["out_tok"] / 1e6 * config.price_out
            rows.append(("Est cost (USD)", f"${cost:.4f}"))
        rows.append(("Note", "estimate only — no files written, no LLM called"))
        ui.estimate(rows)
        return

    llm_calls = 0
    with ui.track("Resolving purposes", len(all_info)) as advance:
        for rel, info in all_info.items():
            path = config.root / rel
            h = file_hash(path)
            if rel in to_resolve:
                had_doc = bool(info.file_doc) or manifest.cached_purpose(rel, h) is not None
                info.purpose = resolve_purpose(info, path, h, manifest, provider)
                if provider is not None and not had_doc:
                    llm_calls += 1
            else:
                info.purpose = info.file_doc.rstrip(".") if info.file_doc else (
                    manifest.cached_purpose(rel, h) or "(undocumented)"
                )
            info.tags = infer_tags(info, config.tag_signals_for(config.spec_for(path)))
            advance()

    manifest.save(set(all_info))

    # Phase 5: write outputs.
    config.output.mkdir(parents=True, exist_ok=True)
    write_index(all_info, config)
    docs = write_file_docs(all_info, config)
    write_moc(all_info, config)
    stale = cleanup_stale_docs(all_info, config)

    documented = sum(1 for i in all_info.values() if not i.purpose.startswith("(undocumented"))
    ui.summary([
        ("Files", str(len(all_info))),
        ("Documented", f"{documented}/{len(all_info)}"),
        ("Used-by edges", str(edges)),
        ("Model calls", str(llm_calls) if config.use_llm else "—"),
        ("Doc files", str(docs)),
        ("Stale removed", str(stale)),
        ("Output dir", str(config.output)),
    ])


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════


def _parse_tag_overrides(values: list[str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """--tag-signal lang:#tag=sub1,sub2  (repeatable)."""
    out: dict[str, dict[str, tuple[str, ...]]] = defaultdict(dict)
    for v in values:
        try:
            lang, rest = v.split(":", 1)
            tag, subs = rest.split("=", 1)
            out[lang][tag] = tuple(s.strip() for s in subs.split(",") if s.strip())
        except ValueError:
            sys.exit(f"Error: bad --tag-signal '{v}'. Expected lang:#tag=sub1,sub2")
    return dict(out)


def parse_args(argv: list[str]) -> Config:
    p = argparse.ArgumentParser(
        description="Generic codebase map generator (tree-sitter + optional LLM purpose).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("root", nargs="?", default=".", help="Repo root (default: .)")
    p.add_argument("--lang", action="append", default=[],
                   help="Language(s): python,kotlin,java,c. Comma-separated or repeatable. Default: python")
    p.add_argument("--output", "-o", default="codemap", help="Output directory (default: codemap)")
    p.add_argument("--scan", choices=["auto", "walk", "gradle"], default="auto",
                   help="Source-root discovery (default: auto)")
    p.add_argument("--no-llm", action="store_true", help="Skip the LLM; structure + docstrings only")
    p.add_argument("--changed", action="store_true", help="Resolve purpose only for git-staged files")
    p.add_argument("--force", action="store_true", help="Ignore the manifest cache; rebuild all")
    p.add_argument("--dry-run", action="store_true",
                   help="Estimate generation tokens/cost without calling the LLM or writing files")
    p.add_argument("--price-in", type=float, default=None,
                   help="USD per 1M input tokens (for --dry-run cost)")
    p.add_argument("--price-out", type=float, default=None,
                   help="USD per 1M output tokens (for --dry-run cost)")
    p.add_argument("--provider", choices=list(PROVIDER_DEFAULTS), default="ollama",
                   help="Purpose model backend (default: ollama)")
    p.add_argument("--base-url", default=None, help="Override the provider endpoint URL")
    p.add_argument("--model", default=None, help="Model id (provider default if omitted)")
    p.add_argument("--api-key", default=None,
                   help="API key (or set OPENROUTER_API_KEY / OPENAI_API_KEY)")
    p.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES_DEFAULT)
    p.add_argument("--ignore-dir", action="append", default=[], help="Extra directory to ignore (repeatable)")
    p.add_argument("--internal-prefix", action="append", default=[],
                   help="Display hint for external-dep grouping (repeatable)")
    p.add_argument("--tag-signal", action="append", default=[],
                   help="Override a tag: lang:#tag=sub1,sub2 (repeatable)")
    args = p.parse_args(argv[1:])

    langs: list[str] = []
    for chunk in (args.lang or ["python"]):
        for part in chunk.split(","):
            part = part.strip().lower()
            if part and part not in langs:
                langs.append(part)
    unknown = [l for l in langs if l not in LANGUAGES]
    if unknown:
        sys.exit(f"Error: unknown language(s): {', '.join(unknown)}. Known: {', '.join(LANGUAGES)}")

    root = Path(args.root).resolve()
    if not root.exists():
        sys.exit(f"Error: root does not exist: {root}")

    # Resolve provider endpoint / model / key (flag > env > provider default).
    use_llm = not args.no_llm
    defaults = PROVIDER_DEFAULTS[args.provider]
    base_url = args.base_url or defaults["base_url"]
    model = args.model or defaults["model"]
    api_key = args.api_key or (os.environ.get(defaults["env"]) if defaults["env"] else None)
    if use_llm and not args.dry_run and args.provider != "ollama" and not api_key:
        env = defaults["env"]
        sys.exit(
            f"Error: --provider {args.provider} needs an API key.\n"
            f"  export {env}=...   (or pass --api-key), or use --no-llm."
        )

    return Config(
        root=root,
        output=Path(args.output) if Path(args.output).is_absolute() else root / args.output,
        langs=langs,
        scan=args.scan,
        use_llm=use_llm,
        changed_only=args.changed,
        force=args.force,
        dry_run=args.dry_run,
        price_in=args.price_in,
        price_out=args.price_out,
        provider=args.provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_file_bytes=args.max_file_bytes,
        ignore_dirs=DEFAULT_IGNORE_DIRS | set(args.ignore_dir),
        internal_prefixes=tuple(args.internal_prefix),
        tag_overrides=_parse_tag_overrides(args.tag_signal),
    )


def main() -> None:
    try:
        build(parse_args(sys.argv))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
