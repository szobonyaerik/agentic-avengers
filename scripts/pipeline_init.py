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
from dataclasses import dataclass
from pathlib import Path

#: Where the pipeline's own env template lands in a repository that has no `.env.example`...
PROJECT_EXAMPLE = ".env.example"
#: ...and where it lands in one that already has that file, WHOEVER wrote it. There is one rule and
#: no third case: an existing `.env.example` is never overwritten. Telling a file an earlier init
#: wrote from the project's own means reading its content, and any such classification is wrong in
#: the case that costs something - a pipeline-written example the operator has since added their own
#: keys to still looks like the template, and "refresh it in place" would destroy them, which is the
#: ownership assumption issue #108 is about.
PIPELINE_EXAMPLE = ".env.pipeline.example"

ENV_FILE = ".env"
COSMIC_RAY = "cosmic-ray.toml"
NO_MISTAKES = ".no-mistakes.yaml"

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


@dataclass(frozen=True)
class Survey:
    """What the repository already holds, and where the pipeline's own files may therefore go."""

    root: Path
    env_example_exists: bool
    pipeline_example_exists: bool
    env_exists: bool
    no_mistakes_exists: bool
    cosmic_ray_present: bool

    @property
    def greenfield(self) -> bool:
        return not (
            self.env_example_exists
            or self.pipeline_example_exists
            or self.env_exists
            or self.no_mistakes_exists
            or self.cosmic_ray_present
        )

    @property
    def existing_examples(self) -> tuple[str, ...]:
        """Every example file already on disk, project's own first, the pipeline's second."""
        return tuple(
            name
            for name, present in (
                (PROJECT_EXAMPLE, self.env_example_exists),
                (PIPELINE_EXAMPLE, self.pipeline_example_exists),
            )
            if present
        )

    @property
    def template_target(self) -> str:
        """Where the pipeline's `env.example` may be written without taking a name it does not own.

        ANY existing example makes it `.env.pipeline.example`. `.env.example` is off limits once it
        exists, whoever wrote it - nothing is overwritten, so provenance decides nothing and is
        never asked. And an existing `.env.pipeline.example` IS the pipeline's example: writing a
        fresh copy of the same template to `.env.example` instead would shadow the operator's
        filled-in one under another name, so a chosen model or provider would quietly revert to a
        template default with nothing anywhere reporting it.
        """
        return PROJECT_EXAMPLE if not self.existing_examples else PIPELINE_EXAMPLE

    @property
    def assemble_sources(self) -> tuple[str, ...]:
        """The example files a fresh `.env` is assembled from, in the order they are named.

        Every example that EXISTS is a source - dropping one silently reverts whatever it declared
        to another file's value - plus the target, when step 2a is about to create it.

        The order answers who wins a conflict, by the one rule this whole path obeys: **the LAST
        source to declare a key wins**. So the pipeline's template is named after the project's own
        example, its defaults filling in what that example does not declare; and in the merge form
        the live `.env` is named after every example, so the operator's own values win outright.
        """
        existing = self.existing_examples
        if self.template_target in existing:
            return existing
        return existing + (self.template_target,)

    @property
    def merge_sources(self) -> tuple[str, ...]:
        """The sources a merge into an EXISTING `.env` names: the PIPELINE's example, then the live
        file. Deliberately NOT `assemble_sources` - the project's own `.env.example` is a source
        when a `.env` is being CREATED and must not be one when it already exists.

        Last-wins protects every key the live file declares. It does nothing for keys the live file
        OMITS, and an example's values are dummies and defaults by construction: a committed
        `.env.example` that drifted ahead of a filled-in `.env` would write `your_api_key_here`, or
        a `DRY_RUN=false` default, into the live file where the app had been falling back to its own
        in-code default. Nothing would report it - the value is syntactically valid, the read-back
        passes because the example declared it and it survived, and a dummy carries no `REPLACE_ME`
        for the run-time refusal to catch. A value nobody chose, arriving silently, through this
        module's own remedy. A live `.env` is already the operator's chosen configuration; the
        project's example describes what the app's config should CONTAIN, not what this operator
        chose.
        """
        return (self.template_target, ENV_FILE)

    @property
    def cosmic_ray_required_by(self) -> tuple[str, ...]:
        return () if self.cosmic_ray_present else COSMIC_RAY_REQUIRED_BY


def survey(root: Path | str = ".") -> Survey:
    root = Path(root)
    return Survey(
        root=root,
        env_example_exists=(root / PROJECT_EXAMPLE).is_file(),
        pipeline_example_exists=(root / PIPELINE_EXAMPLE).is_file(),
        env_exists=(root / ENV_FILE).is_file(),
        no_mistakes_exists=(root / NO_MISTAKES).is_file(),
        cosmic_ray_present=(root / COSMIC_RAY).is_file(),
    )


def report_lines(found: Survey) -> list[str]:
    lines = [f"greenfield: {'yes' if found.greenfield else 'no'}"]

    if found.env_example_exists:
        lines.append(
            f"{PROJECT_EXAMPLE}: EXISTS - never overwrite it, whoever wrote it. "
            f"The pipeline's template goes to {PIPELINE_EXAMPLE}, and the two are assembled with "
            f"`env_assemble.py assemble`, never `cat` (issue #108)."
        )
    else:
        lines.append(
            f"{PROJECT_EXAMPLE}: absent - the pipeline's template may take that name."
        )

    if found.pipeline_example_exists:
        lines.append(
            f"{PIPELINE_EXAMPLE}: EXISTS - leave it alone too. Never-overwrite is the rule for BOTH "
            f"example files, so an earlier init's copy keeps whatever was edited into it; the only "
            f"file ever written over is {ENV_FILE}, through the one merge below."
        )
    else:
        lines.append(f"{PIPELINE_EXAMPLE}: absent.")

    if found.env_exists:
        lines.append(
            f"{ENV_FILE}: EXISTS and holds live credentials, so merge IN PLACE rather than "
            f"replacing it: "
            f"`python3 scripts/env_assemble.py assemble --out {ENV_FILE} "
            f"{' '.join(found.merge_sources)} --force`. The live file is named as its OWN LAST "
            f"SOURCE, because the last source to declare a key wins - so every value you already "
            f"chose beats the template's default for that key, and every pipeline key you do not "
            f"have yet is added. `assemble` reads every source before it writes anything, and the "
            f"read-back then proves every key the live file declared survived, which is what makes "
            f"this safe where a bare --force is not. Name it FIRST instead and the template's "
            f"`REPLACE_ME` key and default GATE_MODEL/GATE_PROVIDER/AUTHOR_FAMILY/MUTATION_POLICY "
            f"overwrite yours. Assembling anywhere else configures nothing: every gate reads "
            f"{ENV_FILE} and no other path."
        )
    else:
        lines.append(
            f"{ENV_FILE}: absent - assemble it: "
            f"`python3 scripts/env_assemble.py assemble --out {ENV_FILE} "
            f"{' '.join(found.assemble_sources)}`. The pipeline's template is named LAST, so its "
            f"defaults fill in whatever the project's own example does not declare."
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
