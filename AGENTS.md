# Plan-Build-Verify pipeline (opencode)

This repository runs the plan-build-verify pipeline. Agents live in `.opencode/agents/`, skills in
`.opencode/skills/` (the same `SKILL.md` files Claude Code uses). **Gates fire in-session** via the
plugin `.opencode/plugin/pipeline-gates.ts`, which is an adapter over the same `scripts/hook_*.sh`
that Claude Code runs — one implementation, two runtimes. The git floor (pre-commit + CI in
`scripts/gate_ci.sh`) backstops them; all of it calls `gate_runner.py` on a fresh cross-family model.

Gates fire when work is **declared done** (a spec reaching `status: done`, a `handover.md`), never on
every code edit — you build with a red → green loop, so red is the expected state while you work.
Run `pytest tests/<feature>/<n>-<slug>/` yourself as often as you like; it costs nothing.

## Conventions (always apply)
1. **Artifacts** under `docs/features/<feature>/` (feature-level: `task-analysis.md`, `overview.md`,
   `plan.md`, `fidelity-report.md`, `scoped/review-*.md`), `docs/features/<feature>/phases/<n>-<slug>/`
   (`test-mapping.md`, `test-evidence.md`, `implementation-report.md`, `test-execution-report.md`,
   `handover.md`, `handover-archive.md`), and spec-level
   `.../phases/<n>-<slug>/specs/<n>.<k>-<subslug>/spec.md`. YAML frontmatter on each. The classes
   `scripts/doc_read_path.py`'s `READ_PATH` governs additionally carry a **`readers:` line** — see 1a.
   `fidelity-report.md`, `scoped/review-*.md`, `implementation-report.md` and
   `test-execution-report.md` are **not** in that table and are not claimed to carry one; whether
   they belong on the read path is [#29](https://github.com/szobonyaerik/agentic-avengers/issues/29),
   open. Promising the line for a class nothing instructs and nothing checks is the gap this rule
   exists to close, so the promise is scoped to what is enforced.
1a. **The read path.** Documentation cost is `size x reads x turns resident`, not size:
   `task-analysis.md` cost ~465k tokens being opened 60 times for one frontmatter field, and
   `handover.md` cost 485k-1,475k being re-read per spec of every later phase. So `handover.md` is a
   **contract card capped at 6144 bytes** (the rest in `handover-archive.md`, which nothing reads),
   `work_kind` rides in the spec's own frontmatter, spec-review reads `overview.md`'s
   `## Contracts and Decisions` header only, `test-mapping.md` is the table (evidence in
   `test-evidence.md`, read on route-back), and a verified phase's specs leave the read path in
   favour of its card. `scripts/doc_read_path.py` is the table and the check; `check --sources` is
   what stops a removed read coming back one caller at a time, and the artifact half is **diff-scoped**
   (it enforces what you changed and only counts the rest, `--all` for a full audit).
   `docs/lessons/` is untouched.
2. **Multi-spec phases + IDs.** A phase is a verifiable slice holding one or more numbered specs
   `<n>.<k>`; requirement ids `R<n>.<k>.<m>`. The Verifier runs **once per phase**, after every spec is green.
3. **Composed quality wall (per spec).** Automated Fidelity Gate on spec write (sets `fidelity_verdict`;
   NO-GO routes back) **and** human grill-me via `@spec-review` (sets `review_status: approved`). A spec
   reaches the implementer only when `fidelity_verdict != NO-GO` and `review_status: approved`. Both
   gates are also what pre-agrees the **seams** the tests get written at.
3a. **Spec-review is also the only cost gate.** `scripts/subprocess_check.py` walks `tests/` for
   spawners lacking `@pytest.mark.subprocess("<why>")` — no model, both modes, on every spec write via
   `hook_spec_review.sh`. It is the only stage that can see cost: fidelity, cross-family review and
   verification all read for *correctness*, and an expensive test is not incorrect. Not a wall-clock
   budget on purpose — one unchanged suite measured 66.43s to 137.76s across seven runs.
   `SUBPROC_CHECK_PATHS` points it at the real root when a project's tests are not at `tests/`; an
   absent root is CLEAN but reported on stderr, never a silent pass.
   **A spec already approved and implemented is re-gated on its changes only**; unchanged text was
   passed before and is not a finding. `scripts/spec_gate_cache.py` keeps the body each gate last
   **approved** (a rejection records its hash, verdict and report, never that reference body) and the
   hook supplies a `## CHANGES SINCE APPROVAL` diff; with none kept, the whole spec is gated.
   A full re-gate is still owed when the requirement set, Scope, Interfaces / contracts, `work_kind`
   or a `binding:` changed, and when the Verifier routed the phase back with a **coverage gap** —
   there the question is what the spec failed to require, so unchanged text is where to look. A
   first gate is always full.
4. **The implementer writes the tests, test-first; locked-after-verify.** Red → green per vertical
   slice (`skills/tdd`), never the whole suite up front. The implementer owns the phase's tests until
   `@avenger-verifier` passes it; from then they are **locked** and weakening one needs
   re-verification (adding is always allowed). Because the code's author wrote its judge, the
   **tests get read** over a bounded review set — tests mapped to the phase ∪ test files it changed,
   plus directly referenced helpers — for tautological / implementation-coupled / missing-negative
   patterns, and `wrong-gamed test` / `coverage gap` route back alongside `code`. `@avenger-verifier`
   picks the set and persists `verdict.json`; the judgement runs cross-family via
   `scripts/verifier_review.sh` (`$VERIFIER_GATE_MODEL`), because every subagent here is Anthropic. Three modes by `work_kind`, all in `skills/tdd`: greenfield (red→green)
   · migration (parity-first, existing suite is the contract) · refactor (baseline-first, behavior
   unchanged). Plus **e2e-author**, run once per feature after the last phase is green.
4a. **Tiered binding, then integration by default.** Every requirement declares `binding:` —
   **`e2e`** (an end user can observe it → carried by a **journey** with the other `e2e` requirements
   on its path, never its own test) · **`integration`** (visible *only* under concurrency, fault
   injection or schema migration, **and the spec says in one sentence why an e2e cannot see it** →
   its own test) · **`none`** (structural/build-time → **no test**; CI, a type checker, or nothing).
   This replaced paired pass/fail criteria on every requirement, which tied suite size to id count:
   288 ids became 458 tests at 4.87:1 test-to-source. A red journey names a journey, not a line —
   that is the accepted trade.
   Whatever does get a test drives it through a **seam** (the public entry point a caller uses), with
   real collaborators; mock only trust/cost boundaries. `test-mapping.md` carries `level`:
   `integration` (default) · `e2e` · `narrow` — **`narrow` needs a written justification**. `binding`
   says *whether and where*, `level` says *how*; a journey's row is `level: e2e` and lists several
   ids, and every requirement not marked `none` appears in some row. Migration is exempt (parity
   outranks the default).
   **The Verifier judges coverage per `binding:`, not per id** — a `binding: e2e` requirement is
   covered by the journey listing it, and `binding: none` is never a gap; the old one-test-per-id
   reading would route back a gap on every deliberately unbound requirement (`skills/verifier-triage`).
4b. **Feature-level e2e**: 1-3 tests (5 max) in `tests/e2e/<feature>/`, tracing to the goal in
   `overview.md` — the one exception to "no spec id → no test". Recorded in `e2e-mapping.md`. Excluded
   from mutation and from the phase verifier hook. **Unchanged by 4a**: the journeys carrying
   `binding: e2e` requirements are *phase*-level and live with the phase's tests; this ceiling governs
   `tests/e2e/<feature>/` only.
5. **Phases run in dependency/risk order**, one at a time, fully through build-and-verify.
6. **Fresh model ≠ author** — every per-phase gate runs on a cross-family model (family ≠ author).
   The one sanctioned exception is the feature-close `no-mistakes` ship gate (`.no-mistakes.yaml`),
   documented in `skills/pipeline-conventions/SKILL.md`.
7. **Gates fail closed, and a stop names its own cause.** A gate that cannot reach a verdict (incl.
   same-family) stops; it never passes. Every stop carries a `cause=` from `scripts/gate_errors.py`
   plus the provider's own words verbatim — a timeout kill, an HTTP 402 and an unreachable provider
   used to be one indistinguishable line, and a day went into reading one as a model failure.
   Four things keep that honest: a hook's `hooks.json` budget must exceed `GATE_CALL_TIMEOUT` plus
   headroom (`scripts/gate_timeouts.py`, asserted); a timeout kills the child's whole **process
   group** and reports measured wall clock (`scripts/proc_group.py`); a rejection emits its report
   and records the judged hash **with its verdict**; and the runner must identify itself
   (`scripts/gate_runner_guard.sh`) rather than being trusted by path.
   Break-glass `GATE_BYPASS="reason"` is logged to `gate-overrides.log`, shown, and recorded in `handover.md`.
   The reason is prose, so under `--auto` it comes from a file — see rule 10.
8. **Mutation score, not coverage.** cosmic-ray, once per phase, **diff-scoped** via `cr-filter-git`.
   The verdict is **deterministic** (`scripts/mutation_score.py`, not a model): score `>=
   MUTATION_MIN_SCORE` (default **0.85**) → GO with no model call; below → survivors are named as
   missing cases and the phase routes back to the implementer. Not 100% on purpose. Baseline-guarded:
   a failing suite would otherwise score 1.0, since a mutant counts as killed whenever tests fail.
   **Optional and OFF by default**: `MUTATION_POLICY` = `off` (default) · `advisory` (reports, never
   blocks) · `enforce` (fails closed). An extra signal, **not** the independence mechanism — that is
   the Verifier's test-quality review. When off, no mutation tool runs anywhere.
9. **Two learning logs, kept apart.** `docs/lessons/` (`skills/self-improvement`) is **per project**
   and about the **work** — a pytest trap, a migration gotcha; any agent appends when something is
   learning-worthy, and reads the *index only* at start, filtered to its role, opening just the prose
   files that matter. **Every lesson carries a `cost`** — tests added, invocations, runtime, tokens —
   and, where the rule could justify unbounded growth, its limit. Ten lessons once came out of one
   feature with **none** about cost; a true rule like "'it already works' precedes a silent
   regression" licenses writing tests forever when nothing bounds it. `docs/features/<feature>/pipeline-observations.md`
   (`skills/pipeline-retrospective`) is about the **machinery** — a gate that misfires, a stage that
   churns — written by the orchestrator as things happen, triaged at feature close, and filed upstream
   on the agentic-avengers repo. On Claude Code the lessons pointer is injected by
   `scripts/hook_lessons.sh` on `SubagentStart`; **opencode has no subagent-start event**, so on this
   runtime this paragraph is the delivery — load `skills/self-improvement` yourself.
10. **Prose belongs in a file and the command reads it — never on a command line.** Under
   `/avenger-run --auto`, `hook_autoapprove.sh` matches its hard-deny regex against the **whole**
   command string, so free text that merely *names* `git push` or `gh pr create` denies the command
   carrying it. Write the text to a file and read it inline — `--intent "$(cat <file>)"`, `--note
   "$(cat <file>)"` — never via `cat`/`echo`/a heredoc, which puts it straight back. **This is an
   invariant, not a list of flags**: `--intent`/`--instructions` on `no-mistakes axi`,
   `--note`/`--evidence` on `scripts/pipeline_observations.py append`, and a `GATE_BYPASS` reason are
   non-exhaustive examples, and absence from that list is not a waiver. `GATE_BYPASS` is no exception
   for being a shell assignment prefix — `GATE_BYPASS="$(cat <file>)" git commit …` works, while
   `export` does not survive between an agent's Bash calls; a multi-line reason file is safe because
   every writer of `gate-overrides.log` normalises the reason through `scripts/bypass_reason.sh`
   through `scripts/bypass_reason.sh`, so the log keeps one parseable record per override. Nothing
   appends to it by hand — the Verifier's per-finding waiver runs
   `bypass_log.sh verifier <finding-id> <waived_by>` too. A value fully determined by a template,
   with only ids, paths and keywords substituted, is not prose and stays inline. Full rule in
   `skills/pipeline-conventions/SKILL.md`.
11. **The pipeline measures itself as it runs, into a record firstmate owns.** Per-phase metrics —
   gate calls with their measured latency and failure `cause`, spec rounds and their byte growth,
   verification attempts, tests before/after, which skills each stage actually loaded, and **which
   stage found each defect** — are written by the stage that observes each fact, at the moment it
   observes it, through `scripts/metrics_sink.py` into firstmate's `fm-pipeline-metrics.sh`. The
   schema is theirs (`docs/pipeline-metrics.md`); this repo adds no key and keeps no second store.
   **Measurement, never a gate**: every failure is swallowed and logged to `.avenger-metrics.log`,
   and no metrics call can fail a phase. Emission attaches to the fact — inside `gate_runner.py`,
   not once per caller — so a new gate is instrumented by existing. Record a defect no script can
   see (Breaker, a probe, running the real path) with `scripts/pipeline_metrics.py defect`, whose
   `--summary` follows rule 10. Off unless `fm-pipeline-metrics.sh` is on `PATH` or
   `AVENGER_METRICS_CMD` names it, and an unconfigured run says so once. Full rule in
   `skills/pipeline-conventions/SKILL.md`.

## Running it
Plan once per feature, then loop per phase. Invoke agents with `@name`:
```
@avenger-task-analyst "<feature brief>"   # sets work_kind: greenfield|migration|refactor
@avenger-solution-architect
@avenger-implementation-planner           # phases, each with candidate specs <n>.<k>
@avenger-spec-writer                      # writes specs/<n>.<k>-<subslug>/spec.md -> fidelity gate runs
/spec-review <spec>               # HITL grill-me; add --auto (or SPEC_REVIEW_MODE=auto) for automated review -> flips review_status: approved
# per phase, in dependency order, per spec:
@avenger-backend-architect <spec>         # or @avenger-frontend-developer
                                          # writes tests + code test-first (mode by work_kind),
                                          # then sets status: done -> phase suite smoke-checked
# once all specs in the phase are green:
@avenger-verifier         <phase>         # cross-family: suite + R-trace + bounded TEST REVIEW
                                          # -> writes verdict.json; on pass the phase's tests LOCK
@avenger-handover         <phase>         # mirrors the verdict + any waivers into handover.md
# once the FINAL phase of the feature is green:
@avenger-backend-architect --e2e <feature> # 1-3 feature-level e2e tests -> tests/e2e/<feature>/
# then feature close, in this order:
git add -A && git commit                  # commit 1: the e2e output — the ship gate needs a clean tree
no-mistakes axi run --intent "$(cat <intent-file>)"   # SHIP GATE: lint, docs, push, PR, CI.
                                          # Runs in both modes. Stops at checks-passed, never merges.
                                          # Intent comes from a FILE, never inline: the --auto deny
                                          # regex matches the whole command string.
# retrospective triage (interactive only): lavish-axi over the observation log, then
git add -A && git commit                  # commit 2: the observation log, after triage
```

## Models / provider
Build agents use OpenRouter model ids set in `scripts/sync_opencode.py` (`MODEL_MAP`). Authenticate
once with OpenRouter (`opencode auth login`) or export `OPENROUTER_API_KEY`. The gate models
(DeepSeek/Gemini) are called by `gate_runner.py` via `OPENROUTER_API_KEY`.

## Regenerate after editing canonical files
The canonical agents live in `agents/` and skills in `skills/`. After changing either:
```
python3 scripts/sync_opencode.py
```
This re-transpiles `.opencode/agents/` and ensures `.opencode/skills/` is linked. Do not edit
`.opencode/agents/` by hand — it is generated.

**`.opencode/plugin/pipeline-gates.ts` is not generated either — but it needs no maintenance.** It is
a thin adapter: it turns an opencode tool event into the same PostToolUse payload and runs the same
`scripts/hook_*.sh`. The gates have **one** implementation. Change a threshold, a trigger, or a
fail-closed rule in `scripts/` and both runtimes get it; the plugin does not need editing and must not
grow logic of its own. (It used to reimplement every gate in TypeScript, and the two copies drifted —
the TS side kept a zero-survivor mutation gate and an unscoped verifier after the bash side moved on.)

## Environment
| var | default | effect |
|---|---|---|
| `MUTATION_POLICY` | `off` | `off` (skip) \| `advisory` (report only) \| `enforce` (fail closed) |
| `MUTATION_MIN_SCORE` | `0.85` | mutation score required to pass the per-phase gate |
| `MUTATION_BASE` | merge-base with default branch | diff base for scoping mutants |
| `PHASE` | most recent phase dir | which phase's tests the verifier hook runs |
| `GATE_MODEL` | per-gate defaults | routes every gate to one model |
| `GATE_BYPASS` | unset | break-glass: logged, visible, never silent |
| `VERIFIER_GATE_MODEL` | `google/gemini-3.1-pro-preview` | model the Verifier's test-quality review runs on; must not be the implementer's family |
| `VERIFIER_SCOPE` | unset | `full` sends the Verifier the whole phase; by default the bundle carries only the specs whose text changed since the last completed review, names the rest as carried forward, and merges their findings back (an open carried finding still forces NO-GO). A spec still holding an open finding is never carried — a finding fixed in a test file changes no spec text, so carrying it would mean nothing ever regenerates it |
| `GATE_CALL_TIMEOUT` | `300` | seconds the provider call gets. The gate hooks refuse to run if their `hooks.json` budget cannot outlive it plus headroom — raise this and raise `hooks/hooks.json` with it |
| `GATE_MODEL_FAMILY` | unset | declare a gate model's vendor family when `scripts/model_vendors.py` has no entry for it; without it an unknown vendor is refused, never guessed |
| `GATE_RUNNER_SHA256` | unset | pin the gate runner by content digest. Callers already refuse a runner that cannot identify itself; this refuses any but the exact one named |
| `VERIFIER_SRC_LIMIT` | `400000` | max chars of **review-set source** sent to that model — it does *not* bound the whole bundle, whose specs, test-mappings and test output are extra and uncounted. A set **over** the cap is refused (exit 2) *before* the model is called — a truncated review is an unreviewed phase. Raise it only to what the gate model can actually read, or split the set |
| `SUBPROC_CHECK_PATHS` | `tests/` | os.pathsep-separated roots the subprocess cost check scans; an absent root scans nothing (CLEAN, reported on stderr) |
| `LESSONS_AGENTS` | `avenger-` | which subagents get the lessons pointer (Claude Code hook only) |
| `LESSONS_OFF` | unset | `1` disables the lessons pointer everywhere |
| `AVENGER_METRICS_CMD` | looked up on `PATH` | path to firstmate's `fm-pipeline-metrics.sh`; without it a run records no metrics and says so once |
| `AVENGER_METRICS_OFF` | unset | `1` disables metrics emission silently |
| `AVENGER_METRICS_PROJECT` | the git repository's name | the record's `project`, which scopes every firstmate lookup |
| `AVENGER_METRICS_TIMEOUT` | `10` | seconds one metrics call may take; one timeout abandons the writer for that process |
| `SKILL_LOAD_OFF` | unset | `1` disables the skill-load observation hook |

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

`AGENTS.md` and `CLAUDE.md` are both real files here on purpose — one per runtime, opencode and
Claude Code — and `skills/pipeline-conventions/SKILL.md` is the canonical source both mirror. A rule
that changes belongs in the skill first, then in whichever of these two the change is visible from.
Do not collapse them into a symlink.
