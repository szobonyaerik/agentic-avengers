"""Tests for issue #65: a merged fix does not reach the running pipeline because phases execute a
cached plugin release, not this repository.

Two things are asserted, matching `scripts/plugin_release.py`'s two jobs:

**The version is derived from the copy that is actually executing** (`executing_root`,
`plugin_version`), not from a constant a stale cached copy would carry unchanged just the same as a
fresh one — so the tests exercise it through `$CLAUDE_PLUGIN_ROOT`, the same variable the harness
sets for a real run.

**The drift guard goes red before it goes green** (issue #69's standing rule): every "detects drift"
test first builds a source and an executing tree that genuinely differ and asserts `stale` /
exit 1, then narrows them to identical content and asserts `fresh` / exit 0 — proof the check can
fail, not just proof it can pass.

Nothing here writes to `~/.claude/plugins/cache/` — every cache root is a `tmp_path` fixture.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plugin_release as pr  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the CLI-level tests drive the real `plugin_release.py check`/`cut` subprocess the way a "
    "hook and an operator's terminal actually would, which is the whole mechanism under test"
)

CLI = [sys.executable, str(ROOT / "scripts" / "plugin_release.py")]


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([*CLI, *args], capture_output=True, text=True, check=False, env=env)  # noqa: S603


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR",
        pr.SOURCE_ROOT_ENV, pr.CACHE_ROOT_ENV, pr.PIN_PATH_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def make_plugin(root: Path, version: str = "1.0.0", *, extra_files: dict[str, str] | None = None) -> Path:
    """A complete shipped payload: one file under every `PLUGIN_PATHS` entry, plus the manifest."""
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "plan-build-verify", "version": version}), encoding="utf-8"
    )
    for rel, filename, body in (
        ("agents", "example-agent.md", "# example agent\n"),
        ("skills", "example-skill/SKILL.md", "# example skill\n"),
        ("commands", "example-command.md", "# example command\n"),
        ("prompts", "example-prompt.md", "# example prompt\n"),
        ("scripts", "verifier_evidence.py", "PARTIAL_MARKERS = ['fixed']\n"),
        ("hooks", "hooks.json", "{}\n"),
        ("docs/templates", "env.example", "# example env\n"),
    ):
        path = root / rel / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    # Sibling to docs/templates, NOT under it — not shipped payload, and a docs/tests edit must
    # never register as drift.
    (root / "docs" / "notes.md").write_text("not shipped\n", encoding="utf-8")
    for rel, body in (extra_files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _git_init(root: Path) -> Path:
    """An empty git repository with an identity, ready to commit."""
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test")
    return root


def git_repo(root: Path, version: str = "1.0.0") -> Path:
    """A git-initialized plugin fixture with one commit, for HEAD-vs-working-tree tests."""
    make_plugin(root, version=version)
    _git_init(root)
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", "initial")
    return root


def count_git_invocations(monkeypatch: pytest.MonkeyPatch, work) -> tuple[object, int]:
    """Run `work()` and report how many git subprocesses it actually spawned."""
    calls: list[list[str]] = []
    real_run = pr.subprocess.run

    def counting(argv, *args, **kwargs):
        calls.append(argv)
        return real_run(argv, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(pr.subprocess, "run", counting)
        result = work()
    return result, len(calls)


def make_registry(path: Path, plugins: dict) -> Path:
    """A fixture `installed_plugins.json`, matching the real schema this machine's copy has."""
    path.write_text(json.dumps({"version": 2, "plugins": plugins}, indent=2), encoding="utf-8")
    return path


def registry_entry(install_path: Path, version: str, *, scope: str = "user", sha: str = "x") -> dict:
    return {
        "scope": scope, "installPath": str(install_path), "version": version,
        "installedAt": "2026-01-01T00:00:00.000Z", "lastUpdated": "2026-01-01T00:00:00.000Z",
        "gitCommitSha": sha,
    }


# --- executing_root / plugin_version: derived from the running copy, not a constant ---------------


def test_executing_root_reads_claude_plugin_root(tmp_path, monkeypatch):  # noqa: F811
    _clean_env(monkeypatch)
    cache_copy = tmp_path / "cache" / "0.10.2"
    cache_copy.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(cache_copy))

    assert pr.executing_root() == cache_copy


def test_executing_root_falls_back_to_its_own_checkout_outside_a_harness_run(monkeypatch):  # noqa: F811
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

    assert pr.executing_root() == ROOT


def test_plugin_version_reads_the_manifest_of_whichever_root_is_asked(tmp_path):  # noqa: F811
    make_plugin(tmp_path, version="0.10.2")

    assert pr.plugin_version(tmp_path) == "0.10.2"


def test_plugin_version_is_none_without_a_manifest(tmp_path):  # noqa: F811
    assert pr.plugin_version(tmp_path) is None


def test_a_stale_cached_copy_and_a_fixed_repo_report_different_versions(tmp_path):  # noqa: F811
    """The exact shape issue #65 measured: 0.10.2 cached with the old code, HEAD already fixed."""
    stale_cache = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")
    fixed_repo = make_plugin(
        tmp_path / "repo", version="0.10.3",
        extra_files={"scripts/verifier_evidence.py": "PARTIAL_MARKERS = ['nothing-stale-here']\n"},
    )

    assert pr.plugin_version(stale_cache) == "0.10.2"
    assert pr.plugin_version(fixed_repo) == "0.10.3"


# --- content_hash: what actually distinguishes "same code" from "different code" ------------------


def test_content_hash_ignores_pycache_and_pyc(tmp_path):  # noqa: F811
    make_plugin(tmp_path)
    baseline = pr.content_hash(tmp_path)

    cache_dir = tmp_path / "scripts" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "verifier_evidence.cpython-311.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "scripts" / "verifier_evidence.pyc").write_bytes(b"\x00\x01")

    assert pr.content_hash(tmp_path) == baseline


def test_content_hash_changes_when_a_shipped_file_changes(tmp_path):  # noqa: F811
    make_plugin(tmp_path)
    before = pr.content_hash(tmp_path)

    (tmp_path / "scripts" / "verifier_evidence.py").write_text(
        "PARTIAL_MARKERS = ['fixed', 'also this']\n", encoding="utf-8"
    )

    assert pr.content_hash(tmp_path) != before


def test_content_hash_ignores_paths_outside_the_shipped_payload(tmp_path):  # noqa: F811
    make_plugin(tmp_path)
    before = pr.content_hash(tmp_path)

    (tmp_path / "docs" / "notes.md").write_text("edited, but not shipped\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text("def test_x(): pass\n", encoding="utf-8")

    assert pr.content_hash(tmp_path) == before


def test_content_hash_is_none_for_a_missing_root(tmp_path):  # noqa: F811
    assert pr.content_hash(tmp_path / "nowhere") is None


# --- source_root: the per-machine, explicitly-configured escape hatch ------------------------------


def test_source_root_env_override_wins(tmp_path, monkeypatch):  # noqa: F811
    _clean_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv(pr.SOURCE_ROOT_ENV, str(repo))

    assert pr.source_root() == repo


def test_source_root_is_the_project_dir_when_it_is_this_plugin(tmp_path, monkeypatch):  # noqa: F811
    _clean_env(monkeypatch)
    repo = make_plugin(tmp_path / "repo")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))

    assert pr.source_root() == repo


def test_source_root_is_none_when_unconfigured(tmp_path, monkeypatch):  # noqa: F811
    _clean_env(monkeypatch)
    other_project = tmp_path / "clickup-agents"
    other_project.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other_project))

    assert pr.source_root() is None


# --- check(): the guard, proven red before it is proven green (issue #69) --------------------------


def test_check_is_unknown_with_no_source_configured(tmp_path):  # noqa: F811
    cache_copy = make_plugin(tmp_path / "cache", version="0.10.2")

    result = pr.check(executing=cache_copy, source=None)

    assert result.status == "unknown"
    assert "AVENGER_SOURCE_REPO" in result.detail


def test_check_is_fresh_when_executing_from_the_source_repo_directly(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo")

    result = pr.check(executing=repo, source=repo)

    assert result.status == "fresh"


def test_check_detects_drift_red_then_green(tmp_path):  # noqa: F811
    """RED: a cache still carrying the pre-fix code against a repo that already merged the fix.
    GREEN: the same two trees, once the cache is brought in line with the repo. Both states are
    asserted, not just the passing one — issue #69's rule for what "proving a guard" requires."""
    repo = make_plugin(tmp_path / "repo", version="0.10.3")
    stale_cache = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")

    red = pr.check(executing=stale_cache, source=repo)
    assert red.status == "stale"
    assert red.executing_version == "0.10.2" and red.source_version == "0.10.3"
    assert "plugin_release.py cut" in red.detail

    fresh_cache = make_plugin(
        tmp_path / "cache" / "0.10.3", version="0.10.3",
        extra_files={"scripts/verifier_evidence.py": (
            (repo / "scripts" / "verifier_evidence.py").read_text(encoding="utf-8")
        )},
    )
    green = pr.check(executing=fresh_cache, source=repo)
    assert green.status == "fresh"


def test_check_is_unknown_when_a_side_cannot_be_hashed(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo")

    result = pr.check(executing=tmp_path / "nowhere", source=repo)

    assert result.status == "unknown"


# --- cut(): the one release step, and its own guard ------------------------------------------------


def test_cut_copies_only_the_shipped_payload(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.2.3")
    cache_root = tmp_path / "cache"

    target = pr.cut(repo, cache_root)

    assert target == cache_root / "1.2.3"
    assert (target / "scripts" / "verifier_evidence.py").is_file()
    assert (target / "hooks" / "hooks.json").is_file()
    assert (target / "docs" / "templates" / "env.example").is_file()  # shipped payload
    assert not (target / "docs" / "notes.md").exists()  # sibling to templates — not shipped


def test_cut_result_reads_fresh_against_its_source(tmp_path):  # noqa: F811
    """The loop this issue asks to close: cut(), then check() against the very thing it produced."""
    repo = make_plugin(tmp_path / "repo", version="1.2.3")
    cache_root = tmp_path / "cache"

    target = pr.cut(repo, cache_root)
    result = pr.check(executing=target, source=repo)

    assert result.status == "fresh"


def test_cut_is_idempotent_on_unchanged_content(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.2.3")
    cache_root = tmp_path / "cache"

    first = pr.cut(repo, cache_root)
    second = pr.cut(repo, cache_root)

    assert first == second


def test_cut_refuses_to_overwrite_a_version_with_different_content(tmp_path):  # noqa: F811
    """A version is released once. A forgotten version bump must fail loudly, not clobber silently."""
    repo = make_plugin(tmp_path / "repo", version="1.2.3")
    cache_root = tmp_path / "cache"
    pr.cut(repo, cache_root)

    (repo / "scripts" / "verifier_evidence.py").write_text(
        "PARTIAL_MARKERS = ['changed but version was not bumped']\n", encoding="utf-8"
    )

    with pytest.raises(FileExistsError):
        pr.cut(repo, cache_root)


def test_cut_requires_a_version(tmp_path):  # noqa: F811
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError):
        pr.cut(repo, tmp_path / "cache")


# --- CLI: what a hook and an operator's terminal actually invoke -----------------------------------


def test_cli_check_exits_1_on_stale(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="0.10.3")
    stale_cache = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")

    result = run_cli("check", "--executing", str(stale_cache), "--source", str(repo))

    assert result.returncode == 1
    assert "STALE" in result.stderr


def test_cli_check_exits_0_on_fresh(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo")

    result = run_cli("check", "--executing", str(repo), "--source", str(repo))

    assert result.returncode == 0
    assert "FRESH" in result.stderr


def test_cli_check_exits_0_and_says_so_when_unconfigured(tmp_path, monkeypatch):  # noqa: F811
    """`unknown` is reported, never enforced — an operator who never set AVENGER_SOURCE_REPO must
    not have every run blocked over a guard they never turned on."""
    cache_copy = make_plugin(tmp_path / "cache")
    env = {"PATH": os.environ.get("PATH", "")}

    result = run_cli("check", "--executing", str(cache_copy), env=env)

    assert result.returncode == 0
    assert "UNKNOWN" in result.stderr


def test_cli_cut_then_check_round_trips(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="2.0.0")
    cache_root = tmp_path / "cache"

    # --no-pin: this test is about the copy + check round-trip, not the registry, and must never
    # risk touching this machine's real install registry via main()'s default-pin resolution.
    cut_result = run_cli(
        "cut", "--repo", str(repo), "--cache-root", str(cache_root), "--no-pin"
    )
    assert cut_result.returncode == 0
    assert str(cache_root / "2.0.0") in cut_result.stdout

    check_result = run_cli(
        "check", "--executing", str(cache_root / "2.0.0"), "--source", str(repo)
    )
    assert check_result.returncode == 0
    assert "FRESH" in check_result.stderr


def test_cli_version_prints_version_and_root(tmp_path, monkeypatch):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="9.9.9")
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(repo)

    result = run_cli("version", env=env)

    assert result.returncode == 0
    assert result.stdout.strip() == f"9.9.9\t{repo}"


# =====================================================================================================
# Round-2 fixes (firstmate, standing autonomy, captain away): pin the install registry on cut(),
# ship docs/templates and detect a missing shipped path loudly, compare committed HEAD not the
# working tree so a dirty tree is never reported as STALE.
# =====================================================================================================


# --- fix: docs/templates is shipped payload, and a missing path is loud, not silent -----------------


def test_plugin_paths_includes_docs_templates():
    assert "docs/templates" in pr.PLUGIN_PATHS


def test_cut_refuses_a_repo_missing_an_expected_shipped_path(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    shutil.rmtree(repo / "docs" / "templates")

    with pytest.raises(ValueError, match="missing expected shipped path"):
        pr.cut(repo, tmp_path / "cache")


def test_cut_succeeds_once_the_missing_path_is_restored(tmp_path):  # noqa: F811
    """RED then GREEN: the same repo that just failed releases cleanly once complete again."""
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    shutil.rmtree(repo / "docs" / "templates")
    with pytest.raises(ValueError, match="missing expected shipped path"):
        pr.cut(repo, tmp_path / "cache")

    (repo / "docs" / "templates").mkdir(parents=True)
    (repo / "docs" / "templates" / "env.example").write_text("# restored\n", encoding="utf-8")

    target = pr.cut(repo, tmp_path / "cache")
    assert (target / "docs" / "templates" / "env.example").is_file()


# --- fix: check() compares committed HEAD, not the working tree; dirty is not stale ------------------


def test_check_compares_committed_head_not_working_tree(tmp_path):  # noqa: F811
    """RED (the bug): hashing the working tree directly would flip this to STALE the moment a
    session makes its own edit — including a session whose entire purpose is that edit. GREEN (the
    fix): status stays fresh; `dirty` says so separately, never as a fourth status value."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    cached = pr.cut(repo, tmp_path / "cache")  # a byte-identical release of the committed content

    baseline = pr.check(executing=cached, source=repo)
    assert baseline.status == "fresh" and baseline.dirty is False

    (repo / "scripts" / "verifier_evidence.py").write_text(
        "PARTIAL_MARKERS = ['fixed', 'mid-edit, not committed yet']\n", encoding="utf-8"
    )

    result = pr.check(executing=cached, source=repo)
    assert result.status == "fresh"   # the fix: an uncommitted edit is not STALE
    assert result.dirty is True       # but it is not invisible either
    assert "uncommitted" in result.detail


def test_check_still_catches_real_drift_against_committed_head(tmp_path):  # noqa: F811
    """The guard still catches the real thing it exists for: a cache that was never released from
    this repo's history, committed or not."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    stale_cache = make_plugin(tmp_path / "cache" / "0.9.0", version="0.9.0")

    result = pr.check(executing=stale_cache, source=repo)

    assert result.status == "stale"
    assert result.dirty is False


def test_check_untracked_file_under_payload_also_reads_dirty(tmp_path):  # noqa: F811
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    cached = pr.cut(repo, tmp_path / "cache")

    (repo / "scripts" / "brand_new_untracked.py").write_text("# not yet added\n", encoding="utf-8")

    result = pr.check(executing=cached, source=repo)
    assert result.status == "fresh" and result.dirty is True


def test_check_falls_back_to_working_tree_hash_for_a_non_git_source(tmp_path):  # noqa: F811
    """A plain directory (every other fixture in this file) has no HEAD to compare — the comparison
    falls back to hashing the working tree exactly as before, and dirty is never claimed for it."""
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    cached = pr.cut(repo, tmp_path / "cache")

    result = pr.check(executing=cached, source=repo)
    assert result.status == "fresh" and result.dirty is False

    (repo / "scripts" / "verifier_evidence.py").write_text("PARTIAL_MARKERS = ['changed']\n", encoding="utf-8")
    edited = pr.check(executing=cached, source=repo)
    assert edited.status == "stale" and edited.dirty is False  # no HEAD concept — same as before


def test_cli_check_prints_a_dirty_note_but_stays_exit_0(tmp_path):  # noqa: F811
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    cached = pr.cut(repo, tmp_path / "cache")
    (repo / "scripts" / "verifier_evidence.py").write_text(
        "PARTIAL_MARKERS = ['fixed', 'wip']\n", encoding="utf-8"
    )

    result = run_cli("check", "--executing", str(cached), "--source", str(repo))

    assert result.returncode == 0
    assert "FRESH" in result.stderr
    assert "uncommitted changes" in result.stderr


# --- fix: _git() decodes explicitly, so it survives a non-UTF-8 ambient locale -----------------------


def test_cli_check_survives_a_non_ascii_path_under_lc_all_c(tmp_path):  # noqa: F811
    """RED (the bug): `_git()` used `text=True`, which decodes git's stdout with the ambient
    locale's preferred encoding. Under `LC_ALL=C` that resolves to ASCII, and `git ls-tree`
    emitting a non-ASCII path (committed HEAD comparison, `_head_content_hash`) raised
    `UnicodeDecodeError` from inside `subprocess.run`, before `_git` ever got to return `None` —
    a crash, not a graceful UNKNOWN. GREEN (the fix): `_git` decodes UTF-8 explicitly regardless
    of locale, so `check` reports a real verdict instead of a traceback.

    `PYTHONUTF8=0`/`PYTHONCOERCECLOCALE=0` are required alongside `LC_ALL=C`/`LANG=C`: CPython on
    POSIX auto-coerces a `C` locale to UTF-8 (PEP 538/540) unless explicitly told not to, which
    would otherwise mask this exact bug in CI.
    """
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    (repo / "scripts" / "ünïcödé.py").write_text("# non-ascii filename\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "add non-ascii path")
    cached = pr.cut(repo, tmp_path / "cache")

    env = dict(os.environ)
    env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")

    result = run_cli("check", "--executing", str(cached), "--source", str(repo), env=env)

    assert "UnicodeDecodeError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.returncode == 0
    assert "FRESH" in result.stderr


# --- fix: cut() pins the install registry, and proves the pin actually resolves ----------------------


def test_plugin_registry_key_derived_from_cache_root_convention():
    key = pr._plugin_registry_key(Path("/x/.claude/plugins/cache/erik-tools/plan-build-verify"))
    assert key == "plan-build-verify@erik-tools"


def test_update_pin_repoints_matching_entries_and_preserves_unrelated_ones(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.1.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    old = cache_root / "1.0.0"
    make_plugin(old, version="1.0.0")
    target = pr.cut(repo, cache_root)

    unrelated = registry_entry("/keep/me", "1.9.0")
    matched_user = registry_entry(old, "1.0.0")
    matched_project = {**registry_entry(old, "1.0.0", scope="project"), "projectPath": "/some/project"}
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "everything-claude-code@everything-claude-code": [unrelated],
        "plan-build-verify@erik-tools": [matched_user, matched_project],
    })

    updated = pr.update_pin(registry_path, cache_root, target, "1.1.0", repo)

    assert updated == 2
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["plugins"]["everything-claude-code@everything-claude-code"][0] == unrelated
    for entry in registry["plugins"]["plan-build-verify@erik-tools"]:
        assert entry["installPath"] == str(target)
        assert entry["version"] == "1.1.0"
    assert registry["plugins"]["plan-build-verify@erik-tools"][1]["projectPath"] == "/some/project"


def test_update_pin_raises_when_no_entry_points_under_the_cache_root(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    target = pr.cut(repo, cache_root)
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "plan-build-verify@erik-tools": [registry_entry("/totally/unrelated/path", "0.1.0")],
    })

    with pytest.raises(ValueError, match="nothing to re-pin"):
        pr.update_pin(registry_path, cache_root, target, "1.0.0", repo)


def test_update_pin_raises_when_the_plugin_key_is_entirely_absent(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    target = pr.cut(repo, cache_root)
    registry_path = make_registry(tmp_path / "installed_plugins.json", {})

    with pytest.raises(ValueError, match="no entries under"):
        pr.update_pin(registry_path, cache_root, target, "1.0.0", repo)


def test_update_pin_raises_on_unreadable_registry(tmp_path):  # noqa: F811
    registry_path = tmp_path / "installed_plugins.json"
    registry_path.write_text("{not json", encoding="utf-8")
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    target = pr.cut(repo, tmp_path / "cache")

    with pytest.raises(ValueError, match="not valid JSON"):
        pr.update_pin(registry_path, tmp_path / "cache", target, "1.0.0", repo)


def test_update_pin_closing_check_catches_a_release_that_does_not_actually_resolve(tmp_path):  # noqa: F811
    """RED: the pin points at a target whose own manifest does not match the claimed version — the
    exact "cut reports success while the old code still runs" failure the closing check exists to
    catch, per issue #69's rule that a guard is proven by failing before it is proven by passing.
    GREEN: a target whose manifest genuinely matches the claimed version succeeds."""
    repo = make_plugin(tmp_path / "repo", version="1.1.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    broken_target = make_plugin(cache_root / "1.1.0", version="9.9.9")  # simulates a broken copy
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "plan-build-verify@erik-tools": [registry_entry(cache_root / "0.9.0", "0.9.0")],
    })

    with pytest.raises(RuntimeError, match="does not actually resolve"):
        pr.update_pin(registry_path, cache_root, broken_target, "1.1.0", repo)

    shutil.rmtree(broken_target)
    good_target = pr.cut(repo, cache_root)
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "plan-build-verify@erik-tools": [registry_entry(cache_root / "0.9.0", "0.9.0")],
    })

    updated = pr.update_pin(registry_path, cache_root, good_target, "1.1.0", repo)
    assert updated == 1


def test_update_pin_sets_git_commit_sha_from_head_when_repo_is_a_git_checkout(tmp_path):  # noqa: F811
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    head_sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    target = pr.cut(repo, cache_root)
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "plan-build-verify@erik-tools": [registry_entry(cache_root / "0.1.0", "0.1.0", sha="old")],
    })

    pr.update_pin(registry_path, cache_root, target, "1.0.0", repo)

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["plugins"]["plan-build-verify@erik-tools"][0]["gitCommitSha"] == head_sha


def test_update_pin_leaves_git_commit_sha_alone_for_a_non_git_repo(tmp_path):  # noqa: F811
    """Best-effort, never fabricated: a repo with no git history has no sha to report, so the
    pin's existing value is left exactly as it was rather than replaced with a placeholder."""
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    target = pr.cut(repo, cache_root)
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "plan-build-verify@erik-tools": [registry_entry(cache_root / "0.1.0", "0.1.0", sha="keep-me")],
    })

    pr.update_pin(registry_path, cache_root, target, "1.0.0", repo)

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["plugins"]["plan-build-verify@erik-tools"][0]["gitCommitSha"] == "keep-me"


def test_cut_without_pin_path_never_touches_a_registry(tmp_path):  # noqa: F811
    """Backward compatibility: `pin_path=None` (the default) is a pure copy, exactly as before this
    round — no registry file is read or required to exist."""
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    target = pr.cut(repo, tmp_path / "cache")
    assert target.is_dir()  # no registry anywhere in this test; nothing raised


def test_cli_cut_with_pin_path_updates_the_registry(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="3.0.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    old = cache_root / "2.0.0"
    make_plugin(old, version="2.0.0")
    registry_path = make_registry(tmp_path / "installed_plugins.json", {
        "plan-build-verify@erik-tools": [registry_entry(old, "2.0.0")],
        "unrelated@marketplace": [registry_entry("/keep/me", "1.0.0")],
    })

    result = run_cli(
        "cut", "--repo", str(repo), "--cache-root", str(cache_root),
        "--pin-path", str(registry_path),
    )

    assert result.returncode == 0
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    plugin_entry = registry["plugins"]["plan-build-verify@erik-tools"][0]
    assert plugin_entry["installPath"] == str(cache_root / "3.0.0")
    assert plugin_entry["version"] == "3.0.0"
    assert registry["plugins"]["unrelated@marketplace"][0]["installPath"] == "/keep/me"


def test_cli_cut_explicit_pin_path_errors_loudly_if_unreadable(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.0.0")

    result = run_cli(
        "cut", "--repo", str(repo), "--cache-root", str(tmp_path / "cache"),
        "--pin-path", str(tmp_path / "does-not-exist.json"),
    )

    assert result.returncode == 1
    assert "cannot read" in result.stderr


def test_cli_cut_no_pin_skips_the_registry_entirely(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    registry_path = make_registry(tmp_path / "installed_plugins.json", {})
    original = registry_path.read_text(encoding="utf-8")

    result = run_cli(
        "cut", "--repo", str(repo), "--cache-root", str(tmp_path / "cache"),
        "--pin-path", str(registry_path), "--no-pin",
    )

    assert result.returncode == 0
    assert registry_path.read_text(encoding="utf-8") == original  # byte-for-byte untouched


def test_cli_cut_default_pin_skips_when_no_registry_present(tmp_path):  # noqa: F811
    """No --pin-path, no --no-pin: main() falls back to $HOME/.claude/plugins/installed_plugins.json
    (AVENGER_PLUGIN_PIN_PATH unset). Run under an isolated, guaranteed-empty $HOME rather than
    trusting that this machine's real file happens to be absent — the hard constraint this whole
    feature is built under is that nothing here may touch a real installation by accident."""
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env.pop(pr.PIN_PATH_ENV, None)
    env.pop(pr.CACHE_ROOT_ENV, None)

    result = run_cli("cut", "--repo", str(repo), "--cache-root", str(tmp_path / "cache"), env=env)

    assert result.returncode == 0
    assert "skipping pin update" in result.stderr
    assert not (fake_home / ".claude").exists()  # nothing was ever created under the fake HOME either


# --- fix: git failures degrade, one process per comparison, and every path round-trips -------------


def test_a_git_call_that_times_out_never_escapes_check(tmp_path, monkeypatch):  # noqa: F811
    """RED (the bug): `subprocess.run(..., timeout=10)` raises `TimeoutExpired`, a `SubprocessError`
    and NOT an `OSError`, so a stalled git escaped `check()` as an uncaught traceback — exiting 1,
    the code `main` reserves for a confirmed STALE, whose prescribed remedy (cut a release, restart)
    cannot repair a stalled git. GREEN: a hung git is the same fact as no git at all — no answer —
    and degrades to the working-tree comparison."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    cached = pr.cut(repo, tmp_path / "cache")

    def hang(argv, *args, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(pr.subprocess, "run", hang)

    result = pr.check(executing=cached, source=repo)
    assert result.status == "fresh"          # fell back to the working tree, did not raise
    assert result.dirty is False             # dirty is a claim only a readable git can make
    assert pr.main(["check", "--executing", str(cached), "--source", str(repo)]) == 0


def test_a_git_call_that_times_out_mid_batch_is_not_partial_content(tmp_path, monkeypatch):  # noqa: F811
    """The listing succeeds and only the blob read stalls: a partial read must never be hashed as
    if it were the committed content."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")

    real_run = pr.subprocess.run

    def hang_on_batch(argv, *args, **kwargs):
        if "cat-file" in argv:
            raise subprocess.TimeoutExpired(argv, 10)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(pr.subprocess, "run", hang_on_batch)

    assert pr._head_content_hash(repo) is None


def test_head_hash_is_one_batch_process_not_one_per_file(tmp_path, monkeypatch):  # noqa: F811
    """RED (the bug): one `git show HEAD:<path>` per tracked file — measured at 110 files in this
    repo — on the phase-open path inside `hook_spec_gate.sh`, whose wall clock `gate_timeouts.py`
    bounds as `metrics_processes x AVENGER_METRICS_TIMEOUT`. A per-file loop spends `len(files)`
    times the git timeout, which that bound cannot see. GREEN: two git invocations total (one
    `ls-tree`, one `cat-file --batch`), no matter how many files there are."""
    small = git_repo(tmp_path / "small", version="1.0.0")
    big = git_repo(tmp_path / "big", version="1.0.0")
    for i in range(40):
        (big / "prompts" / f"extra-{i:02d}.md").write_text(f"# extra {i}\n", encoding="utf-8")
    _run_git(big, "add", "-A")
    _run_git(big, "commit", "-q", "-m", "many files")

    counts = []
    for repo in (small, big):
        digest, invocations = count_git_invocations(
            monkeypatch, lambda repo=repo: pr._head_content_hash(repo)
        )
        assert digest is not None
        counts.append(invocations)

    assert counts == [2, 2], f"expected 2 git invocations regardless of file count, got {counts}"


def test_batched_head_hash_equals_the_per_file_git_show_hash(tmp_path):  # noqa: F811
    """The batch is an optimisation, not a different number: it must stay byte-identical to the
    per-file computation it replaced, and therefore directly comparable to `content_hash`."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    listing = _run_git(repo, "ls-tree", "-r", "-z", "HEAD", "--", *pr.PLUGIN_PATHS).stdout
    paths = sorted(
        record.partition("\t")[2] for record in listing.split("\0") if record.strip()
    )
    reference = hashlib.sha256()
    for path in paths:
        blob = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "show", f"HEAD:{path}"], capture_output=True, check=True
        ).stdout
        reference.update(path.encode("utf-8"))
        reference.update(b"\0")
        reference.update(blob)
        reference.update(b"\0")

    assert pr._head_content_hash(repo) == reference.hexdigest()
    # and it is the same frame `content_hash` hashes in, on a clean tree
    assert pr._head_content_hash(repo) == pr.content_hash(repo)


def test_a_payload_path_with_a_space_and_non_ascii_round_trips(tmp_path):  # noqa: F811
    """RED (the bug): without `-z`, git C-quotes any path with a non-ASCII byte —
    `"docs/templates/caf\\303\\251.md"`, quotes and escapes included — which then names a file that
    cannot be read back, so the HEAD hash silently returned None and `check` fell back to the
    working tree: exactly the false-STALE-on-a-dirty-tree behaviour this module exists to remove,
    with no signal it happened. `docs/templates` is precisely where such a filename appears."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    (repo / "docs" / "templates" / "a b café.md").write_text("# spaced\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "awkward filename")

    head_hash = pr._head_content_hash(repo)
    assert head_hash is not None
    assert head_hash == pr.content_hash(repo)  # the git side saw the same file the filesystem did

    cached = pr.cut(repo, tmp_path / "cache")
    assert (cached / "docs" / "templates" / "a b café.md").is_file()

    clean = pr.check(executing=cached, source=repo)
    assert clean.status == "fresh" and clean.dirty is False

    (repo / "docs" / "templates" / "a b café.md").write_text("# edited, uncommitted\n", encoding="utf-8")
    dirty = pr.check(executing=cached, source=repo)
    assert dirty.status == "fresh" and dirty.dirty is True  # still HEAD-based, not working-tree


def test_a_checkout_nested_below_the_git_top_level_is_not_a_false_stale(tmp_path):  # noqa: F811
    """RED (the bug): `ls-tree --full-tree` resolves both the pathspec and the emitted paths against
    the REPOSITORY root, so a plugin checkout one directory below the git top level matched nothing,
    hashed the empty set, and produced a hard STALE whose only prescribed remedy (`cut` + restart)
    can never clear it — the applicability-boundary wedge CLAUDE.md §3a exists to prevent. GREEN:
    without `--full-tree`, `-C repo` resolves both against `repo`, the same frame `content_hash`
    hashes in."""
    outer = tmp_path / "outer"
    (outer / "unrelated").mkdir(parents=True)
    (outer / "unrelated" / "readme.md").write_text("# not the plugin\n", encoding="utf-8")
    _git_init(outer)
    nested = make_plugin(outer / "plugin", version="1.0.0")
    _run_git(outer, "add", "-A")
    _run_git(outer, "commit", "-q", "-m", "plugin nested inside an outer repo")

    head_hash = pr._head_content_hash(nested)
    assert head_hash is not None
    assert head_hash == pr.content_hash(nested)

    cached = pr.cut(nested, tmp_path / "cache")
    assert pr.check(executing=cached, source=nested).status == "fresh"

    (nested / "scripts" / "verifier_evidence.py").write_text("PARTIAL = ['wip']\n", encoding="utf-8")
    result = pr.check(executing=cached, source=nested)
    assert result.status == "fresh" and result.dirty is True


def test_head_hash_is_none_rather_than_a_hash_of_nothing_when_nothing_matches(tmp_path):  # noqa: F811
    """The backstop behind that root cause, for every other way a listing can come back empty: a
    sha256 of the empty set compares unequal to every real tree, which is a STALE no release clears.
    None instead, so the caller degrades to the working-tree comparison."""
    repo = git_repo(tmp_path / "repo", version="1.0.0")

    assert pr._head_content_hash(repo, paths=("no-such-shipped-dir",)) is None


# --- fix: an unwritable cache or registry surfaces this module's error contract, not a traceback ---


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_cli_cut_reports_an_unwritable_cache_without_a_traceback(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.0.0")
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o555)
    try:
        result = run_cli(
            "cut", "--repo", str(repo), "--cache-root", str(readonly / "cache"), "--no-pin",
        )
    finally:
        readonly.chmod(0o755)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("[plugin-release] ")
    assert "was NOT released" in result.stderr


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_cli_cut_reports_an_unwritable_registry_and_says_the_payload_landed(tmp_path):  # noqa: F811
    """`update_pin` writes atomically through a sibling temp file, so an unwritable registry
    DIRECTORY is the real failure shape. It happens after the payload is fully in place: reported as
    a bare write error it reads as "the release failed", sending an operator to re-run a copy that
    already succeeded, when what is actually owed is the pin alone."""
    repo = make_plugin(tmp_path / "repo", version="1.1.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    old = cache_root / "1.0.0"
    make_plugin(old, version="1.0.0")
    pin_dir = tmp_path / "pins"
    pin_dir.mkdir()
    registry_path = make_registry(
        pin_dir / "installed_plugins.json",
        {"plan-build-verify@erik-tools": [registry_entry(old, "1.0.0")]},
    )
    original = registry_path.read_text(encoding="utf-8")
    pin_dir.chmod(0o555)
    try:
        result = run_cli(
            "cut", "--repo", str(repo), "--cache-root", str(cache_root),
            "--pin-path", str(registry_path),
        )
    finally:
        pin_dir.chmod(0o755)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("[plugin-release] ")
    assert "payload IS released" in result.stderr
    assert (cache_root / "1.1.0" / ".claude-plugin" / "plugin.json").is_file()  # it really did land
    assert registry_path.read_text(encoding="utf-8") == original  # and the pin is untouched


# --- fix: the review findings — plugin identity, non-UTF-8 payload paths, and the pin's error path --


def test_source_root_refuses_a_project_that_is_a_DIFFERENT_plugin(tmp_path, monkeypatch):  # noqa: F811
    """RED: a consumer who uses this pipeline to develop their OWN Claude Code plugin has
    `CLAUDE_PROJECT_DIR` pointing at a plugin repository that is not this one. Accepting it on the
    mere presence of `.claude-plugin/plugin.json` compared the executing release against a payload
    that can never match — a permanent STALE, whose prescribed remedy (`cut` from the source
    checkout) cannot clear it, which is the unclearable wedge CLAUDE.md §3a exists to prevent.
    GREEN: identity is the manifest's `name`, so an unrelated plugin resolves to no source at all
    and `check` reports UNKNOWN — never enforced.
    """
    _clean_env(monkeypatch)
    executing = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")
    other = make_plugin(tmp_path / "their-plugin", version="2.0.0")
    (other / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "someone-elses-plugin", "version": "2.0.0"}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))

    assert pr.source_root(executing) is None
    assert pr.check(executing).status == "unknown"

    ours = make_plugin(tmp_path / "our-plugin", version="0.10.3")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ours))
    assert pr.source_root(executing) == ours


def test_cli_check_does_not_wedge_a_consumer_whose_project_is_another_plugin(tmp_path):  # noqa: F811
    """The same fact at the boundary `/avenger-run` §1 actually calls: exit 0, not the exit 1 that
    halts every run."""
    executing = make_plugin(tmp_path / "cache" / "0.10.2", version="0.10.2")
    other = make_plugin(tmp_path / "their-plugin", version="2.0.0")
    (other / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "someone-elses-plugin", "version": "2.0.0"}), encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k not in (pr.SOURCE_ROOT_ENV, "CLAUDE_PLUGIN_ROOT")}
    env["CLAUDE_PROJECT_DIR"] = str(other)

    result = run_cli("check", "--executing", str(executing), env=env)

    assert result.returncode == 0
    assert "UNKNOWN" in result.stderr


def test_encode_path_round_trips_bytes_a_plain_utf8_encode_cannot():  # noqa: F811
    """RED: both hashers re-encoded a path with `.encode("utf-8")`. Git's output is decoded
    `errors="surrogateescape"` and `Path.rglob` yields the same shape, so a payload filename whose
    bytes are not valid UTF-8 carries lone surrogates and that call raises `UnicodeEncodeError` —
    uncaught, exit 1 with a traceback, indistinguishable from a confirmed STALE. GREEN: the same
    error handler on the way back out restores the original bytes."""
    raw = b"agents/caf\xe9.md"
    decoded = os.fsdecode(raw)

    with pytest.raises(UnicodeEncodeError):
        decoded.encode("utf-8")

    assert pr._encode_path(decoded) == raw


def test_both_hashers_agree_on_a_path_that_is_not_valid_utf8(tmp_path, monkeypatch):  # noqa: F811
    """The two sides of `check()` must produce the SAME digest for the same (path, bytes) pair even
    when the path is not valid UTF-8 — the filesystem side via `content_hash`, the committed side
    via `_head_content_hash`. Driven through fakes rather than a real file because macOS refuses to
    create a filename that is not valid UTF-8 at all, while Linux allows it."""
    root = tmp_path / "repo"
    root.mkdir()
    weird_rel = b"agents/caf\xe9.md"
    weird = Path(os.fsdecode(os.fsencode(root) + b"/" + weird_rel))
    body = b"# not valid utf-8 in the NAME, not the body\n"

    expected = hashlib.sha256()
    expected.update(weird_rel)
    expected.update(b"\0")
    expected.update(body)
    expected.update(b"\0")

    monkeypatch.setattr(pr, "_shipped_files", lambda _root: [weird])
    monkeypatch.setattr(Path, "read_bytes", lambda _self: body)
    assert pr.content_hash(root) == expected.hexdigest()

    listing = "100644 blob 0123456789abcdef0123456789abcdef01234567\t" + os.fsdecode(weird_rel) + "\0"
    monkeypatch.setattr(pr, "_git", lambda _root, *_args: listing)
    monkeypatch.setattr(pr, "_git_batch_blobs", lambda _root, _shas: [body])
    assert pr._head_content_hash(root) == expected.hexdigest()


def test_cli_cut_says_the_payload_landed_when_the_registry_has_no_entry(tmp_path):  # noqa: F811
    """RED: `_pin_after_copy` translated only `OSError`, but `update_pin` raises `ValueError` for an
    unreadable, non-JSON or entry-less registry — every one of them AFTER the payload is on disk.
    Printed bare by `main`, `cannot read .../installed_plugins.json` reads as "the release failed",
    which is the exact misreading that wrapper exists to prevent. GREEN: every failure from the pin
    step carries the payload's real state, with the cause appended verbatim."""
    repo = make_plugin(tmp_path / "repo", version="1.1.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    registry_path = make_registry(tmp_path / "installed_plugins.json", {})

    result = run_cli(
        "cut", "--repo", str(repo), "--cache-root", str(cache_root),
        "--pin-path", str(registry_path),
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "payload IS released" in result.stderr
    assert "no entries under" in result.stderr  # the cause, verbatim
    assert (cache_root / "1.1.0" / ".claude-plugin" / "plugin.json").is_file()


def test_cli_cut_says_the_payload_landed_when_the_registry_is_not_json(tmp_path):  # noqa: F811
    repo = make_plugin(tmp_path / "repo", version="1.1.0")
    cache_root = tmp_path / "cache" / "erik-tools" / "plan-build-verify"
    registry_path = tmp_path / "installed_plugins.json"
    registry_path.write_text("{not json", encoding="utf-8")

    result = run_cli(
        "cut", "--repo", str(repo), "--cache-root", str(cache_root),
        "--pin-path", str(registry_path),
    )

    assert result.returncode == 1
    assert "payload IS released" in result.stderr
    assert "not valid JSON" in result.stderr
    assert (cache_root / "1.1.0" / ".claude-plugin" / "plugin.json").is_file()
