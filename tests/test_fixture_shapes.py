"""Fixture realism as a mechanical check rather than an instruction (issue #33).

The measured defect: 1,009 tests passed against Telegram supergroup ids around 970 million while
real ids are an order of magnitude larger. The column was `sa.Integer()` (int32), so a `/setkey`
credential refusal raised `DataError: value out of int32 range` before it could fire - a security
control shipped non-functional behind a green suite. No stage caught it; the delivery gate driving a
real id end to end did.

It shipped as instruction (`skills/tdd`, `skills/verifier-triage`, the spec-writer's brief) because
no static rule can decide "a shape a real deployment produces" GENERICALLY. That is true, and it is
also not a reason to leave it unenforced: the shape is per-project knowledge, so the PROJECT declares
it and the check is generic. Same split as `SUBPROC_CHECK_PATHS` scoping a generic cost gate.

What is pinned here is that split - a declaration nothing checks is the promise this repo keeps
paying for, and a check that invents a shape nobody declared would be worse.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fixture_shapes  # noqa: E402

pytestmark = pytest.mark.subprocess(
    "the diff scoping is a claim about what git actually reports; a faked diff tests the fake"
)

CONFIG = """
[telegram-chat-id]
names = ["chat_id", "supergroup_id"]
min = 1000000000000
max = 9999999999999
why = "real supergroup ids are ~1e12; a 9-digit fixture fits int32 and hides the overflow"
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A repo with a declaration and a test tree, scanned whole (`--all`) unless a test says else."""
    git(tmp_path, "init", "-q")
    (tmp_path / "fixture-shapes.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def write_test(project: Path, body: str, name: str = "test_fixtures.py") -> Path:
    path = project / "tests" / name
    path.write_text(body, encoding="utf-8")
    return path


def scan(project: Path) -> list[str]:
    """Every violation the checker finds under the project's test root, rendered."""
    shapes = fixture_shapes.load(project / "fixture-shapes.toml")
    violations, _unreadable, _roots = fixture_shapes.scan_path(project / "tests", shapes)
    return [v.render() for v in violations]


# ── the measured defect ──────────────────────────────────────────────────────


def test_the_measured_defect_is_caught(project: Path) -> None:
    """A supergroup id an order of magnitude too small - the exact fixture that shipped."""
    write_test(project, "def test_setkey():\n    chat_id = 970000000\n    assert chat_id\n")

    (violation,) = scan(project)

    assert "chat_id" in violation
    assert "970000000" in violation
    assert "1000000000000" in violation, "the declared bound is what makes it actionable"
    assert "int32" in violation, "the declaration's own `why` is the sentence a reader weighs"


def test_a_realistic_value_passes(project: Path) -> None:
    write_test(project, "def test_setkey():\n    chat_id = 1002345678901\n    assert chat_id\n")

    assert scan(project) == []


def test_a_value_above_the_declared_range_is_caught_too(project: Path) -> None:
    """Unrealistic is not only 'too small'."""
    write_test(project, "chat_id = 99999999999999999\n")

    assert len(scan(project)) == 1


# ── every shape a fixture is actually written in ─────────────────────────────


def test_a_keyword_argument_is_checked(project: Path) -> None:
    write_test(project, "def test_x():\n    send(chat_id=970000000)\n")

    assert len(scan(project)) == 1


def test_a_dict_literal_is_checked(project: Path) -> None:
    """The commonest fixture shape: a payload dict."""
    write_test(project, 'PAYLOAD = {"chat_id": 970000000, "text": "hi"}\n')

    assert len(scan(project)) == 1


def test_an_annotated_assignment_is_checked(project: Path) -> None:
    write_test(project, "chat_id: int = 970000000\n")

    assert len(scan(project)) == 1


def test_a_negative_literal_keeps_its_sign(project: Path) -> None:
    """Telegram supergroup ids are negative in some APIs; -970000000 is not 970000000."""
    write_test(project, "chat_id = -1002345678901\n")

    (violation,) = scan(project)
    assert "-1002345678901" in violation


def test_every_declared_name_is_checked_not_just_the_first(project: Path) -> None:
    write_test(project, "supergroup_id = 970000000\n")

    assert len(scan(project)) == 1


# ── what it deliberately does NOT decide ─────────────────────────────────────


def test_an_undeclared_identifier_is_not_invented_into_a_finding(project: Path) -> None:
    """Nothing here can tell that a declaration is MISSING, and guessing a shape would be worse
    than not checking: the whole reason this shipped as instruction is that no static rule decides
    'a shape a real deployment produces' generically."""
    write_test(project, "user_id = 1\nmessage_id = 2\n")

    assert scan(project) == []


def test_a_computed_value_is_skipped(project: Path) -> None:
    """A fixture built rather than written literally cannot be judged; skipping is the honest
    answer, and it is stated in the module rather than implied."""
    write_test(project, "chat_id = make_id()\nother = chat_id + 1\n")

    assert scan(project) == []


def test_a_string_pattern_is_checked_when_declared(project: Path) -> None:
    (project / "fixture-shapes.toml").write_text(
        '[stripe-customer]\nnames = ["customer_id"]\npattern = "cus_[A-Za-z0-9]{14,}"\n'
        'why = "real Stripe ids are cus_ plus 14+ chars; cus_1 hides a column too narrow"\n',
        encoding="utf-8",
    )
    write_test(project, 'customer_id = "cus_1"\n')

    (violation,) = scan(project)
    assert "cus_1" in violation


# ── the declaration itself must constrain something ──────────────────────────


def test_a_declaration_with_no_names_is_an_error(project: Path) -> None:
    (project / "fixture-shapes.toml").write_text(
        '[x]\nmin = 1\nwhy = "because"\n', encoding="utf-8"
    )

    with pytest.raises(fixture_shapes.ShapeError, match="names"):
        fixture_shapes.load(project / "fixture-shapes.toml")


def test_a_declaration_that_constrains_nothing_is_an_error(project: Path) -> None:
    """A table with no bound and no pattern LOOKS enforced and checks nothing - the exact defect
    class this issue is about, one level up."""
    (project / "fixture-shapes.toml").write_text(
        '[x]\nnames = ["chat_id"]\nwhy = "because"\n', encoding="utf-8"
    )

    with pytest.raises(fixture_shapes.ShapeError, match="constrains nothing"):
        fixture_shapes.load(project / "fixture-shapes.toml")


def test_a_declaration_with_no_reason_is_an_error(project: Path) -> None:
    """`why` is mandatory for the same reason `@pytest.mark.subprocess("<why>")`'s is: the marker
    alone is a rubber stamp, the sentence is what a reviewer weighs."""
    (project / "fixture-shapes.toml").write_text(
        '[x]\nnames = ["chat_id"]\nmin = 1\n', encoding="utf-8"
    )

    with pytest.raises(fixture_shapes.ShapeError, match="why"):
        fixture_shapes.load(project / "fixture-shapes.toml")


# ── absence, and the exit codes ──────────────────────────────────────────────


def test_no_declaration_is_clean_but_never_silent(project: Path, capsys) -> None:
    """A project that has not declared any shape must still be able to work - but a permanently
    green gate with no output is the invisible pass this whole class is about."""
    (project / "fixture-shapes.toml").unlink()
    write_test(project, "chat_id = 970000000\n")

    assert fixture_shapes.main(["--all"]) == fixture_shapes.CLEAN
    assert "no fixture-shapes" in capsys.readouterr().err


def test_a_violation_in_scope_exits_one(project: Path) -> None:
    write_test(project, "chat_id = 970000000\n")

    assert fixture_shapes.main(["--all"]) == fixture_shapes.VIOLATIONS


def test_an_unparseable_test_file_fails_closed(project: Path) -> None:
    """A file the checker cannot read is a file it cannot clear."""
    write_test(project, "def test_x(:\n")

    assert fixture_shapes.main(["--all"]) == fixture_shapes.ERROR


def test_an_unparseable_declaration_fails_closed(project: Path) -> None:
    (project / "fixture-shapes.toml").write_text("[x\nnot toml", encoding="utf-8")

    assert fixture_shapes.main(["--all"]) == fixture_shapes.ERROR


# ── the applicability boundary ───────────────────────────────────────────────


def test_an_untouched_file_is_counted_and_named_never_blocked(project: Path, capsys) -> None:
    """The hostage failure this pipeline has paid for once: a check added after the tree refusing
    every write over files the change never opened."""
    write_test(project, "chat_id = 970000000\n")
    git(project, "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "committed")

    assert fixture_shapes.main([]) == fixture_shapes.CLEAN
    assert "NOT enforced" in capsys.readouterr().err


def test_a_touched_file_still_blocks(project: Path) -> None:
    write_test(project, "chat_id = 1002345678901\n")
    git(project, "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "committed")
    write_test(project, "chat_id = 970000000\n")

    assert fixture_shapes.main([]) == fixture_shapes.VIOLATIONS


# ── it is enforced, not asked for ────────────────────────────────────────────
#
# The whole issue is instruction-versus-mechanism, so a wiring assertion is not enough on its own:
# these drive the real hook, in the shape a phase actually reaches it.


HOOK = ROOT / "scripts" / "hook_verifier.sh"

SPEC = """---
feature: demo
phase: 1-core
spec: 1.1-a
spec_gate: approved
review_status: approved
status: done
---

## Requirements
- R1.1.1 — `binding: integration` — the setkey refusal

## Acceptance criteria
- R1.1.1 — passes when: …; fails when: …
"""

MAPPING = """| requirement | test | level |
|---|---|---|
| R1.1.1 | test_setkey_refuses | integration |
"""


def _phase_project(tmp_path: Path, fixture_body: str) -> tuple[Path, Path]:
    """A project at the moment a spec is stamped done: green suite, mapping recorded, fixtures on
    disk. Committed FIRST without the stamp, so the transition into `done` is what the hook sees."""
    root = tmp_path / "proj"
    spec_dir = root / "docs" / "features" / "demo" / "phases" / "1-core" / "specs" / "1.1-a"
    spec_dir.mkdir(parents=True)
    tests = root / "tests" / "demo" / "1-core"
    tests.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(SPEC.replace("status: done", "status: in-progress"))
    (spec_dir / "test-mapping.md").write_text(MAPPING)
    (tests / "test_setkey.py").write_text(fixture_body)
    (root / "fixture-shapes.toml").write_text(CONFIG)
    _commit_before_the_stamp(root)
    (spec_dir / "spec.md").write_text(SPEC)
    return root, spec_dir / "spec.md"


def _commit_before_the_stamp(root: Path) -> None:
    """Commit the phase WITHOUT the `done` stamp, so the hook sees the transition into it."""
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "before the stamp")


def run_hook(root: Path, spec: Path) -> subprocess.CompletedProcess:
    import json as _json

    return subprocess.run(
        ["bash", str(HOOK)],
        input=_json.dumps({"tool_input": {"file_path": str(spec)}}),
        capture_output=True, text=True, check=False, cwd=str(root),
        env={
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root.parent),
            "CLAUDE_PROJECT_DIR": str(root),
            "AVENGER_METRICS_OFF": "1",
        },
    )


GREEN_SUITE = "def test_setkey_refuses():\n    assert True\n"


def test_the_hook_fails_a_spec_whose_fixture_contradicts_a_declared_shape(tmp_path: Path) -> None:
    """The mechanism, end to end: a green suite and a recorded mapping are no longer enough."""
    root, spec = _phase_project(tmp_path, GREEN_SUITE + "\nchat_id = 970000000\n")

    result = run_hook(root, spec)

    assert result.returncode == 2
    assert "fixture" in result.stderr.lower()
    assert "970000000" in result.stderr
    assert "int32" in result.stderr, "the project's own reason reaches the person who broke it"


def test_the_same_phase_with_a_realistic_fixture_passes_the_hook(tmp_path: Path) -> None:
    """The control: everything else identical, only the fixture's magnitude changed."""
    root, spec = _phase_project(tmp_path, GREEN_SUITE + "\nchat_id = 1002345678901\n")

    result = run_hook(root, spec)

    assert result.returncode == 0, result.stderr


def test_ci_sweeps_it_too() -> None:
    """A rule only an in-session hook applies stops existing the moment a phase is driven any other
    way — the same reason the attempt cap and the carried-items obligation are enforced in both."""
    assert "fixture_shapes.py" in (ROOT / "scripts" / "gate_ci.sh").read_text(encoding="utf-8")
