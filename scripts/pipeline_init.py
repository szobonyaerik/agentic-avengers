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
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402

#: Where the pipeline's own env template lands in a repository that has no `.env.example`...
PROJECT_EXAMPLE = ".env.example"
#: ...and where it lands in one that already has that file, WHOEVER wrote it. There is one rule and
#: no third case for WRITING: an existing example is never overwritten, so provenance decides nothing
#: about what may be written and is never asked there. A pipeline-written example the operator has
#: since added their own keys to still looks like the template, and "refresh it in place" would
#: destroy them, which is the ownership assumption issue #108 is about. Content DOES decide source
#: ORDER (`assemble_sources`) - a different question, and one where reading the file is safe because
#: the answer only ever picks which source wins a key.
PIPELINE_EXAMPLE = ".env.pipeline.example"

#: The template this pipeline ships, and the one `/pipeline-init` step 2a copies. `install.sh`
#: vendors `docs/templates/` beside `scripts/`, so this path resolves in the canonical repository
#: and in a vendored consumer alike.
SHIPPED_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "docs" / "templates" / "env.example"
)

#: The assembler this report tells the operator to run, resolved from THIS file's own location
#: rather than written down. `env_assemble.py` ships beside `pipeline_init.py` in every install
#: shape, so the printed command runs verbatim under the Claude Code plugin (where the pipeline
#: lives at `$CLAUDE_PLUGIN_ROOT` and nothing puts `scripts/` in the operator's repository), under
#: `install.sh` vendoring, and in this repository. A hardcoded `scripts/env_assemble.py` resolved in
#: none of them: `commands/pipeline-init.md` tells the operator in bold to run the command step 0
#: printed rather than a path written elsewhere, so it answered `can't open file` and the pipeline's
#: keys never reached the `.env` every gate reads.
ASSEMBLER = Path(__file__).resolve().parent / "env_assemble.py"

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
    #: Every example on disk whose bytes are IDENTICAL to the shipped template, so it carries no
    #: operator decision. An unreadable example, or a shipped template that cannot be located, is
    #: absent from this set: an unanswerable comparison resolves to EDITED, which is the safe
    #: direction - it can only make an operator's file win a key, never lose one.
    fresh_examples: frozenset[str] = frozenset()

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
        exists, whoever wrote it - nothing is overwritten, so provenance decides nothing HERE and is
        never asked. And an existing `.env.pipeline.example` IS the pipeline's example: writing a
        fresh copy of the same template to `.env.example` instead would shadow the operator's
        filled-in one under another name, so a chosen model or provider would quietly revert to a
        template default with nothing anywhere reporting it.
        """
        return PROJECT_EXAMPLE if not self.existing_examples else PIPELINE_EXAMPLE

    def is_fresh_template(self, name: str) -> bool:
        """Whether `name` carries no operator decision - the shipped template's bytes, unedited.

        A file step 2a has yet to create counts as fresh: `cp -n` writes the shipped template byte
        for byte, so the answer is the same before and after the copy. That is the whole point -
        see `assemble_sources`.
        """
        return name in self.fresh_examples or name not in self.existing_examples

    @property
    def pipeline_example(self) -> str:
        """WHICH FILE HOLDS THE SHIPPED TEMPLATE - already, or once step 2a runs.

        That is the question `assemble_sources` needs, and only that one: there it names the source
        that supplies whatever nothing else declares. An example on disk byte-identical to the
        shipped template already IS that template, under whatever name it happens to carry, so
        nothing further needs creating or naming. Only when no such file exists does step 2a have
        work to do, and then it is `template_target`.

        **It is the WRONG question for a merge**, and answering both with it was a defect: see
        `merge_example`.

        Deciding this by CONTENT is what makes it stable, for the same reason the order is: step 2a's
        own `cp -n` changes presence, and `template_target` is decided purely by presence. In a
        repository with no examples at all the target was `.env.example` before the copy and
        `.env.pipeline.example` after it - so the single authority named a file that had never been
        created, and `assemble` refused with `cannot read .env.pipeline.example`.
        """
        for name in self.existing_examples:
            if name in self.fresh_examples:
                return name
        return self.template_target

    @property
    def merge_example(self) -> str:
        """WHICH FILE CARRIES THE OPERATOR'S PIPELINE CONFIGURATION - the merge's one non-live source.

        A merge names exactly two files, this and the live `.env`, so this one has to be the file
        holding the operator's decisions, not whichever pristine copy happens to be on disk first.
        Answering it with `pipeline_example` dropped them: with `.env.example` byte-identical to the
        shipped template and `.env.pipeline.example` the copy the operator actually filled in, the
        merge named the PRISTINE one and the assembled `.env` came back carrying
        `GATE_MODEL=google/gemini-3.1-pro-preview` and `MUTATION_POLICY=advisory` instead of the
        chosen `deepseek/deepseek-chat` and `enforce` - values that parse, survive the read-back
        because a source declared them, and carry no `REPLACE_ME` for the run-time refusal. The
        CREATE branch got that state right, so the two branches disagreed about one file in one
        repository, and this is the branch that writes a live `.env`.

        Three answers, in order, and each is the pipeline's own file rather than the project's:

        1. An EDITED `.env.pipeline.example`. That name is the pipeline's by construction - it is
           where the template goes once `.env.example` is taken - so edits to it are edits to the
           pipeline's configuration, and they are what the merge exists to carry forward.
        2. Otherwise any PRISTINE example, which is the shipped template verbatim under whatever
           name it carries. A greenfield init writes it to `.env.example`, and there that file IS
           the pipeline's example.
        3. Otherwise `template_target` - nothing on disk holds the template yet and step 2a is about
           to write it there.

        An edited `.env.example` is never the answer, and that is the standing rule rather than an
        oversight: a project that already had that file owns it, it describes what the app's config
        should CONTAIN, and last-wins does nothing for a key the live `.env` omits - so its dummies
        would arrive in a live file as values nobody chose.

        Where both branches CAN name the same file they do: with `.env.pipeline.example` edited it
        is the merge's source and also the last, winning source of `assemble_sources`.
        """
        if PIPELINE_EXAMPLE in self.existing_examples and not self.is_fresh_template(
            PIPELINE_EXAMPLE
        ):
            return PIPELINE_EXAMPLE
        return self.pipeline_example

    @property
    def assemble_sources(self) -> tuple[str, ...]:
        """The example files a fresh `.env` is assembled from, in the order they are named.

        Every EDITED example is a source - dropping one silently reverts whatever it declared to
        another file's value - plus **exactly one** copy of the shipped template, `pipeline_example`.
        A second fresh copy is byte-identical to the first, so naming it adds no key and only makes
        the answer depend on how many copies happen to be lying around.

        CONTENT decides the SET as well as the order, and both for the same reason. The set used to
        fold in `template_target`, which is decided purely by presence, so it moved across step 2a's
        own `cp -n`: a repository with no examples printed `--out .env .env.example` before the copy
        and `--out .env .env.example .env.pipeline.example` after it, naming a file that was never
        created, and `assemble` refused with `cannot read .env.pipeline.example` rather than writing
        anything.

        The order answers who wins a conflict, by the one rule this whole path obeys: **the LAST
        source to declare a key wins**, and by the one principle both branches obey: **the more
        operator-owned file goes later**. What decides which that is, is **CONTENT**: an example
        whose bytes are IDENTICAL to the shipped template is a FRESH template, carries no operator
        decision, and is named FIRST, supplying only what nothing else declares. An example that
        DIFFERS from the shipped template was edited by someone, so it is operator-owned and is
        named after every fresh one. Within a class, `existing_examples` order is kept.

        **PRESENCE cannot decide this, because step 2a's own `cp -n` is what changes presence.**
        Asked before the copy the survey said `(.env.pipeline.example, .env.example)` and asked
        after it said the reverse, so the single authority gave one repository two contradictory
        answers - and the second one let a freshly written, unmodified shipped template beat a
        team's committed, filled-in `.env.example`. Content is stable across the copy, so the answer
        is the same before step 2a, after step 2a, and on a second `/pipeline-init` of an
        already-initialised repository.

        Both directions were wrong once and both are pinned. Named last, a fresh copy beat a
        configured example: a repository whose `.env.example` an earlier init wrote and the team
        filled in and committed gets `.env.pipeline.example` as its target, `cp -n` writes the
        SHIPPED template there, and last-wins handed the assembled `.env` the shipped defaults.
        Named first unconditionally, an operator's filled-in `.env.pipeline.example` lost every key
        it shares with `.env.example` - and since both derive from the same template, that is all of
        them.

        Neither errors: the values are syntactically valid, the read-back passes because a source
        declared them and they survived, and a default carries no `REPLACE_ME` for the run-time
        refusal to catch. That is a value nobody chose arriving silently, which is the class this
        module exists to close.
        """
        named = set(self.existing_examples) | {self.pipeline_example}
        ordered = tuple(
            name for name in (PROJECT_EXAMPLE, PIPELINE_EXAMPLE) if name in named
        )
        fresh = tuple(name for name in ordered if self.is_fresh_template(name))
        return fresh[:1] + tuple(name for name in ordered if name not in fresh)

    @property
    def merge_sources(self) -> tuple[str, ...]:
        """The sources a merge into an EXISTING `.env` names: the PIPELINE's example, then the live
        file. Same principle as `assemble_sources` - the more operator-owned file goes later, and
        nothing is more operator-owned than a live `.env` - so the example is first here whatever
        its content. What differs is the SOURCE SET, not the principle: the project's own EDITED
        `.env.example` is a source when a `.env` is being CREATED and must not be one when it
        already exists.

        `merge_example` and not `pipeline_example`, because the two branches ask different questions
        of the same disk - which file HOLDS the shipped template, and which file carries the
        OPERATOR'S decisions. They are the same file most of the time and sharing one answer looked
        harmless, until a pristine `.env.example` beside a filled-in `.env.pipeline.example` made
        the merge drop everything the operator had chosen. Neither is `template_target`, so no
        answer here moves across step 2a's `cp -n`: in a repository with no examples the target was
        `.env.example` before the copy and `.env.pipeline.example` after it, and the second one
        named a file nothing created.

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
        return (self.merge_example, ENV_FILE)

    @property
    def cosmic_ray_required_by(self) -> tuple[str, ...]:
        return () if self.cosmic_ray_present else COSMIC_RAY_REQUIRED_BY


def _shipped_bytes() -> bytes | None:
    """The shipped template's bytes, or None when it cannot be located or read.

    None is not an error: this survey never blocks. It means no example can be shown to be a fresh
    template, so every one of them is treated as EDITED - the safe direction, since it can only make
    an operator's file win a key, never lose one.
    """
    try:
        return SHIPPED_TEMPLATE.read_bytes()
    except OSError:
        return None


def _fresh_examples(root: Path) -> frozenset[str]:
    """The examples on disk that are byte-identical to the shipped template.

    An example that cannot be read is absent from the set for the same reason a missing shipped
    template empties it: an unanswerable comparison resolves to EDITED, never to fresh.
    """
    shipped = _shipped_bytes()
    if shipped is None:
        return frozenset()

    fresh = set()
    for name in (PROJECT_EXAMPLE, PIPELINE_EXAMPLE):
        try:
            if (root / name).read_bytes() == shipped:
                fresh.add(name)
        except OSError:
            continue
    return frozenset(fresh)


def survey(root: Path | str = ".") -> Survey:
    root = Path(root)
    return Survey(
        root=root,
        env_example_exists=(root / PROJECT_EXAMPLE).is_file(),
        pipeline_example_exists=(root / PIPELINE_EXAMPLE).is_file(),
        env_exists=(root / ENV_FILE).is_file(),
        no_mistakes_exists=(root / NO_MISTAKES).is_file(),
        cosmic_ray_present=(root / COSMIC_RAY).is_file(),
        fresh_examples=_fresh_examples(root),
    )


def report_lines(found: Survey) -> list[str]:
    assembler = shlex.quote(str(ASSEMBLER))
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
            f"`python3 {assembler} assemble --out {ENV_FILE} "
            f"{' '.join(found.merge_sources)} --force`. The live file is named as its OWN LAST "
            f"SOURCE, because the last source to declare a key wins - so every value you already "
            f"chose beats the template's default for that key, and every pipeline key you do not "
            f"have yet is added. `assemble` reads every source before it writes anything, and the "
            f"read-back then proves every key the live file declared survived, which is what makes "
            f"this safe where a bare --force is not. Name it FIRST instead and the template's "
            f"`REPLACE_ME` key and default GATE_MODEL/GATE_PROVIDER/AUTHOR_FAMILY/MUTATION_POLICY "
            f"overwrite yours. The one example named is the file carrying YOUR pipeline "
            f"configuration - an edited {PIPELINE_EXAMPLE} when there is one, otherwise the copy of "
            f"the shipped template - and never a {PROJECT_EXAMPLE} the project already owned, whose "
            f"dummies would arrive as values nobody chose for every key {ENV_FILE} omits. "
            f"Assembling anywhere else configures nothing: every gate reads {ENV_FILE} and no other "
            f"path."
        )
    else:
        lines.append(
            f"{ENV_FILE}: absent - assemble it: "
            f"`python3 {assembler} assemble --out {ENV_FILE} "
            f"{' '.join(found.assemble_sources)}`. The last source to declare a key wins, and what "
            f"decides the order is CONTENT: an example still byte-identical to the shipped template "
            f"carries nobody's decision, so it is named first and only fills in what nothing else "
            f"declares; one that has been edited is named after it, so anything you have already "
            f"configured beats a template default for that key. Content rather than presence, "
            f"because step 2a's own `cp -n` changes presence - this command prints the same order "
            f"before that copy and after it."
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
    raise SystemExit(guard_scope.run(__file__, main))
