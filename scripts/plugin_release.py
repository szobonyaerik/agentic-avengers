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
green, per issue #69's standing rule. The comparison reads the source repository's COMMITTED `HEAD`
(not its working tree) whenever it is a git checkout, and surfaces an uncommitted change under the
shipped payload as a separate `dirty` signal — a dirty tree and a stale release are two different
conditions, and conflating them would wedge anyone developing this repo, which guarantees the guard
gets bypassed.

**`cut()`** is the release step this issue asks to reduce to one documented command: copy the source
repository's shipped payload into `<cache_root>/<version>/`, then — when `pin_path` is given — point
the install registry (`~/.claude/plugins/installed_plugins.json`) at it, so the release is what a
harness actually loads next, not just files sitting in a cache directory nobody points at. It
refuses to overwrite a version whose content already differs (a version is released once), refuses
to release a payload missing one of its own expected `PLUGIN_PATHS` entries (silent breakage
nobody notices until a downstream user does), and refuses to report success when the registry does
not, on read-back, actually resolve to the version just released.

Neither `check()` nor `cut()` ever writes to the real cache or the real install registry by
default: `cut()` takes `cache_root` and `pin_path` as explicit arguments with no implicit fallback
to the live paths, so a caller has to name where it is writing. The `cut` **subcommand** is the one
deliberate exception — an operator running the release remedy with no flags means the live cache and
the live registry, so `main()` falls back to `AVENGER_PLUGIN_CACHE_ROOT`/`DEFAULT_CACHE_ROOT` and
`AVENGER_PLUGIN_PIN_PATH`/`DEFAULT_PIN_PATH` there, and only there (`--no-pin` skips the registry
update). `check` writes nowhere in either form.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess  # noqa: S404 — fixed argv, no shell, used only to read git's own answers
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: The plugin's shipped payload — mirrors CLAUDE.md §7 "canonical-source driven": edit these, and
#: `.claude-plugin/plugin.json` for the version and identity. `docs/templates` ships too (see
#: commands/pipeline-init.md and install.sh's own SRC_SETS) — the rest of docs/ is this repo's own
#: documentation of itself, not runtime payload. Nothing outside this set is part of what a phase
#: executes (tests/, examples/ are the pipeline's own development, not payload).
PLUGIN_PATHS: tuple[str, ...] = (
    "agents", "skills", "commands", "prompts", "scripts", "hooks", ".claude-plugin",
    "docs/templates",
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
PIN_PATH_ENV = "AVENGER_PLUGIN_PIN_PATH"

#: Where a real Claude Code install caches this plugin, and where it pins which cached version each
#: registered installation actually resolves to. Read-only reference for callers deciding a default;
#: nothing in this module writes to either on its own — `cut()` requires both paths explicitly, and
#: `main` only falls back to these constants for the `cut` subcommand, never for `check`.
DEFAULT_CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache" / "erik-tools" / "plan-build-verify"
DEFAULT_PIN_PATH = Path.home() / ".claude" / "plugins" / "installed_plugins.json"


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


#: A git call that never answers is the same fact as a git that is not installed: no answer. Both
#: are `SubprocessError`/`OSError`, and the difference is invisible to every caller here, which
#: degrades to hashing the working tree. Letting a `TimeoutExpired` escape instead would surface as
#: an uncaught traceback and exit 1 — the code `main`'s `check` reserves for a confirmed STALE, whose
#: prescribed remedy (cut a release, restart) cannot repair a stalled git.
_GIT_UNAVAILABLE = (OSError, subprocess.SubprocessError)


def _git(root: Path, *args: str) -> str | None:
    """One git command's stdout as text, or None when it has none — no repo, no git, a failed
    command, or a call that timed out."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10, check=False,
        )
    except _GIT_UNAVAILABLE:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _git_batch_blobs(root: Path, shas: list[str]) -> list[bytes] | None:
    """The raw bytes of every blob in `shas`, in that order, read in ONE `git cat-file --batch`.

    One process for the whole payload, not one per file: this runs on the phase-open path inside
    `hook_spec_gate.sh`, where wall clock is bounded by `gate_timeouts.py`'s
    `metrics_processes x AVENGER_METRICS_TIMEOUT` model — a per-file loop spends `len(shas)` times
    the git timeout, which that bound cannot see.

    `--batch` frames each object as `<oid> <type> <size>\\n<content>\\n`, or `<input> missing\\n`
    for one it cannot resolve. None on any failure to run git, any missing object, or any framing
    this cannot parse exactly, so a partial read can never masquerade as content.
    """
    if not shas:
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input=("\n".join(shas) + "\n").encode("utf-8"),
            capture_output=True, timeout=10, check=False,
        )
    except _GIT_UNAVAILABLE:
        return None
    if proc.returncode != 0:
        return None

    out = proc.stdout
    blobs: list[bytes] = []
    pos = 0
    for _ in shas:
        eol = out.find(b"\n", pos)
        if eol < 0:
            return None
        header = out[pos:eol].split(b" ")
        if len(header) != 3 or header[1] != b"blob":
            return None  # "missing"/"ambiguous", or a type that is not a file's content
        try:
            size = int(header[2])
        except ValueError:
            return None
        start = eol + 1
        end = start + size
        if end + 1 > len(out) or out[end:end + 1] != b"\n":
            return None
        blobs.append(out[start:end])
        pos = end + 1
    return blobs if pos == len(out) else None


def _head_content_hash(repo: Path, paths: tuple[str, ...] = PLUGIN_PATHS) -> str | None:
    """A sha256 over the shipped payload as last COMMITTED, computed the SAME WAY `content_hash`
    hashes a working tree — (relative path, file bytes) pairs, sorted by path — so the two are
    directly comparable. The bytes come from git's object store, never the working tree, so an
    uncommitted edit can never move this number. Hashing `git ls-tree`'s blob shas instead would be
    cheaper still, but they live in a different hash space than `content_hash`'s file-byte digest and
    would never equal it even for identical content — comparability, not cost, is what matters here.

    `ls-tree` runs WITHOUT `--full-tree` deliberately: `--full-tree` resolves both the pathspec and
    the emitted paths against the repository root, so a `repo` nested below the git top level (a
    plugin checkout inside an outer repository) matched NOTHING and hashed the empty set — a hard,
    unclearable STALE. Plain `ls-tree` under `-C repo` resolves both against `repo` itself, which is
    also the frame `content_hash` hashes in. `-z` is what stops git C-quoting a path containing a
    space or a non-ASCII byte, which would otherwise name a file that cannot be read back.

    None when `repo` is not a git work tree, has no commits yet, a command fails, or the listing
    identifies no file at all, so callers fall back to hashing the working tree directly (the only
    notion of "content" a non-git directory has). Never a hash of nothing: an empty digest compares
    unequal to every real tree, which is a false STALE no release can clear.
    """
    listing = _git(repo, "ls-tree", "-r", "-z", "HEAD", "--", *paths)
    if listing is None:
        return None
    tracked: list[tuple[str, str]] = []
    for record in listing.split("\0"):
        if not record.strip():
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[1] == "blob":
            # a submodule (mode 160000, type commit) is not a file to hash
            tracked.append((path, fields[2]))
    if not tracked:
        return None

    tracked.sort()
    blobs = _git_batch_blobs(repo, [sha for _, sha in tracked])
    if blobs is None or len(blobs) != len(tracked):
        return None

    digest = hashlib.sha256()
    for (path, _), content in zip(tracked, blobs):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _working_tree_dirty(repo: Path, paths: tuple[str, ...] = PLUGIN_PATHS) -> bool:
    """Whether `repo` (a git work tree) has any uncommitted change — modified or untracked — under
    the shipped payload. False for a repo `_git` cannot read (no git, not a repo): dirty is a signal
    ABOUT a git comparison, not a fact a non-git directory can carry."""
    output = _git(repo, "status", "--porcelain", "--", *paths)
    return bool(output and output.strip())


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
    """What `check()` found. `status` is one of "fresh", "stale", "unknown".

    `dirty` is orthogonal to `status`, never a fourth status value: a dirty working tree and a stale
    release are two different conditions (issue #65's round-2 fix) — a dirty tree next to content
    that still matches HEAD is `status="fresh", dirty=True`, never `status="stale"`. Only a genuine
    STALE (committed HEAD content differs from the executing copy) may ever fail `main()`'s `check`.
    """

    status: str
    executing_version: str | None
    source_version: str | None
    executing_root: Path
    source_root: Path | None
    detail: str
    dirty: bool = False


def check(executing: Path | None = None, source: Path | None = None) -> DriftResult:
    """Compare the executing copy against the source repository's COMMITTED history.

    `executing`/`source` are overrides for testing; a real run passes neither and gets the harness's
    own `$CLAUDE_PLUGIN_ROOT` and this machine's `$AVENGER_SOURCE_REPO`/project-is-the-repo answer.

    "unknown" is not a failure — it is the documented state of a machine with no source repository
    configured, exactly like `metrics_sink.enabled()` reports no writer configured. It is never
    silent, and it is never enforced: `main()` exits 0 for it, same as "fresh".

    When the source repository is a git work tree, the comparison reads its COMMITTED `HEAD`
    content, never the live working tree: hashing the working tree directly made any in-progress
    uncommitted edit under the shipped payload read as STALE, including during a session whose whole
    purpose is to make that edit — the guard would wedge anyone developing this repo, which
    guarantees it gets bypassed. A non-git source root (every test fixture in
    tests/test_plugin_release.py, and any plain checkout) has no `HEAD` to compare, so it falls back
    to hashing the working tree exactly as before.
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
    exe_hash = content_hash(exe_root)
    head_hash = _head_content_hash(src_root)
    dirty = head_hash is not None and _working_tree_dirty(src_root)
    src_hash = head_hash if head_hash is not None else content_hash(src_root)
    dirty_note = (
        " (the source repo also has uncommitted changes under the shipped payload — not "
        "reflected in this comparison, which reads its committed HEAD)"
        if dirty else ""
    )
    if exe_hash is None or src_hash is None:
        return DriftResult(
            "unknown", exe_version, src_version, exe_root, src_root,
            f"could not hash one side (executing readable={exe_hash is not None}, "
            f"source readable={src_hash is not None}).",
            dirty=dirty,
        )
    if exe_hash == src_hash:
        return DriftResult(
            "fresh", exe_version, src_version, exe_root, src_root,
            f"executing copy (version {exe_version}) matches the source repository's committed "
            f"content.{dirty_note}",
            dirty=dirty,
        )
    return DriftResult(
        "stale", exe_version, src_version, exe_root, src_root,
        f"executing copy is version {exe_version} at {exe_root}, but the source repository at "
        f"{src_root} (version {src_version}) has different COMMITTED content. Merged fixes are NOT "
        f"in effect for this run. Release: `python3 scripts/plugin_release.py cut` from the source "
        f"checkout, then restart Claude Code so the harness re-reads the cache.{dirty_note}",
        dirty=dirty,
    )


def _missing_shipped_paths(repo: Path) -> list[str]:
    """`PLUGIN_PATHS` entries that do not exist in `repo` at all.

    `_shipped_files` silently skips a missing path — the right behaviour for `content_hash`/`check`,
    which also compare deliberately partial test fixtures — but a REAL release missing one of its
    own shipped directories is silent breakage, not a partial comparison: `cut()` claims to produce
    a complete release, so it is the one place this repository is loud about instead.
    """
    return [rel for rel in PLUGIN_PATHS if not (repo / rel).exists()]


def _plugin_registry_key(cache_root: Path) -> str:
    """The install-registry key for a plugin released to `cache_root`: `<name>@<marketplace>`.

    Read off the same cache-path convention this module already assumes elsewhere
    (`.../cache/<marketplace>/<plugin-name>/<version>/`, see `DEFAULT_CACHE_ROOT`) rather than a
    second, independent source — `.claude-plugin/plugin.json` names the plugin but not the
    marketplace it was installed from, so there is nowhere else to read this from.
    """
    cache_root = Path(cache_root)
    return f"{cache_root.name}@{cache_root.parent.name}"


def _git_head_sha(repo: Path) -> str | None:
    """The commit `repo` is releasing, for the registry's `gitCommitSha` — best-effort, never
    fabricated: a non-git `repo` (or one with no commits) leaves the pin's existing value alone."""
    output = _git(repo, "rev-parse", "HEAD")
    sha = (output or "").strip()
    return sha or None


def update_pin(pin_path: Path, cache_root: Path, target: Path, version: str, repo: Path) -> int:
    """Point every registered installation of this plugin at the just-released version.

    A `cut` that copies files into a new cache directory but leaves the install registry pinning the
    old version has not released anything a harness will ever load — `$CLAUDE_PLUGIN_ROOT` keeps
    resolving to what the registry says, not to what is newest on disk. This closes that: it updates
    `installPath`/`version`/`lastUpdated`/`gitCommitSha` on every entry (any scope) whose
    `installPath` currently sits under `cache_root` — i.e. any earlier version of THIS plugin — and
    leaves every other plugin's entries untouched, byte for byte. Written atomically (temp file +
    rename) so a crash mid-write cannot leave the registry half-written.

    Real schema (`~/.claude/plugins/installed_plugins.json`):
        {"version": 2, "plugins": {"<name>@<marketplace>": [
            {"scope": "user"|"project"|"local", "projectPath"?: str, "installPath": str,
             "version": str, "installedAt": str, "lastUpdated": str, "gitCommitSha": str}, ...]}}

    Raises rather than silently doing nothing when there is nothing to point at the release — a
    registry with no entry for this plugin, or with no entry currently pointing under `cache_root` —
    because a no-op here is indistinguishable from a successful pin from the outside.

    THE CLOSING CHECK: after writing, re-reads `pin_path` fresh off disk (never the in-memory
    object, so a write that silently failed to take cannot be missed) and confirms every entry this
    call claims to have updated both persisted and resolves `plugin_version()` back to `version` —
    proof that a harness reading the pin now would actually execute the release, not merely that
    some bytes were written somewhere. Raises `RuntimeError` if that proof fails; `cut()` lets it
    propagate, so a `cut` cannot report success while the pin does not actually resolve to it.
    """
    pin_path = Path(pin_path)
    try:
        raw = pin_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {pin_path}: {exc}") from exc
    try:
        registry = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{pin_path} is not valid JSON: {exc}") from exc
    if not isinstance(registry, dict) or not isinstance(registry.get("plugins"), dict):
        raise ValueError(f"{pin_path} is not an installed-plugins registry (no 'plugins' object)")

    key = _plugin_registry_key(cache_root)
    entries = registry["plugins"].get(key)
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            f"{pin_path} has no entries under {key!r} — nothing registers this plugin's cache at "
            f"{cache_root}, so there is nothing to point at the new release. Install the plugin "
            "once through Claude Code first, or pass --no-pin to release without updating a pin."
        )

    cache_root_resolved = Path(cache_root).resolve()
    sha = _git_head_sha(repo)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
    stamp += f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"

    updated = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            under = Path(str(entry.get("installPath") or "")).resolve().is_relative_to(
                cache_root_resolved
            )
        except (OSError, ValueError):
            under = False
        if not under:
            continue
        entry["installPath"] = str(target)
        entry["version"] = version
        entry["lastUpdated"] = stamp
        if sha:
            entry["gitCommitSha"] = sha
        updated += 1

    if updated == 0:
        raise ValueError(
            f"{pin_path}: no entry under {key!r} currently points under {cache_root} — nothing to "
            "re-pin."
        )

    tmp = pin_path.with_name(pin_path.name + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    tmp.replace(pin_path)

    reread = json.loads(pin_path.read_text(encoding="utf-8"))
    confirmed = [
        e for e in (reread.get("plugins", {}).get(key) or [])
        if isinstance(e, dict) and e.get("installPath") == str(target)
    ]
    if len(confirmed) != updated:
        raise RuntimeError(
            f"{pin_path}: wrote {updated} updated entr{'y' if updated == 1 else 'ies'} but only "
            f"{len(confirmed)} read back pointing at {target} — the pin update did not take."
        )
    for entry in confirmed:
        resolved_version = plugin_version(Path(entry["installPath"]))
        if resolved_version != version:
            raise RuntimeError(
                f"{pin_path} now points at {entry['installPath']}, but plugin_version() there "
                f"reads {resolved_version!r}, not {version!r} — the pin does not actually resolve "
                "to the release. Refusing to report success."
            )
    return updated


def _pin_after_copy(pin_path: Path, cache_root: Path, target: Path, version: str, repo: Path) -> int:
    """`update_pin`, with a write failure re-raised as one that SAYS the payload already landed.

    The registry update is the last step of `cut`, so an `OSError` here (a root-owned or read-only
    registry) happens after the payload is fully in place. Reported as a bare write error it reads
    as "the release failed", sending an operator to re-run a copy that already succeeded; what is
    actually owed is the pin alone.
    """
    try:
        return update_pin(pin_path, cache_root, target, version, repo)
    except OSError as exc:
        raise RuntimeError(
            f"the payload IS released to {target}, but writing the install registry {pin_path} "
            f"failed: {exc}. Nothing points at the release yet — fix the registry's permissions and "
            f"re-run `cut` (the copy step is a no-op on unchanged content), or pass --no-pin and "
            "re-point it by hand."
        ) from exc


def cut(
    repo: Path, cache_root: Path, version: str | None = None, pin_path: Path | None = None
) -> Path:
    """The one release step: copy `repo`'s shipped payload into `<cache_root>/<version>/`.

    Refuses to overwrite a version directory whose content differs from what is being released — a
    version is released once, so a forgotten version bump fails loudly instead of silently
    clobbering the previous release under its own number. Re-running with unchanged content is a
    no-op and returns the existing target, so this is safe to re-run after a partial failure.

    Refuses to release a payload missing one of its own expected `PLUGIN_PATHS` entries — an
    incomplete release is silent breakage nobody notices until a downstream user does.

    `pin_path`, when given, also updates the install registry (`update_pin`) so a released version
    is what `$CLAUDE_PLUGIN_ROOT` actually resolves to next run — not just files sitting in a cache
    directory nobody points at. `None` (the default) skips it entirely, preserving pure-copy
    behaviour for any caller that has not opted in.
    """
    repo = Path(repo)
    version = version or plugin_version(repo)
    if not version:
        raise ValueError(f"{repo} has no version in .claude-plugin/plugin.json — nothing to cut")
    missing = _missing_shipped_paths(repo)
    if missing:
        raise ValueError(
            f"{repo} is missing expected shipped path(s): {', '.join(missing)} — cut() refuses to "
            "release an incomplete payload silently. Restore the missing path, or remove it from "
            "PLUGIN_PATHS if it is genuinely no longer shipped."
        )
    target = Path(cache_root) / version
    new_hash = content_hash(repo)
    if new_hash is None:
        raise ValueError(f"{repo} is not a directory — nothing to release")
    if target.is_dir():
        if content_hash(target) == new_hash:
            if pin_path is not None:
                _pin_after_copy(pin_path, cache_root, target, version, repo)
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

    if pin_path is not None:
        _pin_after_copy(pin_path, cache_root, target, version, repo)

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
    p_cut.add_argument("--pin-path", type=Path, default=None,
                       help="the install registry to re-point at the release (default: "
                            f"${PIN_PATH_ENV} or {DEFAULT_PIN_PATH})")
    p_cut.add_argument("--no-pin", action="store_true",
                       help="copy the payload only; do not touch the install registry")

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
        if result.dirty:
            print(
                "[plugin-release] NOTE: the source repo has uncommitted changes under the shipped "
                "payload — this is normal during development and does not affect the verdict above.",
                file=sys.stderr,
            )
        return 1 if result.status == "stale" else 0

    if args.command == "cut":
        repo = args.repo or Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
        cache_root = args.cache_root or Path(os.environ.get(CACHE_ROOT_ENV) or DEFAULT_CACHE_ROOT)
        if args.no_pin:
            pin_path = None
        elif args.pin_path is not None:
            pin_path = args.pin_path  # explicit — errors loudly below if it cannot be used
        else:
            default_pin = Path(os.environ.get(PIN_PATH_ENV) or DEFAULT_PIN_PATH)
            pin_path = default_pin if default_pin.is_file() else None
            if pin_path is None:
                print(
                    f"[plugin-release] no install registry at {default_pin} — skipping pin update",
                    file=sys.stderr,
                )
        try:
            target = cut(repo, cache_root, args.version, pin_path=pin_path)
        except (ValueError, FileExistsError, RuntimeError) as exc:
            print(f"[plugin-release] {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            # Every write past the payload copy is funnelled through `_pin_after_copy`, which
            # re-raises as RuntimeError above — so a bare OSError here is the copy itself failing,
            # and the release did NOT land. Said outright rather than left to a traceback.
            print(
                f"[plugin-release] could not write the release into {cache_root}: {exc}. The "
                f"payload was NOT released and the install registry was NOT touched.",
                file=sys.stderr,
            )
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
