/**
 * Patton gate for oh-my-pi (can1357/oh-my-pi 18.12.6): on every tool call, ask the shared bridge and block unless
 * it allows; at the main session's stop, ask the bridge whether the gate refuses it.
 *
 *   omp -p --mode json -e /repo/lab/patton_lab/harness/omp/gate.ts ...
 *
 * The bridge runs synchronously: `python3 <PATTON_BRIDGE> omp`, the event as JSON on stdin, one JSON verdict on stdout.
 *
 * Tool calls fail closed. Only exit 0 with {"block": false} allows. A spawn error, a timeout
 * (PATTON_BRIDGE_TIMEOUT_MS, default 15000), a non-zero exit, a signal, or output that is not a verdict blocks the
 * call with a reason the model sees. omp also blocks when a tool_call handler throws or exceeds
 * extensionHandlers.toolCallTimeoutMs (default 30 000 ms); the spawn timeout stays below that, and the handler
 * catches everything so the reason stays ours. Handlers are rebound into subagent sessions (task tool), so calls
 * made by subagents pass through here too. A verdict may carry "context", the gate's discipline reminder: it goes
 * back as the call's `additionalContext`, which omp sends after the tool result with developer priority, or is
 * appended to the refusal when the call is blocked.
 *
 * Stops fail open. At `session_stop` (main session only; omp never fires it for subagents) the extension sends
 * {"event": "Stop", "final": <the last assistant text>, "active": stop_hook_active} and refuses the stop only on
 * exit 0 with {"block": true, "reason"}, as {decision: "block", reason}: omp queues the reason as a hidden
 * continuation message for the model's next request. Anything else, including a timeout
 * (PATTON_BRIDGE_STOP_TIMEOUT_MS, default 25000, below omp's 30 000 ms handler timeout), lets the session stop.
 * omp does not cap the `block` form, so the bridge caps it (PATTON_BRIDGE_STOP_CAP, default 4).
 */
import { spawnSync } from "node:child_process";

const DIALECT = "omp";
const BRIDGE = process.env.PATTON_BRIDGE || "/repo/lab/patton_lab/harness/bridge.py";
const PYTHON = process.env.PATTON_PYTHON || "python3";

function ms(name: string, fallback: number): number {
	const raw = Number(process.env[name]);
	return Number.isFinite(raw) && raw > 0 ? raw : fallback;
}

const TIMEOUT_MS = ms("PATTON_BRIDGE_TIMEOUT_MS", 15000);
const STOP_TIMEOUT_MS = ms("PATTON_BRIDGE_STOP_TIMEOUT_MS", 25000);
const SESSION = `omp-${process.pid}`;

type Verdict = { block: boolean; reason: string; context: string };

function refuse(why: string): Verdict {
	return { block: true, reason: `Patton gate: ${why}, so this call is blocked.`, context: "" };
}

function tail(text: unknown): string {
	const s = String(text ?? "").trim();
	return s ? ` (${s.slice(-300)})` : "";
}

function spawnBridge(payload: unknown, timeout: number) {
	return spawnSync(PYTHON, [BRIDGE, DIALECT], {
		input: JSON.stringify(payload),
		encoding: "utf8",
		timeout,
		killSignal: "SIGKILL",
		maxBuffer: 1 << 20,
		stdio: ["pipe", "pipe", "pipe"],
	});
}

export function askBridge(payload: unknown): Verdict {
	let r;
	try {
		r = spawnBridge(payload, TIMEOUT_MS);
	} catch (err) {
		return refuse(`the bridge could not be started (${String(err).slice(0, 200)})`);
	}
	if (r.error) {
		const code = (r.error as NodeJS.ErrnoException).code;
		if (code === "ETIMEDOUT") return refuse(`the bridge did not answer within ${TIMEOUT_MS} ms`);
		return refuse(`the bridge failed to run (${code || r.error.message})`);
	}
	if (r.status !== 0) {
		const how = r.status === null ? `was killed by ${r.signal}` : `exited with status ${r.status}`;
		return refuse(`the bridge ${how}${tail(r.stderr)}`);
	}
	let out: any;
	try {
		out = JSON.parse(String(r.stdout).trim());
	} catch {
		return refuse("the bridge printed no readable verdict");
	}
	const context = out && typeof out.context === "string" ? out.context : "";
	if (out && out.block === false) return { block: false, reason: "", context };
	if (out && out.block === true && typeof out.reason === "string" && out.reason) {
		return { block: true, reason: out.reason, context };
	}
	return refuse("the bridge printed no readable verdict");
}

/** The gate's reason for refusing this stop, or null to let the agent stop. Never throws. */
export function askStop(payload: unknown): string | null {
	try {
		const r = spawnBridge(payload, STOP_TIMEOUT_MS);
		if (r.error || r.status !== 0) {
			console.error(`Patton gate: the stop was not checked (${r.error ? r.error.message : `exit ${r.status}`})`);
			return null;
		}
		const out = JSON.parse(String(r.stdout).trim());
		return out && out.block === true && typeof out.reason === "string" && out.reason ? out.reason : null;
	} catch (err) {
		console.error(`Patton gate: the stop was not checked (${String(err).slice(0, 200)})`);
		return null;
	}
}

function text(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((p: any) => p && p.type === "text" && typeof p.text === "string")
		.map((p: any) => p.text)
		.join("\n");
}

/** The last assistant message's text in a list of agent messages. */
export function lastAssistantText(messages: unknown): string {
	if (!Array.isArray(messages)) return "";
	for (let i = messages.length - 1; i >= 0; i--) {
		const m: any = messages[i];
		if (m && m.role === "assistant") {
			const t = text(m.content);
			if (t.trim()) return t;
		}
	}
	return "";
}

export default function (pi: any) {
	pi.on("tool_call", async (event: any, ctx: any) => {
		let verdict: Verdict;
		try {
			verdict = askBridge({
				tool: event.toolName,
				input: event.input,
				cwd: (ctx && ctx.cwd) || process.cwd(),
				session: SESSION,
			});
		} catch (err) {
			verdict = refuse(`the extension failed (${String(err).slice(0, 200)})`);
		}
		if (verdict.block) {
			return { block: true, reason: verdict.context ? `${verdict.reason}\n\n${verdict.context}` : verdict.reason };
		}
		return verdict.context ? { additionalContext: verdict.context } : undefined;
	});

	pi.on("session_stop", async (event: any, ctx: any) => {
		try {
			const last = event && event.last_assistant_message;
			const final = last ? lastAssistantText([last]) : lastAssistantText(event && event.messages);
			const reason = askStop({
				event: "Stop",
				final,
				active: Boolean(event && event.stop_hook_active),
				cwd: (ctx && ctx.cwd) || process.cwd(),
				session: SESSION,
			});
			return reason ? { decision: "block", reason } : undefined;
		} catch (err) {
			console.error(`Patton gate: the stop was not checked (${String(err).slice(0, 200)})`);
			return undefined;
		}
	});
}
