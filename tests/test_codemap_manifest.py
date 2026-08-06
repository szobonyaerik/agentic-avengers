"""Tests for codemap's purpose-cache manifest.

This cache holds purposes a user paid a model to generate, and the dangerous direction is losing
them: either dropping them out of the generated map, or rewriting the file with an empty `files`
map. Both have regressed before, so these pin the read path and the write invariant.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import codemap  # noqa: E402

CACHED_PURPOSE = "Cached purpose a model was paid to write"

pytestmark = pytest.mark.subprocess(
    "asserts the codemap CLI's real behaviour, which only exists as a process"
)


def _seed_manifest(out: Path, files: dict[str, dict], model: str = "openai/gpt-5") -> bytes:
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": codemap.MANIFEST_VERSION,
        "prompt_version": codemap.PROMPT_VERSION,
        "model": model,
        "generated": "2026-01-01T00:00:00",
        "files": files,
    }
    path = out / codemap.MANIFEST_FILENAME
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path.read_bytes()


def _manifest_for(out: Path, force: bool = False) -> codemap.Manifest:
    return codemap.Manifest(types.SimpleNamespace(output=out, force=force))


def _run_codemap(root: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "codemap.py"), str(root),
         "--lang", "python", "--output", str(out)],
        capture_output=True, text=True, cwd=root,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A one-file repo whose only module has no docstring, so its purpose can only
    come from the cache or the fallback. Only the tests that generate a real map need a
    tree-sitter grammar; the Manifest invariants below must run without one."""
    pytest.importorskip("tree_sitter_python")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "undocumented.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def test_seeded_manifest_purpose_reaches_the_generated_map(repo: Path) -> None:
    out = repo / "codebase"
    h = codemap.file_hash(repo / "undocumented.py")
    _seed_manifest(out, {"undocumented.py": {"hash": h, "purpose": CACHED_PURPOSE}})

    result = _run_codemap(repo, out)

    assert result.returncode == 0, result.stderr
    assert CACHED_PURPOSE in (out / "INDEX.md").read_text(encoding="utf-8")
    assert CACHED_PURPOSE in (out / "undocumented.md").read_text(encoding="utf-8")


def test_disabled_run_leaves_the_manifest_byte_identical(repo: Path) -> None:
    out = repo / "codebase"
    h = codemap.file_hash(repo / "undocumented.py")
    before = _seed_manifest(out, {"undocumented.py": {"hash": h, "purpose": CACHED_PURPOSE}})

    assert _run_codemap(repo, out).returncode == 0

    assert (out / codemap.MANIFEST_FILENAME).read_bytes() == before


def test_stale_hash_falls_back_instead_of_serving_a_cached_purpose(repo: Path) -> None:
    out = repo / "codebase"
    _seed_manifest(out, {"undocumented.py": {"hash": "0" * 16, "purpose": CACHED_PURPOSE}})

    assert _run_codemap(repo, out).returncode == 0

    doc = (out / "undocumented.md").read_text(encoding="utf-8")
    assert CACHED_PURPOSE not in doc
    assert "(undocumented" in doc


def test_load_accepts_any_writers_model_while_disabled(tmp_path: Path) -> None:
    _seed_manifest(tmp_path, {"a.py": {"hash": "abc", "purpose": CACHED_PURPOSE}})

    manifest = _manifest_for(tmp_path)
    manifest.load()

    assert manifest.cached_purpose("a.py", "abc") == CACHED_PURPOSE
    assert manifest.cached_purpose("a.py", "changed") is None


def test_load_honors_force_and_version_keys(tmp_path: Path) -> None:
    _seed_manifest(tmp_path, {"a.py": {"hash": "abc", "purpose": CACHED_PURPOSE}})
    forced = _manifest_for(tmp_path, force=True)
    forced.load()
    assert forced.cached_purpose("a.py", "abc") is None

    path = tmp_path / codemap.MANIFEST_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**payload, "prompt_version": "0"}), encoding="utf-8")
    stale_prompt = _manifest_for(tmp_path)
    stale_prompt.load()
    assert stale_prompt.cached_purpose("a.py", "abc") is None

    path.write_text(json.dumps({**payload, "version": codemap.MANIFEST_VERSION + 1}),
                    encoding="utf-8")
    stale_version = _manifest_for(tmp_path)
    stale_version.load()
    assert stale_version.cached_purpose("a.py", "abc") is None


def test_save_never_writes_while_disabled(tmp_path: Path) -> None:
    before = _seed_manifest(tmp_path, {"a.py": {"hash": "abc", "purpose": CACHED_PURPOSE}})

    manifest = _manifest_for(tmp_path)
    manifest.load()
    manifest.save(set())

    assert (tmp_path / codemap.MANIFEST_FILENAME).read_bytes() == before


def test_save_refuses_to_empty_a_manifest_it_never_loaded(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Flipping the feature flag back on must not reintroduce the data loss: an existing
    manifest rejected by load() and repopulated by nobody is left alone."""
    monkeypatch.setattr(codemap, "LLM_PURPOSES_ENABLED", True)
    before = _seed_manifest(tmp_path, {"a.py": {"hash": "abc", "purpose": CACHED_PURPOSE}})

    manifest = _manifest_for(tmp_path)
    manifest.load()
    assert manifest.cached_purpose("a.py", "abc") is None
    manifest.save({"a.py"})

    assert (tmp_path / codemap.MANIFEST_FILENAME).read_bytes() == before


def test_save_writes_once_something_was_recorded(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codemap, "LLM_PURPOSES_ENABLED", True)
    _seed_manifest(tmp_path, {"a.py": {"hash": "abc", "purpose": CACHED_PURPOSE}})

    manifest = _manifest_for(tmp_path)
    manifest.load()
    manifest.record("b.py", "def", "fresh purpose")
    manifest.save({"b.py"})

    payload = json.loads((tmp_path / codemap.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert payload["files"] == {"b.py": {"hash": "def", "purpose": "fresh purpose"}}
