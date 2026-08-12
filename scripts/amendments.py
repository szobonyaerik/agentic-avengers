#!/usr/bin/env python3
"""Amendments - changing a verified phase without re-running the whole verification.

The pipeline had no concept of a correction. Once a phase was verified, any change to it - a one-line
fix, a renamed helper, a scrub that turned out to be defeated by JSON escaping - re-opened the whole
phase and cost a full verification round. One measured phase ran **eight** verification attempts;
rounds 3 through 8 were that shape. Meanwhile **80% of re-attempts were the Verifier routing back to
itself**, and 26% of everything it raised was bookkeeping about its own stamps.

An amendment says, in a record on disk: *this change touches these requirement ids, and only these
re-verify.* The verdict then reads **verified at attempt N, plus amendments A1..An** - the attempt
count stops being the only thing that can express "this phase moved".

Two rules make it safe, and both are enforced here rather than asked for:

- **Batched at phase close.** Ordinary amendments accumulate and are re-verified together, once,
  when the phase closes. That is the whole saving: six route-backs become one bundled pass.
- **Security is never batched.** An amendment marked `--security` is **owed re-verification
  immediately**. A phase-8 credential leak must not sit in a batch waiting for the phase to close,
  and the cost argument that justifies batching does not apply to a secret already in a log.

`amendments.json` lives beside `verdict.json` in the phase directory. It is the amendment's own
artifact; `verdict.json` carries only the **ids** of the amendments folded into it, so the frozen
verdict schema gains one short array rather than a nested record.

Usage:
    amendments.py open <phase-dir> --requirements R8.2.30,R8.2.31 --reason-file <f> [--security]
                                          record an amendment; prints its id
    amendments.py close <phase-dir> <A-id> --evidence <path>
                                          mark it re-verified, with the evidence that did it
    amendments.py pending <phase-dir>     list amendments not yet re-verified (exit 1 if any)
    amendments.py due <phase-dir>         exit 1 when re-verification is OWED NOW: a pending
                                          security amendment, or any pending amendment on a phase
                                          whose verdict already passes
    amendments.py scope <phase-dir>       print the requirement ids pending re-verification, one per
                                          line - the re-verify set, not the whole phase
    amendments.py ids <phase-dir>         print the amendment ids for verdict.json's `amendments`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

OK = 0
OWED = 1
ERROR = 2

FILENAME = "amendments.json"
REQUIREMENT_ID = re.compile(r"\AR\d+\.\d+\.\d+\Z")

#: Every document the read-path table governs declares who reads it, **in the document**. JSON has no
#: frontmatter, so it is a top-level key - the same declaration `verdict.json` carries. It is written
#: here, by the only writer, rather than asked of anyone: declaring a reader in
#: `scripts/doc_read_path.py` is not the same as instructing a writer to emit it, and three artifact
#: classes once shipped with the first and not the second.
READERS = [
    "avenger-verifier @ per phase, to scope the re-verify set",
    "phase-handover @ per phase",
]

PENDING = "pending"
VERIFIED = "verified"


class AmendmentError(Exception):
    """A malformed amendment record or request. Always fails the caller closed."""


def path_for(phase_dir: Path) -> Path:
    return Path(phase_dir) / FILENAME


def load(phase_dir: Path) -> dict:
    """The phase's amendment ledger, or an empty one. An unreadable ledger is an error, not empty.

    Treating a corrupt ledger as "no amendments" would silently drop a pending security
    re-verification, which is the one thing this file exists to make impossible to lose.
    """
    target = path_for(phase_dir)
    if not target.is_file():
        return {"phase": Path(phase_dir).name, "readers": list(READERS), "amendments": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AmendmentError(f"cannot read {target}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("amendments"), list):
        raise AmendmentError(f"{target} is not an amendment ledger")
    return data


def save(phase_dir: Path, ledger: dict) -> Path:
    """Write the ledger, always carrying its own `readers:` declaration."""
    ledger["readers"] = list(READERS)
    target = path_for(phase_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return target


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_amendment(
    phase_dir: Path, requirements: list[str], reason: str, security: bool = False
) -> dict:
    """Record a post-verification change against the requirement ids it touches."""
    ids = [r.strip() for r in requirements if r.strip()]
    if not ids:
        raise AmendmentError(
            "an amendment must name the requirement ids it touches — that naming IS the scope of "
            "the re-verification it owes, and an amendment with no ids re-verifies nothing."
        )
    bad = [r for r in ids if not REQUIREMENT_ID.match(r)]
    if bad:
        raise AmendmentError(f"not requirement ids: {', '.join(bad)} (expected R<n>.<k>.<m>)")
    if not reason.strip():
        raise AmendmentError("an amendment must say why it was made")

    ledger = load(phase_dir)
    record = {
        "id": f"A{len(ledger['amendments']) + 1}",
        "requirements": ids,
        "security": bool(security),
        "reason": reason.strip(),
        "status": PENDING,
        "opened_at": _now(),
        "verified_at": None,
        "evidence": None,
    }
    ledger["amendments"].append(record)
    ledger["phase"] = Path(phase_dir).name
    save(phase_dir, ledger)
    return record


def close_amendment(phase_dir: Path, amendment_id: str, evidence: str) -> dict:
    """Mark one amendment re-verified, with the evidence that re-verified it."""
    if not evidence.strip():
        raise AmendmentError(
            "closing an amendment needs its own evidence — the point of the amendment path is that "
            "the amended requirements carry evidence, not that they skip it."
        )
    ledger = load(phase_dir)
    for record in ledger["amendments"]:
        if record.get("id") == amendment_id:
            record["status"] = VERIFIED
            record["verified_at"] = _now()
            record["evidence"] = evidence.strip()
            save(phase_dir, ledger)
            return record
    raise AmendmentError(f"no amendment {amendment_id!r} in {path_for(phase_dir)}")


def pending(phase_dir: Path) -> list[dict]:
    return [a for a in load(phase_dir)["amendments"] if a.get("status") != VERIFIED]


def verdict_passes(phase_dir: Path) -> bool:
    """True when the phase carries a passing verdict. Unreadable or absent counts as not passing."""
    try:
        return (
            json.loads((Path(phase_dir) / "verdict.json").read_text(encoding="utf-8")).get("verdict")
            == "pass"
        )
    except (OSError, ValueError, AttributeError):
        return False


def due(phase_dir: Path) -> list[dict]:
    """Amendments whose re-verification is owed NOW rather than at phase close.

    Two ways that happens, and only two:

    - the amendment is **security-relevant**. Batching is a cost optimisation, and it does not apply
      to a credential already exposed.
    - the phase's verdict **already says pass** while an amendment is pending. That verdict is a
      claim about code that has since changed, and leaving it standing is the pipeline asserting
      something it has not checked.
    """
    open_records = pending(phase_dir)
    if verdict_passes(phase_dir):
        return open_records
    return [a for a in open_records if a.get("security")]


def scope(phase_dir: Path) -> list[str]:
    """The requirement ids pending re-verification - the re-verify set, deduplicated, in order."""
    seen: list[str] = []
    for record in pending(phase_dir):
        for rid in record.get("requirements") or []:
            if rid not in seen:
                seen.append(rid)
    return seen


def _describe(record: dict) -> str:
    tag = " [SECURITY — re-verify now, never batched]" if record.get("security") else ""
    return (
        f"  {record.get('id')}{tag}: {', '.join(record.get('requirements') or [])}\n"
        f"      {record.get('reason')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_open = sub.add_parser("open")
    p_open.add_argument("phase_dir", type=Path)
    p_open.add_argument("--requirements", required=True, help="comma-separated R<n>.<k>.<m> ids")
    p_open.add_argument(
        "--reason-file",
        required=True,
        help="path to a file holding the reason. A reason is author-written prose, and prose "
        "belongs in a file the command reads — never on a command line, where the auto-approve "
        "hook matches its deny regex against the whole command string.",
    )
    p_open.add_argument("--security", action="store_true")

    p_close = sub.add_parser("close")
    p_close.add_argument("phase_dir", type=Path)
    p_close.add_argument("amendment_id")
    p_close.add_argument("--evidence", required=True, help="path to the evidence that re-verified it")

    for name in ("pending", "due", "scope", "ids"):
        p = sub.add_parser(name)
        p.add_argument("phase_dir", type=Path)

    args = parser.parse_args(argv)

    try:
        if args.action == "open":
            try:
                reason = Path(args.reason_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"[amendments] cannot read the reason file: {exc}", file=sys.stderr)
                return ERROR
            record = open_amendment(
                args.phase_dir,
                args.requirements.split(","),
                reason,
                security=args.security,
            )
            print(record["id"])
            if record["security"]:
                print(
                    "  security-relevant: this one is NOT batched — re-verify it now.",
                    file=sys.stderr,
                )
            return OK

        if args.action == "close":
            record = close_amendment(args.phase_dir, args.amendment_id, args.evidence)
            print(f"{record['id']} verified ({record['evidence']})")
            return OK

        if args.action == "ids":
            print("\n".join(a["id"] for a in load(args.phase_dir)["amendments"]))
            return OK

        if args.action == "scope":
            print("\n".join(scope(args.phase_dir)))
            return OK

        records = pending(args.phase_dir) if args.action == "pending" else due(args.phase_dir)
    except AmendmentError as exc:
        print(f"[amendments] {exc}", file=sys.stderr)
        return ERROR

    if not records:
        return OK
    if args.action == "pending":
        print(f"{len(records)} amendment(s) pending re-verification at phase close:", file=sys.stderr)
    else:
        print(
            f"{len(records)} amendment(s) OWE re-verification now — a security amendment is never "
            f"batched, and a passing verdict over pending amendments is a claim about code that "
            f"has since changed:",
            file=sys.stderr,
        )
    for record in records:
        print(_describe(record), file=sys.stderr)
    return OWED


if __name__ == "__main__":
    raise SystemExit(main())
