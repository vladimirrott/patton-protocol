// Patton gate for OpenCode: every tool call goes through the shared bridge before it runs.
//
// OpenCode (v1.19.4) has no command hooks. A plugin's `tool.execute.before` runs in-process and blocks a call by
// throwing (packages/web/src/content/docs/plugins.mdx, ".env protection"). This plugin pipes the call to
//
//     python3 <lab>/patton_lab/harness/bridge.py opencode
//
// synchronously, with a time limit, and lets the call run only when the bridge exited 0 and printed
// {"allow": true}. A spawn error, a timeout, a non-zero exit or output it cannot read all block, with the reason
// in the error the model sees.
//
// The bridge may add "context", the gate's discipline reminder. When the call runs, `tool.execute.after` appends
// it to that call's output (OpenCode passes the output object through the hook and sends what the hook leaves in
// `output.output` to the model); when the call is refused, it follows the reason in the thrown error. Reminders
// are kept per callID and used once.
//
// Environment: PATTON_BRIDGE_CMD (a JSON array, replaces the bridge command; the lab uses it to fake a crash or a
// hang), PATTON_BRIDGE_TIMEOUT_MS (default 30000). PATTON_GATE_ROOT and PATTON_GATE_STATE pass through to the gate.

import { spawnSync } from "node:child_process"
import { fileURLToPath } from "node:url"

const BRIDGE = fileURLToPath(new URL("../../bridge.py", import.meta.url))

function bridgeCommand() {
  const raw = process.env.PATTON_BRIDGE_CMD
  if (!raw) return ["python3", BRIDGE, "opencode"]
  const parsed = JSON.parse(raw)
  if (!Array.isArray(parsed) || parsed.length === 0 || !parsed.every((part) => typeof part === "string")) {
    throw new Error("PATTON_BRIDGE_CMD is not a JSON array of strings")
  }
  return parsed
}

function timeoutMs() {
  const value = Number(process.env.PATTON_BRIDGE_TIMEOUT_MS || 30000)
  return Number.isFinite(value) && value > 0 ? value : 30000
}

function check(payload) {
  let command
  try {
    command = bridgeCommand()
  } catch (err) {
    return { allow: false, reason: `Patton gate plugin: ${err.message}, so it blocks this call.` }
  }
  const limit = timeoutMs()
  let done
  try {
    done = spawnSync(command[0], command.slice(1), {
      input: JSON.stringify(payload),
      encoding: "utf8",
      timeout: limit,
      killSignal: "SIGKILL",
      maxBuffer: 1 << 20,
      env: process.env,
    })
  } catch (err) {
    return { allow: false, reason: `Patton gate plugin could not run the bridge (${err.message}), so it blocks this call.` }
  }
  if (done.error) {
    const what = done.error.code === "ETIMEDOUT" ? `timed out after ${limit} ms` : `failed to start (${done.error.message})`
    return { allow: false, reason: `Patton gate bridge ${what}, so it blocks this call.` }
  }
  if (done.signal) {
    return { allow: false, reason: `Patton gate bridge was killed by ${done.signal}, so it blocks this call.` }
  }
  let answer
  try {
    answer = JSON.parse(String(done.stdout || "").trim().split("\n").pop())
  } catch {
    answer = null
  }
  const context = answer && typeof answer.context === "string" ? answer.context : ""
  if (done.status === 0 && answer && answer.allow === true) return { allow: true, context }
  const reason = answer && typeof answer.reason === "string" && answer.reason
    ? answer.reason
    : `Patton gate bridge exited ${done.status} with no readable answer, so it blocks this call.`
  return { allow: false, reason, context }
}

export const PattonGate = async ({ directory }) => {
  const reminders = new Map()
  return {
    "tool.execute.before": async (input, output) => {
      const verdict = check({
        tool: input.tool,
        args: output.args,
        sessionID: input.sessionID,
        callID: input.callID,
        directory,
      })
      if (!verdict.allow) throw new Error(verdict.context ? `${verdict.reason}\n\n${verdict.context}` : verdict.reason)
      if (verdict.context) reminders.set(input.callID, verdict.context)
    },
    "tool.execute.after": async (input, output) => {
      const context = reminders.get(input.callID)
      if (!context) return
      reminders.delete(input.callID)
      if (output && typeof output.output === "string") output.output = `${output.output}\n\n${context}`
    },
  }
}
