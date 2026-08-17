#!/usr/bin/env python3
"""Whether the plugin copy actually executing right now is the same code as the merged repository.

Issue #65: phases run from `~/.claude/plugins/cache/erik-tools/plan-build-verify/<version>/`, a
release snapshot Claude Code caches on install — never this repository. A fix merged here is inert
for every running phase until someone remembers to cut a release and refresh that cache. The newest
cached copy measured at the time (0.10.2) still carried a defect PR 44 had already fixed and merged;
nothing anywhere said so.

Two things close that gap, deliberately kept in one module because they share the same notion of
"the plugin's shipped payload":

**`check()`** answers "does the copy executing right now match the merged repository" from the copy
that is actually running — `executing_root()` reads `$CLAUDE_PLUGIN_ROOT`, the same variable every
hook in `hooks/hooks.json` already resolves its own path from, never a constant a stale copy would
carry unchanged. A STALE verdict is loud on purpose: wired into `/avenger-run`'s preflight (see
`commands/avenger-run.md` §1), it stops a run before phases execute against code a fix already
replaced — proof is `tests/test_plugin_release.py`, which breaks it and confirms red before proving
green, per issue #69's standing rule.

**`cut()`** is the release step this issue asks to reduce to one documented command: copy the source
repository's shipped payload into `<cache_root>/<version>/`. It refuses to overwrite a version whose
content already differs — a version is released once — so a forgotten version bump fails loudly
instead of silently overwriting one release with another.

Neither function ever writes to the real cache by default: `cut()` takes `cache_root` as a required
argument with no implicit fallback to the live path, so a caller has to name where it is writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: The plugin's shipped payload — mirrors CLAUDE.md §7 "canonical-source driven": edit these, and
#: `.claude-plugin/plugin.json` for the version and identity. Nothing outside this set is part of
#: what a phase executes (docs/, tests/, examples/ are the pipeline's own development, not payload).
PLUGIN_PATHS: tuple[str, ...] = (
    "agents", "skills", "commands", "prompts", "scripts", "hooks", ".claude-plugin",
)

#: Never part of the hash: a compiled artifact of the very files being hashed would make an
#: unmodified tree read as changed depending on whether Python had run in it yet.
IGNORED_DIR_NAMES = frozenset({"__pycache__"})
IGNORED_SUFFIXES = frozenset({".pyc"})

#: Per-machine configuration, the same shape `AVENGER_METRICS_CMD` and `SUBPROC_CHECK_PATHS` already
#: use elsewhere in this repo: an optional override this check is silently OFF without, reported once
#: rather than guessed at.
SOURCE_ROOT_ENV = "AVENGER_SOURCE_REPO"
CACHE_ROOT_ENV = "AVENGER_PLUGIN_CACHE_ROOT"

#: Where a real Claude Code install caches this plugin. Read-only reference for callers deciding a
#: default; nothing in this module writes here on its own — `cut()` requires `cache_root` and `main`
#: only falls back to this constant for the `cut` subcommand, never for `check`.
DEFAULT_CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache" / "erik-tools" / "plan-build-verify"


def executing_root() -> Path:
    """Root of the plugin copy actually running this code.

    `$CLAUDE_PLUGIN_ROOT` is what every hook in `hooks/hooks.json` already resolves its own script
    path from (`hook_skills.sh`, `hook_ponytail.sh`), so this reads the same value the harness set
    for this exact run rather than a second, independent guess. Outside a harness-run process (a
    developer running this file directly, or a test), it falls back to its own checkout — the only
    thing "executing" can mean there.
    """
    declared = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(declared) if declared else Path(__file__).resolve().parent.parent


def plugin_manifest(root: Path) -> dict | None:
    """The parsed `.claude-plugin/plugin.json` at `root`, or None when it is missing or unreadable."""
    try:
        data = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def plugin_version(root: Path) -> str | None:
    """The `version` field this copy's own manifest declares."""
    version = (plugin_manifest(root) or {}).get("version")
    return version if isinstance(version, str) and version.strip() else None


def _shipped_files(root: Path) -> list[Path]:
    """Every file under `PLUGIN_PATHS`, sorted, for a hash order that does not depend on `os.walk`."""
    found: list[Path] = []
    for rel in PLUGIN_PATHS:
        base = root / rel
        if base.is_file():
            found.append(base)
            continue
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in IGNORED_SUFFIXES:
                continue
            if IGNORED_DIR_NAMES & set(path.relative_to(root).parts):
                continue
            found.append(path)
    return sorted(found)


def content_hash(root: Path) -> str | None:
    """A sha256 over every shipped file's relative path and bytes, or None when `root` isn't a dir.

    A version STRING comparison alone misses a release cut under an unbumped version number — this
    is what tells "the same code" from "different code" regardless of what plugin.json says.
    """
    root = Path(root)
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in _shipped_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def source_root() -> Path | None:
    """Where the merged repository this plugin ships from lives on this machine, if findable.

    `AVENGER_SOURCE_REPO` is the explicit, per-machine override — the same shape as
    `AVENGER_METRICS_CMD` elsewhere in this repo: this check is silently OFF without it, and says so
    once rather than guessing a path. The one case resolved without it: the project actively being
    worked on *is* this plugin's own repository (developing plan-build-verify against itself), which
    is exactly the situation this fix was written under.
    """
    declared = os.environ.get(SOURCE_ROOT_ENV)
    if declared:
        return Path(declared)
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project and (Path(project) / ".claude-plugin" / "plugin.json").is_file():
        return Path(project)
    return None


@dataclass(frozen=True)
class DriftResult:
    """What `check()` found. `status` is one of "fresh", "stale", "unknown"."""

    status: str
    executing_version: str | None
    source_version: str | None
    executing_root: Path
    source_root: Path | None
    detail: str


def check(executing: Path | None = None, source: Path | None = None) -> DriftResult:
    """Compare the executing copy against the source repository.

    `executing`/`source` are overrides for testing; a real run passes neither and gets the harness's
    own `$CLAUDE_PLUGIN_ROOT` and this machine's `$AVENGER_SOURCE_REPO`/project-is-the-repo answer.

    "unknown" is not a failure — it is the documented state of a machine with no source repository
    configured, exactly like `metrics_sink.enabled()` reports no writer configured. It is never
    silent, and it is never enforced: `main()` exits 0 for it, same as "fresh".
    """
    exe_root = Path(executing) if executing is not None else executing_root()
    exe_version = plugin_version(exe_root)
    src_root = Path(source) if source is not None else source_root()

    if src_root is None:
        return DriftResult(
            "unknown", exe_version, None, exe_root, None,
            f"no source repository resolvable — set {SOURCE_ROOT_ENV} to your checkout of this "
            "plugin's own repository to enable drift detection.",
        )

    if exe_root.resolve() == src_root.resolve():
        return DriftResult(
            "fresh", exe_version, exe_version, exe_root, src_root,
            "executing directly from the source repository — nothing cached to drift.",
        )

    src_version = plugin_version(src_root)
    exe_hash, src_hash = content_hash(exe_root), content_hash(src_root)
    if exe_hash is None or src_hash is None:
        return DriftResult(
            "unknown", exe_version, src_version, exe_root, src_root,
            f"could not hash one side (executing readable={exe_hash is not None}, "
            f"source readable={src_hash is not None}).",
        )
    if exe_hash == src_hash:
        return DriftResult(
            "fresh", exe_version, src_version, exe_root, src_root,
            f"executing copy (version {exe_version}) matches the source repository's content.",
        )
    return DriftResult(
        "stale", exe_version, src_version, exe_root, src_root,
        f"executing copy is version {exe_version} at {exe_root}, but the source repository at "
        f"{src_root} (version {src_version}) has different content. Merged fixes are NOT in effect "
        f"for this run. Release: `python3 scripts/plugin_release.py cut` from the source checkout, "
        "then restart Claude Code so the harness re-reads the cache.",
    )


def cut(repo: Path, cache_root: Path, version: str | None = None) -> Path:
    """The one release step: copy `repo`'s shipped payload into `<cache_root>/<version>/`.

    Refuses to overwrite a version directory whose content differs from what is being released — a
    version is released once, so a forgotten version bump fails loudly instead of silently
    clobbering the previous release under its own number. Re-running with unchanged content is a
    no-op and returns the existing target, so this is safe to re-run after a partial failure.
    """
    repo = Path(repo)
    version = version or plugin_version(repo)
    if not version:
        raise ValueError(f"{repo} has no version in .claude-plugin/plugin.json — nothing to cut")
    target = Path(cache_root) / version
    new_hash = content_hash(repo)
    if new_hash is None:
        raise ValueError(f"{repo} is not a directory — nothing to release")
    if target.is_dir():
        if content_hash(target) == new_hash:
            return target
        raise FileExistsError(
            f"{target} already holds different content for version {version} — a version is "
            "released once. Bump the version in .claude-plugin/plugin.json before cutting again."
        )

    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    for rel in PLUGIN_PATHS:
        src = repo / rel
        if not src.exists():
            continue
        dst = tmp / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*IGNORED_DIR_NAMES, "*.pyc"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    tmp.replace(target)
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="is the executing copy stale against the source repo?")
    p_check.add_argument("--executing", type=Path, default=None)
    p_check.add_argument("--source", type=Path, default=None)

    p_cut = sub.add_parser("cut", help="the one release step: copy the repo into the plugin cache")
    p_cut.add_argument("--repo", type=Path, default=None)
    p_cut.add_argument("--cache-root", type=Path, default=None)
    p_cut.add_argument("--version", default=None)

    sub.add_parser("version", help="print the executing copy's version and root")
    return parser


def main(argv: list[str] | None = None) -> int:
    """`check` exits 1 only on a confirmed STALE — a genuine, actionable finding, never on
    "unknown" (unconfigured or unreadable), which is reported and left unenforced, the same
    applicability boundary every other check in this repo already draws."""
    args = _build_parser().parse_args(argv)

    if args.command == "check":
        result = check(args.executing, args.source)
        print(
            f"[plugin-release] executing version={result.executing_version} root={result.executing_root}",
            file=sys.stderr,
        )
        print(f"[plugin-release] {result.status.upper()}: {result.detail}", file=sys.stderr)
        return 1 if result.status == "stale" else 0

    if args.command == "cut":
        repo = args.repo or Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
        cache_root = args.cache_root or Path(os.environ.get(CACHE_ROOT_ENV) or DEFAULT_CACHE_ROOT)
        try:
            target = cut(repo, cache_root, args.version)
        except (ValueError, FileExistsError) as exc:
            print(f"[plugin-release] {exc}", file=sys.stderr)
            return 1
        print(f"[plugin-release] released to {target}")
        return 0

    if args.command == "version":
        root = executing_root()
        print(f"{plugin_version(root)}\t{root}")
        return 0

    return 2  # pragma: no cover — argparse's `required=True` already refuses an unknown command


if __name__ == "__main__":
    raise SystemExit(main())
