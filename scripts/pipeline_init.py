#!/usr/bin/env python3
"""What `/pipeline-init` must know about a repository before it writes anything.

The command assumed a greenfield repository (issue #108). Two of those assumptions cost something:

* It copied its own `env.example` template to `.env.example`, claiming a filename a project that
  already has its own tooling already owns. The operator's remedy - concatenating the two examples -
  is what produced the fused key that silently inverted `DRY_RUN` (see `env_assemble.py`).
* It assumed `cosmic-ray.toml` exists at the repo root, while two later steps require it. A repo
  without one learned that at the mutation baseline check, or later still at the first phase's
  mutation gate, which then records `did-not-run` and reports nothing else.

So the state is READ first and the command adapts to it. This is a report, not a gate: it always
exits 0, because a non-greenfield repository is a normal repository, not a defect. What it must not
do is answer wrongly - a step that says a file is missing when the project has one is how the
overwrite happened.

    python3 scripts/pipeline_init.py survey [--root .]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

#: Where the pipeline's own env template lands in a repository that has no `.env.example`...
PROJECT_EXAMPLE = ".env.example"
#: ...and where it lands in one that does. Never the project's own file.
PIPELINE_EXAMPLE = ".env.pipeline.example"

ENV_FILE = ".env"
COSMIC_RAY = "cosmic-ray.toml"
NO_MISTAKES = ".no-mistakes.yaml"

#: The template this command writes, in the layout `install.sh` vendors as well as in this repo.
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "templates" / "env.example"
)

#: The later steps that require `cosmic-ray.toml`, named at init time instead of failing at theirs.
COSMIC_RAY_REQUIRED_BY = (
    "/pipeline-init step 8 (the mutation baseline sanity check)",
    "the per-phase mutation gate (scripts/hook_mutation.sh, scripts/gate_ci.sh)",
)

#: Inlined rather than pointed at: `install.sh` vendors this script and does NOT vendor
#: `AVENGERS.md`, so a remedy naming that file is a remedy a vendored consumer cannot follow.
COSMIC_RAY_TEMPLATE = """\
[cosmic-ray]
module-path = "src"                # your package/dir under test (string OR list of paths/files)
timeout = 30.0
excluded-modules = []
test-command = "pytest -x -q --ignore=tests/e2e"   # e2e is feature-level; not a mutation signal

[cosmic-ray.distributor]
name = "local"
"""


def template_marker(template: Path | str | None = None) -> str | None:
    """The shipped template's own first non-empty line, or None when it cannot be read.

    The marker is READ out of the template rather than restated here, so it can never drift from
    what this command actually writes. A whole-file compare would be too brittle to use: an
    operator who filled in one value or edited one comment still has the pipeline's file, and
    reporting it as the project's own is the wrong answer this exists to stop.
    """
    try:
        text = Path(template if template is not None else TEMPLATE_PATH).read_text(
            encoding="utf-8"
        )
    except OSError:
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def _carries_marker(path: Path, marker: str) -> bool:
    """Whether `path` holds `marker` as one of its own lines. Unreadable answers no."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.strip() == marker for line in text.splitlines())


@dataclass(frozen=True)
class Survey:
    """What the repository already holds, and where the pipeline's own files may therefore go."""

    root: Path
    env_example_owned_by_project: bool
    env_exists: bool
    no_mistakes_exists: bool
    cosmic_ray_present: bool
    #: An `.env.example` an earlier `/pipeline-init` wrote. Never the project's own, so it may be
    #: refreshed in place instead of leaving a second copy of the same template behind it.
    env_example_written_by_pipeline: bool = False

    @property
    def greenfield(self) -> bool:
        return not (
            self.env_example_owned_by_project
            or self.env_example_written_by_pipeline
            or self.env_exists
            or self.no_mistakes_exists
            or self.cosmic_ray_present
        )

    @property
    def template_target(self) -> str:
        """Where the pipeline's `env.example` may be written without taking a name it does not own."""
        return (
            PIPELINE_EXAMPLE if self.env_example_owned_by_project else PROJECT_EXAMPLE
        )

    @property
    def cosmic_ray_required_by(self) -> tuple[str, ...]:
        return () if self.cosmic_ray_present else COSMIC_RAY_REQUIRED_BY


def survey(root: Path | str = ".", template: Path | str | None = None) -> Survey:
    root = Path(root)
    example = root / PROJECT_EXAMPLE
    present = example.is_file()

    written_by_pipeline = False
    if present:
        marker = template_marker(template)
        if marker is None:
            # Cannot tell, so answer the conservative way and say so: the file stays the project's
            # and is never overwritten. Guessing the other direction overwrites somebody's file.
            print(
                f"pipeline-init: cannot read the shipped template "
                f"({template if template is not None else TEMPLATE_PATH}), so it is not possible "
                f"to tell whether {PROJECT_EXAMPLE} is the pipeline's own - treating it as the "
                f"project's and never overwriting it.",
                file=sys.stderr,
            )
        else:
            written_by_pipeline = _carries_marker(example, marker)

    return Survey(
        root=root,
        env_example_owned_by_project=present and not written_by_pipeline,
        env_exists=(root / ENV_FILE).is_file(),
        no_mistakes_exists=(root / NO_MISTAKES).is_file(),
        cosmic_ray_present=(root / COSMIC_RAY).is_file(),
        env_example_written_by_pipeline=written_by_pipeline,
    )


def report_lines(found: Survey) -> list[str]:
    lines = [f"greenfield: {'yes' if found.greenfield else 'no'}"]

    if found.env_example_owned_by_project:
        lines.append(
            f"{PROJECT_EXAMPLE}: EXISTS and is the project's own - never overwrite it. "
            f"The pipeline's template goes to {PIPELINE_EXAMPLE}, and the two are assembled with "
            f"`env_assemble.py assemble`, never `cat` (issue #108)."
        )
    elif found.env_example_written_by_pipeline:
        lines.append(
            f"{PROJECT_EXAMPLE}: EXISTS and is the PIPELINE's own template from an earlier init - "
            f"refresh it in place. Writing a second copy to {PIPELINE_EXAMPLE} would leave two "
            f"copies of the same template with the older one first in the assemble order."
        )
    else:
        lines.append(
            f"{PROJECT_EXAMPLE}: absent - the pipeline's template may take that name."
        )

    if found.env_exists:
        lines.append(
            f"{ENV_FILE}: EXISTS - it holds live credentials, so never overwrite it. Assemble a new "
            f"one only into a different path, or pass --force deliberately."
        )
    else:
        lines.append(
            f"{ENV_FILE}: absent - assemble it: "
            f"`python3 scripts/env_assemble.py assemble --out {ENV_FILE} <examples...>`"
        )

    lines.append(
        f"{NO_MISTAKES}: {'EXISTS - never overwrite it.' if found.no_mistakes_exists else 'absent - copy the template.'}"
    )

    if found.cosmic_ray_present:
        lines.append(f"{COSMIC_RAY}: present.")
    else:
        lines.append(
            f"{COSMIC_RAY}: MISSING at the repo root, and required later by: "
            + "; ".join(COSMIC_RAY_REQUIRED_BY)
            + ". Create it now or those steps cannot run - the mutation gate records `did-not-run` "
            "and reports nothing else. Point `module-path` and `test-command` at this project:\n"
            + COSMIC_RAY_TEMPLATE.rstrip("\n")
        )

    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    look = sub.add_parser("survey", help="report what this repository already holds")
    look.add_argument("--root", default=".", help="project root (default: cwd)")
    args = parser.parse_args(argv)

    if args.command == "survey":
        for line in report_lines(survey(args.root)):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
