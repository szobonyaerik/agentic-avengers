#!/usr/bin/env python3
"""The pipeline's retrospective log — what the pipeline learned about *itself*.

Distinct from `docs/lessons/` (the `self-improvement` skill), which is per-project and about the
*work*. This log is about the *machinery*: a gate that keeps firing on the same spec, a stage that
keeps routing back, a break-glass nobody needed. Its destination is the agentic-avengers repo, not
the project the pipeline happens to be running in.

Observations are appended **as they happen**, because `/avenger-run` is resumable and a dead session
must not take the run's evidence with it. They carry `triage: pending` until a human has seen them.

The `--auto` path is why the sweep exists. An unattended run skips the lavish triage (no human to
poll), and `pipeline_state` makes `done` a *terminal* state — so nothing will ever re-trigger that
feature's close. Without a cross-feature sweep at preflight, those observations would be stranded.
`pending_features()` is that sweep, and it biases toward surfacing: a file it cannot parse is
reported pending rather than skipped, because a human noticing a malformed file beats silently
dropping what the pipeline learned.

    python3 scripts/pipeline_observations.py pending [--root .]
    python3 scripts/pipeline_observations.py append <feature> --kind <kind> --note <text>
    python3 scripts/pipeline_observations.py resolve <feature> [--root .]
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

OBSERVATIONS_FILENAME = "pipeline-observations.md"
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TRIAGE_LINE = re.compile(r"^triage:[ \t]*\S+[ \t]*$", re.MULTILINE)

#: Free-form, but these are the kinds the retrospective skill knows how to triage.
KINDS = ("gate-friction", "route-back", "bypass", "stage-churn", "success", "other")


def _observations_path(root: Path, feature: str) -> Path:
    return Path(root) / "docs" / "features" / feature / OBSERVATIONS_FILENAME


def _header(feature: str) -> str:
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return (
        "---\n"
        f"feature: {feature}\n"
        "stage: pipeline-retrospective\n"
        f"created: {created}\n"
        "triage: pending\n"
        "links: [verdict.json, gate-overrides.log]\n"
        "---\n\n"
        "# Pipeline observations\n\n"
        "What this run revealed about the **pipeline itself** - not about the project. Triaged at\n"
        "feature close; whatever is selected becomes an issue on the agentic-avengers repo.\n"
    )


def read_triage(root: Path, feature: str) -> str | None:
    """The file's triage state: 'pending', 'done', or None when there is no log yet.

    An unparseable file reads as 'pending' — see the module docstring on biasing to surfacing.
    """
    path = _observations_path(root, feature)
    if not path.exists():
        return None
    match = FRONTMATTER.search(path.read_text(encoding="utf-8"))
    if not match:
        return "pending"
    for line in match.group(1).splitlines():
        if line.startswith("triage:"):
            return line.split(":", 1)[1].strip() or "pending"
    return "pending"


def append_observation(
    root: Path,
    feature: str,
    *,
    kind: str,
    note: str,
    evidence: list[str] | None = None,
) -> Path:
    """Append one observation, creating the log (with frontmatter) on first write.

    Appending always re-opens triage: an observation made after a human triaged the file is new
    information, and burying it under a stale `triage: done` would lose it.
    """
    path = _observations_path(root, feature)
    path.parent.mkdir(parents=True, exist_ok=True)

    body = path.read_text(encoding="utf-8") if path.exists() else _header(feature)

    when = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    entry = [f"\n## [{kind}] {when}\n", f"{note}\n"]
    if evidence:
        entry.append("\nEvidence:\n")
        entry.extend(f"- `{item}`\n" for item in evidence)

    body = _reopen_triage(body)
    path.write_text(body + "".join(entry), encoding="utf-8")
    return path


def _reopen_triage(body: str) -> str:
    """Force `triage: pending` in the frontmatter, whatever it currently says."""
    match = FRONTMATTER.search(body)
    if not match:
        return body
    block = match.group(1)
    if TRIAGE_LINE.search(block):
        new_block = TRIAGE_LINE.sub("triage: pending", block, count=1)
    else:
        new_block = block + "\ntriage: pending"
    return body[: match.start(1)] + new_block + body[match.end(1) :]


def resolve_triage(root: Path, feature: str) -> Path:
    """Mark a feature's observations as triaged. Dismissing everything still counts.

    Idempotent: resolving an already-resolved log is a no-op rather than an error, so a retried
    orchestrator step cannot corrupt the flag.
    """
    path = _observations_path(root, feature)
    if not path.exists():
        raise FileNotFoundError(f"no {OBSERVATIONS_FILENAME} for feature '{feature}'")

    body = path.read_text(encoding="utf-8")
    match = FRONTMATTER.search(body)
    if not match:
        # Unparseable: rewrite with a minimal valid header so the sweep stops flagging it,
        # preserving whatever prose was there.
        path.write_text(_header(feature).replace("triage: pending", "triage: done") + body,
                        encoding="utf-8")
        return path

    block = match.group(1)
    if TRIAGE_LINE.search(block):
        new_block = TRIAGE_LINE.sub("triage: done", block, count=1)
    else:
        new_block = block + "\ntriage: done"
    path.write_text(body[: match.start(1)] + new_block + body[match.end(1) :], encoding="utf-8")
    return path


def pending_features(root: Path) -> list[str]:
    """Every feature with observations a human has not yet seen, sorted.

    This is the `/avenger-run` preflight sweep. It deliberately scans **all** features, not the one
    being run: an `--auto` run that finished feature A leaves A pending forever, and the only thing
    that will ever surface it is an interactive run on some other feature.
    """
    features_dir = Path(root) / "docs" / "features"
    if not features_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in features_dir.iterdir()
        if d.is_dir() and read_triage(root, d.name) == "pending"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repo root (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pending", help="list features with untriaged observations")

    ap = sub.add_parser("append", help="append one observation")
    ap.add_argument("feature")
    ap.add_argument("--kind", default="other", help=f"one of: {', '.join(KINDS)}")
    ap.add_argument("--note", required=True)
    ap.add_argument("--evidence", action="append", default=[])

    rp = sub.add_parser("resolve", help="mark a feature's observations as triaged")
    rp.add_argument("feature")

    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "pending":
        for name in pending_features(root):
            print(name)
        return 0

    if args.command == "append":
        path = append_observation(
            root, args.feature, kind=args.kind, note=args.note, evidence=args.evidence
        )
        print(path)
        return 0

    try:
        print(resolve_triage(root, args.feature))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
