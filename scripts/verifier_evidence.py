#!/usr/bin/env python3
"""The Verifier's proof that it executed - a recorded transcript, bound to the code it judged.

## The defect

A verification verdict was consumable with no evidence that anything ran. `verdict.json` carried
`test_quality.reviewed: true`, a boolean the verifying agent wrote about itself, and both
`hook_verifier.sh` and `gate_ci.sh` accepted it as the phase's independence. A stage that skipped its
work and wrote an optimistic stamp was indistinguishable from one that did the work - the same shape
as issue #45 (a Breaker owed twice, run neither time, with zero trace) and issue #69's standing rule
that a check proven only by passing is not proven.

**"The model said GO" is no longer sufficient on its own.** A pass now has to point at a transcript
that a recorder produced by actually running commands, against the code that is actually there.

## What counts as proof

Three properties, each one a distinct way a fabricated pass used to get through:

1. **A recorder produced it.** Every command the Verifier relies on is run through
   `verifier_evidence.py record`, which executes it in its own process group (`proc_group.py`),
   captures the combined output to a log beside the record, and stores the argv, the exit code, the
   MEASURED wall clock and the sha256 of that log. A claim with no log, or a log whose bytes do not
   hash to the recorded digest, is not evidence.

2. **It is bound to the code it judged.** Each entry carries `subject_digest`, a hash over the
   phase's own specs and its test tree at the moment the command ran. `check` recomputes it. Evidence
   recorded against different content is refused with the remedy named - re-run the commands. This is
   what stops a transcript from an earlier attempt being paired with a later diff.

3. **The verdict names the transcript it stands on.** The record has a `chain` - each entry's digest
   folded over the previous one, so the head identifies the exact sequence of runs - and
   `verdict.json` carries it in `execution.chain`. A verdict whose chain does not match the record on
   disk is a verdict written against a different set of runs, and it does not pass. A verdict that
   names no chain at all is the state this module exists to refuse.

Plus a floor: a run recording **0 ms** did not fork a process. `PROCESS_FLOOR_MS` is the same kind of
rule `gate_plausibility.py` applies to a provider call - a number that contradicts itself is refused
rather than believed.

## What a log is allowed to carry

The logs are committed - the gates read them in CI - so a recorded command's output goes through
`evidence_redaction.prepare` **before it touches disk**: every known secret shape replaced by a
marker naming it, then the whole capped to `EVIDENCE_LOG_MAX_BYTES` with an explicit truncation
marker. `output_sha256` and `output_bytes` are computed over those STORED bytes, so `check` still
verifies the log on disk against the record. **The command line goes through the same redactor**:
the record is committed too, and the `adversarial` kind is documented to plant a recognisably
credential-shaped value, so `psql "postgres://app:hunter2@db/prod"` puts the secret on the argv
before the child ever prints anything. The stored argv is what `entry_digest` hashes, so redacting
it costs the chain nothing. The child's working directory is deliberately NOT recorded: it had no
reader, was outside the digest, and an absolute developer path in a committed artifact is a
disclosure with nothing bought for it. Redaction failing writes **no log and no entry** - there
is no raw-log fallback, because `adversarial` is the kind that exists to surface plaintext secrets
and no later commit removes one from git history. **Redaction by pattern is a reduction of risk, not
a guarantee**; the shapes it knows, how to extend them, and what passes straight through are stated
in `evidence_redaction.py`. Nothing is ever pruned: every entry is in the chain and its log is what
`check` hashes, so the growth rule is one capped log per recorded command.

## What this does NOT claim, said rather than implied

There is no secret in this pipeline, so nothing an agent can invoke is unforgeable by that agent. An
agent determined to fabricate could write a plausible log, hash it, and compute the subject digest.
What is closed is the cheap path - a verdict with no execution behind it at all - and the expensive
one is now *visible work with a shape*, not a boolean. The suite claim has a second, independent
check on top: `hook_verifier.sh` and `gate_ci.sh` run the phase's tests themselves, so a transcript
claiming a green suite over a red tree is contradicted by the gate's own run.

Usage:
    verifier_evidence.py record <phase-dir> --kind <kind> [--note TEXT] -- <argv>...
    verifier_evidence.py check  <phase-dir> [--verdict <verdict.json>]
    verifier_evidence.py chain  <phase-dir>       print the chain head, for the verdict's own record
    verifier_evidence.py show   <phase-dir>       the transcript, for a human or a PR
    verifier_evidence.py sweep  [--root .] [--all]   the `check` obligation across phases, for CI
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import applicability  # noqa: E402
import evidence_redaction  # noqa: E402
from proc_group import run_bounded  # noqa: E402

OK = 0
MISSING = 1
ERROR = 2

FILENAME = "verification-evidence.json"
LOG_DIR = "evidence"
SCHEMA = 1
RULE = "execution-evidence"

#: The kinds of run the record distinguishes. Closed, and an unknown kind is a hard failure naming
#: what was invented - the same discipline `spec_gate_triage.BLOCKING` and `applicability.RULES` run
#: on. Guessing a kind into `other` would let the one kind the gate requires quietly stop existing.
#:
#: `suite` is the phase's own test run - the one thing every verification does. `coverage` is a trace
#: of requirement ids to tests. `adversarial` is the one that buys this stage: planting a value and
#: executing it against a real collaborator, which is how phase 8's plaintext-credential leaks were
#: found. `other` is anything else the Verifier ran to reach its verdict.
KINDS = ("suite", "coverage", "adversarial", "other")

#: The kind a passing verdict cannot be reached without. A verification that never ran the phase's
#: tests verified nothing, whatever else it did.
REQUIRED_KIND = "suite"

#: Milliseconds below which a recorded run did not start a process. A fork+exec of even `/bin/true`
#: costs more than this on every platform this runs on; 0 ms is a hand-written number.
PROCESS_FLOOR_MS = 1

#: Seconds a recorded command may run before its process group is killed. Generous, because the
#: commands here are test suites and adversarial drivers; bounded, because a wedged child inside a
#: hook is how a gate comes to read as a model size ceiling.
DEFAULT_BUDGET_S = 1800
BUDGET_ENV = "VERIFIER_EVIDENCE_TIMEOUT"

#: The files whose content decides WHAT was verified. The phase's own specs (what was required) and
#: its test tree (what was run). Production source is deliberately absent: a phase's tests are the
#: contract the Verifier judges against, and hashing the whole repository would make every unrelated
#: edit invalidate the evidence, which is a wedge rather than a guard.
SUBJECT_GLOBS = ("specs/*/spec.md", "specs/*/test-mapping.md")

#: Every document the read-path table governs declares who reads it, in the document. JSON has no
#: frontmatter, so it is a top-level key - written here, by the only writer, because declaring a
#: reader in `scripts/doc_read_path.py` is not the same as instructing anyone to emit it.
READERS = [
    "verifier_evidence.py @ per phase close (hook_verifier.sh, gate_ci.sh)",
    "avenger-verifier @ per phase, writing verdict.json's execution block",
]


class EvidenceError(Exception):
    """A record this cannot read, or a request it cannot honour. Always fails the caller closed."""


# --- paths ---------------------------------------------------------------------------------------


def record_path(phase_dir: Path) -> Path:
    return Path(phase_dir) / FILENAME


def log_dir(phase_dir: Path) -> Path:
    return Path(phase_dir) / LOG_DIR


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def budget_s() -> int:
    raw = (os.environ.get(BUDGET_ENV) or "").strip()
    if not raw:
        return DEFAULT_BUDGET_S
    try:
        value = int(raw)
    except ValueError as exc:
        raise EvidenceError(
            f"{BUDGET_ENV}={raw!r} is not an integer number of seconds"
        ) from exc
    if value <= 0:
        raise EvidenceError(f"{BUDGET_ENV}={raw!r} must be a positive number of seconds")
    return value


# --- the subject: what the evidence was recorded against -------------------------------------------


def _test_root(phase_dir: Path, root: Path) -> Path | None:
    """`tests/<feature>/<n>-<slug>`, or the older `tests/<n>-<slug>` - whichever exists.

    Same resolution `hook_verifier.sh` uses, so the evidence is bound to the tree the gate then runs.
    """
    phase = Path(phase_dir).resolve()
    slug = phase.name
    feature = phase.parents[1].name if len(phase.parents) >= 2 else ""
    for candidate in (root / "tests" / feature / slug, root / "tests" / slug):
        if candidate.is_dir():
            return candidate
    return None


def subject_digest(phase_dir: Path, root: Path | None = None) -> str:
    """A hash over what the Verifier judged: the phase's specs, mappings, and its test tree.

    Each file is labelled relative to the tree it belongs to - `spec:` under the phase directory,
    `test:` under the phase's test root - and never relative to the caller's cwd or to an absolute
    path. That is deliberate: a digest that moved when the recorder was invoked from a different
    directory would report "the code changed" for a command that changed nothing, and the remedy
    (re-run it) would not clear it. Labels are sorted, so filesystem ordering does not enter either.

    A file that cannot be read contributes its label and an explicit marker rather than being
    skipped - a spec that becomes unreadable must change the digest, or the evidence would survive
    its own subject disappearing.
    """
    phase = Path(phase_dir).resolve()
    base = Path(root).resolve() if root else Path.cwd().resolve()
    entries: list[tuple[str, bytes]] = []

    def add(label: str, path: Path) -> None:
        try:
            entries.append((label, path.read_bytes()))
        except OSError:
            entries.append((label, b"<unreadable>"))

    for pattern in SUBJECT_GLOBS:
        for path in sorted(phase.glob(pattern)):
            add("spec:" + path.resolve().relative_to(phase).as_posix(), path)
    tests = _test_root(phase, base)
    if tests is not None:
        for path in sorted(p for p in tests.rglob("*") if p.is_file()):
            if "__pycache__" in path.parts:
                continue
            add("test:" + path.resolve().relative_to(tests.resolve()).as_posix(), path)

    digest = hashlib.sha256()
    for label, payload in sorted(entries):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def partition_by_currency(runs: list[dict], phase_dir: Path,
                          root: Path) -> tuple[list[dict], list[dict]]:
    """(runs recorded against the content that is here now, runs recorded against something else).

    The split is the whole reason evidence is bound to a digest: a transcript stays on the record
    forever, and only the part of it that is still about this code may carry a verdict.
    """
    current_subject = subject_digest(phase_dir, root)
    current = [e for e in runs
               if isinstance(e, dict) and e.get("subject_digest") == current_subject]
    stale = [e for e in runs if isinstance(e, dict) and e not in current]
    return current, stale


# --- the record ------------------------------------------------------------------------------------


def load(phase_dir: Path) -> dict:
    """The record as written, or a fresh empty one. A malformed record is an ERROR, never empty.

    An unreadable record read as "no runs yet" would let a corrupted transcript be repaired by
    recording one more command over the top of it, which is a fabricated pass with extra steps.
    """
    path = record_path(phase_dir)
    if not path.is_file():
        return {"schema": SCHEMA, "phase": Path(phase_dir).name, "readers": list(READERS), "runs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"{path} could not be read as JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise EvidenceError(f"{path} is not a JSON object")
    if data.get("schema") != SCHEMA:
        raise EvidenceError(
            f"{path} declares schema {data.get('schema')!r}; this build writes and reads "
            f"schema {SCHEMA}"
        )
    if not isinstance(data.get("runs"), list):
        raise EvidenceError(f"{path} has no `runs` array")
    return data


def entry_digest(entry: dict, previous: str) -> str:
    """One link of the chain: this entry's identifying fields folded over the previous head.

    Only the fields that say WHAT RAN and WHAT CAME BACK are in the hash. A note is prose and does
    not change what happened, so editing one must not invalidate the chain.
    """
    payload = json.dumps(
        {
            "seq": entry.get("seq"),
            "kind": entry.get("kind"),
            "argv": entry.get("argv"),
            "exit_code": entry.get("exit_code"),
            "elapsed_ms": entry.get("elapsed_ms"),
            "output_sha256": entry.get("output_sha256"),
            "subject_digest": entry.get("subject_digest"),
            "recorded_at": entry.get("recorded_at"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((previous + "\n" + payload).encode("utf-8")).hexdigest()


def chain_head(runs: list[dict]) -> str:
    """The head of the hash chain over every recorded run, in order. Empty record -> empty string.

    An empty string rather than the hash of nothing, deliberately: a verdict claiming
    `execution.chain: ""` then reads as a verdict claiming no runs, which `check` refuses by the
    `REQUIRED_KIND` rule, instead of matching a record that happens to be empty.
    """
    head = ""
    for entry in runs:
        head = entry_digest(entry, head)
    return head


def save(phase_dir: Path, data: dict) -> Path:
    path = record_path(phase_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["readers"] = list(READERS)
    data["chain"] = chain_head(data.get("runs") or [])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# --- record ---------------------------------------------------------------------------------------


def record(phase_dir: Path, kind: str, argv: list[str], *, note: str | None = None,
           root: Path | None = None) -> tuple[dict, int]:
    """Run `argv`, capture what it did, and append it to the phase's record.

    The child's exit code is passed straight back to the caller: the Verifier must see the real
    result of its own command, and a recorder that swallowed a failure would be a worse liar than
    the stamp it replaces.
    """
    phase = Path(phase_dir)
    if not phase.is_dir():
        raise EvidenceError(f"{phase} is not a phase directory")
    if kind not in KINDS:
        raise EvidenceError(
            f"{kind!r} is not one of the {len(KINDS)} run kinds this record knows: "
            f"{', '.join(KINDS)}. A kind outside the set is refused rather than filed under "
            f"'other', because the gate requires a run of kind '{REQUIRED_KIND}' and a mis-filed "
            f"one is a run that stops counting."
        )
    if not argv:
        raise EvidenceError("no command given - `record` needs a command to run after `--`")

    base = Path(root).resolve() if root else Path.cwd().resolve()
    data = load(phase)
    seq = len(data["runs"]) + 1

    try:
        result = run_bounded(argv, budget_s(), cwd=str(base))
    except FileNotFoundError as exc:
        raise EvidenceError(
            f"cannot run {argv[0]!r}: {exc}. Evidence is what the command PRODUCES, so a command "
            f"that cannot start records nothing - fix the command and run it again."
        ) from exc

    raw = (result.stdout or "") + (result.stderr or "")
    # Redacted and capped BEFORE anything touches disk, and the digest is over what is stored, so
    # `check` still verifies the log against the record. Fails closed with no raw-log fallback: the
    # `adversarial` kind exists to surface plaintext secrets, and a credential written into git is
    # not removed by any later commit.
    try:
        stored = evidence_redaction.prepare(raw)
        stored_note = evidence_redaction.redact(note) if note else None
        stored_argv = [evidence_redaction.redact(word) for word in argv]
    except evidence_redaction.RedactionError as exc:
        raise EvidenceError(
            f"this run could not be made safe to store ({exc}). NOTHING was written - no log, and "
            f"no entry on the record - because neither a command's output nor the command line "
            f"itself may reach disk unredacted. This run does NOT count as recorded. Fix what "
            f"failed (scripts/evidence_redaction.py, or {evidence_redaction.EXTRA_ENV} / "
            f"{evidence_redaction.MAX_BYTES_ENV} if you set them) and run the command again."
        ) from exc

    logs = log_dir(phase)
    logs.mkdir(parents=True, exist_ok=True)
    log_file = logs / f"{seq:02d}-{kind}.log"
    log_file.write_text(stored, encoding="utf-8")

    entry = {
        "seq": seq,
        "kind": kind,
        "argv": stored_argv,
        "exit_code": int(result.returncode),
        "timed_out": bool(result.timed_out),
        "elapsed_ms": max(int(result.elapsed * 1000), 0),
        "output_sha256": hashlib.sha256(stored.encode("utf-8")).hexdigest(),
        "output_bytes": len(stored.encode("utf-8")),
        "log": log_file.relative_to(phase).as_posix(),
        "subject_digest": subject_digest(phase, base),
        "recorded_at": _now(),
    }
    if stored_note:
        entry["note"] = stored_note
    data["runs"].append(entry)
    data["phase"] = phase.name
    save(phase, data)
    return entry, int(result.returncode)


# --- check ------------------------------------------------------------------------------------------


def _entry_problems(entry: object, phase: Path) -> list[str]:
    """Every way one recorded run fails to be evidence of a run."""
    if not isinstance(entry, dict):
        return [f"{FILENAME} holds a run that is not an object"]
    label = f"run {entry.get('seq', '?')} ({entry.get('kind', '?')})"
    problems: list[str] = []

    if entry.get("kind") not in KINDS:
        problems.append(f"{label}: kind {entry.get('kind')!r} is not one of {', '.join(KINDS)}")
    argv = entry.get("argv")
    if not isinstance(argv, list) or not argv:
        problems.append(f"{label}: records no command - a run with no argv names nothing that ran")

    elapsed = entry.get("elapsed_ms")
    if not isinstance(elapsed, int) or elapsed < PROCESS_FLOOR_MS:
        problems.append(
            f"{label}: records {elapsed!r} ms, below the {PROCESS_FLOOR_MS} ms floor for starting a "
            f"process at all - this run is presumed not to have happened"
        )

    log_rel = entry.get("log")
    if not isinstance(log_rel, str) or not log_rel:
        problems.append(f"{label}: names no output log, so nothing can be checked against it")
    else:
        log_file = phase / log_rel
        if not log_file.is_file():
            problems.append(
                f"{label}: its output log {log_rel} is missing - the record claims output that is "
                f"not on disk"
            )
        else:
            try:
                actual = hashlib.sha256(log_file.read_bytes()).hexdigest()
            except OSError as exc:
                problems.append(f"{label}: its output log {log_rel} could not be read ({exc})")
            else:
                if actual != entry.get("output_sha256"):
                    problems.append(
                        f"{label}: its output log {log_rel} does not hash to the recorded digest - "
                        f"the log or the record was edited after the run"
                    )

    return problems


def problems(phase_dir: Path, *, verdict_path: Path | None = None,
             root: Path | None = None) -> list[str]:
    """Every reason this phase's execution evidence does not back a verdict. Empty list = it does."""
    phase = Path(phase_dir)
    path = record_path(phase)
    if not path.is_file():
        return [
            f"{phase} has no {FILENAME} - the verdict rests on nothing that was observed to run. "
            f"Run each verification command through `verifier_evidence.py record`."
        ]
    data = load(phase)  # raises EvidenceError on a malformed record; the caller reports it as ERROR
    runs = data["runs"]
    if not runs:
        return [f"{path} records no runs at all - an empty transcript is not evidence of execution"]

    base = Path(root).resolve() if root else Path.cwd().resolve()
    found: list[str] = []
    for entry in runs:
        found.extend(_entry_problems(entry, phase))

    # Which runs are still ABOUT the code that is here. A run recorded before the specs or tests
    # changed is not evidence about them - but it is not a defect either, and refusing the phase for
    # holding one would be a wedge with no remedy: re-running appends a fresh run and leaves the old
    # one on the record forever. So stale runs are SUPERSEDED, counted and named, and the check asks
    # only that the CURRENT ones carry the verdict.
    current, stale = partition_by_currency(runs, phase, base)
    if not current:
        found.append(
            f"every recorded run ({len(stale)}) was made against different content - the phase's "
            f"specs, mappings or tests changed after they ran, so nothing on this record is evidence "
            f"about what is here now. Re-run the verification commands through `record`"
        )
    else:
        passing_suites = [e for e in current
                          if e.get("kind") == REQUIRED_KIND and e.get("exit_code") == 0]
        if not any(e.get("kind") == REQUIRED_KIND for e in current):
            found.append(
                f"no current run of kind '{REQUIRED_KIND}' - a verification that never ran the "
                f"phase's tests against the code that is here verified nothing, whatever else it ran"
            )
        elif not passing_suites:
            found.append(
                f"every current '{REQUIRED_KIND}' run exited non-zero - the phase's own tests did "
                f"not pass in the run this verdict stands on"
            )

    head = chain_head(runs)
    stored = data.get("chain")
    if stored != head:
        found.append(
            f"{path} carries chain {str(stored)[:12]}… but its runs hash to {head[:12]}… - the "
            f"record was edited after it was written"
        )

    if verdict_path is not None:
        found.extend(_verdict_problems(Path(verdict_path), head))
    return found


def _verdict_problems(verdict_path: Path, head: str) -> list[str]:
    """Whether the verdict names the transcript it stands on, and names THIS one."""
    if not verdict_path.is_file():
        return [f"{verdict_path} does not exist, so no verdict claims this evidence"]
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{verdict_path} could not be read as JSON ({exc})"]
    if not isinstance(verdict, dict):
        return [f"{verdict_path} is not a JSON object"]
    execution = verdict.get("execution")
    if not isinstance(execution, dict):
        return [
            f"{verdict_path} has no `execution` block - the verdict does not say what it was "
            f"reached by running. Add it: "
            f'"execution": {{"evidence": "{FILENAME}", "chain": "<verifier_evidence.py chain>"}}'
        ]
    claimed = execution.get("chain")
    if not claimed:
        return [
            f"{verdict_path} `execution` names no `chain` - a verdict that points at no specific "
            f"transcript points at whatever is on disk when someone next looks. Take it from "
            f"`verifier_evidence.py chain <phase-dir>`."
        ]
    if claimed != head:
        return [
            f"{verdict_path} stands on execution chain {str(claimed)[:12]}… but the record on disk "
            f"hashes to {head[:12]}… - this verdict was written against a different set of runs. "
            f"Re-run the verification, or update the verdict from "
            f"`verifier_evidence.py chain <phase-dir>`."
        ]
    return []


def _excepted(phase_dir: Path) -> applicability.Exception_ | None:
    """A disclosed exception on this phase's ledger, or None. Same route as every other rule here."""
    try:
        found = applicability.excepted(Path(phase_dir), RULE, Path(phase_dir).name)
    except applicability.ApplicabilityError as exc:
        print(
            f"[verifier_evidence] {phase_dir} has an exception ledger this cannot read ({exc}). "
            f"No exception is granted - the execution evidence is still owed.",
            file=sys.stderr,
        )
        return None
    if found is not None:
        print(
            f"[verifier_evidence] {phase_dir} - `{RULE}` is not owed: {found.describe()}",
            file=sys.stderr,
        )
    return found


def due(phase_dir: Path, *, verdict_path: Path | None = None,
        root: Path | None = None) -> list[str]:
    """`problems`, minus a disclosed exception. What a gate actually acts on."""
    found = problems(phase_dir, verdict_path=verdict_path, root=root)
    if found and _excepted(Path(phase_dir)) is not None:
        return []
    return found


# --- sweep (CI) --------------------------------------------------------------------------------------


def _phases(root: Path) -> list[Path]:
    """Every phase directory that has closed far enough to carry a verdict."""
    return sorted({v.parent for v in Path(root).glob("docs/features/*/phases/*/verdict.json")})


class SweepResult(NamedTuple):
    """What the sweep decided, with the two outcomes kept apart.

    `failures` is the OBLIGATION - a phase in scope whose verdict is not backed by evidence.
    `undecidable` is a phase whose record could not be READ at all. They exit under different codes
    and are recorded under different names, because a stop naming the wrong cause prescribes a
    remedy that cannot repair it: "record your runs" does not fix malformed JSON.
    """

    failures: list[str]
    undecidable: list[str]


def sweep(root: Path, *, enforce_all: bool = False) -> SweepResult:
    """The obligation across the repository. **Diff-scoped unless `enforce_all`.**

    Diff-scoped even under `--full`, on `carried_items.check`'s precedent rather than
    `verifier_precheck`'s: this obligation lands on a phase directory tree every consumer repo
    already has on disk, and every phase closed before this rule existed has no transcript and never
    can - the remedy does not exist for it (CLAUDE.md §3a: a rule whose remedy is unavailable is a
    wedge, not a gate). `hook_verifier.sh` holds the phase being CLOSED, which the diff touches by
    construction, so nothing is lost; `sweep --all` is the audit for anyone who wants it.

    The scope is applied BEFORE a phase is examined, for two reasons that are one rule. It is the
    boundary's own discipline - an out-of-scope phase is counted and named, never read for a verdict
    - and `due()` recomputes `subject_digest` over every spec, mapping and test file the phase owns,
    which this runs on every commit. Examining a phase in order to discard the answer re-hashes a
    repository's whole historical test corpus to produce a number.

    A phase whose record cannot be read fails only ITSELF: the exception is caught here rather than
    escaping the loop, because one corrupt record aborting the sweep holds every unrelated commit
    hostage - the exact failure the applicability boundary exists to remove.
    """
    phases = _phases(root)
    if not phases:
        print(f"[verifier_evidence] no phases with a verdict under {root} - nothing to check",
              file=sys.stderr)
        return SweepResult([], [])

    scope: set[Path] | None = None
    if not enforce_all:
        scope = applicability.changed_paths(Path(root))
        if scope is None:
            print(
                f"[verifier_evidence] git cannot say what changed under {root}, so the scope is "
                f"unknowable and no phase is checked. Run `sweep --all` for a full audit.",
                file=sys.stderr,
            )
            return SweepResult([], [])

    failures: list[str] = []
    undecidable: list[str] = []
    unenforced = 0
    for phase in phases:
        if not (enforce_all or applicability.touched(phase, scope)):  # type: ignore[arg-type]
            unenforced += 1
            continue
        try:
            found = due(phase, verdict_path=phase / "verdict.json", root=Path(root))
        except EvidenceError as exc:
            undecidable.append(f"{phase}: {exc}")
            continue
        failures.extend(f"{phase}: {line}" for line in found)
    applicability.report_unenforced(
        "verifier_evidence",
        unenforced,
        "phase(s) predate this rule or are untouched - they are checked when you next change "
        "them, and `sweep --all` audits them now",
    )
    return SweepResult(failures, undecidable)


# --- CLI ----------------------------------------------------------------------------------------------


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Everything before the first standalone `--`, and the command after it.

    Done by hand rather than with `argparse.REMAINDER`, which is greedy from the first positional
    and swallowed `--kind` itself. The command is arbitrary argv - it can contain `--kind`,
    `--root`, anything - so the separator has to be honoured before argparse ever sees it.
    """
    for i, token in enumerate(argv):
        if token == "--":
            return argv[:i], argv[i + 1:]
    return list(argv), []


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_rec = sub.add_parser("record", help="run a command and record what it did")
    p_rec.add_argument("phase_dir", type=Path)
    p_rec.add_argument("--kind", required=True, choices=KINDS)
    p_rec.add_argument("--note", default=None, help="why this command was run (prose, not hashed)")
    p_rec.add_argument("--root", default=".", type=Path)

    p_chk = sub.add_parser("check", help="does the evidence back a verdict?")
    p_chk.add_argument("phase_dir", type=Path)
    p_chk.add_argument("--verdict", type=Path, default=None,
                       help="also require this verdict to name the transcript it stands on")
    p_chk.add_argument("--root", default=".", type=Path)

    for name in ("chain", "show"):
        p = sub.add_parser(name)
        p.add_argument("phase_dir", type=Path)

    p_sweep = sub.add_parser("sweep", help="the obligation across every phase with a verdict")
    p_sweep.add_argument("--root", default=".", type=Path)
    p_sweep.add_argument("--all", action="store_true", help="every phase, not just changed ones")

    head, command = _split_command(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(head)
    args.command = command
    return args


def _dispatch(args: argparse.Namespace) -> int:
    if args.action == "record":
        entry, rc = record(args.phase_dir, args.kind, list(args.command),
                           note=args.note, root=args.root)
        print(
            f"[verifier_evidence] recorded run {entry['seq']} ({entry['kind']}): exit "
            f"{entry['exit_code']} in {entry['elapsed_ms']} ms -> {entry['log']}",
            file=sys.stderr,
        )
        return rc

    if args.action == "chain":
        print(chain_head(load(args.phase_dir)["runs"]))
        return OK

    if args.action == "show":
        print(json.dumps(load(args.phase_dir), indent=2))
        return OK

    if args.action == "check":
        found = due(args.phase_dir, verdict_path=args.verdict, root=args.root)
        _report_superseded(args.phase_dir, args.root)
        if not found:
            print(f"[verifier_evidence] {args.phase_dir} - execution evidence holds")
            return OK
        print(
            "verifier evidence: this phase's verdict is not backed by evidence that it executed. A "
            "pass that carries no proof of execution is not a pass:",
            file=sys.stderr,
        )
        for line in found:
            print(f"  x {line}", file=sys.stderr)
        _print_remedy()
        return MISSING

    result = sweep(args.root, enforce_all=args.all)
    if result.undecidable:
        # Reported first and exited under ERROR even when there are also real gaps: a run that could
        # not READ a record has not decided the question, and claiming the obligation would send the
        # fix at "record your runs" - a remedy that cannot repair a record nothing can parse.
        print(
            "verifier evidence: a phase's record could not be read, so the check could not be "
            "decided:",
            file=sys.stderr,
        )
        for line in result.undecidable:
            print(f"  ! {line}", file=sys.stderr)
    if result.failures:
        print(
            "verifier evidence: a phase closed on a verdict with no proof that anything ran:",
            file=sys.stderr,
        )
        for line in result.failures:
            print(f"  x {line}", file=sys.stderr)
    if result.undecidable:
        return ERROR
    if result.failures:
        _print_remedy()
        return MISSING
    return OK


def _report_superseded(phase_dir: Path, root: Path) -> None:
    """Name the runs that stopped counting. A record that quietly holds evidence about code that is
    no longer here reads as more coverage than it has."""
    try:
        runs = load(Path(phase_dir))["runs"]
    except EvidenceError:
        return
    _current, stale = partition_by_currency(runs, Path(phase_dir), Path(root).resolve())
    if stale:
        print(
            f"[verifier_evidence] {len(stale)} recorded run(s) superseded - made before the phase's "
            f"specs, mappings or tests last changed, so they carry no verdict. Kept on the record.",
            file=sys.stderr,
        )


def _print_remedy() -> None:
    """Every refusal names what would satisfy it. A gate whose remedy is unstated is a wedge."""
    print(
        "\n  To satisfy this:\n"
        "    1. Run each verification command through the recorder, e.g.\n"
        "       python3 scripts/verifier_evidence.py record <phase-dir> --kind suite -- \\\n"
        "           pytest -q tests/<feature>/<n>-<slug>\n"
        "       (kinds: " + ", ".join(KINDS) + f"; a '{REQUIRED_KIND}' run that exits 0 is required)\n"
        "    2. Put the transcript's identity in the verdict:\n"
        '       "execution": {"evidence": "' + FILENAME + '", "chain": "<chain>"}\n'
        "       where <chain> is `python3 scripts/verifier_evidence.py chain <phase-dir>`.\n"
        "    3. Re-record after any change to the phase's specs, mappings or tests - evidence is\n"
        "       bound to what it was recorded against, on purpose.\n"
        f"    If this phase genuinely cannot produce a transcript, disclose it instead of working\n"
        f"    around it: scripts/applicability.py record <phase-dir> --rule {RULE} "
        "--subject <phase>\n"
        "        --reason-file <f> --recorded-by <who>",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """The CLI. **Exit 1 means the obligation, and exit 2 means it could not be DECIDED.**"""
    try:
        return _dispatch(_parse(argv))
    except EvidenceError as exc:
        print(f"[verifier_evidence] {exc}", file=sys.stderr)
        return ERROR
    except Exception as exc:  # noqa: BLE001 - an undecidable check is never a satisfied one
        print(f"[verifier_evidence] the check could not be decided: {exc!r}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(main())
