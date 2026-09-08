#!/usr/bin/env python3
"""The Verifier's bookkeeping, done by a script instead of by a model, on every commit, diff-scoped.

A scout measured all 46 Verifier findings across 8 phases of one feature. **Twelve of them (26%)
were bookkeeping** - about the pipeline's own gate stamps, traceability rows and spec headings. On
the worst phase that was 45%, and **attempts 2 and 5 produced nothing but bookkeeping**: roughly 70
minutes of wall clock and ~410k subagent tokens for four stamp-freshness observations.

All twelve were mechanically decidable, and several said so in their own text - one names its
detection method as a `grep`, and four are `spec_gate_cache.py check`, which already existed and
already returned an exit code. The Verifier was shelling out to it by hand, once per phase, and
calling the result a finding.

So they move here. Three checks, no model, run from `hook_verifier.sh` and from CI:

  1. **Traceability** - every requirement id a spec declares appears in some `test-mapping.md` row
     for its phase. `binding: none` requirements are exempt by construction: the tiered-binding rule
     says they get no test, so demanding a row for one would hand the suite back the per-id
     multiplier that rule removed.
  2. **Stamp freshness** - `spec_gate_cache.py check` is fresh for every spec in the phase. The
     defect this catches recurred **twice, six attempts apart, in one phase**, precisely because
     nothing checked it continuously.
  3. **Structure** - every spec still has its `## Acceptance criteria` heading. One amendment
     deleted one, and the finding that caught it noted that no pipeline script parses the heading,
     so nothing broke - which is exactly the argument for a script that does.

**And the ROW is held to its test, not only the id to some row** (issue #97, folded #122). A
`test-mapping.md` row is the claim that a named test proves a named requirement. The check above
confirmed the id appeared somewhere and the test existed somewhere, which made the claim itself
unfalsifiable - a row could pair any id with any test. `skills/tdd` already asks every test to list
the ids it covers, so `row_claims` pairs each row's ids with each row's tests and asks the test's own
text: a test that lists ids and NOT the row's is a finding; a test that lists none is counted and
named, never held, since a corpus written before that instruction is exactly that shape.

What this deliberately does NOT do is judge anything. Coverage judged per `binding:` and adversarial
execution against secrets, resource lifetimes and concurrency invariants stay with the Verifier: they
are the two jobs no script can do, and they produced all three of its user-visible defects. The third
job it used to have - a dedicated reader for gamed tests - is gone with the cross-family reading pass
that carried it, and nothing inherits it.

**Scope: you are responsible for what you change.** Run with no target it checks the phases the
current diff touches, which is what lets it run on *every* commit rather than only on `--full` - a
check that runs once at the end is exactly the "nothing checked it continuously" this replaces.
`--all` audits every phase in the repository, which is what CI's `--full` does. The distinction
matters to a consumer repo upgrading to this version: a full audit would hard-fail its CI over
locked, pre-rule phases nobody touched. It is the same rule as `scripts/doc_read_path.py` (whose
`changed_paths()` this reuses rather than re-implementing), `verifier_evidence.py`, the spec re-gate
cache and the mutation gate - and it behaves the same way at the edge: when git cannot say what
changed, the scope is unknowable, so **nothing is enforced and the check says so out loud**. Falling
back to enforcing everything is the hostage failure the scoping removes.

The mode it ran in is always printed, because a silent fallback is how a check comes to mean
something other than what its caller believes.

Usage:
    verifier_precheck.py [--root .]                        the phases the current diff touches
    verifier_precheck.py <phase-dir> [<phase-dir> ...]     exit 0 = clean, 1 = findings, 2 = error
    verifier_precheck.py --all [--root .]                  every phase under docs/features/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_scope  # noqa: E402

CLEAN = 0
FINDINGS = 1
ERROR = 2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ONE declaration regex and ONE diff-scope mechanism, both imported rather than copied. This module
# held its own copy of the declaration regex, and both copies went blind on table-formatted specs at
# the same moment: the cap read `0/12` and this check reported zero ids owed a trace, so it passed
# vacuously on the very spec it exists to hold. A second copy of a rule is the copy that drifts.
# The diff scope comes from `applicability.py`, which owns the whole boundary this check sits on.
from applicability import (  # noqa: E402
    ApplicabilityError,
    changed_paths,
    excepted,
    report_unenforced,
    touched,
)
from requirement_cap import declared_bindings  # noqa: E402
import spec_done_guard  # noqa: E402 — the one place "what does this `done` certify" is decided
import spec_gate_state  # noqa: E402 — the one place "is this spec gated" is decided

ACCEPTANCE_HEADING = re.compile(
    r"^##+[ \t]*Acceptance criteria\b", re.IGNORECASE | re.MULTILINE
)


def bound_requirements(
    spec: Path, text: str | None = None
) -> tuple[list[str], list[str]]:
    """(requirements owed a trace, requirements exempt) for one spec.

    A requirement whose declaration says `binding: none` is exempt. One that declares no binding at
    all is **owed a trace**, not exempt: a missing binding is a spec defect, and treating it as
    exempt would let the absence of a declaration buy the absence of a test.

    Both the layout and where the binding sits inside it come from `requirement_cap`, which owns
    them. This module used to re-derive the second half from the declaration line alone, so widening
    the shared regex to accept headings and ordered lists silently moved a block later and gave it a
    wronger message.

    `text` is the already-decoded body when the caller has one, so the spec is read once per check
    rather than once here and again for its heading.
    """
    if text is None:
        body, finding = read_artifact(spec)
        if finding is not None:
            return [], []
        text = body or ""
    owed: list[str] = []
    exempt: list[str] = []
    for rid, binding in declared_bindings(text):
        if rid in owed or rid in exempt:
            continue
        (exempt if binding == "none" else owed).append(rid)
    return owed, exempt


def read_artifact(path: Path) -> tuple[str | None, str | None]:
    """(decoded text, finding) for one artifact this module reads — exactly one is not None.

    The decision an unreadable artifact forces is made HERE, at the read, once. The readers below
    used to skip what they could not decode, which is right for THEM - a mapping that yields no rows
    and one that could not be opened both contribute nothing to a set - and wrong for the phase's
    verdict: an unreadable `test-mapping.md` made `traced_ids` return nothing, so a spec whose
    requirements are all `binding: none` produced a fully CLEAN precheck over an artifact nothing
    could read, and a spec with owed ids produced the misattributed finding "appears in no
    test-mapping.md row" whose remedy is to add a row that is already there. An unreadable test file
    does the mirror, hiding real definitions so a live row reads as naming a missing test.

    So: an unreadable artifact is an UNDECIDABLE check, never an absent obligation. **Every** way a
    path that exists can fail to be read answers here, not only a decode failure — a dangling
    symlink and a mode-000 file raise `OSError`, and swallowing those restored the exact
    misattribution above. The one distinction that stays real is ABSENT vs UNDECIDABLE: a path that
    does not exist is never yielded by the globs below and keeps its own semantics (a phase that
    legitimately owes no mapping is clean), while a path the glob DID yield and the read then
    refused is named.
    """
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, (
            f"{path}: unreadable ({exc}). Nothing this check derives from it can be trusted, "
            f"so the phase is not clean - it is undecided."
        )


def read_mappings(phase_dir: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """[(spec directory name, body)] for the phase's mappings, plus a finding per unreadable one.

    Read once and handed to `traced_ids` and `named_tests`, which both used to open the same files
    themselves, after a preliminary sweep had already opened them a third time.
    """
    texts: list[tuple[str, str]] = []
    unreadable: list[str] = []
    for mapping in sorted(phase_dir.glob("specs/*/test-mapping.md")):
        if mapping.is_dir():
            continue
        text, finding = read_artifact(mapping)
        if finding is not None:
            unreadable.append(finding)
            continue
        texts.append((mapping.parent.name, text or ""))
    return texts, unreadable


def traced_ids(mappings: list[tuple[str, str]]) -> set[str]:
    """Every requirement id mentioned anywhere in the phase's `test-mapping.md` tables.

    Mentioned, not parsed into columns: a journey row lists several ids in one cell, and a mapping
    format this over-fits would fail on a table that is perfectly legible to the reader.
    """
    found: set[str] = set()
    for _spec_name, text in mappings:
        found.update(re.findall(r"R\d+\.\d+\.\d+", text))
    return found


#: A test name a row may point at. Deliberately the pytest convention and nothing wider: a cell
#: holding prose would otherwise yield "names" nobody wrote.
TEST_NAME = re.compile(r"\btest_[A-Za-z0-9_]+")

#: A skip decorator directly above a test. Python-specific, and that limit is stated in
#: `named_tests`' docstring rather than implied.
SKIP_DECORATOR = re.compile(r"^[ \t]*@(?:pytest\.mark\.)?skip(?:if)?\b", re.MULTILINE)


def _test_root(phase_dir: Path) -> Path | None:
    """`tests/<feature>/<n>-<slug>`, or the older `tests/<n>-<slug>` — whichever exists.

    The same resolution `verifier_evidence` and `hook_verifier.sh` use, so a row is held against
    exactly the tree the gate then runs. A project laid out otherwise resolves to None and is not
    held at all, which is the fail-open every check on this boundary uses.

    The root is the directory ABOVE `docs/`, recognised by the layout itself
    (`docs/features/<feature>/phases/<n>-<slug>`) rather than by counting parents from an assumed
    depth: counting reached `docs/` and looked for `docs/tests/<feature>/<phase>`, a path the
    canonical layout never has, so every row claim in a real repository resolved to "no readable
    test definitions" and was reported as an unreadable scope. A check that can only run on a
    layout nobody uses is the clean result this issue is about.
    """
    phase = Path(phase_dir).resolve()
    if len(phase.parents) < 5:
        return None
    if phase.parents[0].name != "phases" or phase.parents[2].name != "features":
        return None
    root = phase.parents[4]
    feature = phase.parents[1].name
    for candidate in (
        root / "tests" / feature / phase.name,
        root / "tests" / phase.name,
    ):
        if candidate.is_dir():
            return candidate
    return None


def named_tests(mappings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(test name, the mapping that names it) for every test a row points at.

    Only `test_*` tokens count. A cell holding `n/a`, a dash or the template's own placeholder names
    no test, and inventing one out of prose would report findings nobody could act on.
    """
    out: list[tuple[str, str]] = []
    for spec_name, text in mappings:
        for name in TEST_NAME.findall(text):
            out.append((name, spec_name))
    return out


#: A requirement id, as a row states it and as a test lists it (`skills/tdd`: every test lists the
#: ids it covers - in its name, its docstring, a comment or a marker; this reads the test's TEXT
#: and does not care which).
REQUIREMENT_ID = re.compile(r"R\d+\.\d+\.\d+")

#: A markdown table's separator row. Imported rather than re-spelled: `spec_done_guard` owns it.
SEPARATOR_ROW = spec_done_guard.SEPARATOR_ROW


class DefinedTest(NamedTuple):
    """One test definition: whether it is skipped, and the requirement ids its own text lists."""

    skipped: bool
    lists: frozenset[str]


def _defined_tests(tests: Path) -> tuple[dict[str, DefinedTest], list[str]]:
    """(every test defined under `tests`, by name, with what its text says; unreadable findings).

    Definition and skip detection are Python-specific, and that is the honest bound of this check:
    a project whose tests are written in another language resolves no definitions here, so every
    row would read as naming a missing test. `check_phase` therefore holds a row only when the tree
    yielded at least one definition — the scope is unreadable otherwise, not violated.

    A test's TEXT runs from the decorators above its `def` to the next definition at its own
    indentation or shallower, and the requirement ids in it are what the test itself claims to
    cover. That is what lets a row be held to its test (`row_claims`) rather than to the id's mere
    presence somewhere in the mapping.
    """
    found: dict[str, DefinedTest] = {}
    unreadable: list[str] = []
    for path in sorted(tests.rglob("*.py")):
        if "__pycache__" in path.parts or path.is_dir():
            continue
        text, finding = read_artifact(path)
        if finding is not None:
            unreadable.append(finding)
            continue
        lines = (text or "").splitlines()
        definition = re.compile(r"([ \t]*)(?:async +)?def +(test_[A-Za-z0-9_]+)")
        for index, line in enumerate(lines):
            match = definition.match(line)
            if not match:
                continue
            indent = len(match.group(1).expandtabs())
            above = lines[max(0, index - 6) : index]
            end = index + 1
            while end < len(lines):
                candidate = lines[end]
                if (
                    candidate.strip()
                    and (len(candidate) - len(candidate.lstrip()) <= indent)
                    and re.match(r"[ \t]*(?:@|(?:async +)?def |class )", candidate)
                ):
                    break
                end += 1
            # The ids a test LISTS are read from its own decorators and comments directly above
            # the `def` (contiguous, so the previous test's docstring never leaks in) plus its body
            # down to the next definition. The skip window stays the six lines it always was.
            start = index
            while start > 0 and lines[start - 1].lstrip().startswith(("@", "#")):
                start -= 1
            own = "\n".join(lines[start:end])
            found[match.group(2)] = DefinedTest(
                skipped=bool(SKIP_DECORATOR.search("\n".join(above))),
                lists=frozenset(REQUIREMENT_ID.findall(own)),
            )
    return found, unreadable


def row_claims(mappings: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """(requirement id, test name, spec directory) for every claim a table ROW makes.

    A row is a claim that THIS test proves THIS requirement - the pairing is the trace. Read by
    cell, past the header separator, so an id in one row and a test name in another are never
    paired: that pairing-by-proximity is exactly what let a row assert anything (issue #97, folded
    #122: the precheck confirmed an id appeared in SOME row and never that the row was the one the
    requirement was proved by).
    """
    out: list[tuple[str, str, str]] = []
    for spec_name, text in mappings:
        seen_separator = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            if not seen_separator:
                seen_separator = bool(SEPARATOR_ROW.fullmatch(stripped))
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) < 2:
                continue
            for rid in REQUIREMENT_ID.findall(cells[0]):
                for name in dict.fromkeys(TEST_NAME.findall(cells[1])):
                    out.append((rid, name, spec_name))
    return out


def trace_claims(
    phase_dir: Path, mappings: list[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """(findings for rows whose named test does not exist or is skipped, unreadable findings).

    The precheck used to confirm only that a requirement id appeared in SOME row, never that the
    row's claim matched the test it names — so a row was free to assert anything and the trace was
    decorative. One measured row asserted the exact opposite of its own test and every check passed.

    The phase's test tree is read whatever the rows say, because an unreadable test file is a
    finding in its own right: it is the artifact whose absence from `defined` makes a live row read
    as naming a missing test.

    **What this does NOT do, said rather than implied:** it does not read a row's prose against a
    test's assertions. Generating the claim from the test is the better fix and this is not it; a
    row whose words contradict its existing, running test still passes here.
    """
    rows = named_tests(mappings)
    tests = _test_root(phase_dir)
    defined, unreadable = _defined_tests(tests) if tests is not None else ({}, [])
    if not rows:
        return [], unreadable
    if not defined:
        print(
            f"[verifier_precheck] {phase_dir.name}: no readable test definitions under its test "
            f"tree — trace-row claims not checked",
            file=sys.stderr,
        )
        return [], unreadable
    out: list[str] = []
    for name, spec_name in sorted(set(rows)):
        if name not in defined:
            out.append(
                f"{spec_name}/test-mapping.md names {name}, and there is no test by that name in "
                f"this phase's tests ({tests}). A row that names nothing is a trace that proves "
                f"nothing."
            )
        elif defined[name].skipped:
            out.append(
                f"{spec_name}/test-mapping.md names {name}, which is SKIPPED. A skipped test is "
                f"green output over a requirement nothing exercises."
            )
    # The row's PAIRING, not only its parts: a row that says `R1.1.3 | test_journey` is the claim
    # that test_journey proves R1.1.3, and the test's own text either lists that id or it does not.
    # A test that lists ids and NOT this one contradicts the row - a finding. A test that lists no
    # id at all can corroborate nothing, and is counted and named rather than held (§3a): tests
    # written before `skills/tdd` asked for the ids are exactly that shape, and holding them would
    # owe every consumer repo a rewrite of its test corpus.
    uncorroborated = 0
    for rid, name, spec_name in sorted(set(row_claims(mappings))):
        test = defined.get(name)
        if test is None or test.skipped:
            continue  # already a finding above
        if not test.lists:
            uncorroborated += 1
            continue
        if rid not in test.lists:
            out.append(
                f"{spec_name}/test-mapping.md says {rid} is proved by {name}, but {name} lists "
                f"{', '.join(sorted(test.lists))} and not {rid}. The row is not the row that "
                f"requirement was proved by; a trace the test itself contradicts is decorative."
            )
    if uncorroborated:
        report_unenforced(
            "verifier_precheck",
            uncorroborated,
            f"row claim(s) in {phase_dir.name} name a test whose text lists NO requirement id, so "
            f"the row cannot be corroborated from the test (skills/tdd: every test lists the ids "
            f"it covers) - counted, not held",
        )
    return out, unreadable


def stamp_fresh(spec: Path) -> bool | None:
    """True when the spec's body is unchanged since the gate judged it; None when undecidable.

    Delegated to `spec_gate_state`, which owns what "gated" means (issue #42). This module held its
    own implementation of the question while the stage resolver asked a weaker one, so one spec was
    gated and ungated at the same time depending on which part of the pipeline asked. A second copy
    of a rule is the copy that drifts - the same reason the declaration regex above is imported.
    """
    return spec_gate_state.stamp_fresh(spec)


def excepted_stamp(phase_dir: Path, spec: Path) -> str | None:
    """Why a stale spec-gate stamp is not blocking, or None when it still blocks.

    A stamp goes stale whenever the spec body no longer matches what the gate judged — the same
    condition whether the body changed because it needs re-gating or because the gate provider was
    unreachable when it should have been re-run. Re-gating is the remedy that clears it under
    ordinary circumstances; a disclosed **exception** (`applicability.py`, rule `spec-gate`) is the
    one that still clears it when the provider is down, since recording an exception makes no gate
    call at all. An amendment does not: it re-verifies requirement ids at the Verifier, and never
    touches the spec-gate hash this check reads.

    An unreadable exception ledger grants nothing and says so — under-report, exactly as the
    resolver does everywhere else on this boundary.
    """
    try:
        record = excepted(phase_dir, "spec-gate", spec.resolve().parent.name)
    except ApplicabilityError as exc:
        print(
            f"[verifier_precheck] {phase_dir.name} has an exception ledger this cannot read "
            f"({exc}). No exception is granted.",
            file=sys.stderr,
        )
        return None
    return record.describe() if record else None


def done_stamp_problem(spec: Path, body: str) -> str | None:
    """A `status: done` whose bound digest no longer matches the mapping and tests here now.

    The stamp is bound at the hook that checks it (`spec_done_guard.bind`, issue #97); this is the
    reader at the other end, and the two are one rule by construction because both ask
    `spec_done_guard.done_subject_digest`. A `done` that carries NO digest is a stamp from before the
    rule or from a write no hook saw - counted and named on stderr, never held (§3a), since its
    remedy would be to rewrite a stamp this check has no evidence against. A spec that is not `done`
    owes nothing here.
    """
    if spec_gate_state.frontmatter(body).get("status") != spec_done_guard.DONE:
        return None
    state = spec_done_guard.bound(spec)
    if state is None:
        report_unenforced(
            "verifier_precheck",
            1,
            f"{spec} is `status: done` with no `{spec_done_guard.DONE_DIGEST_FIELD}` - what that "
            f"stamp certifies is UNKNOWABLE (stamped before the rule, or through a write no hook "
            f"sees), so it is counted rather than held",
        )
        return None
    if state:
        return None
    return (
        f"{spec}: `status: done` was bound to different bytes than are here now - its "
        f"test-mapping.md or its own tests changed AFTER it declared done, so the stamp certifies "
        f"work this spec no longer has. Write the spec again through a tool write (it already says "
        f"`status: done`); the hook re-checks the mapping and the suite over what is actually there "
        f"and re-binds, whether or not the spec has shipped."
    )


def check_phase(phase_dir: Path) -> list[str]:
    """Every mechanical finding for one phase, as lines. Empty means clean.

    The mappings here and the phase's test files inside `trace_claims` are opened once each: there
    is deliberately no preliminary sweep re-reading them to discover unreadable ones — the reader
    that opens an artifact is the one that decides what its unreadability means, which is also the
    only shape in which that decision cannot be forgotten. A spec is opened **twice**, and that is
    stated rather than rounded down: once below, and once inside `spec_gate_state.freshness`, which
    owns the stamp question and reads the file itself. Threading a decoded body through that owner
    is a larger change than this check warrants.
    """
    out: list[str] = []
    specs = sorted(phase_dir.glob("specs/*/spec.md"))
    if not specs:
        return out
    mappings, unreadable = read_mappings(phase_dir)
    out.extend(unreadable)
    traced = traced_ids(mappings)
    claims, unreadable_tests = trace_claims(phase_dir, mappings)
    out.extend(unreadable_tests)
    out.extend(claims)

    for spec in specs:
        body, finding = read_artifact(spec)
        if finding is not None:
            out.append(finding)
            continue
        body = body or ""
        owed, _exempt = bound_requirements(spec, body)
        untraced = [rid for rid in owed if rid not in traced]
        if untraced:
            out.append(
                f"{spec}: {len(untraced)} requirement id(s) appear in no test-mapping.md row for "
                f"this phase: {', '.join(untraced)}. (binding: none ids are exempt and not counted.)"
            )

        if not ACCEPTANCE_HEADING.search(body):
            out.append(
                f"{spec}: no `## Acceptance criteria` heading. Nothing parses it, which is exactly "
                f"why an edit can delete it and no gate notice."
            )

        stamp_problem = done_stamp_problem(spec, body)
        if stamp_problem is not None:
            out.append(stamp_problem)

        fresh = stamp_fresh(spec)
        if fresh is False:
            why = excepted_stamp(phase_dir, spec)
            if why is not None:
                report_unenforced(
                    "verifier_precheck",
                    1,
                    f"{spec} stamp is stale but a recorded exception covers it — {why}",
                )
            else:
                out.append(
                    f"{spec}: the spec body has changed since the gate judged it — the spec is "
                    f"UNGATED at this commit. Write it again to re-gate, or record a disclosed "
                    f"exception (scripts/applicability.py record {phase_dir} --rule spec-gate "
                    f"--subject {spec.resolve().parent.name} --reason-file <f>)."
                )
        elif fresh is None:
            out.append(
                f"{spec}: gate-stamp freshness could not be decided (fail closed)."
            )
    return out


def phase_dirs(root: Path) -> list[Path]:
    return sorted(
        p.parent
        for p in Path(root).glob("docs/features/*/phases/*/specs")
        if p.is_dir()
    )


def changed_phase_dirs(root: Path) -> list[Path] | None:
    """The phases the current diff touches, or None when git cannot say what changed.

    None is not "nothing changed" and must never be read as one: the scope is unknowable, so the
    caller enforces nothing and says so. Falling back to every phase would hold a repository hostage
    to history it has not touched, which is the whole reason this is scoped.
    """
    scope = changed_paths(Path(root))
    if scope is None:
        return None
    return [phase for phase in phase_dirs(root) if touched(phase, scope)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phases", nargs="*", type=Path)
    parser.add_argument(
        "--all", action="store_true", help="every phase under docs/features/"
    )
    parser.add_argument("--root", default=".", type=Path)
    args = parser.parse_args(argv)

    if args.all:
        targets, mode = phase_dirs(args.root), "--all: every phase under docs/features/"
    elif args.phases:
        targets, mode = (
            list(args.phases),
            "the phase directories named on the command line",
        )
    else:
        scoped = changed_phase_dirs(args.root)
        if scoped is None:
            print(
                f"[verifier_precheck] git cannot say what changed under {args.root}, so the scope "
                f"is unknowable and no phase is pre-checked. Run `--all` for a full audit.",
                file=sys.stderr,
            )
            return CLEAN
        targets, mode = scoped, "diff-scoped: the phases this diff touches"

    print(
        f"  verifier pre-check scope — {mode} ({len(targets)} phase(s))",
        file=sys.stderr,
    )
    if not targets:
        print(
            "  (no phases with specs in scope — nothing to pre-check)", file=sys.stderr
        )
        return CLEAN

    findings: list[str] = []
    for phase in targets:
        if not Path(phase).is_dir():
            print(
                f"[verifier_precheck] no such phase directory: {phase}", file=sys.stderr
            )
            return ERROR
        findings.extend(check_phase(Path(phase)))

    if not findings:
        return CLEAN
    print(
        "verifier pre-check: mechanical findings (these are bookkeeping, not judgement — the "
        "Verifier used to raise them by hand, once per phase, and 26% of everything it produced "
        "was this class):",
        file=sys.stderr,
    )
    for line in findings:
        print(f"  ✗ {line}", file=sys.stderr)
    return FINDINGS


if __name__ == "__main__":
    raise SystemExit(guard_scope.run(__file__, main))
