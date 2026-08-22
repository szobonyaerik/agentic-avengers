"""The one spec gate, end to end: two mechanical checks, observe, triage, and a script's verdict.

This replaces tests/test_hook_fidelity.py and tests/test_hook_spec_review.py, whose subjects ran two
model rubrics over the same document at the same moment — a spec once passed one and failed the other
on byte-identical text.

What is pinned here is what the collapse must not lose, and what it must newly guarantee:

  * the mechanical checks run BEFORE any paid call, so an over-cap spec costs nothing and is told to
    SPLIT rather than rejected;
  * a block carries the gate's own findings to the author (an unexplained rejection is what produced
    blind rewriting, and one 25k spec reached 51k that way);
  * notes never block, and they land where the implementer reads them;
  * an unchanged body does not re-run a paid gate, and an unchanged BLOCKED body replays its block;
  * everything ambiguous fails closed.

No model is called: `scripts/` is copied and `gate_runner.py` is replaced by a stub, so the real hook
runs and only the paid call is swapped out.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess(
    "the subject under test is a bash hook; running it any other way would test a reimplementation"
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gate_runner import ATTRIBUTION_MARKER, RUNNER_ABI, SAME_FAMILY_MARKER  # noqa: E402

SPEC = """---
feature: demo
phase: 1-demo
spec: 1.1-a
status: {status}
spec_gate: pending
review_status: pending
---

# Spec

## Requirements
{requirements}

## Acceptance criteria
- R1.1.1 — passes when: the caller sees a greeting; fails when: the caller is unauthenticated
"""

#: A stub gate_runner driven entirely by the environment. It answers the OBSERVE pass with
#: $STUB_OBSERVATIONS and the TRIAGE pass with $STUB_CLASSIFICATIONS, branching on --json-key, so one
#: stub covers both calls of a two-call hook. It identifies itself because the hook refuses a runner
#: that cannot (scripts/gate_runner_guard.sh) — a deliberate double says what it is; a scaffold does
#: not.
STUB_RUNNER = """import hashlib
import json
import os
import sys

#: The hook reads this out of the runner rather than spelling it again, so the stub carries it too —
#: from the real module, so the test cannot pass against a marker the runner no longer emits.
SAME_FAMILY_MARKER = "{marker}"
ATTRIBUTION_MARKER = "{attribution}"

if "--identify" in sys.argv:
    print("{abi} " + hashlib.sha256(open(sys.argv[0], "rb").read()).hexdigest())
    sys.exit(0)

key = sys.argv[sys.argv.index("--json-key") + 1] if "--json-key" in sys.argv else "verdict"
out = sys.argv[sys.argv.index("--emit-json") + 1]
target = sys.argv[sys.argv.index("--target") + 1]
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ""
open(sys.argv[0] + ".calls", "a").write(key + "\\n")
open(sys.argv[0] + ".targets", "a").write(open(target).read() + "\\n=== END ===\\n")
open(sys.argv[0] + ".models", "a").write(key + "=" + model + "\\n")
open(sys.argv[0] + ".args", "a").write(json.dumps(sys.argv[1:]) + "\\n")

# The real runner announces a waived same-family call before it calls anything. The stub has no
# families to compare, so it announces whenever the waiver reached it — which is exactly the wiring
# under test here.
if "--same-family-waiver" in sys.argv:
    # One line, reason normalised — the real runner's own shape (gate_runner.waiver_reason), because
    # what the hook does with a multi-line marker is a property worth testing honestly.
    reason = " ".join(sys.argv[sys.argv.index("--same-family-waiver") + 1].split())
    sys.stderr.write(
        SAME_FAMILY_MARKER + ": gate model '" + model + "' (family 'anthropic') is the author's "
        "own family 'anthropic' - this verdict is NOT an independent cross-family judgement. "
        "reason: " + reason + "\\n"
    )

if os.environ.get("STUB_FAIL_" + key.upper()):
    sys.stderr.write("cause=provider-unreachable the stub was told to fail\\n")
    sys.exit(2)

# The real runner attributes every REACHED verdict to the model and transport that produced it,
# on stderr, in one line. The stub does the same, so what the hook does with that line is under
# test rather than reimplemented. STUB_NO_ATTRIBUTION reproduces a runner that says nothing.
if not os.environ.get("STUB_NO_ATTRIBUTION"):
    provider = sys.argv[sys.argv.index("--provider") + 1] if "--provider" in sys.argv else "opencode"
    sys.stderr.write(
        ATTRIBUTION_MARKER + ": model=" + model + " family=stub provider=" + provider + "\\n"
    )

payload = os.environ.get(
    "STUB_OBSERVATIONS" if key == "observations" else "STUB_CLASSIFICATIONS", ""
)
json.dump(json.loads(payload) if payload else {{key: []}}, open(out, "w"))
"""

ONE_REQUIREMENT = "- R1.1.1 — `binding: e2e` — the caller sees a greeting\n"

OBSERVATION = json.dumps(
    {
        "observations": [
            {
                "id": "o1",
                "area": "edge-case",
                "spec_ref": "R1.1.1",
                "statement": "No criterion covers a replayed request.",
            },
        ]
    }
)
BLOCKING = json.dumps(
    {
        "classifications": [
            {
                "id": "o1",
                "category": "missing-requirement",
                "why": "Idempotency is in Scope and no requirement states it.",
            },
        ]
    }
)
NOTE_ONLY = json.dumps(
    {
        "classifications": [
            {
                "id": "o1",
                "category": "note",
                "why": "Worth knowing; nothing is blocked.",
            },
        ]
    }
)
INVENTED = json.dumps(
    {
        "classifications": [
            {"id": "o1", "category": "too-vague", "why": "it feels thin"},
        ]
    }
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "scripts" / "gate_runner.py").write_text(
        STUB_RUNNER.format(
            abi=RUNNER_ABI, marker=SAME_FAMILY_MARKER, attribution=ATTRIBUTION_MARKER
        )
    )
    return tmp_path


def write_spec(
    project: Path, *, status: str = "draft", requirements: str = ONE_REQUIREMENT
) -> Path:
    spec = project / "docs" / "spec.md"
    spec.write_text(SPEC.format(status=status, requirements=requirements))
    return spec


def run_hook(project: Path, spec: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_spec_gate.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % spec,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            **env,
        },
    )


def runner_args(project: Path) -> list[list[str]]:
    """Every argument list the stub runner was invoked with, ONE ELEMENT PER ARGUMENT.

    Not a joined string: a reason is prose with spaces in it, and the whole way this can go wrong is
    a shell expansion that splits it into several arguments — which a joined string renders
    identically to the correct call.
    """
    log = project / "scripts" / "gate_runner.py.args"
    return (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )


def calls(project: Path) -> list[str]:
    """Which passes the stub was asked for — how many paid calls actually happened."""
    log = project / "scripts" / "gate_runner.py.calls"
    return log.read_text().split() if log.exists() else []


def models(project: Path) -> dict[str, str]:
    """Which --model each pass was actually invoked with, keyed by --json-key."""
    log = project / "scripts" / "gate_runner.py.models"
    if not log.exists():
        return {}
    return dict(line.split("=", 1) for line in log.read_text().splitlines() if line)


# ── one gate, two passes, in that order ──────────────────────────────────────


def test_an_approved_spec_runs_observe_then_triage_and_stamps_once(
    project: Path,
) -> None:
    spec = write_spec(project)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"]
    assert "spec_gate: approved" in spec.read_text()


def test_the_two_gates_it_replaced_leave_no_second_stamp(project: Path) -> None:
    """The collapse is only real if one gate writes one thing. `fidelity_verdict` is gone."""
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert "fidelity_verdict" not in spec.read_text()


# ── an unset GATE_TRIAGE_MODEL can no longer reach an unconfigured provider (issue #48) ──────


def test_an_unset_triage_model_falls_back_to_the_configured_gate_model(
    project: Path,
) -> None:
    """GATE_MODEL is set and proven reachable; GATE_TRIAGE_MODEL is deliberately left unset — the
    trap this reproduces. Unpatched, the triage pass reaches for a bare `deepseek/deepseek-chat`
    on OpenRouter, a provider the operator never configured. Patched, it reuses GATE_MODEL."""
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        GATE_MODEL="opencode-go/grok-4.5",
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert result.returncode == 0, result.stderr
    seen = models(project)
    assert seen["observations"] == "opencode-go/grok-4.5"
    assert seen["classifications"] == "opencode-go/grok-4.5"
    assert "deepseek" not in seen["classifications"]


def test_an_explicit_triage_model_still_wins_over_the_fallback(project: Path) -> None:
    """The fallback only fires when the operator never chose — an explicit GATE_TRIAGE_MODEL is
    not overridden by GATE_MODEL."""
    spec = write_spec(project)
    run_hook(
        project,
        spec,
        GATE_MODEL="opencode-go/grok-4.5",
        GATE_TRIAGE_MODEL="opencode-go/deepseek-v4-pro",
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    seen = models(project)
    assert seen["observations"] == "opencode-go/grok-4.5"
    assert seen["classifications"] == "opencode-go/deepseek-v4-pro"


def test_both_gate_models_unset_falls_back_to_the_one_documented_default(
    project: Path,
) -> None:
    """Neither variable set — the last resort is the single documented default, the same one the
    observe pass already used, never a third model on a third provider."""
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    seen = models(project)
    assert seen["observations"] == seen["classifications"]
    assert "deepseek" not in seen["classifications"]


# ── the closed set decides, and the script decides it ────────────────────────


def test_a_blocking_classification_blocks_and_shows_its_findings(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING
    )
    assert result.returncode == 2
    assert "missing-requirement" in result.stderr
    assert "No criterion covers a replayed request." in result.stderr
    assert "route back to avenger-spec-writer" in result.stderr
    assert "spec_gate: blocked" in spec.read_text()


def test_notes_never_block_and_land_in_the_known_open_list(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 0, result.stderr
    notes = spec.parent / "spec-notes.md"
    assert notes.is_file()
    assert "No criterion covers a replayed request." in notes.read_text()
    assert "readers: implementer @ once" in notes.read_text()


def test_a_run_with_no_notes_leaves_no_stale_sidecar(project: Path) -> None:
    """A document no stage reads does not get written, and a stale one says something the gate no
    longer says."""
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert (spec.parent / "spec-notes.md").is_file()
    spec.write_text(spec.read_text().replace("greeting", "greeting message"))
    run_hook(
        project,
        spec,
        STUB_OBSERVATIONS='{"observations": []}',
        STUB_CLASSIFICATIONS='{"classifications": []}',
    )
    assert not (spec.parent / "spec-notes.md").exists()


def test_an_invented_category_fails_closed_rather_than_approving(project: Path) -> None:
    spec = write_spec(project)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=INVENTED
    )
    assert result.returncode == 2
    assert "unknown-category" in result.stderr
    assert "spec_gate: approved" not in spec.read_text()


# ── the mechanical checks run first, and cost nothing ────────────────────────


def test_an_over_cap_spec_is_told_to_split_before_any_paid_call(project: Path) -> None:
    """A rejection for size is one more thing to grow around, so this is not a gate verdict at all —
    and it must not spend a model call to say so."""
    over = "".join(
        f"- R1.1.{i} — `binding: e2e` — behavior {i}\n" for i in range(1, 14)
    )
    spec = write_spec(project, requirements=over)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 2
    assert "SPLIT TRIGGER" in result.stderr
    assert "SPLIT this spec — not to shorten it" in result.stderr
    assert calls(project) == [], "the cap must be decided before the gate is paid for"


def test_a_spec_the_cap_cannot_count_is_not_told_to_split(project: Path) -> None:
    """Exit 1 is OVER the cap; exit 2 is "the count could not be decided". Collapsing the two sent
    the writer to SPLIT a document nobody could count — the same distinction the subprocess branch
    already draws: a file it cannot read is a file it cannot clear."""
    prose = "The system must do X (R1.1.1) and then Y (R1.1.2).\n"
    spec = write_spec(project, requirements=prose)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 2
    assert "could not count this spec" in result.stderr
    assert "NOT a split trigger" in result.stderr
    assert "SPLIT this spec" not in result.stderr
    assert calls(project) == [], "a count that failed must not be paid for either"


def test_a_spec_at_the_cap_still_reaches_the_gate(project: Path) -> None:
    at_cap = "".join(
        f"- R1.1.{i} — `binding: e2e` — behavior {i}\n" for i in range(1, 13)
    )
    spec = write_spec(project, requirements=at_cap)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"]


# ── the paid gate is not re-run on an unchanged body ─────────────────────────


def test_a_frontmatter_only_edit_does_not_re_run_the_gate(project: Path) -> None:
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    spec.write_text(spec.read_text().replace("status: draft", "status: done"))
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"], (
        "the gate ran a second time"
    )


def test_an_unchanged_blocked_body_replays_its_block(project: Path) -> None:
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING
    )
    before = list(calls(project))
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 2
    assert "unchanged since it was judged" in result.stderr
    assert calls(project) == before, "a replayed block must not re-roll a fresh verdict"


def undeclared_spawner(project: Path) -> Path:
    """A git repo whose only touched test spawns a process without declaring why."""
    for args in (
        ("init", "-q"),
        ("config", "user.email", "pipeline@example.com"),
        ("config", "user.name", "pipeline"),
        ("commit", "-q", "--allow-empty", "-m", "root"),
    ):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    tests = project / "phase-tests"
    tests.mkdir()
    (tests / "test_spawn.py").write_text(
        "import subprocess\n\n\ndef test_x():\n    subprocess.run(['true'])\n"
    )
    return tests


def test_a_break_glass_over_the_cost_gate_that_cannot_be_logged_blocks(
    project: Path,
) -> None:
    """The one break-glass caller that does not `exec` must carry the writer's refusal itself.

    Without it the hook falls straight through into the rest of the gate on an override that
    reached no line of gate-overrides.log.
    """
    tests = undeclared_spawner(project)
    (project / "gate-overrides.log").mkdir()  # an append to a directory cannot land
    spec = write_spec(project)

    result = run_hook(
        project,
        spec,
        SUBPROC_CHECK_PATHS=str(tests),
        GATE_BYPASS="the spawners are in locked tests",
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )

    assert result.returncode == 2
    assert "NOT LOGGED" in result.stderr
    assert calls(project) == [], "the gate proceeded on an unlogged override"


def test_a_logged_break_glass_over_the_cost_gate_still_falls_through(
    project: Path,
) -> None:
    """The audited path is unchanged: log it and run the rest of the gate."""
    tests = undeclared_spawner(project)
    spec = write_spec(project)

    result = run_hook(
        project,
        spec,
        SUBPROC_CHECK_PATHS=str(tests),
        GATE_BYPASS="the spawners are in locked tests",
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )

    assert result.returncode == 0, result.stderr
    assert calls(project) == ["observations", "classifications"]
    assert "spec-gate-subprocess" in (project / "gate-overrides.log").read_text()


def test_a_replayed_block_still_honours_break_glass(project: Path) -> None:
    """A bypass silently dropped is the same defect as one silently taken."""
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING
    )
    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=BLOCKING,
        GATE_BYPASS="captain says ship it",
    )
    assert result.returncode == 0, result.stderr
    assert (project / "gate-overrides.log").is_file()


# ── fail closed on either pass ───────────────────────────────────────────────


@pytest.mark.parametrize("failing", ["OBSERVATIONS", "CLASSIFICATIONS"])
def test_a_pass_that_does_not_answer_stops_the_turn(
    project: Path, failing: str
) -> None:
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
        **{f"STUB_FAIL_{failing}": "1"},
    )
    assert result.returncode == 2
    assert "provider-unreachable" in result.stderr
    assert "spec_gate: approved" not in spec.read_text()


def test_the_triage_pass_is_given_the_observations_and_the_spec(project: Path) -> None:
    """It classifies what the first pass reported; it does not review the spec again."""
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    targets = (project / "scripts" / "gate_runner.py.targets").read_text()
    triage_input = targets.split("=== END ===")[1]
    assert "## OBSERVATIONS TO CLASSIFY" in triage_input
    assert "o1" in triage_input
    assert "## SPEC THEY WERE MADE AGAINST" in triage_input


# ── the human sign-off is a separate question ────────────────────────────────


def test_interactive_mode_leaves_review_status_for_the_human(project: Path) -> None:
    spec = write_spec(project)
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert "review_status: pending" in spec.read_text()


def test_auto_mode_carries_the_sign_off_because_nobody_is_there(project: Path) -> None:
    spec = write_spec(project)
    run_hook(
        project,
        spec,
        SPEC_REVIEW_MODE="auto",
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert "review_status: approved" in spec.read_text()


# ── the CONTEXT the closed blocking set depends on ───────────────────────────
#
# `contradiction` is one of the four things that block, and it is defined partly as a statement that
# breaks a binding contract the overview or the prior phase's card declares. Nothing assembled those,
# so half that category was undetectable and the closed set was three items and a claim.


def observe_target(project: Path) -> str:
    return (
        (project / "scripts" / "gate_runner.py.targets")
        .read_text()
        .split("=== END ===")[0]
    )


@pytest.fixture
def in_layout(project: Path):
    """A spec inside `docs/features/<f>/phases/<n>-<slug>/specs/…`, with a feature and a prior phase.

    Yields (project, spec, write_context) — the last one adds the overview and the prior card, so a
    test can run the same spec with and without them.
    """
    phases = project / "docs" / "features" / "demo" / "phases"
    spec = phases / "2-api" / "specs" / "2.1-a" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(SPEC.format(status="draft", requirements=ONE_REQUIREMENT))

    def write_context() -> None:
        (phases.parent / "overview.md").write_text(
            "# Demo\n\n## Goal\n\nSHIP-THE-THING\n\n"
            "## Contracts and Decisions\n\n- VAULT-ONLY-TOKENS\n\n## Risks\n\nUNREAD-RISK\n"
        )
        (phases / "1-store").mkdir(parents=True)
        (phases / "1-store" / "handover.md").write_text(
            "# handover\n\n- PUT-IS-IDEMPOTENT\n"
        )
        (phases / "1-store" / "handover-archive.md").write_text("ARCHIVED-DETAIL\n")

    return project, spec, write_context


def test_the_observe_pass_is_given_the_contracts_it_could_be_contradicting(
    in_layout,
) -> None:
    project, spec, write_context = in_layout
    write_context()

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    target = observe_target(project)
    assert "## CONTEXT (reference only)" in target
    assert "VAULT-ONLY-TOKENS" in target
    assert "PUT-IS-IDEMPOTENT" in target
    assert "## SPEC UNDER REVIEW" in target, (
        "without the marker the observe prompt reads the whole input as the spec, and would review "
        "the context as if it were the document under review"
    )


def test_the_context_carries_only_the_extents_the_read_path_declares(in_layout) -> None:
    project, spec, write_context = in_layout
    write_context()

    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    target = observe_target(project)
    assert "SHIP-THE-THING" not in target, (
        "the overview's contracts header only, not the document"
    )
    assert "UNREAD-RISK" not in target
    assert "ARCHIVED-DETAIL" not in target, (
        "handover-archive.md is the half no stage reads"
    )


def test_absent_context_is_normal_and_never_fails_the_gate(in_layout) -> None:
    """Phase 1 has no prior card and a feature may have no contracts section yet. Omitted and named
    on stderr, so a gate running with no context is visible rather than silent."""
    project, spec, _ = in_layout

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    assert "## CONTEXT (reference only)" not in observe_target(project)
    assert "spec-gate context: absent" in result.stderr


def test_the_context_composes_with_the_re_gate_bundle_rather_than_replacing_it(
    in_layout,
) -> None:
    project, spec, write_context = in_layout
    write_context()
    run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    body = spec.read_text().replace("status: draft", "status: done")
    spec.write_text(
        body.replace("the caller sees a greeting", "the caller sees a salutation")
    )

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    target = (
        (project / "scripts" / "gate_runner.py.targets")
        .read_text()
        .split("=== END ===")[2]
    )
    assert "## CONTEXT (reference only)" in target
    assert "## PREVIOUSLY APPROVED (reference only)" in target
    assert "## CHANGES SINCE APPROVAL" in target


def stamped_report(project: Path, spec: Path) -> str:
    """The report this gate persisted with its verdict — what a later read of it sees.

    Read through `spec_gate_cache` rather than by globbing, so the test cannot pass by finding some
    other file the hook happened to leave in the cache directory.
    """
    import spec_gate_cache

    os.environ["CLAUDE_PROJECT_DIR"] = str(project)
    try:
        return spec_gate_cache.cache_path(spec, "gate", "report").read_text(
            encoding="utf-8"
        )
    finally:
        os.environ.pop("CLAUDE_PROJECT_DIR", None)


def no_contracts_heading(spec: Path) -> None:
    """The clickup-agents overview, in shape: real content under headings this reader never finds.

    Not "no contracts written yet" — the heading the CONTEXT block looks for does not exist, and
    will not exist for any spec this feature ever gates (issue #57: eleven phases of it).
    """
    (spec.parents[4] / "overview.md").write_text(
        "# Demo\n\n## Interfaces & contracts\n\n- VAULT-ONLY-TOKENS\n\n"
        "## Key decisions & trade-offs\n\n- POLLING-NOT-WEBHOOKS\n"
    )


def test_an_overview_with_no_contracts_heading_is_loudly_degraded(in_layout) -> None:
    """The silent pass is the defect. Exit 3 out of the context builder must reach the author."""
    project, spec, _ = in_layout
    no_contracts_heading(spec)

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, (
        "the context block is reference-only and never fails the gate"
    )
    assert "CONTEXT DEGRADED" in result.stderr
    assert "## Contracts and Decisions" in result.stderr
    assert "## CONTEXT (reference only)" not in observe_target(project)


def test_the_degraded_state_rides_the_report_an_approval_stamps(in_layout) -> None:
    """Nothing that reads this verdict later sees the hook's stderr, so a warning printed once is a
    warning gone. An approved spec gated with no contracts must not read like a clean pass."""
    project, spec, _ = in_layout
    no_contracts_heading(spec)

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    assert "spec_gate: approved" in spec.read_text()
    assert "CONTEXT DEGRADED" in stamped_report(project, spec)


def test_the_degraded_state_rides_the_report_a_block_stamps(in_layout) -> None:
    """Same on the other verdict — a blocked spec's report is replayed verbatim on the next write,
    which is exactly where "what was this gate actually able to check?" gets asked."""
    project, spec, _ = in_layout
    no_contracts_heading(spec)

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=BLOCKING
    )

    assert result.returncode == 2
    report = stamped_report(project, spec)
    assert "CONTEXT DEGRADED" in report
    assert "missing-requirement" in report, (
        "the fold must not displace the gate's own findings"
    )


def test_the_banner_names_the_shape_that_actually_fired_missing_overview(
    in_layout,
) -> None:
    """Three shapes exit 3 and each has its own remedy. A banner that always says "no heading" tells
    a feature with NO overview at all to fix a heading it does not have a file to put one in - and
    the persisted report is the durable record, so the wrong cause outlives the run."""
    project, spec, _ = in_layout

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    report = stamped_report(project, spec)
    assert "CONTEXT DEGRADED" in report
    assert "no readable overview.md" in report
    assert "no ## Contracts and Decisions heading" not in report


def test_the_banner_names_the_shape_that_actually_fired_boilerplate(in_layout) -> None:
    """The other misreported shape: the heading IS there, holding only the unfilled template's
    comment. "Add the heading" is a remedy already applied."""
    project, spec, _ = in_layout
    (spec.parents[4] / "overview.md").write_text(
        "# Demo\n\n## Contracts and Decisions\n<!-- fill this in -->\n"
    )

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    report = stamped_report(project, spec)
    assert "CONTEXT DEGRADED" in report
    assert "boilerplate" in report


def test_the_banner_fires_for_an_overview_that_is_not_utf8(in_layout) -> None:
    """The third shape, reached through the one input that used to crash the builder instead: an
    `overview.md` whose bytes are not UTF-8 raised out of `read_text` as exit 1, which this hook
    reads as "could not build, treat as absent" — a stamped report indistinguishable from a clean
    pass, on a spec the gate could only half check."""
    project, spec, _ = in_layout
    (spec.parents[4] / "overview.md").write_bytes(b"# Demo\n\n\xff\xfe\n")

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    report = stamped_report(project, spec)
    assert "CONTEXT DEGRADED" in report
    assert "no readable overview.md" in report


def test_a_feature_carrying_the_heading_stamps_no_warning(in_layout) -> None:
    """The control: without it, a test asserting the warning appears proves nothing about when."""
    project, spec, write_context = in_layout
    write_context()

    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )

    assert result.returncode == 0, result.stderr
    assert "CONTEXT DEGRADED" not in result.stderr
    assert "CONTEXT DEGRADED" not in stamped_report(project, spec)


def test_a_write_that_is_not_a_spec_is_ignored(project: Path) -> None:
    other = project / "docs" / "notes.md"
    other.write_text("# not a spec\n")
    result = run_hook(project, other)
    assert result.returncode == 0
    assert calls(project) == []


# ── the measurement the collapse must not lose ───────────────────────────────
#
# This gate replaced two hooks that had just been instrumented, and losing instrumentation in a
# refactor is how a pipeline stops being able to answer whether it improved. Driven end to end
# through the real hook against a double writer, because "the call is in the file" is not the claim
# — the claim is that a spec write leaves numbers behind.


@pytest.fixture
def measured(project: Path, tmp_path: Path):
    """The hook's environment with a double metrics writer wired in. Yields (spec, read_record)."""
    from metrics_support import DOUBLE

    store = tmp_path / "store"
    store.mkdir()
    double = tmp_path / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    spec_dir = (
        project / "docs" / "features" / "demo" / "phases" / "1-demo" / "specs" / "1.1-a"
    )
    spec_dir.mkdir(parents=True)
    spec = spec_dir / "spec.md"
    spec.write_text(SPEC.format(status="draft", requirements=ONE_REQUIREMENT))
    env = {
        "AVENGER_METRICS_CMD": str(double),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(tmp_path / "diagnostics.log"),
        "DOUBLE_LOG": str(tmp_path / "calls.log"),
        "DOUBLE_STORE": str(store),
    }
    return (
        spec,
        env,
        (lambda: json.loads((store / "phase-01.json").read_text(encoding="utf-8"))),
    )


def test_a_spec_write_leaves_its_phase_its_round_and_its_gate_calls(measured) -> None:
    spec, env, record_of = measured
    project = spec.parents[
        5
    ].parent.parent  # …/docs/features/demo/phases/… -> project root

    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
        **env,
    )

    assert result.returncode == 0, result.stderr
    record = record_of()
    assert record["opened"], "a spec write is where the phase record opens"
    assert record["specs"][0]["bytes_by_round"], (
        "the round the ratchet would show up in"
    )
    assert record["specs"][0]["requirements"] == 1
    # The two provider calls record themselves inside the runner, which this test replaces with a
    # stub; what belongs to the HOOK is the deterministic decide step, and it is a stage of its own
    # because the verdict is derived by a script rather than asked of a model.
    stages = {call["stage"] for call in record["gate_calls"]}
    assert "spec-gate-decide" in stages
    decided = next(c for c in record["gate_calls"] if c["stage"] == "spec-gate-decide")
    assert "observations=1 blocking=0 notes=1" in decided["note"]


def test_the_cheapest_rejection_is_still_measured(measured) -> None:
    """The requirement cap runs before any paid call, so an over-cap spec costs nothing — and would
    be the one case that went unmeasured entirely if `phase-open` sat behind it."""
    spec, env, record_of = measured
    project = spec.parents[5].parent.parent
    spec.write_text(
        SPEC.format(
            status="draft",
            requirements="".join(
                f"- R1.1.{n} — `binding: none` — thing {n}\n" for n in range(1, 14)
            ),
        )
    )

    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
        **env,
    )

    assert result.returncode == 2 and "SPLIT" in result.stderr
    assert calls(project) == [], "the cap must cost no provider call"
    assert record_of()["opened"], "and must still leave the phase measured"


def test_a_writer_that_refuses_everything_does_not_fail_the_gate(measured) -> None:
    """Measurement is never a gate. A phase must not stop because a number went unrecorded."""
    spec, env, _ = measured
    project = spec.parents[5].parent.parent

    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
        DOUBLE_EXIT="3",
        **env,
    )

    assert result.returncode == 0, result.stderr
    assert "spec_gate: approved" in spec.read_text()


def test_a_killed_pass_is_recorded_under_the_pass_that_was_killed(measured) -> None:
    """The runner being killed cannot record its own death, so the hook records it — and this gate
    makes TWO calls, so "which one" is the whole fact. Telling a killed hook apart from a model that
    answered NO-GO is what once read for a day as a model size ceiling."""
    import signal
    import time

    spec, env, record_of = measured
    project = spec.parents[5].parent.parent
    runner = project / "scripts" / "gate_runner.py"
    marker = project / "spawned"
    runner.write_text(
        runner.read_text().replace(
            'open(sys.argv[0] + ".calls", "a").write(key + "\\n")',
            'open(sys.argv[0] + ".calls", "a").write(key + "\\n")\n'
            f'open({str(marker)!r}, "w").write("x")\n'
            "import time as _t; _t.sleep(120)",
        )
    )

    hook = subprocess.Popen(  # noqa: S603
        ["bash", str(project / "scripts" / "hook_spec_gate.sh")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(project),
            "CLAUDE_PROJECT_DIR": str(project),
            "STUB_OBSERVATIONS": OBSERVATION,
            "STUB_CLASSIFICATIONS": NOTE_ONLY,
            **env,
        },
    )
    hook.stdin.write('{"tool_input": {"file_path": "%s"}}' % spec)
    hook.stdin.close()
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), "the observe pass never started"
    hook.send_signal(signal.SIGTERM)
    err = hook.stderr.read()
    hook.wait(timeout=30)

    assert hook.returncode == 2
    assert "HOOK KILLED" in err and "NOT a gate verdict" in err
    killed = [c for c in record_of()["gate_calls"] if c["verdict"] == "killed"]
    assert [c["stage"] for c in killed] == ["spec-gate-observe"]


# ── an explicit same-family waiver: honoured, and impossible to read as independent ──────────
#
# The state this closes was a wedge. The hook hands the runner an author family that cannot be
# cleared from outside the plugin, so a Claude gate model fails closed and the only route through
# was to declare a FALSE author family. Both directions are pinned, per issue 69.


WAIVER_REASON = (
    "captain waived cross-family independence for phase 12: no second vendor reachable"
)


def test_no_waiver_means_the_gate_is_handed_none(project: Path) -> None:
    """The default path. Nobody who has not asked for a waiver gets a weaker gate, and no run that
    did not ask for one acquires a waiver record."""
    spec = write_spec(project)
    result = run_hook(
        project, spec, STUB_OBSERVATIONS=OBSERVATION, STUB_CLASSIFICATIONS=NOTE_ONLY
    )
    assert result.returncode == 0, result.stderr
    assert all("--same-family-waiver" not in args for args in runner_args(project))
    assert not (project / "gate-overrides.log").exists()
    assert "SAME-FAMILY WAIVER" not in stamped_report(project, spec)


def test_an_empty_waiver_is_not_a_waiver(project: Path) -> None:
    """`GATE_SAME_FAMILY_WAIVER=` states nothing, so it must not reach the gate as one — the shell
    default that started all of this substituted on unset AND empty."""
    spec = write_spec(project)
    run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER="",
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert all("--same-family-waiver" not in args for args in runner_args(project))


def test_a_waiver_reaches_the_gate_without_touching_who_the_author_is(
    project: Path,
) -> None:
    """The property the phase worker refused to break: waiving the requirement must never be
    implemented by misreporting the author."""
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=WAIVER_REASON,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert result.returncode == 0, result.stderr
    for args in runner_args(project):
        # One argument, not several: the reason is prose, and a split one reaches argparse as stray
        # arguments that fail the call instead of waiving anything.
        assert args[args.index("--same-family-waiver") + 1] == WAIVER_REASON
        assert args[args.index("--author-family") + 1] == "anthropic", (
            "truthfully, still"
        )


def test_a_waived_verdict_is_stamped_as_waived(project: Path) -> None:
    """The load-bearing half. A verdict produced without decorrelation that reads afterwards like an
    independent one would be strictly worse than the refusal it replaced: it converts a loud refusal
    into a quiet false assurance. The banner rides the report the verdict is kept with, because none
    of the later readers see this hook's stderr."""
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=WAIVER_REASON,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert result.returncode == 0, result.stderr
    report = stamped_report(project, spec)
    assert "SAME-FAMILY WAIVER" in report
    assert "NOT an independent cross-family judgement" in report
    assert WAIVER_REASON in report


def test_a_waived_block_is_stamped_as_waived_too(project: Path) -> None:
    """A block is read again on replay, so it carries the disclosure exactly like a pass. A waiver
    that only surfaced on approvals would hide the same-family judgement that routed a spec back."""
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=WAIVER_REASON,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=BLOCKING,
    )
    assert result.returncode == 2
    assert "SAME-FAMILY WAIVER" in stamped_report(project, spec)


def test_a_waiver_is_audited_once_per_run(project: Path) -> None:
    """Audited or not recorded (CLAUDE.md 3a). The gate makes two calls; the override is one act."""
    spec = write_spec(project)
    run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=WAIVER_REASON,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    log = (project / "gate-overrides.log").read_text().splitlines()
    assert len(log) == 1, log
    assert "gate:spec-gate" in log[0] and "finding:cross-family" in log[0]
    assert WAIVER_REASON in log[0]


def test_a_waiver_that_cannot_be_audited_does_not_hold(project: Path) -> None:
    """An override nobody logged is not an override, so the gate stops rather than judging on the
    author's own family with no durable trace of the waiver anywhere."""
    spec = write_spec(project)
    (project / "gate-overrides.log").mkdir()  # the writer cannot append to a directory
    result = run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=WAIVER_REASON,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert result.returncode == 2
    assert "could not be audited" in result.stderr
    assert "spec_gate: approved" not in spec.read_text()


def test_a_waiver_with_no_marker_to_record_it_fails_closed(project: Path) -> None:
    """The runner honours the waiver; this hook is what makes it visible. If it cannot read the
    marker that says a call was waived, a waived verdict would be stamped as an ordinary one — so
    the run stops instead."""
    spec = write_spec(project)
    runner = project / "scripts" / "gate_runner.py"
    runner.write_text(
        runner.read_text().replace("SAME_FAMILY_MARKER = ", "MARKER_MOVED = ")
    )
    result = run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=WAIVER_REASON,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert result.returncode == 2
    assert "could not read the gate runner's" in result.stderr
    assert calls(project) == [], (
        "and it stops BEFORE paying for a call it could not disclose"
    )


def test_a_multi_line_reason_survives_as_one_argument_and_one_log_record(
    project: Path,
) -> None:
    """A reason is prose from a file (CLAUDE.md section 6), so it can carry newlines. It must reach
    the gate as ONE argument, and `gate-overrides.log` is one tab-separated record per line — a raw
    newline there would split one override into two, the second with no timestamp and no scope."""
    spec = write_spec(project)
    reason = "captain waived cross-family for phase 12\n\nno second vendor is reachable"
    result = run_hook(
        project,
        spec,
        GATE_SAME_FAMILY_WAIVER=reason,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
    )
    assert result.returncode == 0, result.stderr
    for args in runner_args(project):
        assert args[args.index("--same-family-waiver") + 1] == reason
    log = (project / "gate-overrides.log").read_text().splitlines()
    assert len(log) == 1, log
    assert "no second vendor is reachable" in log[0]
    # And the durable banner keeps the whole reason rather than its first line.
    assert "no second vendor is reachable" in stamped_report(project, spec)


# ── the verdict names what produced it ───────────────────────────────────────
#
# Phase 13's first spec gate ran on `anthropic/claude-3-haiku` over the OpenRouter transport, and
# that fact survived ONLY because the worker typed it into a status line. Firstmate then had to rule
# on whether that gate stood using prose as the sole evidence of what had judged it.


def attribution(spec: Path) -> str:
    import spec_gate_cache as cache

    return cache.stored_attribution(cache.split_spec(spec.read_text())[0], "gate")


def test_an_approval_records_the_models_and_transports_that_produced_it(
    project: Path,
) -> None:
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
        GATE_MODEL="x/observer",
        GATE_TRIAGE_MODEL="y/triager",
        GATE_PROVIDER="openrouter",
    )
    assert "APPROVED" in result.stderr, result.stderr
    recorded = attribution(spec)
    assert "x/observer" in recorded and "y/triager" in recorded, recorded
    assert "openrouter" in recorded, recorded
    assert "observe" in recorded and "triage" in recorded, recorded


def test_a_block_records_it_too(project: Path) -> None:
    """A rejection is a verdict, and `which model said no` is what a route-back turns on."""
    spec = write_spec(project)
    result = run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=BLOCKING,
        GATE_MODEL="x/observer",
        GATE_PROVIDER="openrouter",
    )
    assert "BLOCKED" in result.stderr, result.stderr
    assert "x/observer" in attribution(spec)


def test_a_runner_that_attributes_nothing_leaves_a_named_state_not_a_silent_one(
    project: Path,
) -> None:
    """The state this replaces, made visible: no attribution reads as `unrecorded` on the verdict,
    never as an ordinary stamp a later reader would take at face value."""
    import spec_gate_cache as cache

    spec = write_spec(project)
    run_hook(
        project,
        spec,
        STUB_OBSERVATIONS=OBSERVATION,
        STUB_CLASSIFICATIONS=NOTE_ONLY,
        GATE_MODEL="x/observer",
        STUB_NO_ATTRIBUTION="1",
    )
    assert attribution(spec) == cache.UNRECORDED
