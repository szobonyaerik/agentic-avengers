"""A spec's Interfaces block is a claim about code, and until now nothing compared the two.

## The defect

clickup-agents phase 12: spec 12.2's `## Interfaces / contracts` prose said the poll loop polls and
then sleeps. The shipped code had to do the reverse - polling first opened a second concurrent
database session inside whichever test had booted the agent, which failed locked phase-8 tests
intermittently and once hung the suite to its watchdog. The prose was then simply wrong about
shipped behaviour. Phase 13 produced a second instance: a spec body that over-claimed what the code
did.

Both were caught by an implementer choosing to look. **Nothing mechanically compared an Interfaces
block to the code it describes** - the same shape as a fixture nobody checked against the values a
real deployment produces, which `scripts/fixture_shapes.py` closed by making the project declare the
shape and the check generic.

These tests hold the half of that comparison a static rule can actually decide: **a call signature
the Interfaces block names must exist in the code.** What it cannot decide - the ORDER of two
operations, which is exactly phase 12's instance - is pinned here too, so the limit is a tested fact
rather than a sentence in a docstring.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import interface_drift  # noqa: E402

SPEC = """---
feature: demo
phase: 12-poller
spec: 12.2
---

# Spec 12.2

## Requirements
- R12.2.1 the poller sleeps first

## Interfaces / contracts

```python
def poll_once(session) -> int: ...
```

The loop calls `sleep_then_poll()` on every tick.

## Out of scope
none
"""


def spec_at(root: Path, body: str = SPEC) -> Path:
    directory = (
        root
        / "docs"
        / "features"
        / "demo"
        / "phases"
        / "12-poller"
        / "specs"
        / "12.2-poll"
    )
    directory.mkdir(parents=True)
    target = directory / "spec.md"
    target.write_text(body, encoding="utf-8")
    return target


def source(root: Path, relative: str, body: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


# --- the comparison itself -------------------------------------------------------------------------


def test_a_signature_the_code_does_not_have_is_drift(tmp_path: Path) -> None:
    spec = spec_at(tmp_path)
    source(tmp_path, "src/poller.py", "def poll_once(session):\n    return 0\n")
    found = interface_drift.drift(spec, [tmp_path / "src"])
    assert [name for name, _line in found] == ["sleep_then_poll"], (
        "poll_once is defined and sleep_then_poll is not; only the second is a claim about code "
        "that does not exist"
    )


def test_a_block_whose_every_signature_exists_is_clean(tmp_path: Path) -> None:
    spec = spec_at(tmp_path)
    source(
        tmp_path,
        "src/poller.py",
        "def poll_once(session):\n    return 0\n\n\ndef sleep_then_poll():\n    pass\n",
    )
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


def test_a_definition_in_any_supported_language_counts(tmp_path: Path) -> None:
    spec = spec_at(tmp_path)
    source(tmp_path, "src/poller.py", "def poll_once(session):\n    return 0\n")
    source(tmp_path, "src/loop.ts", "export const sleep_then_poll = async () => {};\n")
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


def test_a_spec_with_no_interfaces_section_claims_nothing(tmp_path: Path) -> None:
    spec = spec_at(tmp_path, SPEC.replace("## Interfaces / contracts", "## Notes"))
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


def test_only_the_interfaces_section_is_read(tmp_path: Path) -> None:
    """A signature named in Requirements or Out of scope is not this section's claim, and reading
    the whole spec would turn every mention of a future name into a blocking finding."""
    spec = spec_at(
        tmp_path,
        SPEC.replace(
            "- R12.2.1 the poller sleeps first", "- R12.2.1 calls `never_built()`"
        ),
    )
    source(
        tmp_path,
        "src/poller.py",
        "def poll_once(session):\n    return 0\n\n\ndef sleep_then_poll():\n    pass\n",
    )
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


def test_language_keywords_and_builtins_are_not_claims(tmp_path: Path) -> None:
    spec = spec_at(
        tmp_path,
        SPEC.replace(
            "The loop calls `sleep_then_poll()` on every tick.",
            "It runs `if (ready)` and calls `len()` and `print()`.",
        ),
    )
    source(tmp_path, "src/poller.py", "def poll_once(session):\n    return 0\n")
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


def test_a_dotted_call_is_not_judged(tmp_path: Path) -> None:
    """`json.dumps()` names something in a dependency, and demanding a local definition for it would
    report a finding nobody can act on - the rule fixture_shapes.py follows for a computed fixture."""
    spec = spec_at(
        tmp_path,
        SPEC.replace(
            "The loop calls `sleep_then_poll()` on every tick.",
            "It serialises with `json.dumps()`.",
        ),
    )
    source(tmp_path, "src/poller.py", "def poll_once(session):\n    return 0\n")
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


# --- what it cannot decide, tested rather than asserted in prose ------------------------------------


def test_the_order_of_two_operations_is_not_decidable_here(tmp_path: Path) -> None:
    """Phase 12's own instance. The prose said poll-then-sleep and the code did sleep-then-poll;
    both names exist, so this check is silent. Saying so in a test is the difference between a
    stated limit and a claim this module would otherwise be making about itself."""
    spec = spec_at(tmp_path)
    source(
        tmp_path,
        "src/poller.py",
        "def sleep_then_poll():\n    time.sleep(1)\n    poll_once(None)\n\n\n"
        "def poll_once(session):\n    return 0\n",
    )
    assert interface_drift.drift(spec, [tmp_path / "src"]) == []


# --- the CLI, and the boundary it runs on ------------------------------------------------------------


def test_the_cli_reports_drift_as_exit_1_and_names_the_signature(
    tmp_path: Path, capsys
) -> None:
    spec = spec_at(tmp_path)
    source(tmp_path, "src/poller.py", "def poll_once(session):\n    return 0\n")
    assert (
        interface_drift.main([str(spec), "--source", str(tmp_path / "src"), "--all"])
        == 1
    )
    assert "sleep_then_poll" in capsys.readouterr().err


def test_a_clean_run_says_so_rather_than_passing_invisibly(
    tmp_path: Path, capsys
) -> None:
    spec = spec_at(tmp_path)
    source(
        tmp_path,
        "src/poller.py",
        "def poll_once(session):\n    return 0\n\n\ndef sleep_then_poll():\n    pass\n",
    )
    assert (
        interface_drift.main([str(spec), "--source", str(tmp_path / "src"), "--all"])
        == 0
    )
    assert "interface_drift" in capsys.readouterr().err


def test_an_unreadable_spec_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(interface_drift.DriftError):
        interface_drift.drift(tmp_path / "nothing" / "spec.md", [tmp_path])


def test_no_source_root_is_reported_never_silently_clean(
    tmp_path: Path, capsys
) -> None:
    """A check that scans nothing and exits 0 is the invisible pass this whole class is about."""
    spec = spec_at(tmp_path)
    assert (
        interface_drift.main([str(spec), "--source", str(tmp_path / "gone"), "--all"])
        == 2
    )
    assert "no source" in capsys.readouterr().err.lower()


# --- it is enforced, not asked for --------------------------------------------------------------------


def test_the_spec_done_trigger_runs_it() -> None:
    """The drift becomes real the moment the implementer stamps the spec `done`: that is the first
    point at which the code exists AND the person who wrote both still owns them - the same seam
    `fixture_shapes.py` is asked at."""
    text = (ROOT / "scripts" / "hook_verifier.sh").read_text(encoding="utf-8")
    assert "interface_drift.py" in text and "verifier:interface-drift" in text


def test_ci_sweeps_it_too() -> None:
    assert "interface_drift.py" in (ROOT / "scripts" / "gate_ci.sh").read_text(
        encoding="utf-8"
    )
