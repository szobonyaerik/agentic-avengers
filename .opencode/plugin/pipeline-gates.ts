import type { Plugin } from "@opencode-ai/plugin"
declare const Bun: any
declare const process: any

// Mid-session pipeline gates for opencode — the native equivalent of the Claude Code hooks.
// After a write/edit the plugin routes by path:
//   */spec.md                   -> fidelity gate (cross-family)
//   */src/*                     -> run tests; triage failures
//   */handover.md               -> per-phase mutation gate (cosmic-ray)  [Verifier runs once per phase]
// On failure it THROWS, so opencode surfaces the gate report to the agent and it stops to fix.
// Gates call gate_runner.py with --provider openrouter and --author-family (decorrelated from the
// opencode build model). Requires OPENROUTER_API_KEY in the environment.
// Break-glass: GATE_BYPASS="reason" converts a failing gate into a logged, visible override.
//
// Place at .opencode/plugin/pipeline-gates.ts. Adjust the "/src/" match to your code layout.

// GATE_MODEL, if set, routes every gate to one model; otherwise the decorrelated defaults apply.
const GATE_MODEL = process.env.GATE_MODEL
const FIDELITY_MODEL = GATE_MODEL || "deepseek/deepseek-chat"
const TRIAGE_MODEL = GATE_MODEL || "google/gemini-2.5-pro"
const REVIEW_MODEL = GATE_MODEL || "google/gemini-2.5-pro"
const AUTHOR_FAMILY = process.env.AUTHOR_FAMILY || "anthropic"
// Mutation gate passes at this score (NOT 1.0 — demanding zero survivors turns the Test-Author into
// a mutant-farming loop). MUTATION_BASE overrides the diff base used to scope mutants.
const MUTATION_MIN_SCORE = process.env.MUTATION_MIN_SCORE || "0.85"

export const PipelineGates: Plugin = async ({ $, worktree, directory }) => {
  const ROOT = worktree || directory

  // The active phase's test dir (tests/<slug>), or undefined -> caller falls back to the full suite.
  // $PHASE wins; otherwise the most recently touched docs/features/*/phases/*/ that has tests.
  async function resolvePhaseTests(): Promise<string | undefined> {
    // `test -d`, not Bun.file().exists() — a BunFile reports whether a regular *file* exists, so it
    // is false for a directory, which would make this always fall back to the full suite.
    const has = async (slug: string): Promise<boolean> => {
      if (!slug) return false
      const res = await $`test -d ${ROOT}/tests/${slug}`.nothrow().quiet()
      return res.exitCode === 0
    }
    const envPhase = process.env.PHASE
    if (envPhase) return (await has(envPhase)) ? `tests/${envPhase}` : undefined
    const listed = await $`cd ${ROOT} && ls -td docs/features/*/phases/*/ 2>/dev/null`.nothrow().quiet()
    for (const dir of (listed.stdout?.toString?.() ?? "").split("\n")) {
      const slug = dir.trim().replace(/\/$/, "").split("/").pop()
      if (slug && (await has(slug))) return `tests/${slug}`
    }
    return undefined
  }

  // Diff base for scoping mutants: $MUTATION_BASE, else merge-base with the default branch.
  // Empty -> caller mutates the full module-path rather than silently scoping to nothing.
  async function resolveMutationBase(): Promise<string> {
    if (process.env.MUTATION_BASE) return process.env.MUTATION_BASE
    for (const ref of ["origin/HEAD", "main", "master"]) {
      const res = await $`git -C ${ROOT} merge-base HEAD ${ref}`.nothrow().quiet()
      const sha = (res.stdout?.toString?.() ?? "").trim()
      if (res.exitCode === 0 && sha) return sha
    }
    return ""
  }

  // Pull the tool name + written path out of the (version-varying) hook payload.
  function extract(a: any, b: any): { tool?: string; path?: string } {
    const ev: any = { ...(a || {}), ...(b || {}) }
    const tool = ev.tool ?? ev.input?.tool ?? a?.tool
    const args = ev.args ?? ev.arg ?? ev.input?.args ?? ev.input ?? {}
    let path: string | undefined = args.filePath ?? args.path ?? args.file
    if (!path) {
      const m = JSON.stringify({ a, b }).match(
        /[\w./-]*\/(?:spec\.md|handover\.md)|[\w./-]*\/src\/[\w./-]+/
      )
      path = m ? m[0] : undefined
    }
    return { tool, path }
  }

  // Log + surface a break-glass override, then swallow the failure. Never silent.
  async function bypass(gate: string, reason: string) {
    const when = new Date().toISOString()
    let who = "unknown"
    try { who = (await $`git -C ${ROOT} config user.email`.quiet().nothrow()).stdout?.toString?.().trim() || "unknown" } catch {}
    const line = `${when}\t${who}\tgate:${gate}\treason: ${reason}\n`
    await Bun.write(`${ROOT}/gate-overrides.log`, line, { createPath: true }).catch(async () => {
      // append fallback
      const prev = await Bun.file(`${ROOT}/gate-overrides.log`).text().catch(() => "")
      await Bun.write(`${ROOT}/gate-overrides.log`, prev + line)
    })
    console.warn(`⚠ BYPASSED gate '${gate}' — reason: ${reason} (logged to gate-overrides.log; record in handover.md)`)
  }

  // Automated spec-review: unattended cross-family checklist gate. GO/REVIEW -> stamp
  // review_status: approved in place; NO-GO -> throw (fail closed). Only runs in SPEC_REVIEW_MODE=auto.
  async function specReviewAuto(path: string) {
    const res = await $`python3 ${ROOT}/scripts/gate_runner.py --rubric ${ROOT}/prompts/spec-review-rubric.md --model ${REVIEW_MODEL} --provider openrouter --author-family ${AUTHOR_FAMILY} --print-verdict --target ${path}`
      .nothrow()
      .quiet()
    if (res.exitCode !== 0) {
      const reason = process.env.GATE_BYPASS
      if (reason) return bypass("spec-review-auto", reason)
      throw new Error(`[pipeline gate] spec-review (auto) errored (fail closed):\n${res.stderr?.toString?.() ?? ""}`)
    }
    const verdict = (res.stdout?.toString?.() ?? "").trim().split(/\s+/)[0]
    if (verdict === "GO" || verdict === "REVIEW") {
      const text = await Bun.file(path).text()
      if (/^review_status:\s*pending/m.test(text)) {
        await Bun.write(path, text.replace(/^review_status:\s*pending/m, "review_status: approved"))
        console.warn(`spec-review (auto): ${verdict} -> review_status: approved (${path})`)
      }
      return
    }
    const reason = process.env.GATE_BYPASS
    if (reason) return bypass("spec-review-auto", reason)
    throw new Error(`[pipeline gate] spec-review (auto): NO-GO — route back to avenger-spec-writer\n${res.stderr?.toString?.() ?? ""}`)
  }

  async function gate(name: string, rubric: string, model: string, target: string) {
    const res = await $`python3 ${ROOT}/scripts/gate_runner.py --rubric ${ROOT}/prompts/${rubric} --model ${model} --provider openrouter --author-family ${AUTHOR_FAMILY} --target ${target}`
      .nothrow()
      .quiet()
    if (res.exitCode !== 0) {
      const reason = process.env.GATE_BYPASS
      if (reason) return bypass(name, reason)
      throw new Error(`[pipeline gate] ${name} stopped this turn:\n${res.stderr?.toString?.() ?? res.stderr}`)
    }
  }

  return {
    "tool.execute.after": async (a: any, b: any) => {
      const { tool, path } = extract(a, b)
      if (!path || (tool !== "write" && tool !== "edit")) return

      // 1) fidelity gate on spec writes, then automated spec-review when SPEC_REVIEW_MODE=auto
      if (path.endsWith("/spec.md")) {
        await gate("fidelity", "fidelity-rubric.md", FIDELITY_MODEL, path)
        if (process.env.SPEC_REVIEW_MODE === "auto") await specReviewAuto(path)
        return
      }

      // 2) verifier: run the ACTIVE PHASE's suite on code changes; triage failures (exit 5 = ok).
      //    Scoped on purpose — this fires on every edit and its output is piped to a model, so the
      //    whole suite with full tracebacks made every edit pay for every test in the repo.
      //    Mirrors scripts/hook_verifier.sh; keep the two in step.
      if (path.includes("/src/")) {
        const testPath = await resolvePhaseTests()
        const t = testPath
          ? await $`cd ${ROOT} && pytest -q --tb=short ${testPath}`.nothrow().quiet()
          : await $`cd ${ROOT} && pytest -q --tb=short --ignore=tests/e2e`.nothrow().quiet()
        if (t.exitCode !== 0 && t.exitCode !== 5) {
          const tmp = `${ROOT}/.gate-tmp.txt`
          const scope = testPath ?? "full suite (no active phase resolved)"
          await Bun.write(
            tmp,
            `scope: ${scope}\n\n` + (t.stdout?.toString?.() ?? "") + (t.stderr?.toString?.() ?? "")
          )
          await gate("verifier", "verifier-triage.md", TRIAGE_MODEL, tmp)
        }
        return
      }

      // 3) per-phase mutation gate (cosmic-ray) when the phase handover is written.
      //    Deterministic verdict from scripts/mutation_score.py at MUTATION_MIN_SCORE, diff-scoped
      //    via cr-filter-git, baseline-guarded. Mirrors scripts/hook_mutation.sh — keep in step.
      if (path.endsWith("/handover.md")) {
        const cfg = `${ROOT}/cosmic-ray.toml`
        if (!(await Bun.file(cfg).exists())) {
          const reason = process.env.GATE_BYPASS
          if (reason) return bypass("mutation:no-config", reason)
          throw new Error(`[pipeline gate] mutation: cosmic-ray.toml missing at repo root (fail closed)`)
        }
        const session = `${ROOT}/.gate-session.sqlite`
        const scoped = `${ROOT}/.gate-cosmic-ray.toml`
        const tmp = `${ROOT}/.gate-tmp.txt`
        const cleanup = async () => { await $`rm -f ${session} ${scoped}`.nothrow().quiet() }
        await cleanup()

        // Scoped config = project config + a git-filter section naming the diff base.
        const base = await resolveMutationBase()
        const cfgText = await Bun.file(cfg).text()
        await Bun.write(
          scoped,
          base ? `${cfgText}\n[cosmic-ray.filters.git-filter]\nbranch = "${base}"\n` : cfgText
        )
        if (!base) console.warn("mutation: no diff base resolvable — mutating the full module-path")

        const fail = async (name: string, msg: string) => {
          await cleanup()
          const reason = process.env.GATE_BYPASS
          if (reason) return bypass(name, reason)
          throw new Error(`[pipeline gate] ${msg}`)
        }

        // Baseline FIRST: a mutant counts as killed whenever the test command fails, so a broken
        // suite scores a perfect 1.0. No kill means anything until the unmutated suite is green.
        const baseline = await $`cd ${ROOT} && cosmic-ray baseline ${scoped}`.nothrow().quiet()
        if (baseline.exitCode !== 0) {
          return fail(
            "mutation:baseline-failed",
            `mutation: baseline FAILED — the suite does not pass on unmutated code, so every mutant would score as killed. Refusing to score (fail closed):\n${baseline.stderr?.toString?.() ?? ""}`
          )
        }

        const init = await $`cd ${ROOT} && cosmic-ray init ${scoped} ${session}`.nothrow().quiet()
        if (init.exitCode !== 0) {
          return fail("mutation:errored", `mutation: cosmic-ray init errored (fail closed):\n${init.stderr?.toString?.() ?? ""}`)
        }
        if (base) {
          const filtered = await $`cd ${ROOT} && cr-filter-git --config ${scoped} ${session}`.nothrow().quiet()
          if (filtered.exitCode !== 0) {
            return fail("mutation:filter-errored", `mutation: cr-filter-git errored (fail closed) — refusing to mutate unscoped:\n${filtered.stderr?.toString?.() ?? ""}`)
          }
        }
        const run = await $`cd ${ROOT} && cosmic-ray exec ${scoped} ${session}`.nothrow().quiet()
        if (run.exitCode !== 0) {
          return fail("mutation:errored", `mutation: cosmic-ray exec errored (fail closed):\n${run.stderr?.toString?.() ?? ""}`)
        }

        // 0 = GO (no model call at all), 1 = below threshold, 2 = cannot score.
        const scored = await $`cd ${ROOT} && python3 scripts/mutation_score.py --min-score ${MUTATION_MIN_SCORE} ${session}`
          .nothrow()
          .quiet()
        if (scored.exitCode === 0) {
          await cleanup()
          return
        }
        if (scored.exitCode !== 1) {
          return fail("mutation:unscorable", `mutation: cannot score (fail closed):\n${scored.stderr?.toString?.() ?? ""}`)
        }

        // Below threshold: the model only turns each survivor into the missing test case.
        const dump = await $`cd ${ROOT} && (echo '---- mutation score (deterministic gate verdict: NO-GO) ----'; python3 scripts/mutation_score.py --min-score ${MUTATION_MIN_SCORE} --json ${session}; echo '---- survivors (cosmic-ray dump) ----'; cosmic-ray dump ${session}) 2>&1`
          .nothrow()
          .quiet()
        await Bun.write(tmp, dump.stdout?.toString?.() ?? "")
        await cleanup()
        await gate("mutation", "mutation-interpret.md", TRIAGE_MODEL, tmp)
        // gate() only throws when the runner errors; the verdict is already NO-GO, so stop regardless.
        const reason = process.env.GATE_BYPASS
        if (reason) return bypass("mutation", reason)
        throw new Error(
          `[pipeline gate] mutation: NO-GO — route back to avenger-test-author (add the cases named above).\n${scored.stderr?.toString?.() ?? ""}`
        )
      }
    },
  }
}
