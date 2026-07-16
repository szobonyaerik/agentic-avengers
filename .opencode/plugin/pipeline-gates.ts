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

export const PipelineGates: Plugin = async ({ $, worktree, directory }) => {
  const ROOT = worktree || directory

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

      // 2) verifier: run the suite on code changes; triage failures (exit 5 = no tests = ok)
      if (path.includes("/src/")) {
        const t = await $`cd ${ROOT} && pytest -q`.nothrow().quiet()
        if (t.exitCode !== 0 && t.exitCode !== 5) {
          const tmp = `${ROOT}/.gate-tmp.txt`
          await Bun.write(tmp, (t.stdout?.toString?.() ?? "") + (t.stderr?.toString?.() ?? ""))
          await gate("verifier", "verifier-triage.md", TRIAGE_MODEL, tmp)
        }
        return
      }

      // 3) per-phase mutation gate (cosmic-ray) when the phase handover is written
      if (path.endsWith("/handover.md")) {
        const cfg = `${ROOT}/cosmic-ray.toml`
        if (!(await Bun.file(cfg).exists())) {
          const reason = process.env.GATE_BYPASS
          if (reason) return bypass("mutation:no-config", reason)
          throw new Error(`[pipeline gate] mutation: cosmic-ray.toml missing at repo root (fail closed)`)
        }
        const session = `${ROOT}/session.sqlite`
        const tmp = `${ROOT}/.gate-tmp.txt`
        await $`rm -f ${session}`.nothrow().quiet()
        const run = await $`cd ${ROOT} && cosmic-ray init cosmic-ray.toml session.sqlite && cosmic-ray exec cosmic-ray.toml session.sqlite`
          .nothrow()
          .quiet()
        if (run.exitCode !== 0) {
          await $`rm -f ${session}`.nothrow().quiet()
          const reason = process.env.GATE_BYPASS
          if (reason) return bypass("mutation:errored", reason)
          throw new Error(`[pipeline gate] mutation: cosmic-ray run errored (fail closed):\n${run.stderr?.toString?.() ?? ""}`)
        }
        const dump = await $`cd ${ROOT} && (echo '---- survivors (cosmic-ray dump) ----'; cosmic-ray dump session.sqlite; echo '---- survival rate (cr-rate) ----'; cr-rate session.sqlite) 2>&1`
          .nothrow()
          .quiet()
        await Bun.write(tmp, dump.stdout?.toString?.() ?? "")
        await $`rm -f ${session}`.nothrow().quiet()
        await gate("mutation", "mutation-interpret.md", TRIAGE_MODEL, tmp)
        return
      }
    },
  }
}
