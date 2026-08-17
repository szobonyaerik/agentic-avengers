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
        ("scripts", "verifier_review_check.py", "PARTIAL_MARKERS = ['fixed']\n"),
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


def git_repo(root: Path, version: str = "1.0.0") -> Path:
    """A git-initialized plugin fixture with one commit, for HEAD-vs-working-tree tests."""
    make_plugin(root, version=version)
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-q", "-m", "initial")
    return root


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
        extra_files={"scripts/verifier_review_check.py": "PARTIAL_MARKERS = ['nothing-stale-here']\n"},
    )

    assert pr.plugin_version(stale_cache) == "0.10.2"
    assert pr.plugin_version(fixed_repo) == "0.10.3"


# --- content_hash: what actually distinguishes "same code" from "different code" ------------------


def test_content_hash_ignores_pycache_and_pyc(tmp_path):  # noqa: F811
    make_plugin(tmp_path)
    baseline = pr.content_hash(tmp_path)

    cache_dir = tmp_path / "scripts" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "verifier_review_check.cpython-311.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "scripts" / "verifier_review_check.pyc").write_bytes(b"\x00\x01")

    assert pr.content_hash(tmp_path) == baseline


def test_content_hash_changes_when_a_shipped_file_changes(tmp_path):  # noqa: F811
    make_plugin(tmp_path)
    before = pr.content_hash(tmp_path)

    (tmp_path / "scripts" / "verifier_review_check.py").write_text(
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
        extra_files={"scripts/verifier_review_check.py": (
            (repo / "scripts" / "verifier_review_check.py").read_text(encoding="utf-8")
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
    assert (target / "scripts" / "verifier_review_check.py").is_file()
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

    (repo / "scripts" / "verifier_review_check.py").write_text(
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

    (repo / "scripts" / "verifier_review_check.py").write_text(
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

    (repo / "scripts" / "verifier_review_check.py").write_text("PARTIAL_MARKERS = ['changed']\n", encoding="utf-8")
    edited = pr.check(executing=cached, source=repo)
    assert edited.status == "stale" and edited.dirty is False  # no HEAD concept — same as before


def test_cli_check_prints_a_dirty_note_but_stays_exit_0(tmp_path):  # noqa: F811
    repo = git_repo(tmp_path / "repo", version="1.0.0")
    cached = pr.cut(repo, tmp_path / "cache")
    (repo / "scripts" / "verifier_review_check.py").write_text(
        "PARTIAL_MARKERS = ['fixed', 'wip']\n", encoding="utf-8"
    )

    result = run_cli("check", "--executing", str(cached), "--source", str(repo))

    assert result.returncode == 0
    assert "FRESH" in result.stderr
    assert "uncommitted changes" in result.stderr


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
