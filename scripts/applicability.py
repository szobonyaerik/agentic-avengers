#!/usr/bin/env python3
"""The applicability boundary: what a mechanical rule may BIND, and what it may only REPORT.

Every check in this pipeline was added at some point after the tree it runs on. Run without a
boundary, each one asks *"does the whole repository satisfy a rule we added later?"* when the only
question it can act on is *"does what this change is responsible for satisfy it?"*. Measured in one
run of one feature, that difference cost three separate blocks in a single phase:

  - the **stage resolver** parked forever on a phase closed under a disclosed captain-ordered cap,
    so `/avenger-run --auto` could not start a phase at all;
  - the **requirement cap** fired on two locked, implemented specs whose only prescribed remedy is a
    SPLIT, which a shipped spec cannot take — so no verdict of any kind was reachable for them, ever;
  - the **cost gate** refused every spec write in the phase over 17 undeclared subprocess spawners in
    locked phase-1 and phase-7 tests, none of which the phase had touched.

They look like three defects. They are one, and this module is the one boundary they now share.

## The rule

**A mechanical rule binds what is still OPEN. What is CLOSED it may count and name, never block.**

Closed has exactly three evidences, and no fourth is invented at a call site:

1. **untouched** — the current change does not touch it (`changed_paths`, git). This module owns that
   mechanism; `doc_read_path.py`, `verifier_precheck.py` and `subprocess_check.py` all ask here, and
   `verifier_bundle_scope.py`, `spec_gate_cache.py` and the mutation gate already ran the same rule
   before it had a name. When git cannot say what changed the scope is **unknowable**, so nothing is
   enforced and the caller says so out loud — falling back to enforcing everything is the hostage
   failure the scoping exists to remove.
2. **shipped** — the artifact's own stamps say the pipeline is past it. A spec with `status: done`
   has been implemented, and the remedy the requirement cap prescribes (split into siblings, each
   independently gated, implemented and traced) does not exist for it. **A rule whose remedy is
   unavailable is not a gate, it is a wedge.**
3. **excepted** — a disclosed exception is on the phase's ledger (`exceptions.json`, beside
   `verdict.json` and `amendments.json`) naming the rule, the subject, who recorded it and why.

The third is the one that fixes a cause rather than adding a bypass. Phase 8 of the measured feature
closed under a captain order with two specs never review-stamped; that was deliberate and it was
written down — in prose, in a handover, where no script can read it. The resolver read the absence of
a stamp as unfinished work and parked. **A phase closed with a recorded exception is CLOSED**, and
this ledger is how the resolver can be told so without anyone stamping a human sign-off nobody gave.

## What an exception is not

It is not `GATE_BYPASS`. A break-glass waives one gate call in one session; an exception is durable
state about work that has shipped, and it names the ONE rule and the ONE subject it covers. It is
audited through the same writer as every other override in the pipeline (`scripts/bypass_log.sh`
→ `gate-overrides.log`), and **an exception that could not be logged is not recorded at all** — an
unaudited exception is the silent bypass this pipeline is built to refuse.

The rule set is **CLOSED**. An unknown rule is a hard error that names what was invented, never a
silent no-op: a ledger entry nothing reads is an exception that does not exist, and it would be
discovered as a phase wedged on a rule someone believed was waived.

Usage:
    applicability.py record <phase-dir> --rule <rule> --subject <s> --reason-file <f>
                                            [--recorded-by <who>]      prints the exception id
    applicability.py list <phase-dir>                                  every exception on record
    applicability.py check <phase-dir> --rule <rule> --subject <s>     exit 0 excepted, 1 not
    applicability.py rules                                             the closed set
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec_gate_state  # noqa: E402

OK = 0
NOT_EXCEPTED = 1
ERROR = 2

FILENAME = "exceptions.json"

#: The CLOSED set of rules an exception may cover, and what each one means. A rule outside it is a
#: hard error naming what was invented — the same discipline `spec_gate_triage.BLOCKING` runs on, and
#: for the same reason: guessing either way corrupts the record.
#:
#: **Every entry here is read by a named call site** — `spec-gate` by `pipeline_state.py` and by
#: `verifier_precheck.py` (the stale-stamp check — the only remedy that clears it without a live gate
#: provider), `spec-review` and `verdict` by `pipeline_state.py`, `requirement-cap` by
#: `requirement_cap.py`, `breaker` by `breaker_gate.py`, `execution-evidence` by
#: `verifier_evidence.py`. That is the same rule this module states about a ledger entry: one nothing
#: reads is an exception that does not exist, and it would be discovered as a phase wedged on a rule
#: someone believed was waived. So a further entry is a deliberate edit here **together with the call
#: site that reads it**, never ahead of one. The cost gate is deliberately absent: it needs the
#: *untouched* evidence, not this one, and a rule nothing consults would be a promise with no
#: mechanism behind it.
RULES: dict[str, str] = {
    "spec-gate": "the machine spec gate's approval of a spec",
    "spec-review": "the human spec-review sign-off on a spec",
    "verdict": "the Verifier's passing verdict for a phase",
    "requirement-cap": "the requirement cap's split trigger on a spec",
    "breaker": "a Breaker record owed by a phase that declares criticality: critical",
    "execution-evidence": "the recorded transcript proving a phase's verification actually ran",
}

#: Every document the read-path table governs declares who reads it, in the document. JSON has no
#: frontmatter, so it is a top-level key — written here, by the only writer, because declaring a
#: reader in `scripts/doc_read_path.py` is not the same as instructing anyone to emit it.
#: `doc_read_path.READ_PATH["exceptions.json"]` reads this list rather than keeping a second copy: a
#: reader named in one place and not the other is a declaration that is already wrong, and nothing
#: catches it — the read-path check verifies the key is present, never that it names the real readers.
READERS = [
    "pipeline_state.py @ per phase, resolving the next stage",
    "requirement_cap.py @ per spec write",
    "verifier_precheck.py @ per spec, resolving a stale spec-gate stamp",
    "phase-handover @ per phase",
    "breaker_gate.py @ per phase, resolving the Breaker obligation",
    "verifier_evidence.py @ per phase, resolving the execution-evidence obligation",
]


class ApplicabilityError(Exception):
    """A malformed ledger or request. Always fails the caller closed."""


# --- evidence 1: untouched -----------------------------------------------------------------------
# This module owns the diff-scope mechanism. It lived in `doc_read_path.py`, which was simply the
# first check to need it; a second copy is what goes blind in two places at once, so there is one.


def _git(root: Path, *args: str) -> list[str] | None:
    """git's answer as lines, or None when it has none - no repo, no git, a command that failed."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def changed_paths(root: Path) -> set[Path] | None:
    """Absolute paths the current change touches, or None when the scope is unknowable.

    The union of what is modified against HEAD, what is staged, and what is untracked - so an
    artifact written this session is in scope from the moment it exists, before any commit.

    Every command is pinned to **toplevel-relative** output and joined to the toplevel, because the
    three do not share a path convention on their own: `ls-files` prints cwd-relative names, and
    `diff` prints cwd-relative ones under `diff.relative`. Combining them as if they agreed makes an
    untracked artifact resolve to a path that matches nothing whenever `root` is below the git root -
    and the guard then stops guarding without saying anything, which is worse than not having it.

    **None is not "nothing changed" and must never be read as one.** The caller enforces nothing and
    says so.
    """
    toplevel = _git(root, "rev-parse", "--show-toplevel")
    if not toplevel:
        return None
    base = Path(toplevel[0]).resolve()
    changed: set[Path] = set()
    for args in (
        ("diff", "--name-only", "--no-relative", "HEAD"),
        ("diff", "--cached", "--name-only", "--no-relative", "--diff-filter=ACM"),
        ("ls-files", "--others", "--exclude-standard", "--full-name"),
    ):
        lines = _git(root, *args)
        if lines is None:
            return None
        changed.update((base / line).resolve() for line in lines)
    return changed


def touched(path: Path, scope: set[Path]) -> bool:
    """Whether `path` — a file, or a directory containing one — is inside the change's scope."""
    resolved = Path(path).resolve()
    return any(changed == resolved or resolved in changed.parents for changed in scope)


# --- evidence 2: shipped -------------------------------------------------------------------------


def spec_shipped(spec_path: Path) -> bool:
    """Whether a spec has been implemented, and is therefore past the point of being re-authored.

    Read from `status: done` in the spec's own frontmatter, and deliberately from nothing else —
    through `spec_gate_state.frontmatter`, the repository's one strict reader of a spec's stamps. A
    second, looser parse here would answer a different question than the resolver does: split on the
    first `\n---` a spec happens to contain, a spec with **no** frontmatter at all reads its whole
    body as a header, so prose saying `status: done` would ship it. That
    stamp is what says the implementer finished against this text: the tests exist, the code exists,
    and the phase's suite has run green over both. Splitting it into siblings afterwards would
    renumber requirement ids that `test-mapping.md` rows, `verdict.json` findings and every prior
    handover already point at.

    It cannot be used to evade the cap while a spec is being written. `status: done` is stamped by
    the implementer, not the writer, and it is what fires `hook_verifier.sh` (the phase suite must
    pass) and `verifier_precheck.py` (every requirement id owed a trace must have one). A draft that
    claims to be done fails both, loudly, at the next commit.

    An unreadable spec is **not** shipped: under-report, so the rule keeps binding.
    """
    try:
        text = Path(spec_path).read_text(encoding="utf-8")
    except OSError:
        return False
    return spec_gate_state.frontmatter(text).get("status") == "done"


# --- evidence 3: excepted ------------------------------------------------------------------------


@dataclass(frozen=True)
class Exception_:
    """One recorded exception, as the ledger holds it."""

    id: str
    rule: str
    subject: str
    reason: str
    recorded_by: str
    recorded_at: str

    def describe(self) -> str:
        return (
            f"{self.id} [{self.rule}] {self.subject} — recorded by {self.recorded_by} "
            f"at {self.recorded_at}: {self.reason}"
        )


def path_for(phase_dir: Path) -> Path:
    return Path(phase_dir) / FILENAME


def load(phase_dir: Path) -> dict:
    """The phase's exception ledger, or an empty one.

    An unreadable ledger is an ERROR, never an empty one. Reading a corrupt file as "no exceptions"
    would turn a disclosed closure back into an invisible wedge, which is the failure this ledger
    exists to end.
    """
    target = path_for(phase_dir)
    if not target.is_file():
        return {
            "phase": Path(phase_dir).name,
            "readers": list(READERS),
            "exceptions": [],
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApplicabilityError(f"cannot read {target}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        raise ApplicabilityError(f"{target} is not an exception ledger")
    for entry in data["exceptions"]:
        if not isinstance(entry, dict):
            raise ApplicabilityError(
                f"{target} holds an entry that is not an exception record"
            )
        rule = entry.get("rule")
        if rule not in RULES:
            raise ApplicabilityError(
                f"{target}: exception {entry.get('id')!r} names rule {rule!r}, which is not one of "
                f"the {len(RULES)} rules an exception can cover: {', '.join(sorted(RULES))}. "
                f"A ledger entry nothing reads is an exception that does not exist."
            )
    return data


def save(phase_dir: Path, ledger: dict) -> Path:
    """Write the ledger, always carrying its own `readers:` declaration."""
    ledger["readers"] = list(READERS)
    target = path_for(phase_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return target


def exceptions(phase_dir: Path) -> list[Exception_]:
    """Every exception on the phase's ledger, in the order they were recorded."""
    return [
        Exception_(
            id=str(entry.get("id") or "?"),
            rule=str(entry.get("rule")),
            subject=str(entry.get("subject") or ""),
            reason=str(entry.get("reason") or ""),
            recorded_by=str(entry.get("recorded_by") or "?"),
            recorded_at=str(entry.get("recorded_at") or "?"),
        )
        for entry in load(phase_dir)["exceptions"]
    ]


def excepted(phase_dir: Path, rule: str, subject: str) -> Exception_ | None:
    """The exception covering `rule` for `subject` in this phase, or None.

    An unknown `rule` raises rather than returning None: a caller asking about a rule the closed set
    does not hold would silently never find an exception, and the phase would wedge on a rule
    everybody believed was waived.
    """
    if rule not in RULES:
        raise ApplicabilityError(
            f"{rule!r} is not one of the {len(RULES)} rules an exception can cover: "
            f"{', '.join(sorted(RULES))}"
        )
    for record in exceptions(phase_dir):
        if record.rule == rule and record.subject == subject:
            return record
    return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(phase_dir: Path, record: Exception_) -> None:
    """Log the exception through the pipeline's ONE override writer, or refuse to record it.

    `scripts/bypass_log.sh` is the only thing that appends to `gate-overrides.log`, because the
    reason is author prose and a raw newline or tab would split one override into two records. An
    exception is the fourth kind of override to route through it, and it routes through it for the
    same reason the other three do: the guarantee is structural, not a rule each caller remembers.
    """
    writer = Path(__file__).resolve().parent / "bypass_log.sh"
    if not writer.is_file():
        raise ApplicabilityError(
            f"cannot audit this exception: {writer} is missing. An exception that is not logged is "
            f"a silent bypass, so nothing is recorded."
        )
    env = dict(os.environ)
    env["GATE_BYPASS"] = record.reason
    env.setdefault("CLAUDE_PROJECT_DIR", str(Path.cwd()))
    try:
        proc = subprocess.run(
            [str(writer), f"exception:{record.rule}", record.id, record.recorded_by],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ApplicabilityError(
            f"cannot audit this exception ({exc}); nothing was recorded"
        ) from exc
    if proc.returncode != 0:
        raise ApplicabilityError(
            f"the override log refused this exception ({proc.stderr.strip()}); nothing was recorded"
        )
    sys.stderr.write(proc.stderr)


def record_exception(
    phase_dir: Path, rule: str, subject: str, reason: str, recorded_by: str
) -> Exception_:
    """Record one disclosed exception against one rule and one subject. Audited, or not recorded."""
    if rule not in RULES:
        raise ApplicabilityError(
            f"{rule!r} is not one of the {len(RULES)} rules an exception can cover:\n"
            + "\n".join(f"  {name}: {why}" for name, why in sorted(RULES.items()))
        )
    if not subject.strip():
        raise ApplicabilityError(
            "an exception must name its subject — the spec directory it covers, or the phase. An "
            "exception with no subject is a rule switched off for everything."
        )
    if not reason.strip():
        raise ApplicabilityError(
            "an exception must say why it was made. The reason is the whole disclosure: without it "
            "this is an undisclosed bypass wearing a ledger entry."
        )
    if not Path(phase_dir).is_dir():
        raise ApplicabilityError(f"no such phase directory: {phase_dir}")

    ledger = load(phase_dir)
    record = Exception_(
        id=f"X{len(ledger['exceptions']) + 1}",
        rule=rule,
        subject=subject.strip(),
        reason=reason.strip(),
        recorded_by=recorded_by.strip() or "unknown",
        recorded_at=_now(),
    )
    _audit(Path(phase_dir), record)
    ledger["exceptions"].append(
        {
            "id": record.id,
            "rule": record.rule,
            "subject": record.subject,
            "reason": record.reason,
            "recorded_by": record.recorded_by,
            "recorded_at": record.recorded_at,
        }
    )
    ledger["phase"] = Path(phase_dir).name
    save(phase_dir, ledger)
    return record


# --- the one line a check prints when it counted instead of blocking -----------------------------


def report_unenforced(check: str, count: int, detail: str) -> None:
    """Say what was counted rather than enforced. Visibility without coercion: never an exit code.

    One spelling for every check on this boundary. The failure being designed against is a check
    that silently stops checking: out of scope must look different from clean, in every one of them.
    """
    if not count:
        return
    print(
        f"[{check}] {count} finding(s) NOT enforced — outside what this change is responsible "
        f"for: {detail}",
        file=sys.stderr,
    )


# --- CLI -----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)

    p_record = sub.add_parser("record", help="record one disclosed exception")
    p_record.add_argument("phase_dir", type=Path)
    p_record.add_argument("--rule", required=True, choices=sorted(RULES))
    p_record.add_argument(
        "--subject", required=True, help="the spec directory name, or the phase"
    )
    p_record.add_argument(
        "--reason-file",
        required=True,
        help="path to a file holding the reason. A reason is author-written prose, and prose "
        "belongs in a file the command reads — never on a command line, where the auto-approve "
        "hook matches its deny regex against the whole command string.",
    )
    p_record.add_argument(
        "--recorded-by", default="", help="who decided it (default: git user)"
    )

    p_list = sub.add_parser("list", help="every exception on this phase's ledger")
    p_list.add_argument("phase_dir", type=Path)

    p_check = sub.add_parser("check", help="is this rule excepted for this subject?")
    p_check.add_argument("phase_dir", type=Path)
    p_check.add_argument("--rule", required=True)
    p_check.add_argument("--subject", required=True)

    sub.add_parser("rules", help="the closed set of rules an exception can cover")

    args = parser.parse_args(argv)

    if args.action == "rules":
        for name, why in sorted(RULES.items()):
            print(f"{name}\t{why}")
        return OK

    try:
        if args.action == "record":
            try:
                reason = Path(args.reason_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(
                    f"[applicability] cannot read the reason file: {exc}",
                    file=sys.stderr,
                )
                return ERROR
            who = args.recorded_by or _git_user(args.phase_dir)
            record = record_exception(
                args.phase_dir, args.rule, args.subject, reason, who
            )
            print(record.id)
            return OK

        if args.action == "list":
            found = exceptions(args.phase_dir)
            if not found:
                print(f"no exceptions recorded for {args.phase_dir}", file=sys.stderr)
                return OK
            for record in found:
                print(record.describe())
            return OK

        found_one = excepted(args.phase_dir, args.rule, args.subject)
    except ApplicabilityError as exc:
        print(f"[applicability] {exc}", file=sys.stderr)
        return ERROR

    if found_one is None:
        return NOT_EXCEPTED
    print(found_one.describe())
    return OK


def _git_user(phase_dir: Path) -> str:
    lines = _git(
        Path(phase_dir) if Path(phase_dir).is_dir() else Path.cwd(),
        "config",
        "user.email",
    )
    return lines[0] if lines else "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
