import type { Plugin } from "@opencode-ai/plugin"
declare const process: any

// Mid-session pipeline gates for opencode.
//
// This file used to REIMPLEMENT every gate in TypeScript, in parallel with scripts/hook_*.sh. That
// was two sources of truth for one set of rules, and they drifted: the TS copy kept a zero-survivor
// mutation gate and an unscoped full-suite verifier after the bash side had moved on, so the two
// runtimes silently enforced different standards and only one of them was the one under test.
//
// So it no longer implements anything. It is an ADAPTER: it turns an opencode tool event into the
// PostToolUse payload the bash hooks already read on stdin, and runs those. The gates live in
// scripts/hook_*.sh, once. Every routing decision — which path triggers what, thresholds, diff
// scoping, fail-closed rules, break-glass — is theirs. Adding a gate here means adding it to
// hooks/hooks.json and scripts/, not to this file.
//
// Requires: bash, python3, and the same env the hooks read (OPENROUTER_API_KEY, GATE_MODEL,
// MUTATION_MIN_SCORE, SPEC_REVIEW_MODE, GATE_BYPASS, …). GATE_PROVIDER defaults to openrouter here:
// the hooks default to the opencode CLI, and having opencode shell out to itself mid-session is a
// bad idea.

const HOOKS = [
  "hook_fidelity.sh",
  "hook_spec_review.sh",
  "hook_verifier.sh",
  "hook_mutation.sh",
] as const

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

  return {
    "tool.execute.after": async (a: any, b: any) => {
      const { tool, path } = extract(a, b)
      if (!path || (tool !== "write" && tool !== "edit")) return

      // The same JSON shape Claude Code's PostToolUse hooks receive. Each hook self-filters by path
      // and exits 0 when it has nothing to say, so running all of them in order is exactly what the
      // Claude Code runtime does — and keeps the ordering guarantee (verifier before mutation).
      // Claude Code sends an ABSOLUTE path; match that, or a hook that opens the file directly would
      // miss it and fail closed on what is really a path bug.
      const absolute = path.startsWith("/") ? path : `${ROOT}/${path.replace(/^\.\//, "")}`
      const payload = JSON.stringify({ tool_input: { file_path: absolute } })

      for (const hook of HOOKS) {
        const res = await $`printf %s ${payload} | bash ${ROOT}/scripts/${hook}`
          .env({
            ...process.env,
            CLAUDE_PROJECT_DIR: ROOT,
            GATE_PROVIDER: process.env.GATE_PROVIDER || "openrouter",
          })
          .nothrow()
          .quiet()

        // Hooks are fail-closed: non-zero means stop and show the report. Break-glass is handled
        // inside the hooks (logged + visible), so a bypassed gate already exits 0 by the time we
        // see it — there is nothing to re-decide here.
        if (res.exitCode !== 0) {
          const report =
            (res.stderr?.toString?.() ?? "") || (res.stdout?.toString?.() ?? "")
          throw new Error(`[pipeline gate] ${hook} stopped this turn:\n${report}`)
        }
      }
    },
  }
}
