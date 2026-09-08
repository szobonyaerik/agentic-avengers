"""Every way the gate can fail, driven through the real runner, each naming itself.

This is the end-to-end half of the failure taxonomy: `tests/test_gate_errors.py` pins the classifier,
this pins what an operator actually sees when the gate is invoked and the provider misbehaves. The
`opencode` binary is replaced on PATH by a stub per failure mode — no model is called, and the
runner does not know the difference.

The timeout case is also the money case: the stub leaves a grandchild behind, and the test asserts
the reported duration is the measured one and that nothing survives the kill.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "gate_runner.py"

sys.path.insert(0, str(ROOT / "scripts"))

pytestmark = pytest.mark.subprocess(
    "the subject is the runner's process handling and its CLI-facing failure output"
)

STUBS = {
    # A provider that refuses for billing reasons. The shape that was read as a model rejection.
    "payment": '#!/bin/sh\necho \'{"error":{"code":402,"message":"Insufficient credits"}}\' >&2\nexit 1\n',
    # A provider that cannot be reached at all.
    "unreachable": "#!/bin/sh\necho 'curl: (7) Failed to connect to openrouter.ai port 443: "
    "Connection refused' >&2\nexit 1\n",
    # A provider that answers - a real text event, the shape a reply has under `--format json` -
    # but with nothing a verdict can be read out of.
    "chatty": '#!/bin/sh\necho \'{"type":"text","part":{"id":"p1","text":"I had a think about it '
    "and it seems fine to me.\"}}'\n",
    # An exhausted tier (issue #98): the CLI prints its banner, exits 0 with NO reply content, and a
    # leftover worker keeps the stdout pipe open, so the call outlives its budget with nothing in it.
    "exhausted": '#!/bin/sh\necho "opencode v1.2.3 - free tier"\nsleep 120 &\n'
    'echo "$!" > "$PIDFILE"\nexit 0\n',
    # The same emptiness without the held pipe: exit 0, banner, nothing else, instantly.
    "exhausted_fast": '#!/bin/sh\necho "opencode v1.2.3 - free tier"\nexit 0\n',
    # A provider that wedges, leaving a grandchild holding the pipe.
    "wedged": '#!/bin/sh\nsleep 120 &\necho "$!" > "$PIDFILE"\nsleep 120\n',
    # Local SQLite lock contention, in its fast shape: it fails in about 0.6s naming the lock.
    "locked": "#!/bin/sh\necho 'Error: SQLITE_BUSY: database is locked' >&2\nexit 1\n",
    # The same contention in its SLOW shape: it holds the lock and wedges, so the call is killed by
    # the budget and the lock message only ever reaches stderr. Reported as `timeout`, this is the
    # shape that most invites the wrong diagnosis, and it was diagnosed wrongly twice.
    "locked_wedged": '#!/bin/sh\necho "database is locked" >&2\nsleep 120 &\n'
    'echo "$!" > "$PIDFILE"\nsleep 120\n',
    # A provider that answers properly, so the success path is covered by the same harness.
    "good": '#!/bin/sh\necho \'{"verdict":"GO","report":"fine","route_back":""}\'\n',
}


@pytest.fixture
def gate(tmp_path: Path):
    """Run the real gate runner with a stubbed `opencode` on PATH."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "rubric.md").write_text("Return JSON with a verdict.")
    (tmp_path / "spec.md").write_text(
        "---\nfeature: demo\n---\n\n- R1.1.1 a requirement\n"
    )

    def run(
        stub: str,
        author_family: str = "anthropic",
        model: str = "deepseek/deepseek-chat",
        **env_over,
    ) -> subprocess.CompletedProcess:
        binary = tmp_path / "bin" / "opencode"
        binary.write_text(STUBS[stub])
        binary.chmod(0o755)
        env = {
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            # An empty project dir, so no repo .env leaks into the run.
            "CLAUDE_PROJECT_DIR": str(tmp_path),
            "PIDFILE": str(tmp_path / "grandchild.pid"),
            # The stub answers in milliseconds, which is precisely what
            # `scripts/gate_plausibility.py` refuses as a call that cannot have happened. That
            # refusal has its own end-to-end tests (tests/test_gate_plausibility.py); here it is
            # switched off so these tests keep pinning the failure taxonomy rather than the floor.
            "GATE_MIN_LATENCY_MS": "0",
            **env_over,
        }
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--rubric",
                str(tmp_path / "rubric.md"),
                "--target",
                str(tmp_path / "spec.md"),
                "--author-family",
                author_family,
                *(["--model", model] if model else []),
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    run.tmp = tmp_path  # type: ignore[attr-defined]
    return run


def test_a_billing_refusal_names_billing_and_shows_the_provider(gate) -> None:
    """Reading this as 'the gate model rejected the spec' cost about a day."""
    result = gate("payment")
    assert result.returncode == 2
    assert "cause=provider-payment-required" in result.stderr
    assert "Insufficient credits" in result.stderr, (
        "the provider's own words must survive"
    )


def test_an_unreachable_provider_is_not_confused_with_a_rejection(gate) -> None:
    result = gate("unreachable")
    assert result.returncode == 2
    assert "cause=provider-unreachable" in result.stderr
    assert "Connection refused" in result.stderr


def test_a_reply_with_no_verdict_is_its_own_cause(gate) -> None:
    result = gate("chatty")
    assert result.returncode == 2
    assert "cause=no-verdict" in result.stderr
    assert "I had a think about it" in result.stderr


def test_a_missing_provider_cli_is_its_own_cause(gate) -> None:
    result = gate("good", PATH="/nonexistent")
    assert result.returncode == 2
    assert "cause=provider-not-found" in result.stderr


def test_a_same_family_model_is_still_refused_by_name(gate) -> None:
    result = gate("good", author_family="deepseek")
    assert result.returncode == 2
    assert "cause=cross-family" in result.stderr


def test_a_timeout_names_itself_reports_measured_time_and_leaves_nothing_running(
    gate,
) -> None:
    """The three properties that were all wrong at once: the cause was unnamed, the duration was the
    configured constant rather than the run's, and the work carried on billing after the report."""
    start = time.monotonic()
    result = gate("wedged", GATE_CALL_TIMEOUT="2")
    wall = time.monotonic() - start

    assert result.returncode == 2
    assert "cause=timeout" in result.stderr
    assert "wall clock" in result.stderr

    reported = float(result.stderr.split("after ")[-1].split("s of wall clock")[0])
    assert reported == pytest.approx(wall, abs=1.5), (
        "the reported duration is not the measured one"
    )
    assert reported < 12, f"a 2s budget must not report {reported}s"

    grandchild = int((gate.tmp / "grandchild.pid").read_text().strip())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, 9)
        pytest.fail(
            f"grandchild {grandchild} outlived the timeout — still running, still spending"
        )


# --- an empty reply is its own cause, never a timeout (issue #98) ---------------------------------


def test_an_exhausted_tier_holding_the_pipe_is_named_as_empty_not_timeout(gate) -> None:
    """THE measured case. `opencode run` on an empty free tier exits 0 with its banner and no
    content, a worker holds the pipe, the budget elapses, and the gate reported `cause=timeout` -
    whose remedy, raising the budget, cannot work. The direct child's own exit (0) is what tells a
    call that ENDED from one still running, and the leftover worker is still killed."""
    result = gate("exhausted", GATE_CALL_TIMEOUT="2")
    assert result.returncode == 2
    assert "cause=provider-empty-response" in result.stderr, result.stderr
    assert "cause=timeout" not in result.stderr
    assert "raising GATE_CALL_TIMEOUT cannot help" in result.stderr
    assert "free tier" in result.stderr  # the provider's own words, verbatim

    grandchild = int((gate.tmp / "grandchild.pid").read_text().strip())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, 9)
        pytest.fail(f"grandchild {grandchild} outlived the call")


def test_an_empty_reply_that_returns_promptly_is_the_same_cause(gate) -> None:
    result = gate("exhausted_fast")
    assert result.returncode == 2
    assert "cause=provider-empty-response" in result.stderr, result.stderr
    assert "cause=no-verdict" not in result.stderr


def test_a_slow_provider_still_running_at_the_budget_is_still_a_timeout(gate) -> None:
    """What must remain possible: a timeout that IS a slow provider is reported as one. The wedged
    stub is still running when the group is killed, so its exit is the signal, never 0."""
    result = gate("wedged", GATE_CALL_TIMEOUT="2")
    assert "cause=timeout" in result.stderr
    assert "cause=provider-empty-response" not in result.stderr


def test_the_success_path_is_unchanged_by_all_of_this(gate) -> None:
    result = gate("good")
    assert result.returncode == 0, result.stderr
    assert "OK (GO)" in result.stdout


def test_no_model_anywhere_is_refused_by_name_rather_than_defaulted(gate) -> None:
    """The runner used to carry `deepseek/deepseek-chat` as its `--model` default, so an invocation
    that named no model reached OpenRouter regardless of GATE_PROVIDER — the same shape as issue #48
    one layer down from the spec gate. With no --model and no GATE_MODEL there is now no model at
    all, and the gate says so instead of choosing one."""
    result = gate("good", model="")
    assert result.returncode == 2
    assert "cause=config" in result.stderr
    assert "GATE_MODEL" in result.stderr
    assert "deepseek" not in result.stderr, "no model id may be resolved from thin air"


def test_gate_model_supplies_the_model_when_the_caller_names_none(gate) -> None:
    """The fallback is the operator's own configured model, never a hardcoded one: GATE_MODEL is
    what they already chose and already proved reachable."""
    result = gate("good", model="", GATE_MODEL="deepseek/deepseek-chat")
    assert result.returncode == 0, result.stderr


def test_the_runner_refuses_a_bare_cli_invocation_with_no_model_before_any_provider(
    tmp_path: Path,
) -> None:
    """The runner's own refusal, proven at the CLI and through no caller.

    Every in-repo caller (`hook_spec_gate.sh`, `gate_ci.sh`) resolves a model
    from its own documented default before it ever spawns this script, so nothing in the pipeline
    reaches this branch today and no caller-driven test can cover it. It is kept as defence in depth
    for a future direct caller, and defence nothing exercises is a claim — so this invokes
    `gate_runner.py` the way such a caller would: argv carrying no `--model` at all, an environment
    carrying no GATE_MODEL, and a PATH with no provider CLI on it.

    What it pins is that the refusal is the FIRST thing that happens. With no `opencode` reachable,
    a runner that resolved a model from thin air would fail as `provider-not-found` — a message
    naming the operator's PATH for a defect in their configuration."""
    (tmp_path / "rubric.md").write_text("Return JSON with a verdict.")
    (tmp_path / "spec.md").write_text(
        "---\nfeature: demo\n---\n\n- R1.1.1 a requirement\n"
    )
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--rubric",
            str(tmp_path / "rubric.md"),
            "--target",
            str(tmp_path / "spec.md"),
            "--author-family",
            "anthropic",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": str(empty_bin),
            "HOME": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        },
    )

    assert result.returncode == 2
    assert "cause=config" in result.stderr
    assert "a gate must never run on one nobody chose" in result.stderr
    assert "--model" in result.stderr and "GATE_MODEL" in result.stderr, (
        "name both ways out"
    )
    assert "provider-not-found" not in result.stderr, (
        "it must refuse BEFORE reaching a provider"
    )
    for invented in ("deepseek", "gemini", "openrouter"):
        assert invented not in result.stderr.lower(), (
            "no model or provider may come from thin air"
        )


# ── local lock contention (issue #50) ────────────────────────────────────────


def test_local_lock_contention_names_the_lock_not_the_provider(gate) -> None:
    """Three parallel gate calls produced ZERO verdicts in 900s; the same three serially produced
    all three in 68s. The mechanism was a lock on the operator's own disk, and reporting it as a
    generic provider error sends them to the provider's status page."""
    result = gate("locked")
    assert result.returncode == 2
    assert "cause=provider-locked" in result.stderr
    assert "database is locked" in result.stderr


def test_a_lock_that_wedges_is_not_reported_as_a_bare_timeout(gate) -> None:
    """The dangerous shape. Phase 8 attributed this to a provider-wide outage and adopted
    'gate serially' on that basis - the right rule for the wrong reason - and a phase-9 worker
    withdrew a provider-drain diagnosis on the same evidence."""
    result = gate("locked_wedged", GATE_CALL_TIMEOUT="2")
    assert result.returncode == 2
    assert "cause=provider-locked" in result.stderr
    assert "database is locked" in result.stderr


# A placeholder value must stop a run rather than become its configuration (issue #108). The
# templates ship `REPLACE_ME` in every value the operator must fill in, and an unfilled `.env` used
# to reach the provider as a credential - answered with an authentication error that says nothing
# about the file that caused it. These run on the stubbed `opencode` provider, so the key they are
# pinned on is one that provider actually reads.
def test_a_replace_me_value_in_the_env_stops_the_run_before_any_call(gate) -> None:
    (gate.tmp / ".env").write_text("GATE_MODEL=vendor/REPLACE_ME\n", encoding="utf-8")
    result = gate("good")
    assert result.returncode == 2
    assert "cause=config" in result.stderr
    assert "GATE_MODEL" in result.stderr
    assert "REPLACE_ME" in result.stderr


def test_the_openrouter_key_stops_a_run_on_the_default_provider(gate) -> None:
    """This gate runs the stubbed `opencode` provider - the runner's own default - and the child
    inherits the environment verbatim (`proc_group.run_bounded` passes no `env=`), which is one of
    the two documented ways to authenticate opencode. So a placeholder here DOES reach a run, and
    must be refused before the call rather than come back as an auth error naming no file."""
    result = gate("good", OPENROUTER_API_KEY="sk-or-v1-REPLACE_ME")
    assert result.returncode == 2
    assert "cause=config" in result.stderr
    assert "OPENROUTER_API_KEY" in result.stderr


def test_the_projects_own_env_is_in_scope_too(gate) -> None:
    """The operator wrote that file, so every key in it is theirs to fill in - the scoping narrows
    what is read out of the real ENVIRONMENT, never what the project itself declared."""
    (gate.tmp / ".env").write_text(
        "OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME\n", encoding="utf-8"
    )
    result = gate("good")
    assert result.returncode == 2
    assert "OPENROUTER_API_KEY" in result.stderr


def test_a_filled_in_env_reaches_the_provider(gate) -> None:
    (gate.tmp / ".env").write_text(
        "OPENROUTER_API_KEY=sk-or-v1-real\n", encoding="utf-8"
    )
    assert gate("good").returncode == 0
