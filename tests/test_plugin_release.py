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
    for name in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", pr.SOURCE_ROOT_ENV, pr.CACHE_ROOT_ENV):
        monkeypatch.delenv(name, raising=False)


def make_plugin(root: Path, version: str = "1.0.0", *, extra_files: dict[str, str] | None = None) -> Path:
    """A minimal shipped payload: one file per `PLUGIN_PATHS` entry, plus the manifest."""
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "plan-build-verify", "version": version}), encoding="utf-8"
    )
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "verifier_review_check.py").write_text(
        "PARTIAL_MARKERS = ['fixed']\n", encoding="utf-8"
    )
    (root / "hooks").mkdir(exist_ok=True)
    (root / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
    # Payload the version guard does not track — a docs/tests edit must never register as drift.
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "notes.md").write_text("not shipped\n", encoding="utf-8")
    for rel, body in (extra_files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


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
    assert not (target / "docs").exists()  # not shipped payload


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

    cut_result = run_cli("cut", "--repo", str(repo), "--cache-root", str(cache_root))
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
