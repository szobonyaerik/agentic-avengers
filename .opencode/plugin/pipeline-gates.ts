import type { Plugin } from "@opencode-ai/plugin"
declare const Bun: any

// Mid-session pipeline gates for opencode — the native equivalent of the Claude Code hooks.
// After a write/edit the plugin routes by path:
//   */spec.md                   -> fidelity gate
//   */src/*                     -> run tests; triage failures
//   */implementation-report.md  -> mutation gate
// On failure it THROWS, so opencode surfaces the gate report to the agent and it stops to fix.
// Gates call gate_runner.py with --provider openrouter (cross-family, decorrelated from the
// opencode build model). Requires OPENROUTER_API_KEY in the environment.
//
// Place at .opencode/plugin/pipeline-gates.ts. Adjust the "/src/" match to your code layout.

const FIDELITY_MODEL = "deepseek/deepseek-chat"
const TRIAGE_MODEL = "google/gemini-2.5-pro"

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
        /[\w./-]*\/(?:spec\.md|implementation-report\.md)|[\w./-]*\/src\/[\w./-]+/
      )
      path = m ? m[0] : undefined
    }
    return { tool, path }
  }

  async function gate(rubric: string, model: string, target: string) {
    const res = await $`python3 ${ROOT}/scripts/gate_runner.py --rubric ${ROOT}/prompts/${rubric} --model ${model} --provider openrouter --target ${target}`
      .nothrow()
      .quiet()
    if (res.exitCode !== 0) {
      throw new Error(`[pipeline gate] ${rubric} stopped this turn:\n${res.stderr?.toString?.() ?? res.stderr}`)
    }
  }

  return {
    "tool.execute.after": async (a: any, b: any) => {
      const { tool, path } = extract(a, b)
      if (!path || (tool !== "write" && tool !== "edit")) return

      // 1) fidelity gate on spec writes
      if (path.endsWith("/spec.md")) {
        await gate("fidelity-rubric.md", FIDELITY_MODEL, path)
        return
      }

      // 2) verifier: run the suite on code changes; triage failures (exit 5 = no tests = ok)
      if (path.includes("/src/")) {
        const t = await $`cd ${ROOT} && pytest -q`.nothrow().quiet()
        if (t.exitCode !== 0 && t.exitCode !== 5) {
          const tmp = `${ROOT}/.gate-tmp.txt`
          await Bun.write(tmp, (t.stdout?.toString?.() ?? "") + (t.stderr?.toString?.() ?? ""))
          await gate("verifier-triage.md", TRIAGE_MODEL, tmp)
        }
        return
      }

      // 3) mutation gate when a phase implementation-report is written
      if (path.endsWith("/implementation-report.md")) {
        const m = await $`cd ${ROOT} && (mutmut run; echo '---- results ----'; mutmut results) 2>&1`
          .nothrow()
          .quiet()
        const tmp = `${ROOT}/.gate-tmp.txt`
        await Bun.write(tmp, (m.stdout?.toString?.() ?? "") + (m.stderr?.toString?.() ?? ""))
        await gate("mutation-interpret.md", TRIAGE_MODEL, tmp)
        return
      }
    },
  }
}