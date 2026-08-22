"""The handover gate, and the one thing in it that used to report a limit while enforcing none.

`scripts/verifier_attempts.py` decides the 3-attempt cap, and its own tests cover that decision. What
is pinned HERE is that the decision is acted on: the hook called it as `... || true`, printed the cap
notice, and then routed the phase back anyway, so a fourth attempt proceeded with nothing stopping
it — while `CLAUDE.md` and `skills/pipeline-conventions` both say it "stops the loop", and H4 is
measured on `verification_attempts`.

A cap whose test passes against the un-capped code is not a cap, so these drive the real hook.
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

import breaker_gate  # noqa: E402

PASSING = {
    "verdict": "pass",
    "attempt": 3,
    "findings": [],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / "docs" / "features" / "demo" / "phases" / "1-demo").mkdir(parents=True)
    return tmp_path


def phase_dir(project: Path) -> Path:
    return project / "docs" / "features" / "demo" / "phases" / "1-demo"


def write_spec(
    project: Path, spec: str = "1.1-a", *, criticality: str = "standard"
) -> None:
    """A spec that also satisfies `verifier_precheck.py` (Acceptance criteria heading, a fresh gate
    stamp, no untraced requirement ids) — that check is not what these tests are about, so it is
    driven clean the same way `spec_gate_cache.py` itself would leave a spec after an approval."""
    spec_dir = phase_dir(project) / "specs" / spec
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / "spec.md"
    path.write_text(
        f"---\nfeature: demo\nphase: 1-demo\nspec: {spec}\ncriticality: {criticality}\n---\n\n"
        f"# Spec\n\n## Acceptance criteria\n\nDone.\n"
    )
    subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "spec_gate_cache.py"),
            "stamp",
            str(path),
            "gate",
            "APPROVED",
        ],
        check=True,
        capture_output=True,
    )


def record_evidence(project: Path, kind: str = "suite") -> str:
    """Record one real run through `verifier_evidence.py` and return the transcript's chain head.

    A real subprocess, not a hand-written record: that is the whole point of the artifact, and a
    fixture that fabricated one would test the check against exactly the shape it exists to refuse.
    Recorded AFTER the specs are written, because the record binds to their content.
    """
    subprocess.run(
        [sys.executable, str(project / "scripts" / "verifier_evidence.py"),
         "record", str(phase_dir(project)), "--kind", kind, "--", "/bin/echo", "1 passed"],
        cwd=project, check=True, capture_output=True,
    )
    chain = subprocess.run(
        [sys.executable, str(project / "scripts" / "verifier_evidence.py"),
         "chain", str(phase_dir(project))],
        cwd=project, check=True, capture_output=True, text=True,
    )
    return chain.stdout.strip()


def attempts(project: Path, series: list[tuple[int, int, str]]) -> None:
    """Write the phase's verdict history: the archives, then the live verdict as the last entry."""
    for number, findings, result in series[:-1]:
        (phase_dir(project) / f"verdict-attempt-{number}.json").write_text(
            json.dumps(
                {
                    "attempt": number,
                    "verdict": result,
                    "findings": [{"id": f"a{number}-{i}"} for i in range(findings)],
                }
            )
        )
    number, findings, result = series[-1]
    verdict = {
        "attempt": number,
        "verdict": result,
        "findings": [{"id": f"f{i}", "status": "open"} for i in range(findings)],
        "routed": [{"to": "implementer", "reason": "code issue", "finding_id": "f0"}],
    }
    if result == "pass":
        verdict["findings"] = []
        verdict["execution"] = {
            "evidence": "verification-evidence.json",
            "chain": record_evidence(project),
        }
    (phase_dir(project) / "verdict.json").write_text(json.dumps(verdict))


def run_hook(
    project: Path, card: str = "# handover\n\n## Open items\nnone\n", **env: str
) -> subprocess.CompletedProcess:
    handover = phase_dir(project) / "handover.md"
    # A closing phase states what it carries forward, so the card these tests write says `none`
    # explicitly. `tests/test_carried_items.py` owns that rule; here it is only enough card for the
    # hook to reach the attempt cap, which is what this file pins.
    handover.write_text(card)
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_verifier.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % handover,
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


# ── the cap binds, rather than being announced ───────────────────────────────


def test_a_fourth_attempt_is_refused_at_handover(project: Path) -> None:
    """The failure this replaced: the notice printed, then `fail` routed back regardless, and
    attempt 5 followed. Enforcement was an instruction to the model — the exact class this branch
    replaces everywhere else."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail"), (4, 3, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "a further attempt is" in result.stderr and "refused" in result.stderr
    assert "route back per its findings" not in result.stderr, (
        "at the cap the phase does not go round again; it is carried, waived or escalated"
    )


def test_the_cap_names_the_three_honest_ways_out(project: Path) -> None:
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])

    err = run_hook(project).stderr

    assert "KNOWN-OPEN" in err
    assert "waive" in err
    assert "escalate" in err


def test_under_the_cap_the_phase_still_routes_back_normally(project: Path) -> None:
    """The cap must not swallow the ordinary route-back it sits in front of."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "route back per its findings" in result.stderr
    assert "refused" not in result.stderr


def test_a_phase_that_passes_cleanly_on_its_last_allowed_attempt_still_passes(
    project: Path,
) -> None:
    """The cap is on the LOOP, not on the phase. Turning a successful third attempt into a stop
    would be weakening the gate in the other direction."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 0, "pass")])

    result = run_hook(project)

    assert result.returncode == 0, result.stderr


def test_a_record_the_cap_cannot_read_stops_without_claiming_to_be_the_cap(
    project: Path,
) -> None:
    """`CAPPED` is exit 1 and so is an uncaught exception, so a malformed `attempt` arrived here as a
    cap: the handover was refused with "carry, waive or escalate" for a phase that might be on
    attempt 1, whose real problem is a file none of those three remedies can repair."""
    (phase_dir(project) / "verdict.json").write_text(
        json.dumps(
            {
                "attempt": "N/A",
                "verdict": "fail",
                "findings": [{"id": "f0", "status": "open"}],
                "routed": [
                    {"to": "implementer", "reason": "code issue", "finding_id": "f0"}
                ],
            }
        )
    )

    result = run_hook(project)

    assert result.returncode == 2
    assert "could not be DECIDED" in result.stderr
    assert "not the cap itself" in result.stderr
    assert "A fourth attempt is not one of the three" not in result.stderr
    assert "KNOWN-OPEN" not in result.stderr


def test_the_cap_is_escapable_and_audited_rather_than_a_hard_wedge(
    project: Path,
) -> None:
    """Consistent with every other blocking check here: break-glass through the same `fail()` path,
    logged and visible, never silent."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])

    result = run_hook(
        project, GATE_BYPASS="captain accepts the remaining findings as known-open"
    )

    assert result.returncode == 0, result.stderr
    assert (project / "gate-overrides.log").is_file()


# ── a passing phase does not close over what the previous one carried ────────
#
# The other thing this hook now refuses. Phase 8 of one measured feature wrote down that
# caller-supplied identifiers would become a problem in phases 9 to 12; phase 9 was the first such
# caller and shipped exactly that defect, past every gate, because the prediction was prose.
# `tests/test_carried_items.py` owns the rule; what is pinned here is that the hook acts on it, and
# that it does so WITHOUT masking the stop that would otherwise have fired.


def prior_card(project: Path, items: str) -> None:
    """A phase 0 whose card carries `items`, so 1-demo owes it an answer."""
    directory = project / "docs" / "features" / "demo" / "phases" / "0-prior"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "handover.md").write_text(
        f"---\nreaders: x\n---\n# card\n\n## Open items\n{items}\n", encoding="utf-8"
    )


CARRIED = (
    "| id | kind | title | where |\n|---|---|---|---|\n"
    "| FWD-1 | forward-claim | caller-supplied ids reach the path unencoded | this card |"
)


def test_a_passing_phase_does_not_close_over_an_undischarged_carried_item(
    project: Path,
) -> None:
    attempts(project, [(1, 0, "pass")])
    prior_card(project, CARRIED)

    result = run_hook(project)

    assert result.returncode == 2
    assert "FWD-1" in result.stderr
    assert "no answer in this phase" in result.stderr


def test_discharging_the_item_lets_the_phase_close(project: Path) -> None:
    attempts(project, [(1, 0, "pass")])
    prior_card(project, CARRIED)
    subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "carried_items.py"),
            "discharge",
            str(phase_dir(project)),
            "FWD-1",
            "--as",
            "built",
            "--by",
            "R1.1.3",
        ],
        check=True,
        capture_output=True,
    )

    assert run_hook(project).returncode == 0


def test_a_card_that_says_nothing_about_what_it_carries_does_not_close(
    project: Path,
) -> None:
    """Silence is not `none`. A prediction left out of the table is owed to nobody, which is the
    state phase 8's card was in."""
    attempts(project, [(1, 0, "pass")])

    result = run_hook(project, card="# handover\n\nno items section here\n")

    assert result.returncode == 2
    assert "does not say what it carries forward" in result.stderr


LAST_CARD = (
    "---\nnext: e2e\nreaders: x\n---\n# handover\n\n## Open items\n"
    "| id | kind | title | where |\n|---|---|---|---|\n"
    "| FWD-1 | forward-claim | caller-supplied ids reach the path unencoded | %s |\n"
)


def test_the_last_phase_does_not_close_over_a_forward_claim_owed_to_nobody(
    project: Path,
) -> None:
    """`next: e2e` means no phase follows to answer this row, so the claim has to be filed. Asserted
    through the real hook: a rule only its unit test knows about is the promise-with-no-mechanism
    this whole change removes."""
    attempts(project, [(1, 0, "pass")])

    result = run_hook(project, card=LAST_CARD % "this card")

    assert result.returncode == 2
    assert "FWD-1" in result.stderr
    assert "issue reference" in result.stderr


def test_naming_an_issue_on_that_row_lets_the_last_phase_close(project: Path) -> None:
    attempts(project, [(1, 0, "pass")])

    assert run_hook(project, card=LAST_CARD % "filed as #41").returncode == 0


def test_the_carried_gate_never_masks_the_stop_that_would_have_fired(
    project: Path,
) -> None:
    """It runs inside the `pass` branch on purpose. A phase at the attempt cap is already not
    closing, and answering an unasked question there would hide the one that stopped it."""
    attempts(project, [(1, 6, "fail"), (2, 2, "fail"), (3, 8, "fail")])
    prior_card(project, CARRIED)

    result = run_hook(project, card="# handover\n\nno items section here\n")

    assert "refused" in result.stderr
    assert "carries forward" not in result.stderr


# ── the Breaker obligation (issue #45) ────────────────────────────────────────────────────────────
#
# All four phase-8 specs and all four phase-9 specs of one measured feature declared
# `criticality: critical`, which is what routes the Breaker — and it was owed twice and ran neither
# time, with zero trace of it anywhere in the feature's docs or tests. This drives the REAL hook, the
# same discipline the attempt cap above holds itself to: a rule whose test passes against the
# un-enforced hook is not a rule.


def test_a_critical_phase_does_not_close_without_a_breaker_record(
    project: Path,
) -> None:
    """The gap the issue describes, reproduced directly against the hook that is supposed to stop
    it. Without the fix this phase closes silently — exactly what happened twice already."""
    write_spec(project, criticality="critical")
    attempts(project, [(1, 0, "pass")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "Breaker obligation is not met" in result.stderr
    assert "breaker.json" in result.stderr


def write_breaker(project: Path, data: dict) -> None:
    """A record as the Breaker is instructed to write it, `readers` included - the hook must close
    on the same record `doc_read_path.py` accepts, not one it refuses a commit later."""
    record = {"readers": list(breaker_gate.READERS), **data}
    (phase_dir(project) / "breaker.json").write_text(json.dumps(record))


def test_a_valid_breaker_record_lets_a_critical_phase_close(project: Path) -> None:
    write_spec(project, criticality="critical")
    attempts(project, [(1, 0, "pass")])
    write_breaker(project, {"verdict": "clean", "attacked": ["replay", "auth bypass"]})

    result = run_hook(project)

    assert result.returncode == 0, result.stderr


def test_a_breaker_record_declaring_no_readers_does_not_close_the_phase(
    project: Path,
) -> None:
    """Reproduced against the real hook: a record without `readers` used to clear the close and
    then fail `doc_read_path.py` on the very next commit - two gates disagreeing about one file."""
    write_spec(project, criticality="critical")
    attempts(project, [(1, 0, "pass")])
    (phase_dir(project) / "breaker.json").write_text(
        json.dumps({"verdict": "clean", "attacked": ["replay"]})
    )

    result = run_hook(project)

    assert result.returncode == 2
    assert "declares no `readers`" in result.stderr


def test_a_standard_phase_closes_with_no_breaker_record(project: Path) -> None:
    write_spec(project, criticality="standard")
    attempts(project, [(1, 0, "pass")])

    assert run_hook(project).returncode == 0


def test_an_undecidable_breaker_check_is_not_reported_as_a_breaker_that_never_ran(
    project: Path,
) -> None:
    """`breaker_gate.py` separates OWED (exit 1) from an ERROR that could not DECIDE it (exit 2),
    and `gate_ci.sh` honours the split. Collapsed here, the authoritative enforcement point would
    answer an unreadable check with "run the Breaker, or waive it" - neither of which repairs it,
    the same defect the attempt cap shipped once already.

    The real script is defensive enough that exit 2 comes only from its catch-all, so the exit code
    is forced at the dependency; what is under test is the hook's branching on it, and the hook is
    the real one.
    """
    write_spec(project, criticality="critical")
    attempts(project, [(1, 0, "pass")])
    (project / "scripts" / "breaker_gate.py").write_text(
        "import sys\n"
        "print('[breaker_gate] the check could not be decided: boom', file=sys.stderr)\n"
        "raise SystemExit(2)\n"
    )

    result = run_hook(project)

    assert result.returncode == 2
    assert "could not be DECIDED" in result.stderr
    assert "Breaker obligation is not met" not in result.stderr


def test_the_breaker_gate_never_masks_a_failed_suite(project: Path) -> None:
    """Runs inside the `pass` branch on purpose, same as the carried-items gate: a red suite must
    stop the phase for its own reason, not a Breaker record it never got to check."""
    write_spec(project, criticality="critical")
    attempts(project, [(1, 6, "fail")])

    result = run_hook(project)

    assert result.returncode == 2
    assert "route back per its findings" in result.stderr
    assert "Breaker obligation" not in result.stderr


# ── a `status: done` stamp is not trusted on sight (issue #68) ───────────────────────────────────
#
# An implementer writes its own spec's `status: done` and used to keep working afterward —
# test-mapping.md, test-evidence.md and the phase's mutation gate all landed later. A wedge guard
# watching for that stamp in phase 11 fired at 24 minutes while the agent was still running. What is
# pinned here is that the REAL hook now refuses to let a premature stamp stand: it reverts
# `status: done` back to `status: in-progress` before failing, so no Write/Edit/MultiEdit tool call
# can leave a false "done" on disk.
#
# And that it binds only the TRANSITION into `done`: the applicability boundary (§3a) is what stops
# this guard from rewriting a spec that shipped before the rule existed. The stamp is compared with
# the file's committed HEAD version, so these drive a REAL git repository — a stubbed one would test
# a reimplementation of the boundary.


def spec_done_dir(project: Path, spec: str = "1.1-a") -> Path:
    return phase_dir(project) / "specs" / spec


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(project), check=True, capture_output=True)


def commit_docs(project: Path) -> None:
    """Commit whatever is under `docs/` — the HEAD the boundary reads. `scripts/` stays untracked;
    the guard only ever asks git about the spec it was handed."""
    if not (project / ".git").exists():
        git(project, "init", "-q")
        git(project, "config", "user.email", "t@example.com")
        git(project, "config", "user.name", "t")
    git(project, "add", "docs")
    git(project, "commit", "-qm", "docs")


def spec_text(status: str, *, binding: str | None = "integration") -> str:
    """A spec that OWES a mapping row by default — one requirement bound to a test.

    `binding="none"` is the spec the tiered-binding rule (§4a) gives no test and no mapping row: it
    owes no row, so the mapping check may not ask for one. `binding=None` declares no requirement at
    all, which is a malformed spec rather than that exemption.
    """
    requirements = "" if binding is None else f"- R1.1.1 — binding: {binding}\n"
    return (
        f"---\nfeature: demo\nphase: 1-demo\nspec: 1.1-a\nstatus: {status}\n---\n\n"
        f"# Spec\n\n## Requirements\n{requirements}\n"
        "## Acceptance criteria\n\nDone.\n"
    )


def write_done_spec(
    project: Path,
    *,
    mapping: str | None = "header-only",
    head_status: str = "in-progress",
    binding: str | None = "integration",
) -> Path:
    """A spec stamped `status: done` in the worktree, committed at `head_status` — so the default
    is a stamp that JUST landed, and `head_status="done"` is one that shipped before this rule.

    `test-mapping.md` is in one of six states: missing (`None`), header-only, the shipped template
    copied verbatim (`"template"`, placeholder rows and nothing recorded), a real row (`"row"`), a
    real row whose `why` cell carries angle brackets for ordinary reasons
    (`"row-with-generics"`), or a file that cannot be read at all (`"unreadable"`).
    """
    spec_dir = spec_done_dir(project)
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "spec.md"
    spec_path.write_text(spec_text(head_status, binding=binding))
    if mapping == "header-only":
        (spec_dir / "test-mapping.md").write_text(
            "| requirement | test | level | why |\n|---|---|---|---|\n"
        )
    elif mapping == "template":
        (spec_dir / "test-mapping.md").write_text(
            "| requirement id(s) | test name(s) | level | why |\n|---|---|---|---|\n"
            "| R<n>.<k>.<m> | test_<journey> | e2e | the user path both ids sit on |\n"
            "| R<n>.<k>.<m> | test_<name> | integration | drives <seam> with real collaborators |\n"
        )
    elif mapping == "unreadable":
        (spec_dir / "test-mapping.md").write_bytes(
            b"| R1.1.1 | test_x | integration | \xff\xfe |\n"
        )
    elif mapping == "row":
        (spec_dir / "test-mapping.md").write_text(
            "| requirement | test | level | why |\n|---|---|---|---|\n"
            "| R1.1.1 | test_x.py::test_it | integration | ... |\n"
        )
    elif mapping == "row-with-generics":
        (spec_dir / "test-mapping.md").write_text(
            "| requirement | test | level | why |\n|---|---|---|---|\n"
            "| R1.1.1 | test_parse | integration | drives parse(): Result<Config, Error> |\n"
        )
    commit_docs(project)
    spec_path.write_text(spec_text("done", binding=binding))
    return spec_path


def write_phase_tests(project: Path, *, passing: bool) -> None:
    tests_dir = project / "tests" / "demo" / "1-demo"
    tests_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "def test_it():\n    assert True\n"
        if passing
        else "def test_it():\n    assert False\n"
    )
    (tests_dir / "test_x.py").write_text(body)


def run_spec_done_hook(
    project: Path, spec_path: Path, **env: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_verifier.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % spec_path,
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


def test_a_stamp_with_no_recorded_mapping_is_reverted(project: Path) -> None:
    """Break the guard's happy path: stamp `done` before a single test is mapped. Without the fix
    this stays `status: done` on disk forever — the exact near miss issue #68 describes."""
    spec_path = write_done_spec(project, mapping="header-only")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "records no" in result.stderr
    text = spec_path.read_text()
    assert "status: in-progress" in text
    assert "status: done" not in text


def test_a_stamp_over_the_template_copied_verbatim_is_reverted(project: Path) -> None:
    """The flow this issue actually describes: the implementer copies the mapping template (which
    ships placeholder ROWS), records nothing, and stamps. A row count passes that; this must not."""
    spec_path = write_done_spec(project, mapping="template")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "placeholder" in result.stderr
    assert "status: in-progress" in spec_path.read_text()


def test_a_real_row_carrying_generics_keeps_its_stamp(project: Path) -> None:
    """The false-positive direction, through the real hook. `Result<Config, Error>` in a `why` cell
    is ordinary in the TS/Java/Rust repos this pipeline is vendored into; read as a template
    placeholder it reverts a spec that recorded its test properly — worse than the gap it closes."""
    spec_path = write_done_spec(project, mapping="row-with-generics")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 0, result.stderr
    assert "status: done" in spec_path.read_text()


def test_an_unreadable_mapping_does_not_revert_the_stamp(project: Path) -> None:
    """A non-UTF-8 mapping is a check that could not run, not an empty one. Reverting there rewrites
    a spec whose mapping may be full of rows and prescribes a remedy that cannot repair it."""
    spec_path = write_done_spec(project, mapping="unreadable")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "could not be DECIDED" in result.stderr
    assert "records no" not in result.stderr
    assert "status: done" in spec_path.read_text()


def test_a_stamp_with_no_mapping_file_at_all_is_reverted(project: Path) -> None:
    spec_path = write_done_spec(project, mapping=None)
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "status: in-progress" in spec_path.read_text()


def test_a_stamp_over_a_red_suite_is_reverted(project: Path) -> None:
    """Mapping recorded, but the tests it maps to don't pass — still not done, still reverted."""
    spec_path = write_done_spec(project, mapping="row")
    write_phase_tests(project, passing=False)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "the suite is RED" in result.stderr
    text = spec_path.read_text()
    assert "status: in-progress" in text
    assert "status: done" not in text


def test_a_genuinely_complete_spec_keeps_its_stamp(project: Path) -> None:
    """The guard must not fire on the happy path — proof it discriminates, not just blocks."""
    spec_path = write_done_spec(project, mapping="row")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 0, result.stderr
    assert "status: done" in spec_path.read_text()


def test_a_stamp_already_done_at_head_is_counted_and_never_rewritten(
    project: Path,
) -> None:
    """The wedge: a spec whose `done` predates this rule — vendored into a consumer repo, or a
    verified phase reopened by an amendment — must not be rewritten to `in-progress`. That stamp is
    the ONLY evidence `applicability.spec_shipped` reads, and no exception can be recorded for this
    rule, so a revert here has no remedy and loops on every re-stamp."""
    spec_path = write_done_spec(project, mapping="header-only", head_status="done")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 0, result.stderr
    assert "status: done" in spec_path.read_text()
    assert "NOT enforced" in result.stderr
    assert "records no" not in result.stderr


def test_a_shipped_spec_over_a_red_suite_still_keeps_its_stamp(project: Path) -> None:
    """The suite check pre-dates this guard and still stops the phase — but the REVERT is the part
    bound by the boundary, so a shipped spec does not lose its stamp to somebody else's red test."""
    spec_path = write_done_spec(project, mapping="row", head_status="done")
    write_phase_tests(project, passing=False)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "the suite is RED" in result.stderr
    assert "status: done" in spec_path.read_text()


def test_a_spec_owed_no_mapping_row_keeps_its_stamp_with_no_mapping_at_all(
    project: Path,
) -> None:
    """The tiered-binding rule (§4a) gives a `binding: none` requirement no test and no mapping row,
    so this spec's absent mapping is correct. Reverting it prescribes "finish recording the mapping"
    — inventing a row for a test the rules forbid — and there is no way out of that loop: no
    exception is recordable for `spec-done`, and re-stamping re-fires the hook."""
    spec_path = write_done_spec(project, mapping=None, binding="none")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 0, result.stderr
    assert "status: done" in spec_path.read_text()
    assert "records no" not in result.stderr


def test_a_spec_declaring_no_requirements_gets_no_free_pass_from_the_exemption(
    project: Path,
) -> None:
    """The exemption's mirror. An all-`binding: none` spec and a spec declaring nothing produce the
    same empty binding list, so reading one as the other would let a `done` stamp stand over an
    absent mapping — issue #68's own state. This is its own stop, with the remedy that exists."""
    spec_path = write_done_spec(project, mapping=None, binding=None)
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "declares no requirement at all" in result.stderr
    assert "status: done" in spec_path.read_text()
    assert "records no" not in result.stderr


def test_an_unresolvable_test_dir_does_not_revert_over_an_unrelated_red_test(
    project: Path,
) -> None:
    """The revert is spec-scoped, so its evidence must be too. With no phase test directory the hook
    runs the whole repository minus e2e — the permanent state of a project whose tests live anywhere
    else — and one unrelated failure there says nothing about whether THIS spec is done, while the
    implementer is told outright that pre-existing failures are expected and are to be surfaced."""
    spec_path = write_done_spec(project, mapping="row")
    other = project / "tests" / "unrelated"
    other.mkdir(parents=True)
    (other / "test_other.py").write_text("def test_it():\n    assert False\n")

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "could not be" in result.stderr
    assert "RESOLVED" in result.stderr
    assert "status: done" in spec_path.read_text()


def test_break_glass_leaves_the_stamp_as_written_rather_than_reverting_underneath_it(
    project: Path,
) -> None:
    """`fail()` hands off to bypass_log.sh, which exits 0 — so a revert that ran first cleared the
    failure and left the spec at `in-progress` anyway: an override with no reachable end state,
    since re-stamping re-fires the hook and `spec-done` has no disclosed-exception route. A bypass an
    operator cannot act on is a trap, so break-glass restores what it overrides."""
    spec_path = write_done_spec(project, mapping="header-only")
    write_phase_tests(project, passing=True)

    result = run_spec_done_hook(
        project,
        spec_path,
        GATE_BYPASS="captain accepts this stamp; mapping lands next commit",
    )

    assert result.returncode == 0, result.stderr
    assert "status: done" in spec_path.read_text()
    assert "BYPASSED" in result.stderr
    assert "LEFT AS WRITTEN" in result.stderr
    assert (
        "gate:verifier:spec-done-mapping"
        in (project / "gate-overrides.log").read_text()
    )


def test_break_glass_over_a_red_phase_suite_also_leaves_the_stamp_done(
    project: Path,
) -> None:
    """The second revert site. Both must agree, or break-glass means something different depending
    on which check happened to fire first."""
    spec_path = write_done_spec(project, mapping="row")
    write_phase_tests(project, passing=False)

    result = run_spec_done_hook(
        project,
        spec_path,
        GATE_BYPASS="captain accepts the red test; fix lands next commit",
    )

    assert result.returncode == 0, result.stderr
    assert "status: done" in spec_path.read_text()


def test_an_undecidable_stamp_check_does_not_rewrite_the_spec(project: Path) -> None:
    """Exit 1 is the boundary; anything else could not DECIDE it. Collapsed into one branch, an
    unreadable check prescribed "finish recording the mapping" — a remedy that cannot repair it —
    and rewrote the spec on the strength of a check that never ran."""
    spec_path = write_done_spec(project, mapping="row")
    write_phase_tests(project, passing=True)
    (project / "scripts" / "spec_done_guard.py").write_text(
        "import sys\n"
        "print('[spec-done] the check could not be decided: boom', file=sys.stderr)\n"
        "raise SystemExit(2)\n"
    )

    result = run_spec_done_hook(project, spec_path)

    assert result.returncode == 2
    assert "could not be DECIDED" in result.stderr
    assert "records no" not in result.stderr
    assert "status: done" in spec_path.read_text()


# ── a pass must prove it EXECUTED ────────────────────────────────────────────────────────────────
#
# This is the check that replaced `test_quality.reviewed`, a boolean the verifying agent wrote about
# itself and which both this hook and CI accepted as the phase's independence. Each test below drives
# the hook RED on a state that must not close, then repairs exactly that state and drives it GREEN —
# issue #69's rule that a guard proven only by passing is not proven.


def test_a_passing_verdict_with_no_transcript_does_not_close_the_phase(project: Path) -> None:
    """The exact state every pre-existing pass was in: a verdict saying `pass`, and nothing else."""
    write_spec(project)
    (phase_dir(project) / "verdict.json").write_text(json.dumps({
        "verdict": "pass", "attempt": 1, "findings": [],
    }))
    refused = run_hook(project)
    assert refused.returncode == 2
    assert "no execution evidence" in refused.stderr.lower() or "proves the verification ran" in refused.stderr
    assert "verification-evidence.json" in refused.stderr, "the refusal names the artifact"

    # Repair it the way the message prescribes, and nothing else changes.
    chain = record_evidence(project)
    (phase_dir(project) / "verdict.json").write_text(json.dumps({
        "verdict": "pass", "attempt": 1, "findings": [],
        "execution": {"evidence": "verification-evidence.json", "chain": chain},
    }))
    assert run_hook(project).returncode == 0


def test_a_verdict_that_names_a_different_transcript_does_not_close_the_phase(project: Path) -> None:
    """A verdict written against one set of runs cannot be paired with another."""
    write_spec(project)
    record_evidence(project)
    (phase_dir(project) / "verdict.json").write_text(json.dumps({
        "verdict": "pass", "attempt": 1, "findings": [],
        "execution": {"evidence": "verification-evidence.json", "chain": "0" * 64},
    }))
    refused = run_hook(project)
    assert refused.returncode == 2
    assert "different set of runs" in refused.stderr


def test_an_unreadable_transcript_is_not_reported_as_a_missing_one(project: Path) -> None:
    """Two different stops with two different remedies. Collapsed into one, a corrupt record is
    reported as evidence that was never recorded, and 'run the commands again' cannot repair JSON."""
    write_spec(project)
    chain = record_evidence(project)
    (phase_dir(project) / "verification-evidence.json").write_text("{ not json")
    (phase_dir(project) / "verdict.json").write_text(json.dumps({
        "verdict": "pass", "attempt": 1, "findings": [],
        "execution": {"evidence": "verification-evidence.json", "chain": chain},
    }))
    refused = run_hook(project)
    assert refused.returncode == 2
    assert "could not be DECIDED" in refused.stderr
    assert "recording a run will not repair it" in refused.stderr


# ── the Verifier's defects are emitted where they are CONCLUDED, not at the close ────────────────


def run_hook_on(project: Path, path: Path, **env: str) -> subprocess.CompletedProcess:
    """Drive the hook for an arbitrary written file, the way the harness does."""
    return subprocess.run(
        ["bash", str(project / "scripts" / "hook_verifier.sh")],
        input='{"tool_input": {"file_path": "%s"}}' % path,
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


def metrics_double(project: Path) -> dict[str, str]:
    """Point the sink at the shared test double, in this project's own store."""
    sys.path.insert(0, str(ROOT / "tests"))
    from metrics_support import DOUBLE

    double = project / "fm-pipeline-metrics.sh"
    double.write_text(DOUBLE, encoding="utf-8")
    double.chmod(0o755)
    (project / "store").mkdir(exist_ok=True)
    return {
        "AVENGER_METRICS_CMD": str(double),
        "AVENGER_METRICS_PROJECT": "unit-test",
        "AVENGER_METRICS_LOG": str(project / "metrics.log"),
        "DOUBLE_LOG": str(project / "calls.log"),
        "DOUBLE_STORE": str(project / "store"),
    }


def test_a_failing_verdict_emits_its_defects_when_it_is_written(project: Path) -> None:
    """The fact is decided when the Verifier writes the verdict, so that is where it is emitted.

    Emitted only at the handover, a finding raised on attempt 1 and fixed by attempt 2 is gone from
    `verdict.json` before anything reads it — which is how phase 12 closed reporting one defect
    against at least five, four of them the Verifier's own.
    """
    env = metrics_double(project)
    verdict = phase_dir(project) / "verdict.json"
    verdict.write_text(json.dumps({"attempt": 1, "verdict": "fail", "findings": [
        {"id": "aaa", "kind": "code", "instruction": "poll before sleep opens a second session"},
        {"id": "bbb", "kind": "code", "instruction": "InvalidToken raised outside the catch"},
    ]}))

    result = run_hook_on(project, verdict, **env)

    assert result.returncode == 0, result.stderr
    record = json.loads((project / "store" / "phase-01.json").read_text())
    assert {d["id"] for d in record["defects"]} == {"verifier-aaa", "verifier-bbb"}
    assert all(d["found_by"] == "verifier" for d in record["defects"])


def test_emitting_a_verdict_never_gates_anything(project: Path) -> None:
    """Measurement, not a gate: an unwritable record does not stop the Verifier writing a verdict."""
    verdict = phase_dir(project) / "verdict.json"
    verdict.write_text(json.dumps({"attempt": 1, "verdict": "fail", "findings": [{"id": "aaa"}]}))

    assert run_hook_on(project, verdict, AVENGER_METRICS_OFF="1").returncode == 0


def test_a_phase_does_not_close_reporting_fewer_defects_than_its_verdicts_describe(
    project: Path,
) -> None:
    """The check that makes the absence visible. Silence used to read as a clean phase.

    The emission is fail-open by design — measurement may never fail a phase — so on its own a
    writer that refuses every entry looks exactly like a phase that found nothing. That is the
    state reproduced here: the record exists, the Verifier concluded six findings across two
    attempts, and every `add` is refused.
    """
    env = metrics_double(project)
    write_spec(project)
    attempts(project, [(1, 6, "fail"), (2, 0, "pass")])
    subprocess.run(
        [sys.executable, str(project / "scripts" / "pipeline_metrics.py"), "phase-open",
         str(phase_dir(project))],
        cwd=project, check=False, capture_output=True,
        env={"PATH": os.environ["PATH"], "HOME": str(project),
             "CLAUDE_PROJECT_DIR": str(project), **env},
    )

    result = run_hook(project, DOUBLE_REFUSE="add", **env)

    assert result.returncode == 2
    assert "fewer defects" in result.stderr
    assert "a1-0" in result.stderr, "the check names what was concluded and never recorded"


def test_a_phase_whose_defects_are_all_recorded_closes(project: Path) -> None:
    """Mutation guard for the check above: it must pass when the producer actually produced."""
    env = metrics_double(project)
    write_spec(project)
    attempts(project, [(1, 6, "fail"), (2, 0, "pass")])

    result = run_hook(project, **env)

    assert result.returncode == 0, result.stderr
    record = json.loads((project / "store" / "phase-01.json").read_text())
    assert len(record["defects"]) == 6, "the close-time emission fills the record it is checked on"
